"""CLI for the Zetherion Dev Agent."""

from __future__ import annotations

import asyncio
import sys
import time

import click  # type: ignore[import-not-found]

from zetherion_dev_agent.config import AgentConfig


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


async def _watch_loop(config: AgentConfig, *, once: bool, verbose: bool) -> None:
    """Main watch loop."""
    from zetherion_dev_agent.sender import send_event
    from zetherion_dev_agent.state import ScanState
    from zetherion_dev_agent.watchers.annotations import diff_annotations, scan_annotations
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
                state.known_tags[repo_path] = [t.name for t in current_tags]

            # --- Annotations ---
            if config.annotations_enabled:
                current_annotations = scan_annotations(repo_path)

                # Reconstruct previous annotations from state
                from zetherion_dev_agent.watchers.annotations import Annotation

                old_annotations = []
                for key, content in state.known_annotations.get(repo_path, {}).items():
                    parts = key.split(":", 2)
                    if len(parts) >= 2:
                        old_annotations.append(
                            Annotation(
                                annotation_type=parts[0],
                                content=content,
                                file=parts[1] if len(parts) > 1 else "",
                                line=0,
                            )
                        )

                added, removed = diff_annotations(old_annotations, current_annotations)

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
                    f"{a.annotation_type}:{a.file}": a.content for a in current_annotations
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

        time.sleep(config.scan_interval)


if __name__ == "__main__":
    main()
