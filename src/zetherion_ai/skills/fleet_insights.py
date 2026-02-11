"""Fleet Insights Skill — central-instance only.

Surfaces cross-instance telemetry data via the skill framework.
Only registers when ``TELEMETRY_CENTRAL_MODE=true``.

Intents:
- ``fleet_status``  — instance count, versions, last report
- ``fleet_report``  — aggregated metrics across instances
- ``fleet_health``  — cross-fleet health summary
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.telemetry.receiver import TelemetryReceiver
    from zetherion_ai.telemetry.storage import TelemetryStorage

log = get_logger("zetherion_ai.skills.fleet_insights")

# Weekly analysis every ~7 days at 5-min heartbeat = 7*24*12 = 2016 beats
_WEEKLY_BEAT_INTERVAL = 2016


class FleetInsightsSkill(Skill):
    """Surfaces cross-instance telemetry insights on the central instance."""

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        receiver: TelemetryReceiver | None = None,
        storage: TelemetryStorage | None = None,
    ) -> None:
        super().__init__(memory)
        self._receiver = receiver
        self._storage = storage
        self._beat_count: int = 0

    # ------------------------------------------------------------------
    # Skill ABC
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="fleet_insights",
            description="Cross-instance telemetry insights (central only)",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                }
            ),
            intents=[
                "fleet_status",
                "fleet_report",
                "fleet_health",
            ],
        )

    async def initialize(self) -> bool:
        """No-op — receiver/storage are injected at construction time."""
        if self._receiver is None:
            log.info("fleet_insights_no_receiver")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle fleet insight queries."""
        intent = request.intent

        if intent == "fleet_status":
            return await self._handle_status(request)
        elif intent == "fleet_report":
            return await self._handle_report(request)
        elif intent == "fleet_health":
            return await self._handle_health(request)
        else:
            return SkillResponse.error_response(request.id, f"Unknown fleet intent: {intent}")

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Generate weekly fleet analysis."""
        self._beat_count += 1
        actions: list[HeartbeatAction] = []

        if self._receiver is None:
            return actions

        if self._beat_count % _WEEKLY_BEAT_INTERVAL != 0:
            return actions

        summary = await self._receiver.get_fleet_summary()
        if summary.get("total_instances", 0) == 0:
            return actions

        actions.append(
            HeartbeatAction(
                skill_name="fleet_insights",
                action_type="send_message",
                user_id=user_ids[0] if user_ids else "",
                data={
                    "message": (
                        f"**Weekly Fleet Report**: "
                        f"{summary['total_instances']} instances reporting. "
                        f"Versions: {summary.get('versions', {})}"
                    ),
                },
                priority=5,
            )
        )
        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """No runtime fragment for fleet insights."""
        return None

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_status(self, request: SkillRequest) -> SkillResponse:
        """Return fleet status summary."""
        if self._receiver is None:
            return SkillResponse.error_response(request.id, "Fleet insights not configured")

        summary = await self._receiver.get_fleet_summary()
        total = summary.get("total_instances", 0)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"{total} instance(s) reporting to central",
            data=summary,
        )

    async def _handle_report(self, request: SkillRequest) -> SkillResponse:
        """Return aggregated fleet metrics."""
        if self._storage is None:
            return SkillResponse.error_response(request.id, "Fleet storage not configured")

        aggregates = await self._storage.get_aggregates(limit=10)
        reports = await self._storage.get_reports(limit=10)

        data: dict[str, Any] = {
            "recent_aggregates": len(aggregates),
            "recent_reports": len(reports),
        }

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"{len(reports)} recent report(s), {len(aggregates)} aggregate(s)",
            data=data,
        )

    async def _handle_health(self, request: SkillRequest) -> SkillResponse:
        """Return cross-fleet health overview."""
        if self._receiver is None:
            return SkillResponse.error_response(request.id, "Fleet insights not configured")

        summary = await self._receiver.get_fleet_summary()

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Fleet health overview",
            data={
                "total_instances": summary.get("total_instances", 0),
                "versions": summary.get("versions", {}),
                "last_report": summary.get("last_report"),
            },
        )
