"""CLI for the Zetherion Dev Agent."""

from __future__ import annotations

import asyncio
import sys

import click  # type: ignore[import-not-found]

from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.daemon import DevAutopilotDaemon
from zetherion_dev_agent.policy_store import PolicyStore


@click.group()  # type: ignore[misc]
def main() -> None:
    """Zetherion Dev Agent — monitors your development and reports to Zetherion."""


@main.command()  # type: ignore[misc]
@click.option("--webhook-url", prompt="Discord webhook URL", help="Discord webhook URL")  # type: ignore[misc]
@click.option(  # type: ignore[misc]
    "--repos",
    multiple=True,
    default=["."],
    help="Repository paths to watch (can specify multiple)",
)
@click.option("--agent-name", default="zetherion-dev-agent", help="Webhook username")  # type: ignore[misc]
def init(webhook_url: str, repos: tuple[str, ...], agent_name: str) -> None:
    """Initialize the dev agent configuration."""
    from pathlib import Path

    resolved_repos = [str(Path(r).resolve()) for r in repos]

    config = AgentConfig(
        webhook_url=webhook_url,
        agent_name=agent_name,
        repos=resolved_repos,
    )
    config.save()
    click.echo(f"Config saved. Watching {len(resolved_repos)} repo(s).")
    click.echo(f"  Agent name: {agent_name}")
    click.echo(f"  Repos: {', '.join(resolved_repos)}")
    click.echo("\nRun `zetherion-dev-agent watch` to start monitoring.")


@main.command()  # type: ignore[misc]
@click.option("--once", is_flag=True, help="Run a single scan then exit")  # type: ignore[misc]
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")  # type: ignore[misc]
def watch(once: bool, verbose: bool) -> None:
    """Start watching repositories for dev activity."""
    config = AgentConfig.load()
    if not config.webhook_url:
        click.echo("Error: No webhook URL configured. Run `zetherion-dev-agent init` first.")
        sys.exit(1)
    if not config.repos:
        click.echo("Error: No repos configured. Run `zetherion-dev-agent init` first.")
        sys.exit(1)

    click.echo(f"Watching {len(config.repos)} repo(s) (interval: {config.scan_interval}s)")
    if verbose:
        for r in config.repos:
            click.echo(f"  - {r}")

    asyncio.run(_watch_loop(config, once=once, verbose=verbose))


@main.command()  # type: ignore[misc]
def status() -> None:
    """Show current agent status and configuration."""
    from zetherion_dev_agent.state import ScanState

    config = AgentConfig.load()
    state = ScanState.load()

    click.echo("=== Zetherion Dev Agent ===\n")
    click.echo(f"Webhook URL: {'configured' if config.webhook_url else 'NOT SET'}")
    click.echo(f"Agent name:  {config.agent_name}")
    click.echo(f"Interval:    {config.scan_interval}s")
    click.echo(f"Repos:       {len(config.repos)}")
    for r in config.repos:
        sha = state.last_commit_sha.get(r, "none")
        annotations = len(state.known_annotations.get(r, {}))
        click.echo(f"  - {r}")
        click.echo(f"    Last SHA: {sha[:8] if sha != 'none' else 'none'}")
        click.echo(f"    Known annotations: {annotations}")

    click.echo(f"\nClaude Code: {'enabled' if config.claude_code_enabled else 'disabled'}")
    click.echo(f"Annotations: {'enabled' if config.annotations_enabled else 'disabled'}")
    click.echo(f"Git:         {'enabled' if config.git_enabled else 'disabled'}")
    click.echo(f"Container monitor: {'enabled' if config.container_monitor_enabled else 'disabled'}")
    click.echo(f"Cleanup:           {'enabled' if config.cleanup_enabled else 'disabled'}")
    click.echo(
        f"Cleanup schedule:  {config.cleanup_hour:02d}:{config.cleanup_minute:02d} "
        "(local time)"
    )
    click.echo(f"Autopilot API:     http://{config.api_host}:{config.api_port}/v1")


@main.command()  # type: ignore[misc]
@click.option("--once", is_flag=True, help="Run one discovery + cleanup cycle then exit")  # type: ignore[misc]
@click.option("--dry-run-cleanup", is_flag=True, help="Only plan cleanup actions during --once")  # type: ignore[misc]
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")  # type: ignore[misc]
def daemon(once: bool, dry_run_cleanup: bool, verbose: bool) -> None:
    """Run autopilot daemon (project discovery, approvals, and nightly cleanup)."""
    config = AgentConfig.load()
    token_created = config.ensure_api_token()
    if token_created:
        config.save()
        click.echo("Generated local API token in config.toml.")
    click.echo(
        f"Autopilot API endpoint: http://{config.api_host}:{config.api_port}/v1 "
        "(Authorization: Bearer <api_token>)"
    )
    if not config.webhook_url:
        click.echo("Webhook URL not configured; Discord prompts/reports will be skipped.")
    if once:
        daemon_runtime = DevAutopilotDaemon(config)
        result = asyncio.run(daemon_runtime.run_once(dry_run_cleanup=dry_run_cleanup))
        asyncio.run(daemon_runtime.close())
        click.echo("One-shot autopilot run complete.")
        click.echo(f"Projects discovered: {', '.join(result['projects_discovered']) or 'none'}")
        cleanup_summary = result["cleanup_summary"]
        click.echo(
            "Cleanup summary: "
            f"{cleanup_summary['success_count']} success, "
            f"{cleanup_summary['failure_count']} failed, "
            f"{cleanup_summary['project_count']} total project(s)."
        )
        return

    try:
        asyncio.run(_run_daemon(config, verbose=verbose))
    except KeyboardInterrupt:
        click.echo("Daemon stopped.")


@main.group()  # type: ignore[misc]
def policy() -> None:
    """Manage per-project cleanup policies."""


@policy.command("list")  # type: ignore[misc]
def policy_list() -> None:
    """List current per-project policies."""
    config = AgentConfig.load()
    store = PolicyStore(config.database_path)
    try:
        rows = store.list_policies()
    finally:
        store.close()
    if not rows:
        click.echo("No project policies configured yet.")
        return
    click.echo(f"Policies ({len(rows)}):")
    for row in rows:
        click.echo(
            f"  - {row['project_id']}: mode={row['mode']} "
            f"(source={row['source']}, updated={row['updated_at']})"
        )


@policy.command("set")  # type: ignore[misc]
@click.argument("project_id")  # type: ignore[misc]
@click.argument("mode", type=click.Choice(["ask", "auto_clean", "never_clean"]))  # type: ignore[misc]
@click.option("--notes", default="", help="Optional notes stored with the policy")  # type: ignore[misc]
def policy_set(project_id: str, mode: str, notes: str) -> None:
    """Set policy mode for a project ID."""
    config = AgentConfig.load()
    store = PolicyStore(config.database_path)
    try:
        store.set_policy(project_id, mode, source="cli", notes=notes)
    finally:
        store.close()
    click.echo(f"Policy set: {project_id} -> {mode}")


@policy.command("pending")  # type: ignore[misc]
def policy_pending() -> None:
    """List pending project approvals."""
    config = AgentConfig.load()
    store = PolicyStore(config.database_path)
    try:
        pending = store.list_pending_approvals()
    finally:
        store.close()
    if not pending:
        click.echo("No pending approvals.")
        return
    click.echo(f"Pending approvals ({len(pending)}):")
    for item in pending:
        click.echo(
            f"  - {item.project_id}: first_seen={item.first_seen_at}, "
            f"last_prompted={item.last_prompted_at or 'never'}, prompts={item.prompt_count}"
        )


@main.command(name="cleanup")  # type: ignore[misc]
@click.option("--project-id", default="", help="Run cleanup for one project ID")  # type: ignore[misc]
@click.option("--dry-run/--execute", default=True, help="Plan only or execute cleanup")  # type: ignore[misc]
def cleanup_command(project_id: str, dry_run: bool) -> None:
    """Run cleanup immediately using current policies."""
    config = AgentConfig.load()
    daemon_runtime = DevAutopilotDaemon(config)
    summary = asyncio.run(
        daemon_runtime.run_cleanup_cycle(
            dry_run=dry_run,
            project_id=project_id.strip() or None,
        )
    )
    asyncio.run(daemon_runtime.close())
    click.echo(
        f"Cleanup run complete: {summary.success_count} success, "
        f"{summary.failure_count} failed, {summary.project_count} project(s)."
    )


async def _run_daemon(config: AgentConfig, *, verbose: bool) -> None:
    """Run legacy watch loop and autopilot daemon together."""
    daemon_runtime = DevAutopilotDaemon(config)
    watch_task: asyncio.Task[None] | None = None
    if config.webhook_url and config.repos:
        watch_task = asyncio.create_task(_watch_loop(config, once=False, verbose=verbose))
    try:
        await daemon_runtime.run_forever()
    finally:
        if watch_task is not None:
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)


async def _watch_loop(config: AgentConfig, *, once: bool, verbose: bool) -> None:
    """Main watch loop."""
    from zetherion_dev_agent.sender import send_event
    from zetherion_dev_agent.state import ScanState
    from zetherion_dev_agent.watchers.annotations import (
        annotation_state_key,
        diff_annotations,
        parse_state_annotation,
        scan_annotations,
    )
    from zetherion_dev_agent.watchers.claude_code import get_new_sessions
    from zetherion_dev_agent.watchers.git import (
        get_latest_sha,
        get_new_commits,
        get_repo_name,
        get_tags,
    )

    state = ScanState.load()

    while True:
        events_sent = 0

        for repo_path in config.repos:
            project = get_repo_name(repo_path)

            # --- Git commits ---
            if config.git_enabled:
                last_sha = state.last_commit_sha.get(repo_path)
                commits = get_new_commits(repo_path, last_sha)

                for commit in commits:
                    fields = {
                        "project": project,
                        "sha": commit.sha[:7],
                        "files_changed": str(commit.files_changed),
                        "diff_summary": f"+{commit.insertions} -{commit.deletions}",
                        "branch": commit.branch,
                        "message": commit.message,
                    }
                    ok = await send_event(
                        config.webhook_url,
                        config.agent_name,
                        "commit",
                        commit.message,
                        fields,
                    )
                    if ok:
                        events_sent += 1
                    if verbose:
                        status = "sent" if ok else "FAILED"
                        click.echo(f"  [{status}] commit {commit.sha[:7]}: {commit.message[:60]}")

                    # Heuristic deploy marker: commits on main/release branches.
                    branch = commit.branch.lower()
                    if branch in {"main", "master"} or branch.startswith("release/"):
                        deploy_fields = {
                            "project": project,
                            "environment": "production",
                            "source": "heuristic_commit",
                            "commit_sha": commit.sha,
                            "branch": commit.branch,
                            "status": "candidate",
                            "title": f"Deploy candidate ({commit.branch})",
                        }
                        deploy_ok = await send_event(
                            config.webhook_url,
                            config.agent_name,
                            "deploy",
                            f"Deploy candidate: {commit.sha[:7]} on {commit.branch}",
                            deploy_fields,
                        )
                        if deploy_ok:
                            events_sent += 1
                        if verbose:
                            click.echo(
                                f"  [{'sent' if deploy_ok else 'FAILED'}] deploy "
                                f"candidate {commit.sha[:7]}"
                            )

                # Update state
                new_sha = get_latest_sha(repo_path)
                if new_sha:
                    state.last_commit_sha[repo_path] = new_sha

                # --- Tags ---
                current_tags = get_tags(repo_path)
                known = set(state.known_tags.get(repo_path, []))
                for tag in current_tags:
                    if tag.name not in known:
                        fields = {
                            "project": project,
                            "tag_name": tag.name,
                            "sha": tag.sha,
                        }
                        ok = await send_event(
                            config.webhook_url,
                            config.agent_name,
                            "tag",
                            f"New tag: {tag.name}",
                            fields,
                        )
                        if ok:
                            events_sent += 1
                        if verbose:
                            click.echo(f"  [{'sent' if ok else 'FAILED'}] tag {tag.name}")

                        # Heuristic deploy marker: new release tags.
                        deploy_fields = {
                            "project": project,
                            "environment": "production",
                            "source": "heuristic_tag",
                            "commit_sha": tag.sha,
                            "tag_name": tag.name,
                            "status": "tagged",
                            "title": f"Release tag {tag.name}",
                        }
                        deploy_ok = await send_event(
                            config.webhook_url,
                            config.agent_name,
                            "deploy",
                            f"Release tag observed: {tag.name}",
                            deploy_fields,
                        )
                        if deploy_ok:
                            events_sent += 1
                        if verbose:
                            click.echo(f"  [{'sent' if deploy_ok else 'FAILED'}] deploy tag marker")
                state.known_tags[repo_path] = [t.name for t in current_tags]

            # --- Annotations ---
            if config.annotations_enabled:
                current_annotations = scan_annotations(repo_path)

                # Reconstruct previous annotations from state
                old_annotations = []
                saw_legacy_keys = False
                for key, content in state.known_annotations.get(repo_path, {}).items():
                    annotation, is_legacy = parse_state_annotation(key, content)
                    if is_legacy:
                        saw_legacy_keys = True
                    if annotation is not None:
                        old_annotations.append(annotation)

                added, removed = diff_annotations(old_annotations, current_annotations)
                if saw_legacy_keys:
                    # Avoid one-time false "removed" noise while migrating old keys.
                    removed = []

                for ann in added:
                    fields = {
                        "project": project,
                        "annotation_type": ann.annotation_type,
                        "file": ann.file,
                        "line": str(ann.line),
                        "action": "added",
                    }
                    ok = await send_event(
                        config.webhook_url,
                        config.agent_name,
                        "annotation",
                        ann.content,
                        fields,
                    )
                    if ok:
                        events_sent += 1
                    if verbose:
                        click.echo(
                            f"  [{'sent' if ok else 'FAILED'}] +{ann.annotation_type}: "
                            f"{ann.content[:50]}"
                        )

                for ann in removed:
                    fields = {
                        "project": project,
                        "annotation_type": ann.annotation_type,
                        "file": ann.file,
                        "line": str(ann.line),
                        "action": "removed",
                    }
                    ok = await send_event(
                        config.webhook_url,
                        config.agent_name,
                        "annotation",
                        ann.content,
                        fields,
                    )
                    if ok:
                        events_sent += 1

                # Update state
                state.known_annotations[repo_path] = {
                    annotation_state_key(a): a.content for a in current_annotations
                }

            # --- Claude Code sessions ---
            if config.claude_code_enabled:
                sessions = get_new_sessions(repo_path, since=state.last_session_time or None)
                for session in sessions:
                    fields = {
                        "project": session.project,
                        "session_id": session.session_id[:12],
                        "duration_minutes": "0",
                        "tools_used": str(session.tools_used),
                        "summary": session.summary[:200],
                    }
                    ok = await send_event(
                        config.webhook_url,
                        config.agent_name,
                        "session",
                        session.summary[:500],
                        fields,
                    )
                    if ok:
                        events_sent += 1
                    if verbose:
                        click.echo(
                            f"  [{'sent' if ok else 'FAILED'}] session: " f"{session.summary[:50]}"
                        )

                if sessions:
                    state.last_session_time = sessions[0].timestamp

        state.save()

        if verbose or events_sent > 0:
            click.echo(f"Scan complete: {events_sent} event(s) sent")

        if once:
            break

        await asyncio.sleep(config.scan_interval)


if __name__ == "__main__":
    main()
