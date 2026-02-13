"""Tests for client_insights skill — portfolio intelligence for James."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.client_insights import ClientInsightsSkill

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TENANT_A = {
    "tenant_id": uuid4(),
    "name": "Bob's Plumbing",
    "domain": "bobsplumbing.com",
    "is_active": True,
    "config": {},
}

_TENANT_B = {
    "tenant_id": uuid4(),
    "name": "Sarah's Salon",
    "domain": "sarahssalon.com",
    "is_active": True,
    "config": {},
}

_INTERACTIONS = [
    {
        "interaction_id": uuid4(),
        "tenant_id": _TENANT_A["tenant_id"],
        "sentiment": "positive",
        "intent": "enquiry",
        "outcome": "resolved",
    },
    {
        "interaction_id": uuid4(),
        "tenant_id": _TENANT_A["tenant_id"],
        "sentiment": "negative",
        "intent": "complaint",
        "outcome": "escalated",
    },
    {
        "interaction_id": uuid4(),
        "tenant_id": _TENANT_A["tenant_id"],
        "sentiment": "neutral",
        "intent": "enquiry",
        "outcome": "resolved",
    },
]


@dataclass
class _FakeResult:
    content: str = "{}"
    model: str = "test"
    provider: str = "test"
    input_tokens: int = 10
    output_tokens: int = 20
    latency_ms: float = 100.0
    estimated_cost_usd: float = 0.0


def _make_broker() -> MagicMock:
    broker = MagicMock()
    analysis = json.dumps(
        {
            "patterns": ["Most enquiries relate to pricing"],
            "recommendations": ["Add FAQ to Bob's chatbot"],
            "risks": ["Sarah's sentiment declining"],
            "opportunities": ["Bob's customers ask about gas safety"],
        }
    )
    broker.infer = AsyncMock(return_value=_FakeResult(content=analysis))
    return broker


def _make_tenant_manager() -> AsyncMock:
    tm = AsyncMock()
    tm.list_tenants = AsyncMock(return_value=[_TENANT_A, _TENANT_B])
    tm.get_tenant = AsyncMock(return_value=_TENANT_A)
    tm.get_interactions = AsyncMock(return_value=_INTERACTIONS)
    return tm


@pytest.fixture
def skill() -> ClientInsightsSkill:
    return ClientInsightsSkill(
        inference_broker=_make_broker(),
        tenant_manager=_make_tenant_manager(),
    )


@pytest.fixture
def skill_no_tm() -> ClientInsightsSkill:
    return ClientInsightsSkill(inference_broker=_make_broker(), tenant_manager=None)


# ---------------------------------------------------------------------------
# Metadata & init
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_name(self, skill: ClientInsightsSkill) -> None:
        assert skill.metadata.name == "client_insights"

    def test_intents(self, skill: ClientInsightsSkill) -> None:
        assert "client_portfolio_summary" in skill.metadata.intents
        assert "client_health_check" in skill.metadata.intents
        assert "cross_tenant_analysis" in skill.metadata.intents


class TestInitialize:
    @pytest.mark.asyncio
    async def test_init_success(self, skill: ClientInsightsSkill) -> None:
        assert await skill.initialize() is True

    @pytest.mark.asyncio
    async def test_safe_init_ready(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_aggregate_tenant(self) -> None:
        summary = ClientInsightsSkill._aggregate_tenant(_TENANT_A, _INTERACTIONS)
        assert summary["name"] == "Bob's Plumbing"
        assert summary["total_interactions"] == 3
        # avg sentiment: (0.5 + -0.5 + 0.0) / 3 = 0.0
        assert summary["avg_sentiment"] == 0.0
        # escalation: 1/3
        assert abs(summary["escalation_rate"] - 0.333) < 0.01
        # resolution: 2/3
        assert abs(summary["resolution_rate"] - 0.667) < 0.01
        assert "enquiry" in summary["top_intents"]

    def test_aggregate_empty_interactions(self) -> None:
        summary = ClientInsightsSkill._aggregate_tenant(_TENANT_A, [])
        assert summary["total_interactions"] == 0
        assert summary["avg_sentiment"] == 0.0
        assert summary["escalation_rate"] == 0.0
        assert summary["resolution_rate"] == 0.0

    def test_health_indicator_green(self) -> None:
        summary = {
            "escalation_rate": 0.05,
            "avg_sentiment": 0.3,
        }
        assert ClientInsightsSkill._health_indicator(summary) == "[GREEN]"

    def test_health_indicator_amber(self) -> None:
        summary = {
            "escalation_rate": 0.05,
            "avg_sentiment": -0.3,
        }
        assert ClientInsightsSkill._health_indicator(summary) == "[AMBER]"

    def test_health_indicator_red(self) -> None:
        summary = {
            "escalation_rate": 0.20,
            "avg_sentiment": 0.5,
        }
        assert ClientInsightsSkill._health_indicator(summary) == "[RED]"


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------


class TestPortfolioSummary:
    @pytest.mark.asyncio
    async def test_portfolio_summary(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_portfolio_summary")
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["count"] == 2
        assert "Bob's Plumbing" in resp.message

    @pytest.mark.asyncio
    async def test_portfolio_no_clients(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.list_tenants = AsyncMock(return_value=[])
        req = SkillRequest(intent="client_portfolio_summary")
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "No active clients" in resp.message

    @pytest.mark.asyncio
    async def test_portfolio_no_tm(self, skill_no_tm: ClientInsightsSkill) -> None:
        await skill_no_tm.safe_initialize()
        req = SkillRequest(intent="client_portfolio_summary")
        resp = await skill_no_tm.safe_handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_health_check",
            context={"tenant_id": str(_TENANT_A["tenant_id"])},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "Bob's Plumbing" in resp.message
        assert "health" in resp.data

    @pytest.mark.asyncio
    async def test_health_check_not_found(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.get_tenant = AsyncMock(return_value=None)
        req = SkillRequest(
            intent="client_health_check",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "not found" in resp.error.lower()

    @pytest.mark.asyncio
    async def test_health_check_no_tenant_id(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_health_check")
        resp = await skill.safe_handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# Cross-tenant analysis
# ---------------------------------------------------------------------------


class TestCrossTenantAnalysis:
    @pytest.mark.asyncio
    async def test_cross_tenant(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="cross_tenant_analysis")
        resp = await skill.safe_handle(req)
        assert resp.success is True
        analysis = resp.data["analysis"]
        assert len(analysis["patterns"]) > 0
        assert len(analysis["recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_cross_tenant_no_broker(self, skill_no_tm: ClientInsightsSkill) -> None:
        await skill_no_tm.safe_initialize()
        req = SkillRequest(intent="cross_tenant_analysis")
        resp = await skill_no_tm.safe_handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_l3_triggers(self, skill: ClientInsightsSkill) -> None:
        # Simulate enough beats to trigger L3 (every 12th beat)
        skill._beat_count = 11  # Next beat will be 12
        actions = await skill.on_heartbeat(["user123"])
        # Should have run L3 aggregation; with escalation_rate ~33% > 15%
        assert any(a.skill_name == "client_insights" for a in actions)

    @pytest.mark.asyncio
    async def test_heartbeat_no_tm(self, skill_no_tm: ClientInsightsSkill) -> None:
        skill_no_tm._beat_count = 11
        actions = await skill_no_tm.on_heartbeat(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_no_l3_on_wrong_interval(self, skill: ClientInsightsSkill) -> None:
        skill._beat_count = 5  # Not a multiple of 12
        actions = await skill.on_heartbeat(["user123"])
        assert actions == []


# ---------------------------------------------------------------------------
# Unknown intent
# ---------------------------------------------------------------------------


class TestUnknownIntent:
    @pytest.mark.asyncio
    async def test_unknown(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="bogus")
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "Unknown" in resp.error


class TestAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_initialize_without_broker_logs_warning(self) -> None:
        skill = ClientInsightsSkill(inference_broker=None, tenant_manager=_make_tenant_manager())
        # Keep this explicit call to hit init warning paths.
        assert await skill.initialize() is True

    @pytest.mark.asyncio
    async def test_cross_tenant_requires_broker(self) -> None:
        skill = ClientInsightsSkill(inference_broker=None, tenant_manager=_make_tenant_manager())
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is False
        assert "No LLM configured" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_cross_tenant_broker_failure_returns_empty_analysis(self) -> None:
        broker = MagicMock()
        broker.infer = AsyncMock(side_effect=RuntimeError("boom"))
        skill = ClientInsightsSkill(inference_broker=broker, tenant_manager=_make_tenant_manager())
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is True
        assert resp.data["analysis"] == {
            "patterns": [],
            "recommendations": [],
            "risks": [],
            "opportunities": [],
        }

    @pytest.mark.asyncio
    async def test_run_l3_aggregation_no_tenant_manager(self) -> None:
        skill = ClientInsightsSkill(inference_broker=_make_broker(), tenant_manager=None)
        actions = await skill._run_l3_aggregation(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_run_l3_aggregation_no_alerts(self) -> None:
        tm = _make_tenant_manager()
        tm.get_interactions = AsyncMock(
            return_value=[
                {"sentiment": "positive", "intent": "enquiry", "outcome": "resolved"},
                {"sentiment": "neutral", "intent": "enquiry", "outcome": "resolved"},
            ]
        )
        skill = ClientInsightsSkill(inference_broker=_make_broker(), tenant_manager=tm)
        actions = await skill._run_l3_aggregation(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_on_heartbeat_triggers_l4_path(self, skill: ClientInsightsSkill) -> None:
        skill._beat_count = 287
        skill._run_l3_aggregation = AsyncMock(return_value=[])
        skill._run_l4_analysis = AsyncMock(return_value=[])
        actions = await skill.on_heartbeat(["user123"])
        assert actions == []
        skill._run_l4_analysis.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_l4_analysis_builds_message(self, skill: ClientInsightsSkill) -> None:
        analysis = {
            "patterns": ["Pattern A"],
            "risks": ["Risk B"],
            "opportunities": ["Opportunity C"],
            "recommendations": ["Recommendation D"],
        }
        skill.handle = AsyncMock(return_value=MagicMock(success=True, data={"analysis": analysis}))
        actions = await skill._run_l4_analysis(["user123"])
        assert len(actions) == 1
        message = actions[0].data["message"]
        assert "Weekly Cross-Tenant Intelligence" in message
        assert "Pattern A" in message
        assert "Recommendation D" in message

    @pytest.mark.asyncio
    async def test_run_l4_analysis_no_data_returns_empty(self, skill: ClientInsightsSkill) -> None:
        skill.handle = AsyncMock(return_value=MagicMock(success=True, data={"analysis": {}}))
        actions = await skill._run_l4_analysis(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_run_l4_analysis_requires_user_ids(self, skill: ClientInsightsSkill) -> None:
        skill.handle = AsyncMock(
            return_value=MagicMock(success=True, data={"analysis": {"patterns": ["X"]}})
        )
        actions = await skill._run_l4_analysis([])
        assert actions == []

    def test_aggregate_tenant_ignores_none_intents(self) -> None:
        tenant = {"tenant_id": uuid4(), "name": "NoIntent Co", "domain": "example.com"}
        interactions = [
            {"sentiment": "positive", "intent": None, "outcome": "resolved"},
            {"sentiment": "neutral", "intent": "faq", "outcome": "resolved"},
        ]
        summary = ClientInsightsSkill._aggregate_tenant(tenant, interactions)
        assert "faq" in summary["top_intents"]
        assert None not in summary["top_intents"]
