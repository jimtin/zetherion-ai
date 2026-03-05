"""Focused tests for SkillsServer announcement API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.announcements.policy import AnnouncementPolicyDecision
from zetherion_ai.announcements.storage import (
    AnnouncementDelivery,
    AnnouncementReceipt,
    AnnouncementSeverity,
    AnnouncementUserPreferences,
)
from zetherion_ai.skills.base import SkillResponse
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer


@pytest.fixture
def mock_registry() -> SkillRegistry:
    registry = MagicMock(spec=SkillRegistry)
    registry.list_ready_skills.return_value = []
    registry.skill_count = 0
    registry.handle_request = AsyncMock(
        return_value=SkillResponse(request_id="req", success=True, message="ok")
    )
    registry.run_heartbeat = AsyncMock(return_value=[])
    registry.list_skills.return_value = []
    registry.get_skill.return_value = None
    registry.get_status_summary.return_value = {"status": "ok"}
    registry.get_system_prompt_fragments.return_value = []
    registry.list_intents.return_value = {}
    return registry


def _headers() -> dict[str, str]:
    return {"X-API-Secret": "skills-secret"}


@pytest.mark.asyncio
async def test_announcement_emit_event_schedules_delivery(mock_registry: SkillRegistry) -> None:
    repository = MagicMock()
    policy = MagicMock()
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    policy.evaluate_event = AsyncMock(
        return_value=AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="digest",
            severity=AnnouncementSeverity.HIGH,
            scheduled_for=now + timedelta(hours=1),
            reason_code="digest_window",
            suppression_id=11,
        )
    )
    repository.create_event = AsyncMock(
        return_value=AnnouncementReceipt(
            status="accepted",
            event_id="evt-1",
            reason_code="accepted_new",
        )
    )
    repository.create_delivery = AsyncMock(return_value=None)

    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/announcements/events",
            headers=_headers(),
            json={
                "source": "provider_monitor",
                "category": "provider.billing",
                "target_user_id": 42,
                "title": "Billing issue",
                "body": "Credits exhausted",
            },
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["ok"] is True
        assert payload["receipt"]["status"] == "scheduled"
        assert payload["receipt"]["event_id"] == "evt-1"
        assert payload["decision"]["delivery_mode"] == "digest"

    repository.create_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_announcement_emit_event_deduped_does_not_schedule_delivery(
    mock_registry: SkillRegistry,
) -> None:
    repository = MagicMock()
    policy = MagicMock()
    policy.evaluate_event = AsyncMock(
        return_value=AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="immediate",
            severity=AnnouncementSeverity.CRITICAL,
            scheduled_for=datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
            reason_code="critical_immediate",
            suppression_id=11,
        )
    )
    repository.create_event = AsyncMock(
        return_value=AnnouncementReceipt(
            status="deduped",
            event_id="evt-existing",
            reason_code="idempotency_key_conflict",
        )
    )
    repository.create_delivery = AsyncMock(return_value=None)

    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/announcements/events",
            headers=_headers(),
            json={
                "source": "deploy",
                "category": "deploy.failed",
                "target_user_id": 42,
                "title": "Deploy failed",
                "body": "Health checks failed",
            },
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["receipt"]["status"] == "deduped"
        assert payload["receipt"]["event_id"] == "evt-existing"

    repository.create_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_announcement_emit_event_accepts_string_target_user_id(
    mock_registry: SkillRegistry,
) -> None:
    repository = MagicMock()
    policy = MagicMock()
    policy.evaluate_event = AsyncMock(
        return_value=AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="immediate",
            severity=AnnouncementSeverity.CRITICAL,
            scheduled_for=datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
            reason_code="critical_immediate",
            suppression_id=99,
        )
    )
    repository.create_event = AsyncMock(
        return_value=AnnouncementReceipt(
            status="accepted",
            event_id="evt-parse-str",
            reason_code="accepted_new",
        )
    )
    repository.create_delivery = AsyncMock(return_value=None)

    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/announcements/events",
            headers=_headers(),
            json={
                "source": "provider_monitor",
                "category": "provider.billing",
                "target_user_id": "42",
                "title": "Billing issue",
                "body": "Credits exhausted",
            },
        )
        assert response.status == 200

    create_event_call = repository.create_event.await_args
    assert create_event_call is not None
    assert create_event_call.args[0].target_user_id == 42


@pytest.mark.asyncio
async def test_announcement_emit_event_rejects_boolean_target_user_id(
    mock_registry: SkillRegistry,
) -> None:
    repository = MagicMock()
    policy = MagicMock()
    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/announcements/events",
            headers=_headers(),
            json={
                "source": "provider_monitor",
                "category": "provider.billing",
                "target_user_id": True,
                "title": "Billing issue",
                "body": "Credits exhausted",
            },
        )
        assert response.status == 400
        payload = await response.json()
        assert payload["error"] == "Invalid target_user_id"

    repository.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_announcement_batch_and_flush_paths(mock_registry: SkillRegistry) -> None:
    repository = MagicMock()
    policy = MagicMock()
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    policy.evaluate_event = AsyncMock(
        return_value=AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="digest",
            severity=AnnouncementSeverity.NORMAL,
            scheduled_for=now + timedelta(minutes=30),
            reason_code="digest_window",
            suppression_id=10,
        )
    )
    repository.create_event = AsyncMock(
        return_value=AnnouncementReceipt(
            status="accepted",
            event_id="evt-1",
            reason_code="accepted_new",
        )
    )
    repository.create_delivery = AsyncMock(return_value=None)
    repository.list_due_deliveries = AsyncMock(
        return_value=[
            AnnouncementDelivery(
                delivery_id=1,
                event_id="evt-1",
                channel="discord_dm",
                scheduled_for=now,
                sent_at=None,
                status="scheduled",
                error_code=None,
                error_detail=None,
                retry_count=0,
                created_at=now,
                updated_at=now,
            )
        ]
    )

    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        batch = await client.post(
            "/announcements/events/batch",
            headers=_headers(),
            json={
                "events": [
                    {
                        "source": "skills",
                        "category": "skill.reminder",
                        "target_user_id": 42,
                        "title": "Reminder",
                        "body": "Review your queue",
                    },
                    "bad-payload",
                ]
            },
        )
        assert batch.status == 200
        batch_payload = await batch.json()
        assert batch_payload["count"] == 1
        assert len(batch_payload["errors"]) == 1

        flush = await client.post(
            "/announcements/dispatch/flush",
            headers=_headers(),
            json={"limit": 10},
        )
        assert flush.status == 200
        flush_payload = await flush.json()
        assert flush_payload["count"] == 1
        assert flush_payload["deliveries"][0]["event_id"] == "evt-1"


@pytest.mark.asyncio
async def test_announcement_preferences_get_and_put(mock_registry: SkillRegistry) -> None:
    repository = MagicMock()
    policy = MagicMock()
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    repository.get_user_preferences = AsyncMock(
        side_effect=[
            AnnouncementUserPreferences(
                user_id=42,
                timezone="UTC",
                digest_enabled=True,
                digest_window_local="09:00",
                immediate_categories=[],
                muted_categories=[],
                max_immediate_per_hour=6,
                updated_at=now,
            ),
            AnnouncementUserPreferences(
                user_id=42,
                timezone="Australia/Sydney",
                digest_enabled=True,
                digest_window_local="08:30",
                immediate_categories=["security.critical"],
                muted_categories=["insight.summary"],
                max_immediate_per_hour=4,
                updated_at=now,
            ),
        ]
    )
    repository.upsert_user_preferences = AsyncMock(
        return_value=AnnouncementUserPreferences(
            user_id=42,
            timezone="Australia/Sydney",
            digest_enabled=True,
            digest_window_local="08:30",
            immediate_categories=["security.critical"],
            muted_categories=["insight.summary"],
            max_immediate_per_hour=4,
            updated_at=now,
        )
    )

    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        listed = await client.get(
            "/announcements/users/42/preferences",
            headers=_headers(),
        )
        assert listed.status == 200
        listed_payload = await listed.json()
        assert listed_payload["preferences"]["timezone"] == "UTC"

        updated = await client.put(
            "/announcements/users/42/preferences",
            headers=_headers(),
            json={
                "timezone": "Australia/Sydney",
                "digest_window_local": "08:30",
                "immediate_categories": ["security.critical"],
                "muted_categories": ["insight.summary"],
                "max_immediate_per_hour": 4,
            },
        )
        assert updated.status == 200
        updated_payload = await updated.json()
        assert updated_payload["preferences"]["timezone"] == "Australia/Sydney"
        assert updated_payload["preferences"]["max_immediate_per_hour"] == 4


@pytest.mark.asyncio
async def test_announcement_endpoints_require_auth(mock_registry: SkillRegistry) -> None:
    repository = MagicMock()
    policy = MagicMock()
    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        announcement_repository=repository,
        announcement_policy_engine=policy,
    )
    app = server.create_app()

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/announcements/events",
            json={
                "source": "provider_monitor",
                "category": "provider.billing",
                "target_user_id": 42,
                "title": "Billing issue",
                "body": "Credits exhausted",
            },
        )
        assert response.status == 401
