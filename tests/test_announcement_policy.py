"""Unit tests for announcement policy routing behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.announcements.policy import AnnouncementPolicyEngine
from zetherion_ai.announcements.storage import (
    AnnouncementEventInput,
    AnnouncementSeverity,
)


def _settings(values: dict[tuple[str, str], object]):
    def _resolve(namespace: str, key: str, default: object) -> object:
        return values.get((namespace, key), default)

    return _resolve


@pytest.fixture
def repository():
    repo = MagicMock()
    repo.get_user_preferences = AsyncMock(return_value=None)
    repo.get_personal_profile_preferences = AsyncMock(return_value={})
    repo.upsert_suppression_observation = AsyncMock(
        return_value=SimpleNamespace(
            id=11,
            next_allowed_at=None,
            state="active",
            occurrence_count=1,
        )
    )
    repo.count_recent_events = AsyncMock(return_value=0)
    repo.mark_suppression_notified = AsyncMock(return_value=None)
    return repo


@pytest.mark.asyncio
async def test_provider_billing_defaults_to_high_and_digest(repository):
    engine = AnnouncementPolicyEngine(
        repository,
        setting_resolver=_settings(
            {
                ("notifications", "announcement_timezone_default"): "UTC",
                ("scheduler", "announcement_digest_window_local"): "09:00",
            }
        ),
    )
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    decision = await engine.evaluate_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.billing",
            severity=AnnouncementSeverity.NORMAL,
            target_user_id=42,
            title="Billing issue",
            body="Credits exhausted.",
        ),
        now=now,
    )

    assert decision.severity is AnnouncementSeverity.HIGH
    assert decision.delivery_mode == "digest"
    assert decision.status == "scheduled"
    assert decision.reason_code == "digest_window"
    repository.mark_suppression_notified.assert_not_awaited()


@pytest.mark.asyncio
async def test_critical_category_routes_immediately(repository):
    engine = AnnouncementPolicyEngine(repository, setting_resolver=_settings({}))
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    decision = await engine.evaluate_event(
        AnnouncementEventInput(
            source="deploy",
            category="deploy.failed",
            severity=AnnouncementSeverity.NORMAL,
            target_user_id=42,
            title="Deploy failed",
            body="Runtime health gate failed.",
        ),
        now=now,
    )

    assert decision.severity is AnnouncementSeverity.CRITICAL
    assert decision.delivery_mode == "immediate"
    assert decision.reason_code == "critical_immediate"
    repository.mark_suppression_notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_personal_profile_immediate_category_still_respects_quiet_hours(repository):
    engine = AnnouncementPolicyEngine(repository, setting_resolver=_settings({}))
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    decision = await engine.evaluate_event(
        AnnouncementEventInput(
            source="updater",
            category="update.available",
            severity=AnnouncementSeverity.NORMAL,
            target_user_id=42,
            title="Update available",
            body="New version found.",
        ),
        personal_profile={
            "timezone": "UTC",
            "preferences": {
                "announcements": {"immediate_categories": ["update.available"]},
                "quiet_hours": {"start_hour": 0, "end_hour": 23},
            },
        },
        now=now,
    )

    assert decision.delivery_mode == "digest"
    assert decision.reason_code == "quiet_hours_digest_fallback"


@pytest.mark.asyncio
async def test_rate_limited_immediate_category_falls_back_to_digest(repository):
    repository.count_recent_events = AsyncMock(return_value=5)
    engine = AnnouncementPolicyEngine(repository, setting_resolver=_settings({}))
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    decision = await engine.evaluate_event(
        AnnouncementEventInput(
            source="skills",
            category="skill.reminder",
            severity=AnnouncementSeverity.NORMAL,
            target_user_id=42,
            title="Reminder",
            body="Task needs review.",
        ),
        personal_profile={
            "timezone": "UTC",
            "preferences": {
                "announcements": {
                    "immediate_categories": ["skill.reminder"],
                    "max_immediate_per_hour": 2,
                }
            },
        },
        now=now,
    )

    assert decision.delivery_mode == "digest"
    assert decision.reason_code == "rate_limited_to_digest"


@pytest.mark.asyncio
async def test_active_suppression_cooldown_defers_event(repository):
    repository.upsert_suppression_observation = AsyncMock(
        return_value=SimpleNamespace(
            id=15,
            next_allowed_at=datetime(2026, 3, 6, 11, 0, tzinfo=UTC),
            state="active",
            occurrence_count=2,
        )
    )
    engine = AnnouncementPolicyEngine(repository, setting_resolver=_settings({}))
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    decision = await engine.evaluate_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.auth",
            severity=AnnouncementSeverity.HIGH,
            target_user_id=42,
            title="Auth issue",
            body="Token invalid.",
        ),
        now=now,
    )

    assert decision.delivery_mode == "deferred"
    assert decision.reason_code == "suppression_cooldown_active"
    assert decision.scheduled_for == datetime(2026, 3, 6, 11, 0, tzinfo=UTC)
    repository.mark_suppression_notified.assert_not_awaited()
