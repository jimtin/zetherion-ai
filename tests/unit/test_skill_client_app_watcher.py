"""Tests for client_app_watcher skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.client_app_watcher import ClientAppWatcherSkill


def _req(intent: str, *, user_id: str = "user-1", context: dict | None = None) -> SkillRequest:
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message="test",
        context=context or {},
    )


@pytest.fixture
def tenant_manager() -> AsyncMock:
    tm = AsyncMock()
    tm.list_tenants = AsyncMock(return_value=[])
    tm.list_recommendations = AsyncMock(return_value=[])
    tm.get_funnel_daily = AsyncMock(return_value=[])
    tm.add_recommendation_feedback = AsyncMock(return_value={"feedback_id": "fb-1"})
    return tm


@pytest.mark.asyncio
async def test_initialize_and_metadata(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    assert await skill.safe_initialize() is True
    assert skill.status == SkillStatus.READY
    assert skill.metadata.name == "client_app_watcher"
    assert "app_watch_run_analysis" in skill.metadata.intents


@pytest.mark.asyncio
async def test_handle_returns_error_when_not_configured() -> None:
    skill = ClientAppWatcherSkill(None)
    await skill.safe_initialize()
    response = await skill.safe_handle(_req("app_watch_run_analysis"))
    assert response.success is False
    assert "not configured" in (response.error or "").lower()


@pytest.mark.asyncio
async def test_unknown_intent(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    response = await skill.safe_handle(_req("not_a_real_intent"))
    assert response.success is False
    assert "unknown client_app_watcher intent" in (response.error or "").lower()


@pytest.mark.asyncio
async def test_on_heartbeat_no_tenant_manager() -> None:
    skill = ClientAppWatcherSkill(None)
    await skill.safe_initialize()
    actions = await skill.on_heartbeat(["owner"])
    assert actions == []


@pytest.mark.asyncio
async def test_on_heartbeat_no_action_until_interval(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    actions = await skill.on_heartbeat(["owner"])
    assert actions == []


@pytest.mark.asyncio
async def test_on_heartbeat_emits_high_risk_action(tenant_manager: AsyncMock) -> None:
    tenant_manager.list_tenants = AsyncMock(
        return_value=[{"tenant_id": "t-1", "name": "Tenant One"}]
    )
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    skill._beat_count = 287  # next heartbeat triggers daily run

    aggregator = MagicMock()
    aggregator.compute_daily_funnel = AsyncMock(return_value=[{"stage_name": "conversion"}])
    aggregator.detect_release_regression = AsyncMock(return_value={"regression": True})
    engine = MagicMock()
    engine.generate_candidates = MagicMock(return_value=[{"type": "release"}])
    engine.persist_candidates = AsyncMock(
        return_value=[{"recommendation_id": "r1", "risk_class": "high"}]
    )

    with (
        patch(
            "zetherion_ai.skills.client_app_watcher.AnalyticsAggregator",
            return_value=aggregator,
        ),
        patch("zetherion_ai.skills.client_app_watcher.RecommendationEngine", return_value=engine),
    ):
        actions = await skill.on_heartbeat(["owner"])

    assert len(actions) == 1
    assert actions[0].action_type == "send_message"
    assert "high-risk recommendation" in actions[0].data["message"]


@pytest.mark.asyncio
async def test_run_analysis_success(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()

    aggregator = MagicMock()
    aggregator.summarize_session = AsyncMock(return_value={"funnel_stage": "engaged"})
    aggregator.compute_daily_funnel = AsyncMock(return_value=[{"stage_name": "page_view"}])
    aggregator.detect_release_regression = AsyncMock(return_value={"regression": False})
    engine = MagicMock()
    engine.generate_candidates = MagicMock(return_value=[{"type": "foo"}])
    engine.persist_candidates = AsyncMock(return_value=[{"recommendation_id": "rec-1"}])

    with (
        patch(
            "zetherion_ai.skills.client_app_watcher.AnalyticsAggregator",
            return_value=aggregator,
        ),
        patch("zetherion_ai.skills.client_app_watcher.RecommendationEngine", return_value=engine),
    ):
        response = await skill.safe_handle(
            _req(
                "app_watch_run_analysis",
                context={"tenant_id": "tenant-1", "web_session_id": "ws-1"},
            )
        )

    assert response.success is True
    assert response.data["summary"]["funnel_stage"] == "engaged"
    assert response.data["recommendations"][0]["recommendation_id"] == "rec-1"


@pytest.mark.asyncio
async def test_run_analysis_requires_tenant_id(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    response = await skill.safe_handle(_req("app_watch_run_analysis"))
    assert response.success is False
    assert "tenant_id is required" in (response.error or "")


@pytest.mark.asyncio
async def test_get_recommendations_and_funnel(tenant_manager: AsyncMock) -> None:
    tenant_manager.list_recommendations = AsyncMock(return_value=[{"recommendation_id": "r-1"}])
    tenant_manager.get_funnel_daily = AsyncMock(return_value=[{"stage_name": "page_view"}])
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()

    rec_response = await skill.safe_handle(
        _req("app_watch_get_recommendations", context={"tenant_id": "tenant-1", "limit": 5})
    )
    assert rec_response.success is True
    assert rec_response.data["recommendations"][0]["recommendation_id"] == "r-1"

    funnel_response = await skill.safe_handle(
        _req(
            "app_watch_get_funnel",
            context={"tenant_id": "tenant-1", "metric_date": "2026-02-25", "limit": 5},
        )
    )
    assert funnel_response.success is True
    assert funnel_response.data["funnel"][0]["stage_name"] == "page_view"


@pytest.mark.asyncio
async def test_get_funnel_invalid_metric_date_bubbles_as_skill_error(
    tenant_manager: AsyncMock,
) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    response = await skill.safe_handle(
        _req(
            "app_watch_get_funnel",
            context={"tenant_id": "tenant-1", "metric_date": "invalid-date"},
        )
    )
    assert response.success is False
    assert "Skill error:" in (response.error or "")


@pytest.mark.asyncio
async def test_ack_recommendation_requires_fields(tenant_manager: AsyncMock) -> None:
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()
    response = await skill.safe_handle(_req("app_watch_ack_recommendation", context={}))
    assert response.success is False
    assert "required" in (response.error or "").lower()


@pytest.mark.asyncio
async def test_ack_recommendation_success(tenant_manager: AsyncMock) -> None:
    tenant_manager.add_recommendation_feedback = AsyncMock(return_value={"feedback_id": "fb-1"})
    skill = ClientAppWatcherSkill(tenant_manager)
    await skill.safe_initialize()

    response = await skill.safe_handle(
        _req(
            "app_watch_ack_recommendation",
            context={
                "tenant_id": "tenant-1",
                "recommendation_id": "rec-1",
                "feedback_type": "accepted",
                "note": "Looks good",
            },
        )
    )
    assert response.success is True
    assert response.data["feedback"]["feedback_id"] == "fb-1"
    tenant_manager.add_recommendation_feedback.assert_awaited_once()
