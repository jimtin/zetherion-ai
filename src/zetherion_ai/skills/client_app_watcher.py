"""Client app watcher skill.

Provides tenant-facing behavior analytics and recommendation workflows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from zetherion_ai.analytics import AnalyticsAggregator, RecommendationEngine
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
    from zetherion_ai.api.tenant import TenantManager

log = get_logger("zetherion_ai.skills.client_app_watcher")


class ClientAppWatcherSkill(Skill):
    """Behavior monitoring and recommendation orchestration for tenant apps."""

    INTENTS = [
        "app_watch_run_analysis",
        "app_watch_get_recommendations",
        "app_watch_get_funnel",
        "app_watch_ack_recommendation",
    ]

    def __init__(self, tenant_manager: TenantManager | None = None) -> None:
        super().__init__(memory=None)
        self._tenant_manager = tenant_manager
        self._beat_count = 0
        self._daily_interval = 288

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="client_app_watcher",
            description="Tenant app behavior analytics and recommendations",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                }
            ),
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        if self._tenant_manager is None:
            log.warning("client_app_watcher_no_tenant_manager")
        log.info("client_app_watcher_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        if self._tenant_manager is None:
            return SkillResponse.error_response(request.id, "App watcher is not configured.")

        handlers = {
            "app_watch_run_analysis": self._handle_run_analysis,
            "app_watch_get_recommendations": self._handle_get_recommendations,
            "app_watch_get_funnel": self._handle_get_funnel,
            "app_watch_ack_recommendation": self._handle_ack_recommendation,
        }
        handler = handlers.get(request.intent)
        if handler is None:
            return SkillResponse.error_response(
                request.id, f"Unknown client_app_watcher intent: {request.intent}"
            )
        return await handler(request)

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        if self._tenant_manager is None:
            return []

        self._beat_count += 1
        if self._beat_count % self._daily_interval != 0:
            return []

        tenants = await self._tenant_manager.list_tenants()
        actions: list[HeartbeatAction] = []
        if not tenants:
            return actions

        aggregator = AnalyticsAggregator(self._tenant_manager)
        engine = RecommendationEngine(self._tenant_manager)

        for tenant in tenants:
            tenant_id = str(tenant["tenant_id"])
            funnel_rows = await aggregator.compute_daily_funnel(tenant_id)
            release = await aggregator.detect_release_regression(tenant_id)
            summary = {
                "events_by_type": {},
                "friction": {
                    "rage_clicks": 0,
                    "dead_clicks": 0,
                    "js_errors": 0,
                    "api_errors": 0,
                },
            }
            candidates = engine.generate_candidates(
                session_summary=summary,
                funnel_rows=funnel_rows,
                release_regression=release,
            )
            persisted = await engine.persist_candidates(tenant_id, candidates, source="heartbeat")
            high_risk = [p for p in persisted if p.get("risk_class") == "high"]
            if high_risk and user_ids:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="send_message",
                        user_id=user_ids[0],
                        data={
                            "message": (
                                "App watcher detected "
                                f"{len(high_risk)} high-risk recommendation(s) "
                                f"for tenant {tenant.get('name', tenant_id)}."
                            )
                        },
                        priority=6,
                    )
                )

        return actions

    async def _handle_run_analysis(self, request: SkillRequest) -> SkillResponse:
        assert self._tenant_manager is not None
        tenant_id = str(request.context.get("tenant_id", "")).strip()
        if not tenant_id:
            return SkillResponse.error_response(request.id, "tenant_id is required")

        web_session_id = request.context.get("web_session_id")
        session_id = request.context.get("session_id")

        aggregator = AnalyticsAggregator(self._tenant_manager)
        summary = await aggregator.summarize_session(
            tenant_id,
            web_session_id=web_session_id,
            session_id=session_id,
        )
        funnel_rows = await aggregator.compute_daily_funnel(tenant_id)
        release = await aggregator.detect_release_regression(tenant_id)

        engine = RecommendationEngine(self._tenant_manager)
        candidates = engine.generate_candidates(
            session_summary=summary,
            funnel_rows=funnel_rows,
            release_regression=release,
        )
        persisted = await engine.persist_candidates(tenant_id, candidates)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Analysis complete. Generated {len(persisted)} recommendation(s).",
            data={
                "summary": summary,
                "funnel": funnel_rows,
                "release": release,
                "recommendations": persisted,
            },
        )

    async def _handle_get_recommendations(self, request: SkillRequest) -> SkillResponse:
        assert self._tenant_manager is not None
        tenant_id = str(request.context.get("tenant_id", "")).strip()
        if not tenant_id:
            return SkillResponse.error_response(request.id, "tenant_id is required")

        status = request.context.get("status")
        limit = int(request.context.get("limit", 20))
        recommendations = await self._tenant_manager.list_recommendations(
            tenant_id,
            status=status,
            limit=max(1, min(limit, 200)),
        )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Found {len(recommendations)} recommendation(s).",
            data={"recommendations": recommendations},
        )

    async def _handle_get_funnel(self, request: SkillRequest) -> SkillResponse:
        assert self._tenant_manager is not None
        tenant_id = str(request.context.get("tenant_id", "")).strip()
        if not tenant_id:
            return SkillResponse.error_response(request.id, "tenant_id is required")

        metric_date_raw = request.context.get("metric_date")
        metric_date = None
        if isinstance(metric_date_raw, str) and metric_date_raw.strip():
            metric_date = datetime.fromisoformat(metric_date_raw).date()

        rows = await self._tenant_manager.get_funnel_daily(
            tenant_id,
            metric_date=metric_date,
            limit=max(1, min(int(request.context.get("limit", 200)), 500)),
        )
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Found {len(rows)} funnel row(s).",
            data={"funnel": rows},
        )

    async def _handle_ack_recommendation(self, request: SkillRequest) -> SkillResponse:
        assert self._tenant_manager is not None
        tenant_id = str(request.context.get("tenant_id", "")).strip()
        recommendation_id = str(request.context.get("recommendation_id", "")).strip()
        feedback_type = str(request.context.get("feedback_type", "")).strip()

        if not tenant_id or not recommendation_id or not feedback_type:
            return SkillResponse.error_response(
                request.id,
                "tenant_id, recommendation_id and feedback_type are required",
            )

        note = request.context.get("note")
        actor = request.context.get("actor") or f"user:{request.user_id}"
        feedback = await self._tenant_manager.add_recommendation_feedback(
            tenant_id,
            recommendation_id,
            feedback_type=feedback_type,
            note=note,
            actor=actor,
        )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Recommendation feedback recorded.",
            data={"feedback": feedback, "recorded_at": datetime.now(tz=UTC).isoformat()},
        )
