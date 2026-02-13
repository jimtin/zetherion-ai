"""HTTP server for the updater sidecar.

Exposes a REST API for triggering updates, rollbacks,
and checking status. Only accessible on the internal
Docker network.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

from aiohttp import web

from updater_sidecar.auth import get_or_create_secret, validate_secret
from updater_sidecar.executor import UpdateExecutor
from updater_sidecar.models import (
    HistoryEntry,
    RollbackRequest,
    SidecarStatus,
    UpdateRequest,
)

log = logging.getLogger("updater_sidecar.server")

MAX_HISTORY = 50


def create_app(
    executor: UpdateExecutor | None = None,
    secret: str | None = None,
) -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()

    # Store executor and config in app state
    app["executor"] = executor or UpdateExecutor(
        project_dir=os.environ.get("PROJECT_DIR", "/project"),
        compose_file=os.environ.get("COMPOSE_FILE", "/project/docker-compose.yml"),
        health_urls=_parse_health_urls(os.environ.get("HEALTH_URLS", "")),
        state_path=os.environ.get("UPDATER_STATE_PATH", "/app/data/updater-state.json"),
        route_config_path=os.environ.get(
            "UPDATER_TRAEFIK_DYNAMIC_PATH",
            "/project/config/traefik/dynamic/updater-routes.yml",
        ),
        pause_on_failure=os.environ.get("AUTO_UPDATE_PAUSE_ON_FAILURE", "true").lower() != "false",
    )
    app["secret"] = secret or ""
    history: deque[HistoryEntry] = deque(maxlen=MAX_HISTORY)
    app["history"] = history
    app["start_time"] = time.monotonic()

    # Register routes
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/update/apply", handle_apply)
    app.router.add_post("/update/rollback", handle_rollback)
    app.router.add_post("/update/unpause", handle_unpause)
    app.router.add_get("/update/history", handle_history)
    app.router.add_get("/diagnostics", handle_diagnostics)

    return app


def _parse_health_urls(urls_str: str) -> dict[str, str]:
    """Parse HEALTH_URLS env var into a dict.

    Format: 'http://svc1:8080/health,http://svc2:8443/health'
    Maps container name to URL based on URL hostname.
    """
    if not urls_str:
        return {}

    result: dict[str, str] = {}
    for url in urls_str.split(","):
        url = url.strip()
        if not url:
            continue
        # Extract hostname from URL (e.g., 'zetherion-ai-skills' from
        # 'http://zetherion-ai-skills:8080/health')
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            result[hostname] = url
        except Exception:
            log.warning("Could not parse health URL: %s", url)
    return result


def _check_auth(request: web.Request) -> bool:
    """Validate the request's auth header against the shared secret."""
    secret = request.app["secret"]  # gitleaks:allow
    if not secret:
        # No secret configured — allow all requests (internal network only)
        return True
    request_secret = request.headers.get("X-Updater-Secret")  # gitleaks:allow
    return validate_secret(request_secret, secret)


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint — always returns 200."""
    return web.json_response({"status": "ok"})


async def handle_status(request: web.Request) -> web.Response:
    """Return current sidecar status."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    executor: UpdateExecutor = request.app["executor"]
    history: deque[HistoryEntry] = request.app["history"]
    start_time: float = request.app["start_time"]

    last_result = history[-1].result if history else None
    status = SidecarStatus(
        state=executor.state,
        current_operation=executor.current_operation,
        last_result=last_result,
        uptime_seconds=round(time.monotonic() - start_time, 2),
        active_color=executor.active_color,
        paused=executor.paused,
        pause_reason=executor.pause_reason,
        last_checked_at=executor.last_checked_at,
        last_attempted_tag=executor.last_attempted_tag,
        last_good_tag=executor.last_good_tag,
    )
    return web.json_response(status.to_dict())


async def handle_apply(request: web.Request) -> web.Response:
    """Trigger an update to a specific tag."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    executor: UpdateExecutor = request.app["executor"]

    if executor.is_busy:
        return web.json_response({"error": "Update already in progress"}, status=409)
    if executor.paused:
        return web.json_response(
            {
                "error": (
                    f"Updates are paused: {executor.pause_reason}"
                    if executor.pause_reason
                    else "Updates are paused"
                ),
            },
            status=423,
        )

    try:
        data = await request.json()
        update_req = UpdateRequest.from_dict(data)
    except (ValueError, Exception) as exc:
        return web.json_response({"error": f"Invalid request: {exc}"}, status=400)

    log.info("Starting update to %s (v%s)", update_req.tag, update_req.version)
    result = await executor.apply_update(update_req.tag, update_req.version)

    # Record in history
    history: deque[HistoryEntry] = request.app["history"]
    history.append(
        HistoryEntry(
            tag=update_req.tag,
            version=update_req.version,
            result=result,
        )
    )

    status_code = 200 if result.status == "success" else 500
    return web.json_response(result.to_dict(), status=status_code)


async def handle_rollback(request: web.Request) -> web.Response:
    """Rollback to a previous git SHA."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    executor: UpdateExecutor = request.app["executor"]

    if executor.is_busy:
        return web.json_response({"error": "Operation already in progress"}, status=409)

    try:
        data = await request.json()
        rollback_req = RollbackRequest.from_dict(data)
    except (ValueError, Exception) as exc:
        return web.json_response({"error": f"Invalid request: {exc}"}, status=400)

    log.info("Starting rollback to %s", rollback_req.previous_sha[:12])
    result = await executor.rollback(rollback_req.previous_sha)

    # Record in history
    history: deque[HistoryEntry] = request.app["history"]
    history.append(
        HistoryEntry(
            tag=f"rollback:{rollback_req.previous_sha[:12]}",
            version="rollback",
            result=result,
        )
    )

    status_code = 200 if result.status == "success" else 500
    return web.json_response(result.to_dict(), status=status_code)


async def handle_unpause(request: web.Request) -> web.Response:
    """Clear paused state so automatic rollouts can continue."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    executor: UpdateExecutor = request.app["executor"]
    if executor.is_busy:
        return web.json_response({"error": "Operation already in progress"}, status=409)

    resumed = await executor.unpause()
    if not resumed:
        return web.json_response({"error": "Could not resume updates"}, status=500)
    return web.json_response({"resumed": True, "status": "ok"})


async def handle_history(request: web.Request) -> web.Response:
    """Return recent update history."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    history: deque[HistoryEntry] = request.app["history"]
    return web.json_response({"entries": [entry.to_dict() for entry in history]})


async def handle_diagnostics(request: web.Request) -> web.Response:
    """Return container-level diagnostics."""
    if not _check_auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    executor: UpdateExecutor = request.app["executor"]
    diagnostics = await executor.get_diagnostics()
    return web.json_response(diagnostics)


async def run_server(
    host: str = "0.0.0.0",  # noqa: S104  # nosec B104
    port: int = 9090,
) -> None:
    """Start the updater sidecar server."""
    secret_path = os.environ.get("UPDATER_SECRET_PATH", "/app/data/.updater-secret")
    secret = get_or_create_secret(secret_path)  # gitleaks:allow

    app = create_app(secret=secret)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    log.info("Updater sidecar started on %s:%d", host, port)

    # Run forever
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
