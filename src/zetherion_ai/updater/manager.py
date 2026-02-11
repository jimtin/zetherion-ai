"""Update manager for auto-update pipeline.

Handles checking for new GitHub releases, applying updates via
git pull + docker compose build, and rolling back on failure.
All subprocess calls are confined to this module.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx

from zetherion_ai import __version__
from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.health.storage import HealthStorage

log = get_logger("zetherion_ai.updater.manager")

# Semver regex: v1.2.3 or 1.2.3 (optional leading 'v')
_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)" r"(?:-(?P<pre>[a-zA-Z0-9.]+))?$"
)


class UpdateStatus(Enum):
    """Status of an update attempt."""

    CHECKING = "checking"
    AVAILABLE = "available"
    NO_UPDATE = "no_update"
    DOWNLOADING = "downloading"
    BUILDING = "building"
    RESTARTING = "restarting"
    VALIDATING = "validating"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class ReleaseInfo:
    """Information about a GitHub release."""

    tag: str
    version: str  # normalised, no 'v' prefix
    published_at: str
    html_url: str
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "version": self.version,
            "published_at": self.published_at,
            "html_url": self.html_url,
            "body": self.body[:500],
        }


@dataclass
class UpdateResult:
    """Result of an update attempt."""

    status: UpdateStatus
    current_version: str
    target_version: str | None = None
    previous_git_sha: str | None = None
    new_git_sha: str | None = None
    error: str | None = None
    health_check_passed: bool | None = None
    steps_completed: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "previous_git_sha": self.previous_git_sha,
            "new_git_sha": self.new_git_sha,
            "error": self.error,
            "health_check_passed": self.health_check_passed,
            "steps_completed": self.steps_completed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def parse_semver(version_str: str) -> tuple[int, int, int, str] | None:
    """Parse a semver string into (major, minor, patch, pre).

    Returns None if the string is not valid semver.
    """
    m = _SEMVER_RE.match(version_str.strip())
    if m is None:
        return None
    return (
        int(m.group("major")),
        int(m.group("minor")),
        int(m.group("patch")),
        m.group("pre") or "",
    )


def is_newer(candidate: str, current: str) -> bool:
    """Return True if *candidate* is a newer semver than *current*."""
    c = parse_semver(candidate)
    cur = parse_semver(current)
    if c is None or cur is None:
        return False

    # Compare (major, minor, patch); pre-release sorts lower
    c_tuple = (c[0], c[1], c[2], c[3] == "")
    cur_tuple = (cur[0], cur[1], cur[2], cur[3] == "")
    return c_tuple > cur_tuple


class UpdateManager:
    """Manages the update lifecycle.

    Typical flow:
    1. ``check_for_update()`` — query GitHub for the latest release
    2. ``apply_update(release)`` — git pull, build, restart, validate
    3. If validation fails → ``rollback(previous_sha)``
    """

    def __init__(
        self,
        github_repo: str,
        storage: HealthStorage | None = None,
        compose_file: str = "docker-compose.yml",
        project_dir: str = ".",
        health_url: str = "http://localhost:8080/health",
        github_token: str | None = None,
    ) -> None:
        self._repo = github_repo
        self._storage = storage
        self._compose_file = compose_file
        self._project_dir = project_dir
        self._health_url = health_url
        self._github_token = github_token

    @property
    def current_version(self) -> str:
        return __version__

    # ------------------------------------------------------------------
    # Check for updates
    # ------------------------------------------------------------------

    async def check_for_update(self) -> ReleaseInfo | None:
        """Query GitHub API for the latest release.

        Returns a ``ReleaseInfo`` if a newer version is available,
        or None if already up to date.
        """
        try:
            headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
            if self._github_token:
                headers["Authorization"] = f"Bearer {self._github_token}"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{self._repo}/releases/latest",
                    headers=headers,
                )

            if resp.status_code == 404:
                log.debug("updater_no_releases")
                return None

            if resp.status_code != 200:
                log.warning(
                    "updater_github_api_error",
                    status=resp.status_code,
                )
                return None

            data = resp.json()
            tag = data.get("tag_name", "")
            version = tag.lstrip("v")

            if not is_newer(version, self.current_version):
                log.debug(
                    "updater_up_to_date",
                    current=self.current_version,
                    latest=version,
                )
                return None

            release = ReleaseInfo(
                tag=tag,
                version=version,
                published_at=data.get("published_at", ""),
                html_url=data.get("html_url", ""),
                body=data.get("body", ""),
            )
            log.info(
                "updater_new_release_found",
                current=self.current_version,
                new=version,
            )
            return release

        except httpx.RequestError as exc:
            log.warning("updater_check_failed", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Apply update
    # ------------------------------------------------------------------

    async def apply_update(self, release: ReleaseInfo) -> UpdateResult:
        """Apply an update: git pull → docker build → restart → validate.

        Returns an ``UpdateResult`` with the outcome.
        """
        result = UpdateResult(
            status=UpdateStatus.DOWNLOADING,
            current_version=self.current_version,
            target_version=release.version,
        )

        # Record the current git SHA for rollback
        sha = await self._run_cmd("git rev-parse HEAD")
        result.previous_git_sha = sha.strip() if sha else None

        # Record update attempt in storage
        await self._record_update(result)

        # Step 1: git fetch + checkout tag
        result.status = UpdateStatus.DOWNLOADING
        ok = await self._run_cmd(f"git fetch origin tag {release.tag}")
        if ok is None:
            result.status = UpdateStatus.FAILED
            result.error = "git fetch failed"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result
        result.steps_completed.append("git_fetch")

        ok = await self._run_cmd(f"git checkout {release.tag}")
        if ok is None:
            result.status = UpdateStatus.FAILED
            result.error = "git checkout failed"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result
        result.steps_completed.append("git_checkout")

        # Step 2: docker compose build
        result.status = UpdateStatus.BUILDING
        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} build",
            timeout=600,
        )
        if ok is None:
            result.status = UpdateStatus.FAILED
            result.error = "docker build failed"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result
        result.steps_completed.append("docker_build")

        # Record new git SHA
        new_sha = await self._run_cmd("git rev-parse HEAD")
        result.new_git_sha = new_sha.strip() if new_sha else None

        # Step 3: restart services
        result.status = UpdateStatus.RESTARTING
        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} up -d --no-deps bot",
            timeout=120,
        )
        if ok is None:
            result.status = UpdateStatus.FAILED
            result.error = "service restart failed"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result
        result.steps_completed.append("service_restart")

        # Step 4: validate health
        result.status = UpdateStatus.VALIDATING
        healthy = await self._validate_health(retries=6, delay=10)
        result.health_check_passed = healthy

        if not healthy:
            log.warning(
                "updater_health_check_failed",
                version=release.version,
            )
            # Rollback
            rollback_ok = await self.rollback(result.previous_git_sha or "")
            result.status = UpdateStatus.ROLLED_BACK if rollback_ok else UpdateStatus.FAILED
            result.error = "health check failed after update"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result

        result.status = UpdateStatus.SUCCESS
        result.completed_at = datetime.now().isoformat()
        log.info(
            "updater_success",
            version=release.version,
        )
        await self._record_update(result)
        return result

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def rollback(self, previous_sha: str) -> bool:
        """Rollback to a previous git SHA.

        Returns True if rollback succeeded.
        """
        if not previous_sha:
            log.error("updater_rollback_no_sha")
            return False

        log.info("updater_rolling_back", sha=previous_sha[:12])

        ok = await self._run_cmd(f"git checkout {previous_sha}")
        if ok is None:
            log.error("updater_rollback_checkout_failed")
            return False

        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} build",
            timeout=600,
        )
        if ok is None:
            log.error("updater_rollback_build_failed")
            return False

        ok = await self._run_cmd(
            f"docker compose -f {self._compose_file} up -d --no-deps bot",
            timeout=120,
        )
        if ok is None:
            log.error("updater_rollback_restart_failed")
            return False

        log.info("updater_rollback_complete")
        return True

    # ------------------------------------------------------------------
    # Health validation
    # ------------------------------------------------------------------

    async def _validate_health(self, retries: int = 6, delay: int = 10) -> bool:
        """Check the health endpoint, retrying on failure."""
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(self._health_url)
                if resp.status_code == 200:
                    log.debug(
                        "updater_health_ok",
                        attempt=attempt + 1,
                    )
                    return True
            except httpx.RequestError:
                pass

            if attempt < retries - 1:
                await asyncio.sleep(delay)

        return False

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

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
                    "updater_cmd_failed",
                    cmd=cmd,
                    returncode=proc.returncode,
                    stderr=stderr.decode()[:500],
                )
                return None

            return stdout.decode()

        except TimeoutError:
            log.warning("updater_cmd_timeout", cmd=cmd, timeout=timeout)
            return None
        except Exception as exc:
            log.warning("updater_cmd_error", cmd=cmd, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def _record_update(self, result: UpdateResult) -> None:
        """Record an update attempt in storage."""
        if self._storage is None:
            return

        try:
            from zetherion_ai.health.storage import (
                UpdateRecord,
            )
            from zetherion_ai.health.storage import (
                UpdateStatus as StorageStatus,
            )

            status_map = {
                UpdateStatus.SUCCESS: StorageStatus.SUCCESS,
                UpdateStatus.FAILED: StorageStatus.FAILED,
                UpdateStatus.ROLLED_BACK: StorageStatus.ROLLED_BACK,
            }
            storage_status = status_map.get(result.status, StorageStatus.APPLYING)

            record = UpdateRecord(
                timestamp=datetime.now(),
                version=result.target_version or "",
                previous_version=result.current_version,
                git_sha=result.new_git_sha or result.previous_git_sha or "",
                status=storage_status,
                health_check_result=result.to_dict(),
            )
            await self._storage.save_update_record(record)
        except Exception as exc:
            log.warning("updater_record_failed", error=str(exc))
