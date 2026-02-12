"""Update manager for auto-update pipeline.

Handles checking for new GitHub releases and delegating update
application to the updater sidecar container via HTTP.
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
    """Manages the update lifecycle via the updater sidecar.

    Typical flow:
    1. ``check_for_update()`` — query GitHub for the latest release
    2. ``apply_update(release)`` — delegate to updater sidecar via HTTP
    3. Sidecar handles git pull, build, restart, health check, rollback
    """

    def __init__(
        self,
        github_repo: str,
        storage: HealthStorage | None = None,
        updater_url: str = "",
        updater_secret: str = "",
        health_url: str = "http://localhost:8080/health",
        github_token: str | None = None,
    ) -> None:
        self._repo = github_repo
        self._storage = storage
        self._updater_url = updater_url.rstrip("/")
        self._updater_secret = updater_secret
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
    # Apply update via sidecar
    # ------------------------------------------------------------------

    async def apply_update(self, release: ReleaseInfo) -> UpdateResult:
        """Apply an update by delegating to the updater sidecar.

        The sidecar handles: git fetch → checkout → docker build →
        rolling restart → health validation → rollback on failure.
        """
        result = UpdateResult(
            status=UpdateStatus.DOWNLOADING,
            current_version=self.current_version,
            target_version=release.version,
        )

        # Record update attempt in storage
        await self._record_update(result)

        if not self._updater_url:
            result.status = UpdateStatus.FAILED
            result.error = "No updater sidecar URL configured"
            result.completed_at = datetime.now().isoformat()
            await self._record_update(result)
            return result

        try:
            headers: dict[str, str] = {}
            if self._updater_secret:
                headers["X-Updater-Secret"] = self._updater_secret

            async with httpx.AsyncClient(timeout=900) as client:
                resp = await client.post(
                    f"{self._updater_url}/update/apply",
                    json={
                        "tag": release.tag,
                        "version": release.version,
                    },
                    headers=headers,
                )

            if resp.status_code == 409:
                result.status = UpdateStatus.FAILED
                result.error = "Update already in progress"
                result.completed_at = datetime.now().isoformat()
                await self._record_update(result)
                return result

            data = resp.json()
            result.previous_git_sha = data.get("previous_sha")
            result.new_git_sha = data.get("new_sha")
            result.steps_completed = data.get("steps_completed", [])

            sidecar_status = data.get("status", "failed")
            if sidecar_status == "success":
                result.status = UpdateStatus.SUCCESS
                result.health_check_passed = True
            elif sidecar_status == "rolled_back":
                result.status = UpdateStatus.ROLLED_BACK
                result.error = data.get("error", "health check failed")
                result.health_check_passed = False
            else:
                result.status = UpdateStatus.FAILED
                result.error = data.get("error", "unknown error")

        except httpx.RequestError as exc:
            result.status = UpdateStatus.FAILED
            result.error = f"Cannot reach updater sidecar: {exc}"

        result.completed_at = datetime.now().isoformat()
        await self._record_update(result)
        return result

    # ------------------------------------------------------------------
    # Rollback via sidecar
    # ------------------------------------------------------------------

    async def rollback(self, previous_sha: str) -> bool:
        """Rollback to a previous git SHA via the updater sidecar.

        Returns True if rollback succeeded.
        """
        if not previous_sha:
            log.error("updater_rollback_no_sha")
            return False

        if not self._updater_url:
            log.error("updater_rollback_no_url")
            return False

        log.info("updater_rolling_back", sha=previous_sha[:12])

        try:
            headers: dict[str, str] = {}
            if self._updater_secret:
                headers["X-Updater-Secret"] = self._updater_secret

            async with httpx.AsyncClient(timeout=900) as client:
                resp = await client.post(
                    f"{self._updater_url}/update/rollback",
                    json={"previous_sha": previous_sha},
                    headers=headers,
                )

            data = resp.json()
            if data.get("status") == "success":
                log.info("updater_rollback_complete")
                return True

            log.error(
                "updater_rollback_failed",
                error=data.get("error", "unknown"),
            )
            return False

        except httpx.RequestError as exc:
            log.error("updater_rollback_error", error=str(exc))
            return False

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
