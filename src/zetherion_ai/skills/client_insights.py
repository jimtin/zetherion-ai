"""Client insights skill — relationship intelligence for James.

Aggregates signals from tenant conversations to provide James with
portfolio-level intelligence about his clients.

Levels:
    L3 (periodic): Per-tenant aggregation — volume, sentiment, conversion
    L4 (periodic): Cross-tenant benchmarks, patterns, churn risk
    L5 (continuous): Bot improvement suggestions based on thresholds
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.portfolio import (
    DERIVATION_KIND_TENANT_HEALTH,
    build_owner_portfolio_snapshot,
    build_tenant_health_derived_dataset,
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


class ClientInsightsSkill(Skill):
    """Portfolio-level intelligence about James's client tenants."""

    def __init__(
        self,
        inference_broker: InferenceBroker | None = None,
        tenant_manager: TenantManager | None = None,
        portfolio_storage: PortfolioStorage | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._broker = inference_broker
        self._tenant_manager = tenant_manager
        self._portfolio_storage = portfolio_storage
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
        elif intent == "client_health_check":
            return await self._handle_health_check(request)
        elif intent == "cross_tenant_analysis":
            return await self._handle_cross_tenant(request)
        return SkillResponse.error_response(
            request.id,
            f"Unknown client_insights intent: {intent}",
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        self._beat_count += 1
        actions: list[HeartbeatAction] = []

        if self._tenant_manager is None or self._portfolio_storage is None:
            return actions

        if self._beat_count % _L3_HEARTBEAT_INTERVAL == 0:
            actions.extend(await self._run_l3_aggregation(user_ids))

        if self._beat_count % _L4_HEARTBEAT_INTERVAL == 0:
            actions.extend(await self._run_l4_analysis(user_ids))

        return actions

    async def _handle_portfolio_summary(self, request: SkillRequest) -> SkillResponse:
        if self._tenant_manager is None or self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")

        tenants = await self._tenant_manager.list_tenants()
        if not tenants:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No active clients.",
                data={"tenants": [], "count": 0},
            )

        snapshots = [
            await self._derive_owner_snapshot_for_tenant(t, source="client_portfolio_summary")
            for t in tenants
        ]
        summaries = [snapshot["summary"] for snapshot in snapshots]

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

        if not tenant_id or self._tenant_manager is None or self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "tenant_id is required.")

        tenant = await self._tenant_manager.get_tenant(tenant_id)
        if tenant is None:
            return SkillResponse.error_response(request.id, f"Tenant `{tenant_id}` not found.")

        snapshot = await self._derive_owner_snapshot_for_tenant(
            tenant,
            source="client_health_check",
        )
        summary = snapshot["summary"]
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
            data={"health": summary, "provenance": snapshot.get("provenance", {})},
        )

    async def _handle_cross_tenant(self, request: SkillRequest) -> SkillResponse:
        if self._tenant_manager is None or self._portfolio_storage is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")
        if self._broker is None:
            return SkillResponse.error_response(
                request.id,
                "No LLM configured for cross-tenant analysis.",
            )

        tenants = await self._tenant_manager.list_tenants()
        snapshots = [
            await self._derive_owner_snapshot_for_tenant(t, source="cross_tenant_analysis")
            for t in tenants
        ]
        summaries = [snapshot["summary"] for snapshot in snapshots]

        import json

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
            analysis = TenantIntelligenceSkill._parse_json_response(result.content)
        except Exception:
            log.exception("cross_tenant_analysis_failed")
            analysis = {
                "patterns": [],
                "recommendations": [],
                "risks": [],
                "opportunities": [],
            }

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Cross-tenant analysis complete.",
            data={
                "analysis": analysis,
                "portfolio": summaries,
            },
        )

    async def _run_l3_aggregation(self, user_ids: list[str]) -> list[HeartbeatAction]:
        actions: list[HeartbeatAction] = []
        if self._tenant_manager is None or self._portfolio_storage is None:
            return actions

        tenants = await self._tenant_manager.list_tenants()
        alerts: list[str] = []

        for tenant in tenants:
            snapshot = await self._derive_owner_snapshot_for_tenant(
                tenant,
                source="heartbeat_l3_aggregation",
            )
            summary = snapshot["summary"]
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

        if alerts and user_ids:
            actions.append(
                HeartbeatAction(
                    skill_name="client_insights",
                    action_type="send_message",
                    user_id=user_ids[0],
                    data={"message": "\n".join(alerts)},
                    priority=6,
                )
            )

        return actions

    async def _run_l4_analysis(self, user_ids: list[str]) -> list[HeartbeatAction]:
        actions: list[HeartbeatAction] = []
        if self._tenant_manager is None or self._broker is None or self._portfolio_storage is None:
            return actions

        request = SkillRequest(intent="cross_tenant_analysis")
        response = await self.handle(request)

        if response.success and response.data.get("analysis") and user_ids:
            analysis = response.data["analysis"]
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

            if len(parts) > 1:
                actions.append(
                    HeartbeatAction(
                        skill_name="client_insights",
                        action_type="send_message",
                        user_id=user_ids[0],
                        data={"message": "\n".join(parts)},
                        priority=5,
                    )
                )

        return actions

    async def _derive_owner_snapshot_for_tenant(
        self,
        tenant: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        if self._tenant_manager is None or self._portfolio_storage is None:
            raise RuntimeError("Client insights storage is not configured")

        tenant_id = str(tenant.get("tenant_id") or "").strip()
        interactions = await self._tenant_manager.get_interactions(tenant_id, limit=100)
        raw_summary = self._aggregate_tenant(tenant, interactions)
        derived_payload = build_tenant_health_derived_dataset(
            zetherion_tenant_id=tenant_id,
            tenant_name=str(tenant.get("name") or raw_summary.get("name") or "Unknown"),
            raw_summary=raw_summary,
            source=source,
            provenance={
                "input_count": len(interactions),
                "tenant_domain": tenant.get("domain"),
            },
        )
        derived_dataset = await self._portfolio_storage.upsert_tenant_derived_dataset(
            zetherion_tenant_id=tenant_id,
            tenant_name=str(tenant.get("name") or raw_summary.get("name") or "Unknown"),
            derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
            source=source,
            summary=derived_payload["summary"],
            provenance=derived_payload["provenance"],
        )
        snapshot_payload = build_owner_portfolio_snapshot(
            source_dataset_id=str(derived_dataset.get("dataset_id") or ""),
            derived_summary=(derived_dataset.get("summary") or {}),
            source=source,
            provenance={
                "tenant_domain": tenant.get("domain"),
                "source_dataset_id": str(derived_dataset.get("dataset_id") or ""),
            },
        )
        return await self._portfolio_storage.upsert_owner_portfolio_snapshot(
            zetherion_tenant_id=tenant_id,
            tenant_name=str(tenant.get("name") or raw_summary.get("name") or "Unknown"),
            derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
            source_dataset_id=str(derived_dataset.get("dataset_id") or ""),
            source=source,
            summary=snapshot_payload["summary"],
            provenance=snapshot_payload["provenance"],
        )

    @staticmethod
    def _aggregate_tenant(
        tenant: dict[str, Any],
        interactions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sentiment_map = {
            "very_negative": -1.0,
            "negative": -0.5,
            "neutral": 0.0,
            "positive": 0.5,
            "very_positive": 1.0,
        }

        total = len(interactions)
        sentiments = [
            sentiment_map.get(i.get("sentiment", "neutral"), 0.0)
            for i in interactions
            if i.get("sentiment")
        ]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        outcomes = [i.get("outcome") for i in interactions if i.get("outcome")]
        escalated = sum(1 for outcome in outcomes if outcome == "escalated")
        resolved = sum(1 for outcome in outcomes if outcome == "resolved")

        escalation_rate = escalated / total if total > 0 else 0.0
        resolution_rate = resolved / len(outcomes) if outcomes else 0.0

        intents = [i.get("intent") for i in interactions if i.get("intent")]
        intent_counts: dict[str, int] = {}
        for intent in intents:
            if intent is not None:
                intent_counts[intent] = intent_counts.get(intent, 0) + 1

        behavior_summaries = [
            interaction
            for interaction in interactions
            if interaction.get("interaction_type") == "web_behavior_summary"
            and isinstance(interaction.get("entities"), dict)
        ]
        behavior_total = len(behavior_summaries)
        behavior_converted = 0
        funnel_counter: Counter[str] = Counter()
        for interaction in behavior_summaries:
            entities = interaction.get("entities") or {}
            if not isinstance(entities, dict):
                continue
            summary = entities.get("web_behavior_summary") or {}
            if not isinstance(summary, dict):
                continue
            stage = str(summary.get("funnel_stage", "unknown"))
            funnel_counter[stage] += 1
            if bool(summary.get("converted")):
                behavior_converted += 1

        behavior_conversion_rate = (
            behavior_converted / behavior_total if behavior_total > 0 else 0.0
        )

        return {
            "tenant_id": str(tenant.get("tenant_id", "")),
            "name": tenant.get("name", "Unknown"),
            "domain": tenant.get("domain"),
            "total_interactions": total,
            "avg_sentiment": round(avg_sentiment, 2),
            "escalation_rate": round(escalation_rate, 3),
            "resolution_rate": round(resolution_rate, 3),
            "top_intents": dict(
                sorted(intent_counts.items(), key=lambda item: item[1], reverse=True)[:5]
            ),
            "behavior_sessions": behavior_total,
            "behavior_conversion_rate": round(behavior_conversion_rate, 3),
            "top_funnel_stages": dict(funnel_counter.most_common(5)),
        }

    @staticmethod
    def _health_indicator(summary: dict[str, Any]) -> str:
        return f"[{health_indicator_for_summary(summary).upper()}]"
