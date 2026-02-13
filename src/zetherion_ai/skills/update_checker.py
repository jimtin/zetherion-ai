"""Update Checker Skill for Zetherion AI.

Integrates the updater/ package into the skill framework.  Checks for
new GitHub releases on a configurable schedule and either notifies the
owner or auto-applies updates.

Intents:
- ``check_update``  — check for a new release now
- ``apply_update``  — apply a pending update
- ``rollback_update`` — rollback to the previous version
- ``update_status`` — show current version and last update info
- ``resume_updates`` — clear updater pause and resume auto-rollouts
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zetherion_ai import __version__
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.health.storage import HealthStorage
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.updater.manager import ReleaseInfo, UpdateManager

log = get_logger("zetherion_ai.skills.update_checker")

# Check every 6th heartbeat (~30 min at default 5-min interval)
_DEFAULT_CHECK_EVERY_N_BEATS = 6


class UpdateCheckerSkill(Skill):
    """Monitors for new releases and manages the update lifecycle."""

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        db_pool: asyncpg.Pool | None = None,
        github_repo: str = "",
        github_token: str | None = None,
        auto_apply: bool = False,
        enabled: bool = True,
        updater_url: str = "",
        updater_secret: str = "",  # noqa: S107  # nosec B107 — not a real password
        check_every_n_beats: int = _DEFAULT_CHECK_EVERY_N_BEATS,
    ) -> None:
        super().__init__(memory)
        self._db_pool = db_pool
        self._github_repo = github_repo
        self._github_token = github_token
        self._auto_apply = auto_apply
        self._enabled = enabled
        self._updater_url = updater_url
        self._updater_secret = updater_secret
        self._check_every_n_beats = max(1, int(check_every_n_beats))

        # Lazily initialised
        self._manager: UpdateManager | None = None
        self._storage: HealthStorage | None = None
        self._pending_release: ReleaseInfo | None = None
        self._beat_count: int = 0

    # ------------------------------------------------------------------
    # Skill ABC
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="update_checker",
            description="Checks for new releases and manages updates",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                    Permission.SEND_DM,
                }
            ),
            intents=[
                "check_update",
                "apply_update",
                "rollback_update",
                "update_status",
                "resume_updates",
            ],
        )

    async def initialize(self) -> bool:
        """Set up the update manager."""
        if not self._enabled or not self._github_repo:
            log.info(
                "update_checker_disabled",
                enabled=self._enabled,
                repo=self._github_repo,
            )
            return True

        from zetherion_ai.health.storage import HealthStorage
        from zetherion_ai.updater.manager import UpdateManager

        self._storage = HealthStorage()
        if self._db_pool is not None:
            await self._storage.initialize(self._db_pool)

        self._manager = UpdateManager(
            github_repo=self._github_repo,
            storage=self._storage,
            github_token=self._github_token,
            updater_url=self._updater_url,
            updater_secret=self._updater_secret,
        )

        log.info("update_checker_initialized", repo=self._github_repo)
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle update-related queries."""
        intent = request.intent

        if intent == "check_update":
            return await self._handle_check(request)
        elif intent == "apply_update":
            return await self._handle_apply(request)
        elif intent == "rollback_update":
            return await self._handle_rollback(request)
        elif intent == "update_status":
            return await self._handle_status(request)
        elif intent == "resume_updates":
            return await self._handle_resume(request)
        else:
            return SkillResponse.error_response(request.id, f"Unknown update intent: {intent}")

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for updates periodically."""
        self._beat_count += 1
        actions: list[HeartbeatAction] = []

        if not self._enabled or self._manager is None:
            return actions

        if self._beat_count % self._check_every_n_beats != 0:
            return actions

        release = await self._manager.check_for_update()
        if release is None:
            return actions

        self._pending_release = release

        if self._auto_apply:
            log.info("update_checker_auto_applying", version=release.version)
            result = await self._manager.apply_update(release)

            if result.status.value == "success":
                actions.append(
                    HeartbeatAction(
                        skill_name="update_checker",
                        action_type="send_message",
                        user_id=user_ids[0] if user_ids else "",
                        data={
                            "message": (
                                f"**Update Applied**: "
                                f"v{self._manager.current_version} "
                                f"-> v{release.version}"
                            ),
                        },
                        priority=8,
                    )
                )
            else:
                actions.append(
                    HeartbeatAction(
                        skill_name="update_checker",
                        action_type="send_message",
                        user_id=user_ids[0] if user_ids else "",
                        data={
                            "message": (f"**Update Failed**: v{release.version} ({result.error})"),
                        },
                        priority=9,
                    )
                )
        else:
            # Notify owner about available update
            actions.append(
                HeartbeatAction(
                    skill_name="update_checker",
                    action_type="send_message",
                    user_id=user_ids[0] if user_ids else "",
                    data={
                        "message": (
                            f"**Update Available**: v{release.version} "
                            f"(currently v{self._manager.current_version})\n"
                            f"Reply 'apply update' to install."
                        ),
                    },
                    priority=7,
                )
            )

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Inject version info into agent context."""
        version = __version__
        pending = ""
        if self._pending_release:
            pending = f" | Update v{self._pending_release.version} available"
        return f"[Version] v{version}{pending}"

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_check(self, request: SkillRequest) -> SkillResponse:
        """Check for updates now."""
        if self._manager is None:
            return SkillResponse.error_response(request.id, "Update checker not configured")

        release = await self._manager.check_for_update()
        if release is None:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=(f"Already up to date (v{self._manager.current_version})"),
                data={"up_to_date": True, "version": self._manager.current_version},
            )

        self._pending_release = release
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=(
                f"Update available: v{release.version} (currently v{self._manager.current_version})"
            ),
            data={"up_to_date": False, "release": release.to_dict()},
        )

    async def _handle_apply(self, request: SkillRequest) -> SkillResponse:
        """Apply a pending update."""
        if self._manager is None:
            return SkillResponse.error_response(request.id, "Update checker not configured")

        if self._pending_release is None:
            # Check first
            release = await self._manager.check_for_update()
            if release is None:
                return SkillResponse(
                    request_id=request.id,
                    success=True,
                    message="No update available.",
                    data={},
                )
            self._pending_release = release

        result = await self._manager.apply_update(self._pending_release)
        self._pending_release = None

        return SkillResponse(
            request_id=request.id,
            success=result.status.value == "success",
            message=f"Update {result.status.value}: {result.error or 'OK'}",
            data=result.to_dict(),
        )

    async def _handle_rollback(self, request: SkillRequest) -> SkillResponse:
        """Rollback to the previous version."""
        if self._manager is None:
            return SkillResponse.error_response(request.id, "Update checker not configured")

        # Get last update record from storage
        if self._storage is None or self._storage._pool is None:
            return SkillResponse.error_response(request.id, "No update history available")

        try:
            records = await self._storage.get_update_history(limit=1)
            if not records:
                return SkillResponse(
                    request_id=request.id,
                    success=False,
                    message="No update history to rollback to.",
                    data={},
                )

            last = records[0]
            sha = last.git_sha
            if not sha:
                return SkillResponse.error_response(request.id, "No git SHA in last update record")

            ok = await self._manager.rollback(sha)
            return SkillResponse(
                request_id=request.id,
                success=ok,
                message="Rollback complete" if ok else "Rollback failed",
                data={"rolled_back_to": sha[:12]},
            )
        except Exception as exc:
            return SkillResponse.error_response(request.id, f"Rollback failed: {exc}")

    async def _handle_status(self, request: SkillRequest) -> SkillResponse:
        """Return current version and update status."""
        data: dict[str, Any] = {
            "current_version": __version__,
            "auto_apply": self._auto_apply,
            "enabled": self._enabled,
            "repo": self._github_repo,
            "check_every_n_beats": self._check_every_n_beats,
        }

        if self._pending_release:
            data["pending_release"] = self._pending_release.to_dict()

        if self._manager is not None:
            sidecar_status = await self._manager.get_sidecar_status()
            if sidecar_status:
                data["sidecar"] = sidecar_status

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Running v{__version__}",
            data=data,
        )

    async def _handle_resume(self, request: SkillRequest) -> SkillResponse:
        """Resume automatic rollouts if updater sidecar is paused."""
        if self._manager is None:
            return SkillResponse.error_response(request.id, "Update checker not configured")

        resumed = await self._manager.unpause_rollouts()
        return SkillResponse(
            request_id=request.id,
            success=resumed,
            message="Updates resumed." if resumed else "Failed to resume updates.",
            data={"resumed": resumed},
        )
