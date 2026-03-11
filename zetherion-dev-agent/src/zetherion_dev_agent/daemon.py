"""Local dev-environment autopilot daemon.

Coordinates:
- Docker project discovery
- Per-project approval memory
- Nightly cleanup for approved projects
- Local control API for UI/CLI integration
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.docker_monitor import CleanupResult, DockerMonitor, ProjectSnapshot
from zetherion_dev_agent.policy_store import PolicyMode, PolicyStore
from zetherion_dev_agent.sender import send_event


def _json_response(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


@dataclass
class CleanupCycleSummary:
    """Summary payload for one cleanup cycle."""

    started_at: str
    dry_run: bool
    project_count: int
    success_count: int
    failure_count: int
    results: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "dry_run": self.dry_run,
            "project_count": self.project_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "results": self.results,
        }


class DevAutopilotDaemon:
    """Asynchronous daemon for project discovery and cleanup automation."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        monitor: DockerMonitor | None = None,
        store: PolicyStore | None = None,
    ) -> None:
        self._config = config
        self._config.ensure_api_token()
        self._monitor = monitor or DockerMonitor()
        self._store = store or PolicyStore(config.database_path)
        self._projects: dict[str, ProjectSnapshot] = {}
        self._last_cleanup_date: str | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._stop_event = asyncio.Event()

    @property
    def store(self) -> PolicyStore:
        return self._store

    async def close(self) -> None:
        """Shutdown API server and close resources."""
        self._stop_event.set()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        self._store.close()

    # ------------------------------------------------------------------
    # Public loops
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Run discovery + cleanup loops until cancelled."""
        await self._start_api_server()
        discovery_task = asyncio.create_task(self._discovery_loop())
        cleanup_task = asyncio.create_task(self._cleanup_loop())
        try:
            await self._stop_event.wait()
        finally:
            discovery_task.cancel()
            cleanup_task.cancel()
            await asyncio.gather(discovery_task, cleanup_task, return_exceptions=True)
            await self.close()

    async def run_once(self, *, dry_run_cleanup: bool = False) -> dict[str, Any]:
        """Run one discovery cycle and one cleanup cycle."""
        await self.discovery_cycle()
        summary = await self.run_cleanup_cycle(dry_run=dry_run_cleanup)
        return {
            "projects_discovered": sorted(self._projects),
            "cleanup_summary": summary.to_dict(),
        }

    async def discovery_cycle(self) -> None:
        """Discover projects and trigger prompts for new/pending approvals."""
        if not self._config.container_monitor_enabled:
            return
        self._projects = self._monitor.discover_projects()

        for project_id, project in sorted(self._projects.items()):
            await self._emit_project_discovery(project)
            should_prompt = self._store.record_project_discovery(project_id)
            if should_prompt:
                await self._emit_approval_prompt(project, reason="new_project")

        due = self._store.list_reprompt_due(reprompt_hours=self._config.approval_reprompt_hours)
        for pending in due:
            project = self._projects.get(pending.project_id)
            if project is None:
                continue
            await self._emit_approval_prompt(project, reason="reminder")

    async def run_cleanup_cycle(
        self,
        *,
        dry_run: bool,
        project_id: str | None = None,
    ) -> CleanupCycleSummary:
        """Execute cleanup for approved projects or one explicit project."""
        started_at = datetime.now(UTC).isoformat()
        self._projects = self._monitor.discover_projects()
        target_ids: list[str] = []
        if project_id is not None:
            target_ids = [project_id]
        else:
            if not self._config.cleanup_enabled:
                return CleanupCycleSummary(
                    started_at=started_at,
                    dry_run=dry_run,
                    project_count=0,
                    success_count=0,
                    failure_count=0,
                    results=[],
                )
            target_ids = [
                entry["project_id"]
                for entry in self._store.list_policies(mode="auto_clean")
                if isinstance(entry.get("project_id"), str)
            ]

        results: list[CleanupResult] = []
        for target_id in target_ids:
            project = self._projects.get(target_id)
            if project is None:
                missing = CleanupResult(
                    project_id=target_id,
                    dry_run=dry_run,
                    success=False,
                    error="project_not_found",
                )
                results.append(missing)
                self._store.record_cleanup_run(
                    project_id=target_id,
                    actions=[],
                    dry_run=dry_run,
                    success=False,
                    error=missing.error,
                )
                continue

            result = self._monitor.run_cleanup(project, dry_run=dry_run, exited_older_than_hours=24)
            results.append(result)
            self._store.record_cleanup_run(
                project_id=target_id,
                actions=[action.to_dict() for action in result.actions],
                dry_run=dry_run,
                success=result.success,
                error=result.error,
            )
            await self._emit_cleanup_report(result)

        success_count = sum(1 for item in results if item.success)
        failure_count = len(results) - success_count
        return CleanupCycleSummary(
            started_at=started_at,
            dry_run=dry_run,
            project_count=len(results),
            success_count=success_count,
            failure_count=failure_count,
            results=[item.to_dict() for item in results],
        )

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    async def _discovery_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.discovery_cycle()
            await asyncio.sleep(max(15, int(self._config.scan_interval)))

    async def _cleanup_loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now().astimezone()
            today = now.date().isoformat()
            if (
                now.hour == int(self._config.cleanup_hour)
                and now.minute == int(self._config.cleanup_minute)
                and self._last_cleanup_date != today
            ):
                await self.run_cleanup_cycle(dry_run=False)
                self._last_cleanup_date = today
            await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    async def _start_api_server(self) -> None:
        app = web.Application(middlewares=[self._auth_middleware])
        app["daemon"] = self
        app.router.add_get("/v1/health", self._handle_health)
        app.router.add_post("/v1/bootstrap", self._handle_bootstrap)
        app.router.add_get("/v1/projects", self._handle_projects)
        app.router.add_post("/v1/discovery/run", self._handle_discovery_run)
        app.router.add_get("/v1/approvals/pending", self._handle_pending_approvals)
        app.router.add_post("/v1/projects/{project_id}/policy", self._handle_set_policy)
        app.router.add_post("/v1/cleanup/run", self._handle_cleanup_run)
        app.router.add_get("/v1/cleanup/history", self._handle_cleanup_history)
        app.router.add_get("/v1/events", self._handle_events)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self._config.api_host,
            port=int(self._config.api_port),
        )
        await self._site.start()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path in {"/v1/health", "/v1/bootstrap"}:
            return await handler(request)

        expected = self._config.api_token
        header = request.headers.get("Authorization", "")
        provided = ""
        if header.lower().startswith("bearer "):
            provided = header[7:].strip()
        if not provided:
            provided = request.headers.get("X-Dev-Agent-Token", "").strip()
        if not expected or provided != expected:
            return _json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return _json_response({"status": "ok"})

    async def _handle_bootstrap(self, request: web.Request) -> web.Response:
        bootstrap_secret = (self._config.bootstrap_secret or "").strip()
        if not bootstrap_secret:
            return _json_response({"error": "Bootstrap is not enabled"}, status=403)

        provided = request.headers.get("X-Bootstrap-Secret", "").strip()
        if provided != bootstrap_secret:
            return _json_response({"error": "Unauthorized bootstrap secret"}, status=401)

        if self._config.bootstrap_require_once:
            bootstrapped_at = self._store.get_meta("bootstrap_completed_at")
            if bootstrapped_at:
                return _json_response(
                    {
                        "error": "Bootstrap already completed",
                        "already_bootstrapped": True,
                        "bootstrap_completed_at": bootstrapped_at,
                        "api_base_url": (
                            f"http://{self._config.api_host}:{int(self._config.api_port)}/v1"
                        ),
                    },
                    status=409,
                )

        try:
            data = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return _json_response({"error": "JSON body must be an object"}, status=400)

        webhook_url = str(data.get("webhook_url", "")).strip()
        if webhook_url:
            self._config.webhook_url = webhook_url
        agent_name = str(data.get("agent_name", "")).strip()
        if agent_name:
            self._config.agent_name = agent_name

        repos = data.get("repos")
        if isinstance(repos, list):
            normalized: list[str] = []
            for item in repos:
                value = str(item).strip()
                if value:
                    normalized.append(value)
            self._config.repos = normalized

        for key, attr in (
            ("cleanup_hour", "cleanup_hour"),
            ("cleanup_minute", "cleanup_minute"),
            ("approval_reprompt_hours", "approval_reprompt_hours"),
            ("scan_interval", "scan_interval"),
        ):
            raw = data.get(key)
            if raw is None:
                continue
            try:
                setattr(self._config, attr, int(raw))
            except (TypeError, ValueError):
                return _json_response({"error": f"{key} must be an integer"}, status=400)

        for key, attr in (
            ("container_monitor_enabled", "container_monitor_enabled"),
            ("cleanup_enabled", "cleanup_enabled"),
            ("git_enabled", "git_enabled"),
            ("annotations_enabled", "annotations_enabled"),
            ("claude_code_enabled", "claude_code_enabled"),
        ):
            raw = data.get(key)
            if raw is None:
                continue
            if isinstance(raw, bool):
                setattr(self._config, attr, raw)
            else:
                return _json_response({"error": f"{key} must be a boolean"}, status=400)

        rotate_api_token = bool(data.get("rotate_api_token", False))
        if rotate_api_token or not self._config.api_token:
            self._config.api_token = secrets.token_urlsafe(32)

        self._config.save()
        completed_at = datetime.now(UTC).isoformat()
        self._store.set_meta("bootstrap_completed_at", completed_at)
        await self._publish_event(
            {
                "type": "bootstrap_completed",
                "at": completed_at,
                "agent_name": self._config.agent_name,
            }
        )
        return _json_response(
            {
                "ok": True,
                "api_token": self._config.api_token,
                "api_base_url": (f"http://{self._config.api_host}:{int(self._config.api_port)}/v1"),
                "bootstrap_completed_at": completed_at,
            }
        )

    async def _handle_projects(self, _request: web.Request) -> web.Response:
        projects = self._monitor.discover_projects()
        self._projects = projects
        policies = {
            row["project_id"]: row["mode"]
            for row in self._store.list_policies()
            if isinstance(row.get("project_id"), str)
        }
        pending = {item.project_id for item in self._store.list_pending_approvals()}

        payload = []
        for project_id, project in sorted(projects.items()):
            mode = policies.get(project_id, "ask")
            payload.append(
                {
                    "project_id": project_id,
                    "policy_mode": mode,
                    "pending_approval": project_id in pending and mode == "ask",
                    "total_containers": project.total_containers,
                    "running_containers": project.running_containers,
                    "containers": [
                        {
                            "id": container.container_id,
                            "name": container.name,
                            "state": container.state,
                            "status": container.status,
                            "image": container.image,
                        }
                        for container in sorted(project.containers, key=lambda item: item.name)
                    ],
                }
            )
        return _json_response({"projects": payload})

    async def _handle_discovery_run(self, _request: web.Request) -> web.Response:
        await self.discovery_cycle()
        pending = self._store.list_pending_approvals()
        return _json_response(
            {
                "ok": True,
                "projects_discovered": sorted(self._projects),
                "pending_approvals": [
                    {
                        "project_id": item.project_id,
                        "first_seen_at": item.first_seen_at,
                        "prompt_count": item.prompt_count,
                    }
                    for item in pending
                ],
            }
        )

    async def _handle_pending_approvals(self, _request: web.Request) -> web.Response:
        approvals = [
            {
                "project_id": item.project_id,
                "first_seen_at": item.first_seen_at,
                "last_prompted_at": item.last_prompted_at,
                "prompt_count": item.prompt_count,
                "status": item.status,
            }
            for item in self._store.list_pending_approvals()
        ]
        return _json_response({"pending": approvals})

    async def _handle_set_policy(self, request: web.Request) -> web.Response:
        project_id = request.match_info.get("project_id", "").strip()
        if not project_id:
            return _json_response({"error": "Missing project_id"}, status=400)
        try:
            data = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return _json_response({"error": "JSON body must be an object"}, status=400)
        mode_raw = str(data.get("mode", "")).strip().lower()
        if mode_raw not in {"ask", "auto_clean", "never_clean"}:
            return _json_response({"error": "mode must be ask|auto_clean|never_clean"}, status=400)
        mode: PolicyMode = mode_raw  # type: ignore[assignment]
        source = str(data.get("source", "api"))
        notes = str(data.get("notes", ""))
        self._store.set_policy(project_id, mode, source=source, notes=notes)
        await self._publish_event(
            {
                "type": "policy_updated",
                "project_id": project_id,
                "mode": mode,
                "source": source,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        return _json_response({"ok": True, "project_id": project_id, "mode": mode})

    async def _handle_cleanup_run(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return _json_response({"error": "JSON body must be an object"}, status=400)
        project_id = str(data.get("project_id", "")).strip() or None
        dry_run = bool(data.get("dry_run", True))
        summary = await self.run_cleanup_cycle(dry_run=dry_run, project_id=project_id)
        return _json_response({"summary": summary.to_dict()})

    async def _handle_cleanup_history(self, request: web.Request) -> web.Response:
        project_id = request.query.get("project_id")
        raw_limit = request.query.get("limit", "50")
        try:
            limit = max(1, int(raw_limit))
        except ValueError:
            return _json_response({"error": "limit must be an integer"}, status=400)
        history = self._store.list_cleanup_runs(limit=limit, project_id=project_id)
        return _json_response({"history": history})

    async def _handle_events(self, _request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(_request)

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            await response.write(b": connected\n\n")
            while True:
                event = await queue.get()
                event_type = str(event.get("type", "message"))
                payload = json.dumps(event, separators=(",", ":"))
                body = f"event: {event_type}\ndata: {payload}\n\n"
                await response.write(body.encode("utf-8"))
        except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
            pass
        finally:
            self._subscribers.discard(queue)
        return response

    # ------------------------------------------------------------------
    # Event emitters
    # ------------------------------------------------------------------

    async def _emit_approval_prompt(self, project: ProjectSnapshot, *, reason: str) -> None:
        self._store.mark_prompted(project.project_id)
        message = (
            f"Discovered Docker project '{project.project_id}' "
            f"({project.total_containers} container(s), {project.running_containers} running). "
            "Approve automatic nightly cleanup?"
        )
        fields = {
            "project": project.project_id,
            "containers": str(project.total_containers),
            "running": str(project.running_containers),
            "reason": reason,
            "approve_hint": "Use local app/API/CLI to set mode=auto_clean",
        }
        if self._config.webhook_url:
            await send_event(
                self._config.webhook_url,
                self._config.agent_name,
                "cleanup_approval",
                message,
                fields,
            )
        await self._publish_event(
            {
                "type": "approval_prompt",
                "project_id": project.project_id,
                "reason": reason,
                "containers": project.total_containers,
                "running": project.running_containers,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    async def _emit_project_discovery(self, project: ProjectSnapshot) -> None:
        """Emit project-discovery events for journal ingestion."""
        fields = {
            "project": project.project_id,
            "total_containers": str(project.total_containers),
            "running_containers": str(project.running_containers),
        }
        message = (
            f"Container project discovered: {project.project_id} "
            f"({project.total_containers} container(s), {project.running_containers} running)."
        )
        if self._config.webhook_url:
            await send_event(
                self._config.webhook_url,
                self._config.agent_name,
                "container_project",
                message,
                fields,
            )
        await self._publish_event(
            {
                "type": "project_discovery",
                "project_id": project.project_id,
                "total_containers": project.total_containers,
                "running_containers": project.running_containers,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    async def _emit_cleanup_report(self, result: CleanupResult) -> None:
        successful_actions = sum(1 for action in result.actions if action.success)
        fields = {
            "project": result.project_id,
            "dry_run": str(result.dry_run).lower(),
            "actions": str(len(result.actions)),
            "successful_actions": str(successful_actions),
            "status": "success" if result.success else "failed",
        }
        if result.error:
            fields["error"] = result.error[:240]
        message = (
            f"Cleanup {('dry-run ' if result.dry_run else '')}report for {result.project_id}: "
            f"{successful_actions}/{len(result.actions)} actions successful."
        )
        if self._config.webhook_url:
            await send_event(
                self._config.webhook_url,
                self._config.agent_name,
                "cleanup_report",
                message,
                fields,
            )
        await self._publish_event(
            {
                "type": "cleanup_report",
                "project_id": result.project_id,
                "dry_run": result.dry_run,
                "success": result.success,
                "actions": [action.to_dict() for action in result.actions],
                "error": result.error,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    async def _publish_event(self, event: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        for queue in list(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    _ = queue.get_nowait()
            queue.put_nowait(event)
