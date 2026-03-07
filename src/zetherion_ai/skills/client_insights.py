"""Client insights skill — relationship intelligence for James.

Aggregates owner-safe tenant portfolio snapshots to provide James with
portfolio-level intelligence about his clients.

Levels:
    L3 (periodic): Per-tenant aggregation — volume, sentiment, conversion
    L4 (periodic): Cross-tenant benchmarks, patterns, churn risk
    L5 (continuous): Bot improvement suggestions based on thresholds
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.portfolio import (
    DERIVATION_KIND_TENANT_HEALTH,
    OwnerPortfolioPipeline,
    aggregate_tenant_interactions,
    health_indicator_for_summary,
)
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.api.tenant import TenantManager
    from zetherion_ai.portfolio.storage import PortfolioStorage

log = get_logger("zetherion_ai.skills.client_insights")

_L3_HEARTBEAT_INTERVAL = 12
_L4_HEARTBEAT_INTERVAL = 288
_SENTIMENT_DROP_THRESHOLD = -0.2
_ESCALATION_RATE_THRESHOLD = 0.15

_L4_ANALYSIS_PROMPT = """\
You are a business intelligence analyst reviewing derived tenant health data
across multiple client tenants. Provide strategic insights for the platform
owner. Use only the provided derived summaries. Do not assume access to raw
messages or direct tenant conversation transcripts.

Respond ONLY with valid JSON. No markdown, no commentary.

Tenant derived summaries:
{tenant_data}

Analyse and provide:
{{
  "patterns": ["<cross-tenant pattern or trend>"],
  "recommendations": ["<actionable recommendation for the platform owner>"],
  "risks": ["<churn risk or concern, with tenant name>"],
  "opportunities": ["<growth or upsell opportunity>"]
}}
"""

_EMPTY_ANALYSIS: dict[str, list[str]] = {
    "patterns": [],
    "recommendations": [],
    "risks": [],
    "opportunities": [],
}


class ClientInsightsSkill(Skill):
    """Portfolio-level intelligence about James's client tenants."""

    def __init__(
        self,
        inference_broker: InferenceBroker | None = None,
        tenant_manager: TenantManager | None = None,
        portfolio_storage: PortfolioStorage | None = None,
        portfolio_pipeline: OwnerPortfolioPipeline | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._broker = inference_broker
        self._tenant_manager = tenant_manager
        self._portfolio_storage = portfolio_storage
        self._portfolio_pipeline = portfolio_pipeline
        if (
            self._portfolio_pipeline is None
            and tenant_manager is not None
            and portfolio_storage is not None
        ):
            self._portfolio_pipeline = OwnerPortfolioPipeline(
                tenant_manager=tenant_manager,
                portfolio_storage=portfolio_storage,
            )
        self._beat_count = 0

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="client_insights",
            description="Portfolio intelligence and cross-tenant analytics for James",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                    Permission.SEND_DM,
                }
            ),
            intents=[
                "client_portfolio_summary",
                "client_health_check",
                "cross_tenant_analysis",
            ],
        )

    async def initialize(self) -> bool:
        if self._broker is None:
            log.warning("client_insights_no_broker")
        if self._tenant_manager is None:
            log.warning("client_insights_no_tenant_manager")
        if self._portfolio_storage is None:
            log.warning("client_insights_no_portfolio_storage")
        else:
            await self._portfolio_storage.initialize()
        log.info("client_insights_initialized")
        return True

    async def cleanup(self) -> None:
        if self._portfolio_storage is not None:
            await self._portfolio_storage.close()

    async def handle(self, request: SkillRequest) -> SkillResponse:
        intent = request.intent
        if intent == "client_portfolio_summary":
            return await self._handle_portfolio_summary(request)
        if intent == "client_health_check":
            return await self._handle_health_check(request)
        if intent == "cross_tenant_analysis":
            return await self._handle_cross_tenant(request)
        return SkillResponse.error_response(
            request.id,
            f"Unknown client_insights intent: {intent}",
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        self._beat_count += 1
        run_l3 = self._beat_count % _L3_HEARTBEAT_INTERVAL == 0
        run_l4 = self._beat_count % _L4_HEARTBEAT_INTERVAL == 0
        if not run_l3 and not run_l4:
            return []

        summaries = await self._prepare_owner_portfolio_summaries(
            refresh=bool(self._portfolio_pipeline),
            source="heartbeat_owner_portfolio_refresh",
        )

        actions: list[HeartbeatAction] = []
        if run_l3:
            actions.extend(await self._run_l3_aggregation(user_ids, summaries=summaries))
        if run_l4:
            actions.extend(await self._run_l4_analysis(user_ids, summaries=summaries))
        return actions

    async def _handle_portfolio_summary(self, request: SkillRequest) -> SkillResponse:
        if self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")

        summaries = await self._prepare_owner_portfolio_summaries(
            refresh=bool(request.context.get("refresh_portfolio")),
            source=str(request.context.get("source") or "client_portfolio_summary"),
        )
        if not summaries:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No owner portfolio snapshots available yet.",
                data={"portfolio": [], "count": 0},
            )

        lines = [f"**Portfolio Summary — {len(summaries)} client(s):**\n"]
        for summary in summaries:
            status = self._health_indicator(summary)
            lines.append(
                f"- {status} **{summary['tenant_name']}** — "
                f"{summary['total_interactions']} interactions, "
                f"avg sentiment: {summary['avg_sentiment']}"
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="\n".join(lines),
            data={"portfolio": summaries, "count": len(summaries)},
        )

    async def _handle_health_check(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        if not tenant_id or self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "tenant_id is required.")

        refresh_portfolio = bool(ctx.get("refresh_portfolio"))
        source = str(ctx.get("source") or "client_health_check")
        snapshot: dict[str, Any] | None = None
        if refresh_portfolio:
            if self._tenant_manager is None or self._portfolio_pipeline is None:
                return SkillResponse.error_response(
                    request.id,
                    "Portfolio refresh is not configured.",
                )
            tenant = await self._tenant_manager.get_tenant(tenant_id)
            if tenant is None:
                return SkillResponse.error_response(
                    request.id,
                    f"Tenant `{tenant_id}` not found.",
                )
            snapshot = await self._portfolio_pipeline.refresh_tenant_health_snapshot(
                tenant,
                source=source,
            )
        else:
            snapshot = await self._portfolio_storage.get_owner_portfolio_snapshot(
                zetherion_tenant_id=tenant_id,
                derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
            )
        if snapshot is None:
            return SkillResponse.error_response(
                request.id,
                f"Owner portfolio snapshot for tenant `{tenant_id}` not found.",
            )

        summary = snapshot.get("summary") or {}
        status = self._health_indicator(summary)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=(
                f"{status} **{summary['tenant_name']}**\n"
                f"- Interactions: {summary['total_interactions']}\n"
                f"- Avg sentiment: {summary['avg_sentiment']}\n"
                f"- Escalation rate: {summary['escalation_rate']:.0%}\n"
                f"- Resolution rate: {summary['resolution_rate']:.0%}"
            ),
            data={
                "health": summary,
                "provenance": snapshot.get("provenance", {}),
                "snapshot_id": snapshot.get("snapshot_id"),
                "source_dataset_id": snapshot.get("source_dataset_id"),
            },
        )

    async def _handle_cross_tenant(self, request: SkillRequest) -> SkillResponse:
        if self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")
        if self._broker is None:
            return SkillResponse.error_response(
                request.id,
                "No LLM configured for cross-tenant analysis.",
            )

        summaries = await self._prepare_owner_portfolio_summaries(
            refresh=bool(request.context.get("refresh_portfolio")),
            source=str(request.context.get("source") or "cross_tenant_analysis"),
        )
        if not summaries:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No owner portfolio snapshots available yet.",
                data={"analysis": dict(_EMPTY_ANALYSIS), "portfolio": []},
            )

        analysis = await self._analyse_cross_tenant_summaries(summaries)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Cross-tenant analysis complete.",
            data={
                "analysis": analysis,
                "portfolio": summaries,
            },
        )

    async def _prepare_owner_portfolio_summaries(
        self,
        *,
        refresh: bool,
        source: str,
    ) -> list[dict[str, Any]]:
        if self._portfolio_storage is None:
            return []
        if refresh and self._portfolio_pipeline is not None:
            await self._portfolio_pipeline.refresh_all_tenant_health_snapshots(source=source)
        snapshots = await self._portfolio_storage.list_owner_portfolio_snapshots(
            derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
        )
        return [
            snapshot_summary
            for snapshot in snapshots
            if isinstance(snapshot.get("summary"), dict)
            for snapshot_summary in [dict(snapshot.get("summary") or {})]
        ]

    async def _analyse_cross_tenant_summaries(
        self,
        summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._broker is None:
            return dict(_EMPTY_ANALYSIS)

        prompt = _L4_ANALYSIS_PROMPT.format(tenant_data=json.dumps(summaries, default=str))

        try:
            from zetherion_ai.agent.providers import TaskType
            from zetherion_ai.skills.tenant_intelligence import TenantIntelligenceSkill

            result = await self._broker.infer(
                prompt=prompt,
                task_type=TaskType.DATA_EXTRACTION,
                temperature=0.3,
                max_tokens=1000,
            )
            return TenantIntelligenceSkill._parse_json_response(result.content)
        except Exception:
            log.exception("cross_tenant_analysis_failed")
            return dict(_EMPTY_ANALYSIS)

    async def _run_l3_aggregation(
        self,
        user_ids: list[str],
        *,
        summaries: list[dict[str, Any]] | None = None,
    ) -> list[HeartbeatAction]:
        if not user_ids:
            return []
        if summaries is None:
            summaries = await self._prepare_owner_portfolio_summaries(
                refresh=False,
                source="heartbeat_l3_aggregation",
            )
        alerts: list[str] = []
        for summary in summaries:
            if summary["escalation_rate"] > _ESCALATION_RATE_THRESHOLD:
                alerts.append(
                    f"High escalation rate ({summary['escalation_rate']:.0%}) "
                    f"for **{summary['tenant_name']}** — chatbot may need tuning."
                )
            elif summary["avg_sentiment"] < _SENTIMENT_DROP_THRESHOLD:
                alerts.append(
                    f"Sentiment dropped for **{summary['tenant_name']}** "
                    f"(avg {summary['avg_sentiment']})."
                )
        if not alerts:
            return []
        return [
            HeartbeatAction(
                skill_name="client_insights",
                action_type="send_message",
                user_id=user_ids[0],
                data={"message": "\n".join(alerts)},
                priority=6,
            )
        ]

    async def _run_l4_analysis(
        self,
        user_ids: list[str],
        *,
        summaries: list[dict[str, Any]] | None = None,
    ) -> list[HeartbeatAction]:
        if not user_ids:
            return []
        if summaries is None:
            summaries = await self._prepare_owner_portfolio_summaries(
                refresh=False,
                source="heartbeat_l4_analysis",
            )
        if not summaries or self._broker is None:
            return []

        analysis = await self._analyse_cross_tenant_summaries(summaries)
        parts = ["**Weekly Cross-Tenant Intelligence:**\n"]
        for key, label in [
            ("patterns", "Patterns"),
            ("risks", "Risks"),
            ("opportunities", "Opportunities"),
            ("recommendations", "Recommendations"),
        ]:
            items = analysis.get(key, [])
            if items:
                parts.append(f"**{label}:**")
                for item in items:
                    parts.append(f"  - {item}")
        if len(parts) == 1:
            return []
        return [
            HeartbeatAction(
                skill_name="client_insights",
                action_type="send_message",
                user_id=user_ids[0],
                data={"message": "\n".join(parts)},
                priority=5,
            )
        ]

    @staticmethod
    def _aggregate_tenant(
        tenant: dict[str, Any],
        interactions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return aggregate_tenant_interactions(tenant, interactions)

    @staticmethod
    def _health_indicator(summary: dict[str, Any]) -> str:
        return f"[{health_indicator_for_summary(summary).upper()}]"
