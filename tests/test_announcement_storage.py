"""Unit tests for announcement domain storage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.announcements.storage import (
    AnnouncementEventInput,
    AnnouncementPreferencePatch,
    AnnouncementRepository,
    AnnouncementSeverity,
)


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool/connection pair."""
    pool = MagicMock()
    conn = AsyncMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire_ctx

    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    return pool, conn


@pytest.fixture
def repository():
    """Repository under test."""
    return AnnouncementRepository()


@pytest.fixture
def now():
    """Deterministic timestamp."""
    return datetime(2026, 3, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_initialize_creates_schema(repository, mock_pool):
    pool, conn = mock_pool

    await repository.initialize(pool)

    assert repository._pool is pool
    conn.execute.assert_awaited_once()
    schema_sql = conn.execute.call_args[0][0]
    assert "CREATE TABLE IF NOT EXISTS announcement_events" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS announcement_deliveries" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS announcement_user_preferences" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS announcement_digest_state" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS announcement_suppression_state" in schema_sql


@pytest.mark.asyncio
async def test_create_event_accepts_new_idempotent_event(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {"event_id": "evt-new", "occurred_at": now}

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.billing",
            severity=AnnouncementSeverity.HIGH,
            target_user_id=42,
            title="Billing issue",
            body="Provider credits depleted.",
            payload={"provider": "claude"},
            idempotency_key="provider-billing-claude",
            occurred_at=now,
        )
    )

    assert receipt.status == "accepted"
    assert receipt.event_id == "evt-new"
    assert receipt.reason_code == "accepted_new"


@pytest.mark.asyncio
async def test_create_event_dedupes_by_idempotency_key(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        None,
        {"event_id": "evt-existing"},
    ]

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.billing",
            severity="high",
            target_user_id=42,
            title="Billing issue",
            body="Provider credits depleted.",
            idempotency_key="provider-billing-claude",
            occurred_at=now,
        )
    )

    assert receipt.status == "deduped"
    assert receipt.event_id == "evt-existing"
    assert receipt.reason_code == "idempotency_key_conflict"
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_event_dedupes_by_fingerprint_bucket(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {"event_id": "evt-bucket"}

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.auth",
            severity="high",
            target_user_id=42,
            title="Auth issue",
            body="Provider authentication failed.",
            fingerprint="provider:claude:auth",
            occurred_at=now,
        ),
        dedupe_window_minutes=5,
    )

    assert receipt.status == "deduped"
    assert receipt.event_id == "evt-bucket"
    assert receipt.reason_code == "fingerprint_bucket_conflict"
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_user_preferences_returns_defaults_when_absent(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    preferences = await repository.get_user_preferences(99)

    assert preferences.user_id == 99
    assert preferences.timezone == "UTC"
    assert preferences.digest_enabled is True
    assert preferences.digest_window_local == "09:00"
    assert preferences.immediate_categories == []
    assert preferences.muted_categories == []
    assert preferences.max_immediate_per_hour == 6


@pytest.mark.asyncio
async def test_upsert_user_preferences_merges_patch(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        {
            "user_id": 7,
            "timezone": "UTC",
            "digest_enabled": True,
            "digest_window_local": "09:00",
            "immediate_categories_json": ["security.critical"],
            "muted_categories_json": [],
            "max_immediate_per_hour": 6,
            "updated_at": datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
        },
        {
            "user_id": 7,
            "timezone": "Australia/Sydney",
            "digest_enabled": True,
            "digest_window_local": "09:00",
            "immediate_categories_json": ["security.critical"],
            "muted_categories_json": ["insight.summary"],
            "max_immediate_per_hour": 4,
            "updated_at": datetime(2026, 3, 5, 11, 0, tzinfo=UTC),
        },
    ]

    updated = await repository.upsert_user_preferences(
        7,
        AnnouncementPreferencePatch(
            timezone="Australia/Sydney",
            muted_categories=["insight.summary"],
            max_immediate_per_hour=4,
        ),
    )

    assert updated.user_id == 7
    assert updated.timezone == "Australia/Sydney"
    assert updated.digest_enabled is True
    assert updated.immediate_categories == ["security.critical"]
    assert updated.muted_categories == ["insight.summary"]
    assert updated.max_immediate_per_hour == 4
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_suppression_observation_returns_state(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {
        "id": 101,
        "source": "provider_monitor",
        "category": "provider.billing",
        "target_user_id": 42,
        "fingerprint": "provider:claude:billing",
        "state": "active",
        "occurrence_count": 3,
        "first_seen": datetime(2026, 3, 5, 9, 0, tzinfo=UTC),
        "last_seen": now,
        "last_notified_at": None,
        "next_allowed_at": None,
        "resolved_at": None,
        "updated_at": now,
    }

    state = await repository.upsert_suppression_observation(
        source="provider_monitor",
        category="provider.billing",
        target_user_id=42,
        fingerprint="provider:claude:billing",
        seen_at=now,
    )

    assert state.id == 101
    assert state.occurrence_count == 3
    assert state.state == "active"


@pytest.mark.asyncio
async def test_retention_purges_return_deleted_counts(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.execute.side_effect = ["DELETE 5", "DELETE 3", "DELETE 2"]

    deleted_events = await repository.purge_expired_events(14)
    deleted_deliveries = await repository.purge_expired_deliveries(14)
    deleted_suppression = await repository.purge_resolved_suppressions(14)

    assert deleted_events == 5
    assert deleted_deliveries == 3
    assert deleted_suppression == 2
