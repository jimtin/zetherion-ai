"""Tests for client_insights skill -- portfolio intelligence for James."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.client_insights import ClientInsightsSkill

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


def _snapshot(
    tenant: dict[str, object],
    *,
    health_indicator: str = "green",
    avg_sentiment: float = 0.2,
    escalation_rate: float = 0.05,
) -> dict[str, object]:
    tenant_id = str(tenant["tenant_id"])
    suffix = tenant_id[-8:]
    return {
        "snapshot_id": f"ops_{suffix}",
        "zetherion_tenant_id": tenant_id,
        "tenant_name": str(tenant["name"]),
        "derivation_kind": "tenant_health_summary",
        "trust_domain": "owner_portfolio",
        "source_dataset_id": f"tds_{suffix}",
        "source": "seed",
        "summary": {
            "tenant_id": tenant_id,
            "tenant_name": str(tenant["name"]),
            "health_indicator": health_indicator,
            "total_interactions": 12,
            "avg_sentiment": avg_sentiment,
            "escalation_rate": escalation_rate,
            "resolution_rate": 0.8,
            "behavior_sessions": 4,
            "behavior_conversion_rate": 0.5,
            "top_intents": {"quote": 4},
            "top_funnel_stages": {"pricing": 3},
        },
        "provenance": {
            "input_trust_domain": "tenant_derived",
            "output_trust_domain": "owner_portfolio",
            "source_dataset_id": f"tds_{suffix}",
        },
    }


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


def _make_portfolio_storage() -> MagicMock:
    storage = MagicMock()
    storage.initialize = AsyncMock()
    storage.close = AsyncMock()
    storage.list_owner_portfolio_snapshots = AsyncMock(
        return_value=[
            _snapshot(_TENANT_A, health_indicator="green", avg_sentiment=0.2, escalation_rate=0.05),
            _snapshot(_TENANT_B, health_indicator="amber", avg_sentiment=-0.3, escalation_rate=0.1),
        ]
    )
    storage.get_owner_portfolio_snapshot = AsyncMock(return_value=_snapshot(_TENANT_A))
    storage.upsert_tenant_derived_dataset = AsyncMock()
    storage.upsert_owner_portfolio_snapshot = AsyncMock()
    return storage


def _make_pipeline() -> MagicMock:
    pipeline = MagicMock()
    pipeline.refresh_all_tenant_health_snapshots = AsyncMock(
        return_value=[_snapshot(_TENANT_A), _snapshot(_TENANT_B)]
    )
    pipeline.refresh_tenant_health_snapshot = AsyncMock(return_value=_snapshot(_TENANT_A))
    return pipeline


@pytest.fixture
def portfolio_storage() -> MagicMock:
    return _make_portfolio_storage()


@pytest.fixture
def tenant_manager() -> AsyncMock:
    return _make_tenant_manager()


@pytest.fixture
def pipeline() -> MagicMock:
    return _make_pipeline()


@pytest.fixture
def skill(
    portfolio_storage: MagicMock,
    tenant_manager: AsyncMock,
    pipeline: MagicMock,
) -> ClientInsightsSkill:
    return ClientInsightsSkill(
        inference_broker=_make_broker(),
        tenant_manager=tenant_manager,
        portfolio_storage=portfolio_storage,
        portfolio_pipeline=pipeline,
    )


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
        skill._portfolio_storage.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_safe_init_ready(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY
        skill._portfolio_storage.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_closes_storage(self, skill: ClientInsightsSkill) -> None:
        await skill.cleanup()
        skill._portfolio_storage.close.assert_awaited_once()


class TestAggregation:
    def test_aggregate_tenant(self) -> None:
        summary = ClientInsightsSkill._aggregate_tenant(_TENANT_A, _INTERACTIONS)
        assert summary["name"] == "Bob's Plumbing"
        assert summary["total_interactions"] == 3
        assert summary["avg_sentiment"] == 0.0
        assert abs(summary["escalation_rate"] - 0.333) < 0.01
        assert abs(summary["resolution_rate"] - 0.667) < 0.01
        assert "enquiry" in summary["top_intents"]

    def test_aggregate_tenant_ignores_none_intents(self) -> None:
        tenant = {"tenant_id": uuid4(), "name": "NoIntent Co", "domain": "example.com"}
        interactions = [
            {"sentiment": "positive", "intent": None, "outcome": "resolved"},
            {"sentiment": "neutral", "intent": "faq", "outcome": "resolved"},
        ]
        summary = ClientInsightsSkill._aggregate_tenant(tenant, interactions)
        assert "faq" in summary["top_intents"]
        assert None not in summary["top_intents"]

    def test_health_indicator_green(self) -> None:
        summary = {"escalation_rate": 0.05, "avg_sentiment": 0.3}
        assert ClientInsightsSkill._health_indicator(summary) == "[GREEN]"

    def test_health_indicator_amber(self) -> None:
        summary = {"escalation_rate": 0.05, "avg_sentiment": -0.3}
        assert ClientInsightsSkill._health_indicator(summary) == "[AMBER]"

    def test_health_indicator_red(self) -> None:
        summary = {"escalation_rate": 0.20, "avg_sentiment": 0.5}
        assert ClientInsightsSkill._health_indicator(summary) == "[RED]"


class TestPortfolioSummary:
    @pytest.mark.asyncio
    async def test_portfolio_summary_reads_owner_snapshots_only(
        self,
        skill: ClientInsightsSkill,
        tenant_manager: AsyncMock,
    ) -> None:
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="client_portfolio_summary"))
        assert resp.success is True
        assert resp.data["count"] == 2
        assert "Bob's Plumbing" in resp.message
        tenant_manager.list_tenants.assert_not_awaited()
        tenant_manager.get_interactions.assert_not_awaited()
        skill._portfolio_storage.list_owner_portfolio_snapshots.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_portfolio_no_snapshots(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        skill._portfolio_storage.list_owner_portfolio_snapshots = AsyncMock(return_value=[])
        resp = await skill.safe_handle(SkillRequest(intent="client_portfolio_summary"))
        assert resp.success is True
        assert "No owner portfolio snapshots" in resp.message
        assert resp.data["count"] == 0

    @pytest.mark.asyncio
    async def test_portfolio_refresh_uses_pipeline(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        await skill.safe_handle(
            SkillRequest(
                intent="client_portfolio_summary",
                context={"refresh_portfolio": True, "source": "manual_refresh"},
            )
        )
        skill._portfolio_pipeline.refresh_all_tenant_health_snapshots.assert_awaited_once_with(
            source="manual_refresh"
        )

    @pytest.mark.asyncio
    async def test_portfolio_no_storage_errors(self, tenant_manager: AsyncMock) -> None:
        skill = ClientInsightsSkill(
            inference_broker=_make_broker(),
            tenant_manager=tenant_manager,
            portfolio_storage=None,
        )
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="client_portfolio_summary"))
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_portfolio_can_run_without_tenant_manager(
        self,
        portfolio_storage: MagicMock,
    ) -> None:
        skill = ClientInsightsSkill(
            inference_broker=_make_broker(),
            tenant_manager=None,
            portfolio_storage=portfolio_storage,
        )
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="client_portfolio_summary"))
        assert resp.success is True
        assert resp.data["count"] == 2


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_reads_stored_snapshot_only(
        self,
        skill: ClientInsightsSkill,
        tenant_manager: AsyncMock,
    ) -> None:
        await skill.safe_initialize()
        resp = await skill.safe_handle(
            SkillRequest(
                intent="client_health_check", context={"tenant_id": str(_TENANT_A["tenant_id"])}
            )
        )
        assert resp.success is True
        assert "Bob's Plumbing" in resp.message
        assert resp.data["provenance"]["input_trust_domain"] == "tenant_derived"
        assert resp.data["provenance"]["output_trust_domain"] == "owner_portfolio"
        assert resp.data["source_dataset_id"].startswith("tds_")
        tenant_manager.get_interactions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_health_check_refresh_uses_pipeline(
        self,
        skill: ClientInsightsSkill,
        tenant_manager: AsyncMock,
    ) -> None:
        await skill.safe_initialize()
        resp = await skill.safe_handle(
            SkillRequest(
                intent="client_health_check",
                context={
                    "tenant_id": str(_TENANT_A["tenant_id"]),
                    "refresh_portfolio": True,
                    "source": "cgs-migration",
                },
            )
        )
        assert resp.success is True
        tenant_manager.get_tenant.assert_awaited_once_with(str(_TENANT_A["tenant_id"]))
        skill._portfolio_pipeline.refresh_tenant_health_snapshot.assert_awaited_once()
        kwargs = skill._portfolio_pipeline.refresh_tenant_health_snapshot.await_args.kwargs
        assert kwargs["source"] == "cgs-migration"

    @pytest.mark.asyncio
    async def test_health_check_not_found_when_snapshot_missing(
        self,
        skill: ClientInsightsSkill,
    ) -> None:
        await skill.safe_initialize()
        skill._portfolio_storage.get_owner_portfolio_snapshot = AsyncMock(return_value=None)
        resp = await skill.safe_handle(
            SkillRequest(
                intent="client_health_check", context={"tenant_id": str(_TENANT_A["tenant_id"])}
            )
        )
        assert resp.success is False
        assert "snapshot" in (resp.error or "").lower()

    @pytest.mark.asyncio
    async def test_health_check_refresh_requires_pipeline(
        self, portfolio_storage: MagicMock
    ) -> None:
        skill = ClientInsightsSkill(
            inference_broker=_make_broker(),
            tenant_manager=None,
            portfolio_storage=portfolio_storage,
            portfolio_pipeline=None,
        )
        await skill.safe_initialize()
        resp = await skill.safe_handle(
            SkillRequest(
                intent="client_health_check",
                context={"tenant_id": str(_TENANT_A["tenant_id"]), "refresh_portfolio": True},
            )
        )
        assert resp.success is False
        assert "refresh" in (resp.error or "").lower()


class TestCrossTenantAnalysis:
    @pytest.mark.asyncio
    async def test_cross_tenant_uses_stored_owner_summaries_only(
        self,
        skill: ClientInsightsSkill,
        tenant_manager: AsyncMock,
    ) -> None:
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is True
        analysis = resp.data["analysis"]
        assert len(analysis["patterns"]) > 0
        prompt = skill._broker.infer.await_args.kwargs["prompt"]
        assert "bobsplumbing.com" not in prompt
        assert str(_INTERACTIONS[0]["interaction_id"]) not in prompt
        assert '"tenant_name": "Bob\'s Plumbing"' in prompt
        tenant_manager.get_interactions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cross_tenant_refresh_uses_pipeline(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        await skill.safe_handle(
            SkillRequest(
                intent="cross_tenant_analysis",
                context={"refresh_portfolio": True, "source": "manual_cross_tenant_refresh"},
            )
        )
        skill._portfolio_pipeline.refresh_all_tenant_health_snapshots.assert_awaited_once_with(
            source="manual_cross_tenant_refresh"
        )

    @pytest.mark.asyncio
    async def test_cross_tenant_no_broker(self, portfolio_storage: MagicMock) -> None:
        skill = ClientInsightsSkill(
            inference_broker=None,
            tenant_manager=_make_tenant_manager(),
            portfolio_storage=portfolio_storage,
        )
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is False
        assert "No LLM configured" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_cross_tenant_no_snapshots_returns_empty(
        self, skill: ClientInsightsSkill
    ) -> None:
        await skill.safe_initialize()
        skill._portfolio_storage.list_owner_portfolio_snapshots = AsyncMock(return_value=[])
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is True
        assert resp.data["analysis"] == {
            "patterns": [],
            "recommendations": [],
            "risks": [],
            "opportunities": [],
        }

    @pytest.mark.asyncio
    async def test_cross_tenant_broker_failure_returns_empty_analysis(
        self,
        portfolio_storage: MagicMock,
    ) -> None:
        broker = MagicMock()
        broker.infer = AsyncMock(side_effect=RuntimeError("boom"))
        skill = ClientInsightsSkill(
            inference_broker=broker,
            tenant_manager=_make_tenant_manager(),
            portfolio_storage=portfolio_storage,
        )
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="cross_tenant_analysis"))
        assert resp.success is True
        assert resp.data["analysis"] == {
            "patterns": [],
            "recommendations": [],
            "risks": [],
            "opportunities": [],
        }


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_l3_triggers_refresh_and_alert(
        self, skill: ClientInsightsSkill
    ) -> None:
        skill._beat_count = 11
        actions = await skill.on_heartbeat(["user123"])
        assert len(actions) == 1
        assert actions[0].skill_name == "client_insights"
        skill._portfolio_pipeline.refresh_all_tenant_health_snapshots.assert_awaited_once_with(
            source="heartbeat_owner_portfolio_refresh"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_no_l3_on_wrong_interval(self, skill: ClientInsightsSkill) -> None:
        skill._beat_count = 5
        actions = await skill.on_heartbeat(["user123"])
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
    async def test_run_l3_aggregation_no_alerts(self, skill: ClientInsightsSkill) -> None:
        summaries = [
            {
                "tenant_name": "Healthy Co",
                "avg_sentiment": 0.4,
                "escalation_rate": 0.01,
            }
        ]
        actions = await skill._run_l3_aggregation(["user123"], summaries=summaries)
        assert actions == []

    @pytest.mark.asyncio
    async def test_run_l4_analysis_builds_message(self, skill: ClientInsightsSkill) -> None:
        summaries = [
            snapshot["summary"] for snapshot in [_snapshot(_TENANT_A), _snapshot(_TENANT_B)]
        ]
        actions = await skill._run_l4_analysis(["user123"], summaries=summaries)
        assert len(actions) == 1
        message = actions[0].data["message"]
        assert "Weekly Cross-Tenant Intelligence" in message
        assert "Add FAQ to Bob's chatbot" in message

    @pytest.mark.asyncio
    async def test_run_l4_analysis_no_data_returns_empty(self, skill: ClientInsightsSkill) -> None:
        skill._analyse_cross_tenant_summaries = AsyncMock(return_value={})
        actions = await skill._run_l4_analysis(
            ["user123"], summaries=[_snapshot(_TENANT_A)["summary"]]
        )
        assert actions == []

    @pytest.mark.asyncio
    async def test_run_l4_analysis_requires_user_ids(self, skill: ClientInsightsSkill) -> None:
        actions = await skill._run_l4_analysis([], summaries=[_snapshot(_TENANT_A)["summary"]])
        assert actions == []


class TestUnknownIntent:
    @pytest.mark.asyncio
    async def test_unknown(self, skill: ClientInsightsSkill) -> None:
        await skill.safe_initialize()
        resp = await skill.safe_handle(SkillRequest(intent="bogus"))
        assert resp.success is False
        assert "Unknown" in resp.error
