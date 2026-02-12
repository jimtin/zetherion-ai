"""Update executor — runs git and docker compose operations.

This module handles the actual update lifecycle:
git fetch → git checkout → docker build → rolling restart → health check → rollback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from updater_sidecar.health_checker import HealthCheckConfig, check_service_health
from updater_sidecar.models import UpdateResult

log = logging.getLogger("updater_sidecar.executor")

# Services to rebuild and restart (in dependency order)
APP_SERVICES = [
    "zetherion-ai-skills",
    "zetherion-ai-api",
    "zetherion-ai-bot",
]

# Health check URLs for each service (skills and api have HTTP endpoints)
DEFAULT_HEALTH_URLS: dict[str, str] = {
    "zetherion-ai-skills": "http://zetherion-ai-skills:8080/health",
    "zetherion-ai-api": "http://zetherion-ai-api:8443/health",
}


class UpdateExecutor:
    """Executes update operations: git pull, docker build, rolling restart."""

    def __init__(
        self,
        project_dir: str = "/project",
        compose_file: str = "/project/docker-compose.yml",
        health_urls: dict[str, str] | None = None,
    ) -> None:
        self._project_dir = project_dir
        self._compose_file = compose_file
        self._health_urls = health_urls or DEFAULT_HEALTH_URLS
        self._lock = asyncio.Lock()
        self._state = "idle"
        self._current_operation: str | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_operation(self) -> str | None:
        return self._current_operation

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    async def apply_update(self, tag: str, version: str) -> UpdateResult:
        """Apply an update to the given tag.

        Acquires an exclusive lock to prevent concurrent updates.
        Raises RuntimeError if an update is already in progress.
        """
        if self._lock.locked():
            return UpdateResult(
                status="failed",
                error="Update already in progress",
            )

        async with self._lock:
            return await self._do_apply(tag, version)

    async def rollback(self, previous_sha: str) -> UpdateResult:
        """Rollback to a previous git SHA."""
        if self._lock.locked():
            return UpdateResult(
                status="failed",
                error="Operation already in progress",
            )

        async with self._lock:
            return await self._do_rollback_full(previous_sha)

    async def _do_apply(self, tag: str, version: str) -> UpdateResult:
        """Internal: perform the full update sequence."""
        start = time.monotonic()
        self._state = "updating"
        self._current_operation = f"Updating to {tag}"

        result = UpdateResult(status="failed")

        try:
            # Step 1: Record current SHA for rollback
            prev_sha = await self._run_cmd("git rev-parse HEAD")
            if prev_sha is None:
                result.error = "Failed to get current git SHA"
                return result
            result.previous_sha = prev_sha.strip()

            # Step 2: Git fetch tag
            self._current_operation = f"Fetching tag {tag}"
            ok = await self._run_cmd(f"git fetch origin tag {tag} --force")
            if ok is None:
                result.error = "git fetch failed"
                return result
            result.steps_completed.append("git_fetch")

            # Step 3: Git checkout tag
            self._current_operation = f"Checking out {tag}"
            ok = await self._run_cmd(f"git checkout {tag}")
            if ok is None:
                result.error = "git checkout failed"
                return result
            result.steps_completed.append("git_checkout")

            # Step 4: Record new SHA
            new_sha = await self._run_cmd("git rev-parse HEAD")
            result.new_sha = new_sha.strip() if new_sha else None

            # Step 5: Docker build all app services
            self._current_operation = "Building Docker images"
            services = " ".join(APP_SERVICES)
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} build {services}",
                timeout=600,
            )
            if ok is None:
                result.error = "docker build failed"
                await self._attempt_rollback(result.previous_sha)
                result.status = "rolled_back"
                return result
            result.steps_completed.append("docker_build")

            # Step 6: Rolling restart in dependency order
            for service in APP_SERVICES:
                self._current_operation = f"Restarting {service}"
                ok = await self._run_cmd(
                    f"docker compose -f {self._compose_file} "
                    f"up -d --no-deps {service}",
                    timeout=120,
                )
                if ok is None:
                    result.error = f"Failed to restart {service}"
                    await self._attempt_rollback(result.previous_sha)
                    result.status = "rolled_back"
                    return result
                result.steps_completed.append(f"restart_{service}")

                # Wait for health (if service has a health URL)
                health_url = self._health_urls.get(service)
                if health_url:
                    self._current_operation = f"Waiting for {service} health"
                    healthy = await check_service_health(
                        health_url, HealthCheckConfig(retries=6, delay_seconds=10)
                    )
                    if not healthy:
                        result.error = f"Health check failed for {service}"
                        await self._attempt_rollback(result.previous_sha)
                        result.status = "rolled_back"
                        return result
                    result.steps_completed.append(f"health_{service}")

            # Success
            result.status = "success"
            log.info("Update to %s completed successfully", tag)

        except Exception as exc:
            result.error = f"Unexpected error: {exc}"
            log.exception("Update failed with unexpected error")
        finally:
            elapsed = time.monotonic() - start
            result.duration_seconds = round(elapsed, 2)
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self._state = "idle"
            self._current_operation = None

        return result

    async def _do_rollback_full(self, previous_sha: str) -> UpdateResult:
        """Perform a full rollback to a previous SHA."""
        start = time.monotonic()
        self._state = "rolling_back"
        self._current_operation = f"Rolling back to {previous_sha[:12]}"

        result = UpdateResult(status="failed", previous_sha=previous_sha)

        try:
            ok = await self._attempt_rollback(previous_sha)
            if ok:
                result.status = "success"
                result.new_sha = previous_sha
            else:
                result.error = "Rollback failed"
        finally:
            elapsed = time.monotonic() - start
            result.duration_seconds = round(elapsed, 2)
            result.completed_at = datetime.now(timezone.utc).isoformat()
            self._state = "idle"
            self._current_operation = None

        return result

    async def _attempt_rollback(self, previous_sha: str) -> bool:
        """Attempt to rollback to a previous SHA.

        Returns True if rollback succeeded.
        """
        if not previous_sha:
            log.error("Cannot rollback: no previous SHA")
            return False

        log.info("Rolling back to %s", previous_sha[:12])

        # Checkout previous SHA
        ok = await self._run_cmd(f"git checkout {previous_sha}")
        if ok is None:
            log.error("Rollback: git checkout failed")
            return False

        # Rebuild
        services = " ".join(APP_SERVICES)
        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} build {services}",
            timeout=600,
        )
        if ok is None:
            log.error("Rollback: docker build failed")
            return False

        # Restart all in order
        for service in APP_SERVICES:
            ok = await self._run_cmd(
                f"docker compose -f {self._compose_file} "
                f"up -d --no-deps {service}",
                timeout=120,
            )
            if ok is None:
                log.error("Rollback: failed to restart %s", service)
                return False

        # Verify health
        for service, url in self._health_urls.items():
            healthy = await check_service_health(
                url, HealthCheckConfig(retries=6, delay_seconds=10)
            )
            if not healthy:
                log.error("Rollback: health check failed for %s", service)
                return False

        log.info("Rollback to %s completed successfully", previous_sha[:12])
        return True

    async def get_diagnostics(self) -> dict:
        """Gather container-level diagnostics."""
        diagnostics: dict = {}

        # Git status
        sha = await self._run_cmd("git rev-parse HEAD")
        diagnostics["git_sha"] = sha.strip() if sha else "unknown"

        branch = await self._run_cmd("git describe --tags --exact-match 2>/dev/null || git branch --show-current")
        diagnostics["git_ref"] = branch.strip() if branch else "unknown"

        status = await self._run_cmd("git status --porcelain")
        diagnostics["git_clean"] = status is not None and status.strip() == ""

        # Docker containers
        ps_output = await self._run_cmd(
            "docker compose -f {file} ps --format json".format(
                file=self._compose_file
            )
        )
        diagnostics["containers_raw"] = ps_output.strip() if ps_output else "unavailable"

        # Disk space
        disk = await self._run_cmd("df -h / | tail -1")
        diagnostics["disk_usage"] = disk.strip() if disk else "unavailable"

        return diagnostics

    async def _run_cmd(
        self, cmd: str, timeout: int = 120
    ) -> str | None:
        """Run a shell command and return stdout, or None on failure."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._project_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

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
