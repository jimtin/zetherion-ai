"""Update executor — orchestrates GitHub-tag builds and blue/green cutovers.

Lifecycle:
1. Fetch release tags from GitHub and checkout target tag
2. Build inactive color services from source
3. Bring up inactive services and validate direct health
4. Flip Traefik dynamic routing to the new color
5. Validate routed health and restart bot gracefully
6. Stop old color services
7. Auto-rollback and pause further rollouts on failure
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from updater_sidecar.health_checker import HealthCheckConfig, check_service_health
from updater_sidecar.models import UpdateResult

log = logging.getLogger("updater_sidecar.executor")

COLORS = ("blue", "green")

SKILLS_SERVICES: dict[str, str] = {
    "blue": "zetherion-ai-skills-blue",
    "green": "zetherion-ai-skills-green",
}

API_SERVICES: dict[str, str] = {
    "blue": "zetherion-ai-api-blue",
    "green": "zetherion-ai-api-green",
}

BOT_SERVICE = "zetherion-ai-bot"

DEFAULT_HEALTH_URLS: dict[str, str] = {
    # Direct service health checks
    "zetherion-ai-skills-blue": "http://zetherion-ai-skills-blue:8080/health",
    "zetherion-ai-skills-green": "http://zetherion-ai-skills-green:8080/health",
    "zetherion-ai-api-blue": "http://zetherion-ai-api-blue:8443/health",
    "zetherion-ai-api-green": "http://zetherion-ai-api-green:8443/health",
    # Routed health checks through Traefik
    "routed_skills": "http://zetherion-ai-traefik:8080/health",
    "routed_api": "http://zetherion-ai-traefik:8443/health",
}

DEFAULT_STATE_PATH = "/app/data/updater-state.json"
DEFAULT_TRAEFIK_DYNAMIC_PATH = "/project/config/traefik/dynamic/updater-routes.yml"


class UpdateExecutor:
    """Executes update operations with blue/green cutover semantics."""

    def __init__(
        self,
        project_dir: str = "/project",
        compose_file: str = "/project/docker-compose.yml",
        health_urls: dict[str, str] | None = None,
        state_path: str = DEFAULT_STATE_PATH,
        route_config_path: str = DEFAULT_TRAEFIK_DYNAMIC_PATH,
        pause_on_failure: bool = True,
    ) -> None:
        if route_config_path == DEFAULT_TRAEFIK_DYNAMIC_PATH and project_dir != "/project":
            route_config_path = f"{project_dir}/config/traefik/dynamic/updater-routes.yml"
        if state_path == DEFAULT_STATE_PATH and project_dir != "/project":
            state_path = f"{project_dir}/data/updater-state.json"

        self._project_dir = project_dir
        self._compose_file = compose_file
        self._health_urls = {**DEFAULT_HEALTH_URLS, **(health_urls or {})}
        self._state_path = Path(state_path)
        self._route_config_path = Path(route_config_path)
        self._pause_on_failure = pause_on_failure
        self._lock = asyncio.Lock()
        self._state = "idle"
        self._current_operation: str | None = None
        self._runtime = self._load_state()
        self._ensure_routing_config(self.active_color)

    # ------------------------------------------------------------------
    # Public status surface
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_operation(self) -> str | None:
        return self._current_operation

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @property
    def active_color(self) -> str:
        color = str(self._runtime.get("active_color", "blue"))
        return color if color in COLORS else "blue"

    @property
    def paused(self) -> bool:
        return bool(self._runtime.get("paused", False))

    @property
    def pause_reason(self) -> str:
        return str(self._runtime.get("pause_reason", ""))

    @property
    def last_checked_at(self) -> str | None:
        value = self._runtime.get("last_checked_at")
        return str(value) if value else None

    @property
    def last_attempted_tag(self) -> str | None:
        value = self._runtime.get("last_attempted_tag")
        return str(value) if value else None

    @property
    def last_good_tag(self) -> str | None:
        value = self._runtime.get("last_good_tag")
        return str(value) if value else None

    def status_snapshot(self) -> dict[str, Any]:
        """Return updater runtime status fields for API responses."""
        return {
            "active_color": self.active_color,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "last_checked_at": self.last_checked_at,
            "last_attempted_tag": self.last_attempted_tag,
            "last_good_tag": self.last_good_tag,
        }

    async def unpause(self) -> bool:
        """Resume updates by clearing paused state."""
        if self._lock.locked():
            return False
        self._runtime["paused"] = False
        self._runtime["pause_reason"] = ""
        self._runtime["resumed_at"] = self._now_iso()
        self._save_state()
        return True

    # ------------------------------------------------------------------
    # Primary flows
    # ------------------------------------------------------------------

    async def apply_update(self, tag: str, version: str) -> UpdateResult:
        """Apply an update to the given release tag."""
        if self._lock.locked():
            return UpdateResult(status="failed", error="Update already in progress")

        if self.paused:
            return UpdateResult(
                status="failed",
                error=f"Rollouts are paused: {self.pause_reason or 'manual resume required'}",
                active_color=self.active_color,
                paused=True,
                pause_reason=self.pause_reason,
            )

        async with self._lock:
            return await self._do_apply(tag=tag, version=version)

    async def rollback(self, previous_sha: str) -> UpdateResult:
        """Rollback to a previous git SHA and restore active color health."""
        if self._lock.locked():
            return UpdateResult(status="failed", error="Operation already in progress")

        async with self._lock:
            return await self._do_rollback_full(previous_sha)

    async def _do_apply(self, tag: str, version: str) -> UpdateResult:
        start = time.monotonic()
        previous_color = self.active_color
        target_color = self._inactive_color(previous_color)

        self._state = "updating"
        self._current_operation = f"Updating to {tag}"
        self._mark_attempt(tag)

        result = UpdateResult(
            status="failed",
            active_color=previous_color,
            target_color=target_color,
            paused=self.paused,
            pause_reason=self.pause_reason,
        )

        try:
            prev_sha = await self._run_cmd("git rev-parse HEAD")
            if prev_sha is None:
                result.error = "Failed to get current git SHA"
                return await self._pause_and_rollback(result, previous_color)
            result.previous_sha = prev_sha.strip()

            # Always fetch the exact release tag from origin so builds are
            # sourced from GitHub, not local branch state.
            tag_ref = f"refs/tags/{tag}"
            tag_refspec_safe = shlex.quote(f"{tag_ref}:{tag_ref}")
            self._current_operation = f"Fetching {tag_ref} from origin"
            ok = await self._run_cmd(f"git fetch --force origin {tag_refspec_safe}")
            if ok is None:
                result.error = "git fetch tag failed"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("git_fetch_tags")

            self._current_operation = f"Checking out {tag}"
            tag_ref_safe = shlex.quote(tag_ref)
            ok = await self._run_cmd(f"git checkout --force {tag_ref_safe}")
            if ok is None:
                result.error = "git checkout failed"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("git_checkout_tag")

            new_sha = await self._run_cmd("git rev-parse HEAD")
            result.new_sha = new_sha.strip() if new_sha else None

            target_services = [
                SKILLS_SERVICES[target_color],
                API_SERVICES[target_color],
            ]
            build_services = [*target_services, BOT_SERVICE]
            services_str = " ".join(build_services)

            self._current_operation = f"Building target color ({target_color})"
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} build {services_str}",
                timeout=1200,
            )
            if ok is None:
                result.error = "docker build failed"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("docker_build")

            for service in target_services:
                self._current_operation = f"Starting {service}"
                ok = await self._run_cmd(
                    f"docker compose -f {self._compose_file} up -d --no-deps {service}",
                    timeout=180,
                )
                if ok is None:
                    result.error = f"Failed to start {service}"
                    return await self._pause_and_rollback(result, previous_color)
                result.steps_completed.append(f"start_{service}")

                health_url = self._health_urls.get(service)
                if health_url:
                    self._current_operation = f"Waiting for {service} health"
                    healthy = await check_service_health(
                        health_url,
                        HealthCheckConfig(retries=8, delay_seconds=8),
                    )
                    if not healthy:
                        result.error = f"Health check failed for {service}"
                        return await self._pause_and_rollback(result, previous_color)
                    result.steps_completed.append(f"health_{service}")

            self._current_operation = f"Switching traffic to {target_color}"
            if not self._switch_active_color(target_color):
                result.error = "Failed to write Traefik route config"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("route_switch")

            for routed_name in ("routed_skills", "routed_api"):
                routed_url = self._health_urls[routed_name]
                self._current_operation = f"Validating {routed_name}"
                healthy = await check_service_health(
                    routed_url,
                    HealthCheckConfig(retries=8, delay_seconds=5),
                )
                if not healthy:
                    result.error = f"Routed health failed for {routed_name}"
                    return await self._pause_and_rollback(result, previous_color)
                result.steps_completed.append(f"health_{routed_name}")

            self._current_operation = "Restarting bot"
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} up -d --no-deps {BOT_SERVICE}",
                timeout=180,
            )
            if ok is None:
                result.error = "Failed to restart bot"
                return await self._pause_and_rollback(result, previous_color)
            if not await self._is_service_running(BOT_SERVICE):
                result.error = "Bot did not return to running state"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("restart_bot")

            old_services = [
                SKILLS_SERVICES[previous_color],
                API_SERVICES[previous_color],
            ]
            old_services_str = " ".join(old_services)
            self._current_operation = f"Stopping old color ({previous_color})"
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} stop {old_services_str}",
                timeout=180,
            )
            if ok is None:
                result.error = f"Failed to stop old services ({previous_color})"
                return await self._pause_and_rollback(result, previous_color)
            result.steps_completed.append("stop_old_color")

            self._runtime.update(
                {
                    "active_color": target_color,
                    "last_good_tag": tag,
                    "last_success_at": self._now_iso(),
                    "paused": False,
                    "pause_reason": "",
                }
            )
            self._save_state()

            result.status = "success"
            result.active_color = target_color
            result.paused = False
            result.pause_reason = ""
            log.info("Update to %s completed successfully", tag)
            return result

        except Exception as exc:
            result.error = f"Unexpected error: {exc}"
            log.exception("Update failed with unexpected error")
            return await self._pause_and_rollback(result, previous_color)
        finally:
            elapsed = time.monotonic() - start
            result.duration_seconds = round(elapsed, 2)
            result.completed_at = self._now_iso()
            self._state = "idle"
            self._current_operation = None

    async def _do_rollback_full(self, previous_sha: str) -> UpdateResult:
        start = time.monotonic()
        self._state = "rolling_back"
        self._current_operation = f"Rolling back to {previous_sha[:12]}"

        result = UpdateResult(
            status="failed",
            previous_sha=previous_sha,
            active_color=self.active_color,
            paused=self.paused,
            pause_reason=self.pause_reason,
        )

        try:
            ok = await self._attempt_rollback(previous_sha, self.active_color)
            if ok:
                result.status = "success"
                result.new_sha = previous_sha
            else:
                result.error = "Rollback failed"
        finally:
            elapsed = time.monotonic() - start
            result.duration_seconds = round(elapsed, 2)
            result.completed_at = self._now_iso()
            self._state = "idle"
            self._current_operation = None

        return result

    async def _pause_and_rollback(self, result: UpdateResult, previous_color: str) -> UpdateResult:
        """Pause further rollouts and attempt rollback to the previous healthy state."""
        rollback_ok = await self._attempt_rollback(result.previous_sha or "", previous_color)
        if rollback_ok:
            result.status = "rolled_back"
        else:
            result.status = "failed"

        self._runtime["last_failure_at"] = self._now_iso()
        if self._pause_on_failure:
            self._runtime["paused"] = True
            self._runtime["pause_reason"] = result.error or "rollout failed"
            result.paused = True
            result.pause_reason = self._runtime["pause_reason"]
        self._save_state()
        return result

    async def _attempt_rollback(self, previous_sha: str, previous_color: str) -> bool:
        """Attempt to restore code + traffic to the previous healthy state."""
        if not previous_sha:
            log.error("Cannot rollback: no previous SHA")
            return False

        if previous_color not in COLORS:
            log.error("Cannot rollback: invalid color %s", previous_color)
            return False

        log.info("Rolling back to %s (%s)", previous_sha[:12], previous_color)

        sha_safe = shlex.quote(previous_sha)
        ok = await self._run_cmd(f"git checkout --force {sha_safe}")
        if ok is None:
            log.error("Rollback: git checkout failed")
            return False

        rollback_services = [
            SKILLS_SERVICES[previous_color],
            API_SERVICES[previous_color],
            BOT_SERVICE,
        ]
        services_str = " ".join(rollback_services)
        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} build {services_str}",
            timeout=1200,
        )
        if ok is None:
            log.error("Rollback: docker build failed")
            return False

        for service in (SKILLS_SERVICES[previous_color], API_SERVICES[previous_color]):
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} up -d --no-deps {service}",
                timeout=180,
            )
            if ok is None:
                log.error("Rollback: failed to start %s", service)
                return False

            health_url = self._health_urls.get(service)
            if health_url:
                healthy = await check_service_health(
                    health_url,
                    HealthCheckConfig(retries=8, delay_seconds=8),
                )
                if not healthy:
                    log.error("Rollback: health check failed for %s", service)
                    return False

        if not self._switch_active_color(previous_color):
            log.error("Rollback: failed to switch route back to %s", previous_color)
            return False

        for routed_name in ("routed_skills", "routed_api"):
            healthy = await check_service_health(
                self._health_urls[routed_name],
                HealthCheckConfig(retries=8, delay_seconds=5),
            )
            if not healthy:
                log.error("Rollback: routed health failed for %s", routed_name)
                return False

        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} up -d --no-deps {BOT_SERVICE}",
            timeout=180,
        )
        if ok is None or not await self._is_service_running(BOT_SERVICE):
            log.error("Rollback: bot restart failed")
            return False

        inactive = self._inactive_color(previous_color)
        inactive_services = [SKILLS_SERVICES[inactive], API_SERVICES[inactive]]
        inactive_str = " ".join(inactive_services)
        await self._run_cmd(
            f"docker compose -f {self._compose_file} stop {inactive_str}",
            timeout=180,
        )

        self._runtime["active_color"] = previous_color
        self._save_state()
        log.info("Rollback to %s completed successfully", previous_sha[:12])
        return True

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    async def get_diagnostics(self) -> dict[str, object]:
        """Gather container-level diagnostics."""
        diagnostics: dict[str, object] = {}

        sha = await self._run_cmd("git rev-parse HEAD")
        diagnostics["git_sha"] = sha.strip() if sha else "unknown"

        git_ref_cmd = "git describe --tags --exact-match 2>/dev/null || git branch --show-current"
        branch = await self._run_cmd(git_ref_cmd)
        diagnostics["git_ref"] = branch.strip() if branch else "unknown"

        status = await self._run_cmd("git status --porcelain")
        diagnostics["git_clean"] = status is not None and status.strip() == ""

        ps_output = await self._run_cmd(f"docker compose -f {self._compose_file} ps --format json")
        diagnostics["containers_raw"] = ps_output.strip() if ps_output else "unavailable"

        diagnostics.update(self.status_snapshot())

        disk = await self._run_cmd("df -h / | tail -1")
        diagnostics["disk_usage"] = disk.strip() if disk else "unavailable"

        return diagnostics

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _inactive_color(current: str) -> str:
        return "green" if current == "blue" else "blue"

    def _mark_attempt(self, tag: str) -> None:
        self._runtime["last_checked_at"] = self._now_iso()
        self._runtime["last_attempted_tag"] = tag
        self._save_state()

    def _default_state(self) -> dict[str, Any]:
        return {
            "active_color": "blue",
            "last_good_tag": "",
            "paused": False,
            "pause_reason": "",
            "last_checked_at": None,
            "last_attempted_tag": None,
            "last_success_at": None,
            "last_failure_at": None,
            "resumed_at": None,
        }

    def _load_state(self) -> dict[str, Any]:
        default = self._default_state()
        if not self._state_path.exists():
            return default
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return default
            return {**default, **data}
        except Exception:
            log.warning("updater_state_load_failed: %s", self._state_path)
            return default

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._runtime, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_path.replace(self._state_path)
        except Exception:
            log.exception("updater_state_save_failed: %s", self._state_path)

    def _ensure_routing_config(self, color: str) -> None:
        """Ensure Traefik route config exists for the active color."""
        if self._route_config_path.exists():
            return
        self._switch_active_color(color)

    def _switch_active_color(self, color: str) -> bool:
        """Atomically write Traefik dynamic route config and update active color."""
        if color not in COLORS:
            return False
        content = self._build_traefik_config(color)
        try:
            self._route_config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._route_config_path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self._route_config_path)
            self._runtime["active_color"] = color
            self._save_state()
            return True
        except Exception:
            log.exception("traefik_route_write_failed: %s", self._route_config_path)
            return False

    def _build_traefik_config(self, active_color: str) -> str:
        """Build Traefik dynamic config for active blue/green routing."""
        return f"""http:
  routers:
    skills:
      entryPoints:
        - skills
      rule: \"PathPrefix(`/`)\"
      service: skills-{active_color}
    api:
      entryPoints:
        - api
      rule: \"PathPrefix(`/`)\"
      service: api-{active_color}
  services:
    skills-blue:
      loadBalancer:
        servers:
          - url: \"http://{SKILLS_SERVICES['blue']}:8080\"
    skills-green:
      loadBalancer:
        servers:
          - url: \"http://{SKILLS_SERVICES['green']}:8080\"
    api-blue:
      loadBalancer:
        servers:
          - url: \"http://{API_SERVICES['blue']}:8443\"
    api-green:
      loadBalancer:
        servers:
          - url: \"http://{API_SERVICES['green']}:8443\"
"""

    async def _is_service_running(self, service: str) -> bool:
        """Check whether a compose service is currently running."""
        output = await self._run_cmd(
            f"docker compose -f {self._compose_file} ps --services --status running {service}"
        )
        if output is None:
            return False
        services = {line.strip() for line in output.splitlines() if line.strip()}
        return service in services

    async def _run_cmd(self, cmd: str, timeout: int = 120) -> str | None:
        """Run a shell command and return stdout, or None on failure."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._project_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode != 0:
                log.warning(
                    "Command failed: %s (rc=%d): %s",
                    cmd,
                    proc.returncode,
                    stderr.decode()[:500],
                )
                return None

            return stdout.decode()

        except TimeoutError:
            log.warning("Command timed out (%ds): %s", timeout, cmd)
            return None
        except Exception as exc:
            log.warning("Command error: %s: %s", cmd, exc)
            return None
