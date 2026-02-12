"""Client insights skill — relationship intelligence for James.

Aggregates signals from tenant conversations to provide James with
portfolio-level intelligence about his clients.

Levels:
    L3 (periodic): Per-tenant aggregation — volume, sentiment, conversion
    L4 (periodic): Cross-tenant benchmarks, patterns, churn risk
    L5 (continuous): Bot improvement suggestions based on thresholds
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
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.api.tenant import TenantManager

log = get_logger("zetherion_ai.skills.client_insights")

# Heartbeat frequency: run L3 every 12th beat (~1 hour at 5-min beats)
_L3_HEARTBEAT_INTERVAL = 12
# L4 cross-tenant analysis: every 288th beat (~24 hours)
_L4_HEARTBEAT_INTERVAL = 288

# Thresholds for L5 alerts
_SENTIMENT_DROP_THRESHOLD = -0.2  # >20% sentiment drop triggers alert
_ESCALATION_RATE_THRESHOLD = 0.15  # >15% escalation rate triggers alert

# LLM prompt for cross-tenant analysis
_L4_ANALYSIS_PROMPT = """\
You are a business intelligence analyst reviewing chat data across multiple
client tenants. Provide strategic insights for the platform owner.

Respond ONLY with valid JSON. No markdown, no commentary.

Tenant data:
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
    ) -> None:
        super().__init__(memory=None)
        self._broker = inference_broker
        self._tenant_manager = tenant_manager
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
        log.info("client_insights_initialized")
        return True

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
        """Periodic aggregation and alerting."""
        self._beat_count += 1
        actions: list[HeartbeatAction] = []

        if self._tenant_manager is None:
            return actions

        # L3: Per-tenant aggregation
        if self._beat_count % _L3_HEARTBEAT_INTERVAL == 0:
            l3_actions = await self._run_l3_aggregation(user_ids)
            actions.extend(l3_actions)

        # L4: Cross-tenant analysis
        if self._beat_count % _L4_HEARTBEAT_INTERVAL == 0:
            l4_actions = await self._run_l4_analysis(user_ids)
            actions.extend(l4_actions)

        return actions

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_portfolio_summary(self, request: SkillRequest) -> SkillResponse:
        """On-demand: "How are my clients doing?" """
        if self._tenant_manager is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")

        tenants = await self._tenant_manager.list_tenants()
        if not tenants:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No active clients.",
                data={"tenants": [], "count": 0},
            )

        summaries = []
        for t in tenants:
            tenant_id = str(t["tenant_id"])
            interactions = await self._tenant_manager.get_interactions(tenant_id, limit=100)
            summary = self._aggregate_tenant(t, interactions)
            summaries.append(summary)

        # Build readable report
        lines = [f"**Portfolio Summary — {len(summaries)} client(s):**\n"]
        for s in summaries:
            status = self._health_indicator(s)
            lines.append(
                f"- {status} **{s['name']}** — "
                f"{s['total_interactions']} interactions, "
                f"avg sentiment: {s['avg_sentiment']}"
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="\n".join(lines),
            data={"portfolio": summaries, "count": len(summaries)},
        )

    async def _handle_health_check(self, request: SkillRequest) -> SkillResponse:
        """Health check for a specific tenant."""
        ctx = request.context
        tenant_id = ctx.get("tenant_id", "")

        if not tenant_id or self._tenant_manager is None:
            return SkillResponse.error_response(request.id, "tenant_id is required.")

        tenant = await self._tenant_manager.get_tenant(tenant_id)
        if tenant is None:
            return SkillResponse.error_response(request.id, f"Tenant `{tenant_id}` not found.")

        interactions = await self._tenant_manager.get_interactions(tenant_id, limit=100)
        summary = self._aggregate_tenant(tenant, interactions)
        status = self._health_indicator(summary)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=(
                f"{status} **{summary['name']}**\n"
                f"- Interactions: {summary['total_interactions']}\n"
                f"- Avg sentiment: {summary['avg_sentiment']}\n"
                f"- Escalation rate: {summary['escalation_rate']:.0%}\n"
                f"- Resolution rate: {summary['resolution_rate']:.0%}"
            ),
            data={"health": summary},
        )

    async def _handle_cross_tenant(self, request: SkillRequest) -> SkillResponse:
        """Cross-tenant analysis using LLM."""
        if self._tenant_manager is None:
            return SkillResponse.error_response(request.id, "Client insights not configured.")
        if self._broker is None:
            return SkillResponse.error_response(
                request.id, "No LLM configured for cross-tenant analysis."
            )

        tenants = await self._tenant_manager.list_tenants()
        summaries = []
        for t in tenants:
            interactions = await self._tenant_manager.get_interactions(
                str(t["tenant_id"]), limit=100
            )
            summaries.append(self._aggregate_tenant(t, interactions))

        import json

        prompt = _L4_ANALYSIS_PROMPT.format(tenant_data=json.dumps(summaries, default=str))

        try:
            from zetherion_ai.agent.providers import TaskType

            result = await self._broker.infer(
                prompt=prompt,
                task_type=TaskType.DATA_EXTRACTION,
                temperature=0.3,
                max_tokens=1000,
            )
            from zetherion_ai.skills.tenant_intelligence import (
                TenantIntelligenceSkill,
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
            data={"analysis": analysis},
        )

    # ------------------------------------------------------------------
    # L3 — Periodic per-tenant aggregation
    # ------------------------------------------------------------------

    async def _run_l3_aggregation(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Run L3 per-tenant aggregation and generate alerts."""
        actions: list[HeartbeatAction] = []
        if self._tenant_manager is None:
            return actions

        tenants = await self._tenant_manager.list_tenants()
        alerts: list[str] = []

        for t in tenants:
            tenant_id = str(t["tenant_id"])
            interactions = await self._tenant_manager.get_interactions(tenant_id, limit=100)
            summary = self._aggregate_tenant(t, interactions)

            # L5: Check thresholds
            if summary["escalation_rate"] > _ESCALATION_RATE_THRESHOLD:
                alerts.append(
                    f"High escalation rate ({summary['escalation_rate']:.0%}) "
                    f"for **{summary['name']}** — chatbot may need tuning."
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

    # ------------------------------------------------------------------
    # L4 — Cross-tenant analysis (periodic)
    # ------------------------------------------------------------------

    async def _run_l4_analysis(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Run L4 cross-tenant analysis and notify James."""
        actions: list[HeartbeatAction] = []
        if self._tenant_manager is None or self._broker is None:
            return actions

        # Use the handle method to run the analysis
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

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_tenant(
        tenant: dict[str, Any],
        interactions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate interaction data for a single tenant."""
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
        escalated = sum(1 for o in outcomes if o == "escalated")
        resolved = sum(1 for o in outcomes if o == "resolved")

        escalation_rate = escalated / total if total > 0 else 0.0
        resolution_rate = resolved / len(outcomes) if outcomes else 0.0

        intents = [i.get("intent") for i in interactions if i.get("intent")]
        intent_counts: dict[str, int] = {}
        for intent in intents:
            if intent is not None:
                intent_counts[intent] = intent_counts.get(intent, 0) + 1

        return {
            "tenant_id": str(tenant.get("tenant_id", "")),
            "name": tenant.get("name", "Unknown"),
            "domain": tenant.get("domain"),
            "total_interactions": total,
            "avg_sentiment": round(avg_sentiment, 2),
            "escalation_rate": round(escalation_rate, 3),
            "resolution_rate": round(resolution_rate, 3),
            "top_intents": dict(
                sorted(intent_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
        }

    @staticmethod
    def _health_indicator(summary: dict[str, Any]) -> str:
        """Return a red/amber/green text indicator."""
        if summary["escalation_rate"] > _ESCALATION_RATE_THRESHOLD:
            return "[RED]"
        if summary["avg_sentiment"] < -0.2:
            return "[AMBER]"
        return "[GREEN]"
