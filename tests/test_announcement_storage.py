"""Unit tests for announcement domain storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.announcements.storage import (
    _SCHEMA,
    AnnouncementEventInput,
    AnnouncementPreferencePatch,
    AnnouncementRecipient,
    AnnouncementRepository,
    AnnouncementSeverity,
    AnnouncementUserPreferences,
    _coerce_datetime,
    _dedupe_bucket,
    _parse_json_list,
    _row_count,
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


def test_schema_migrates_legacy_announcement_tables_before_recipient_indexes():
    alter_events = _SCHEMA.index("ALTER TABLE announcement_events")
    backfill_suppression = _SCHEMA.index("UPDATE announcement_suppression_state")
    recipient_index = _SCHEMA.index(
        "CREATE INDEX IF NOT EXISTS idx_announcement_events_recipient_fingerprint_bucket"
    )
    suppression_lookup_drop = _SCHEMA.index(
        "DROP INDEX IF EXISTS idx_announcement_suppression_lookup;"
    )
    suppression_unique = _SCHEMA.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_announcement_suppression_recipient_fingerprint"
    )

    assert alter_events < recipient_index
    assert backfill_suppression < suppression_lookup_drop
    assert suppression_lookup_drop < suppression_unique
    assert (
        "pg_get_constraintdef(oid) LIKE '%(source, category, target_user_id, fingerprint)%'"
        in _SCHEMA
    )


def test_storage_helper_functions_cover_edge_cases(now):
    assert AnnouncementSeverity.coerce("unknown") is AnnouncementSeverity.NORMAL
    assert _parse_json_list('["a"," ",1]') == ["a", "1"]
    assert _parse_json_list("{bad-json") == []
    assert _parse_json_list(5) == []
    assert _parse_json_list(["x", " ", 2]) == ["x", "2"]
    assert _coerce_datetime("2026-03-06T10:00:00+00:00") is not None
    assert _coerce_datetime("bad-ts") is None
    assert _row_count("UPDATE 2") == 2
    assert _row_count("   ") == 0
    assert _row_count("UPDATE nope") == 0
    assert _row_count(None) == 0
    assert _dedupe_bucket(now, 5).isdigit()


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
async def test_create_event_idempotency_conflict_without_existing_inserts(
    repository,
    mock_pool,
    now,
):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        None,
        None,
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

    assert receipt.status == "accepted"
    assert receipt.reason_code == "accepted_new"
    conn.execute.assert_awaited_once()


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
async def test_create_event_fingerprint_fallback_none_inserts(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

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

    assert receipt.status == "accepted"
    assert receipt.reason_code == "accepted_new"
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_event_accepts_non_idempotent_insert_path(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="scheduler",
            category="skill.reminder",
            severity="normal",
            target_user_id=42,
            title="Reminder",
            body="Follow up on tasks.",
            occurred_at=now,
        )
    )

    assert receipt.status == "accepted"
    assert receipt.reason_code == "accepted_new"
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_event_accepts_webhook_recipient(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="tenant_app",
            category="build.completed",
            severity="normal",
            title="Build completed",
            body="Webhook recipient should receive this event.",
            recipient=AnnouncementRecipient(
                channel="webhook",
                routing_key="",
                webhook_url="https://example.com/hooks/tenant-a",
                metadata={"subscription_id": "sub-1"},
            ),
            occurred_at=now,
        )
    )

    assert receipt.status == "accepted"
    args = conn.execute.await_args.args
    assert args[6] == 0
    assert args[7] == "webhook:url:https://example.com/hooks/tenant-a"
    assert '"channel":"webhook"' in args[8]


@pytest.mark.asyncio
async def test_create_event_normalizes_naive_occurred_at(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {
        "event_id": "evt-naive",
        "occurred_at": datetime(2026, 3, 6, 12, 0),
    }

    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="provider_monitor",
            category="provider.billing",
            severity=AnnouncementSeverity.HIGH,
            target_user_id=42,
            title="Billing issue",
            body="Credits exhausted.",
            idempotency_key="naive-ts",
            occurred_at=datetime(2026, 3, 6, 12, 0),
        )
    )

    assert receipt.status == "accepted"


@pytest.mark.asyncio
async def test_claim_due_deliveries_returns_processing_rows(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetch.return_value = [
        {
            "delivery_id": 55,
            "event_id": "evt-123",
            "channel": "discord_dm",
            "scheduled_for": now,
            "sent_at": None,
            "status": "processing",
            "error_code": None,
            "error_detail": None,
            "retry_count": 1,
            "created_at": now,
            "updated_at": now,
        }
    ]

    deliveries = await repository.claim_due_deliveries(limit=10)

    assert len(deliveries) == 1
    assert deliveries[0].delivery_id == 55
    assert deliveries[0].status == "processing"
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_delivery_normalizes_naive_scheduled_time(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    naive = datetime(2026, 3, 6, 10, 0)
    conn.fetchrow.return_value = {
        "delivery_id": 9,
        "event_id": "evt-1",
        "channel": "discord_dm",
        "scheduled_for": datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
        "sent_at": None,
        "status": "scheduled",
        "error_code": None,
        "error_detail": None,
        "retry_count": 0,
        "created_at": datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 3, 6, 10, 0, tzinfo=UTC),
    }

    delivery = await repository.create_delivery(
        event_id="evt-1",
        channel="discord_dm",
        scheduled_for=naive,
    )

    assert delivery.delivery_id == 9
    assert delivery.status == "scheduled"
    scheduled_arg = conn.fetchrow.await_args.args[3]
    assert scheduled_arg.tzinfo is not None


@pytest.mark.asyncio
async def test_list_due_deliveries_returns_expected_rows(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetch.return_value = [
        {
            "delivery_id": 1,
            "event_id": "evt-1",
            "channel": "discord_dm",
            "scheduled_for": now,
            "sent_at": None,
            "status": "scheduled",
            "error_code": None,
            "error_detail": None,
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
        }
    ]

    due = await repository.list_due_deliveries(limit=5)

    assert len(due) == 1
    assert due[0].delivery_id == 1
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_delivery_sent_returns_true_on_update(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.execute.return_value = "UPDATE 1"

    ok = await repository.mark_delivery_sent(delivery_id=1)

    assert ok is True
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_delivery_failed_retry_and_terminal_paths(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]

    retry_ok = await repository.mark_delivery_failed(
        delivery_id=2,
        error_code="transient",
        error_detail="temporary outage",
        retry_delay_seconds=120,
        terminal=False,
    )
    terminal_ok = await repository.mark_delivery_failed(
        delivery_id=3,
        error_code="fatal",
        error_detail="permanent failure",
        retry_delay_seconds=1,
        terminal=True,
    )

    assert retry_ok is True
    assert terminal_ok is True
    first = conn.execute.await_args_list[0].args
    second = conn.execute.await_args_list[1].args
    assert first[2] == "retry"
    assert second[2] == "failed"


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
async def test_get_user_preferences_without_defaults_returns_none(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    preferences = await repository.get_user_preferences(99, with_defaults=False)

    assert preferences is None


@pytest.mark.asyncio
async def test_get_event_returns_record(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {
        "event_id": "evt-1",
        "source": "skills",
        "category": "skill.reminder",
        "severity": "high",
        "tenant_id": "tenant-1",
        "target_user_id": 42,
        "title": "Reminder",
        "body": "Review queue",
        "payload_json": {"foo": "bar"},
        "fingerprint": "fp-1",
        "idempotency_key": "id-1",
        "occurred_at": now,
        "created_at": now,
        "state": "digest",
    }

    event = await repository.get_event("evt-1")

    assert event is not None
    assert event.event_id == "evt-1"
    assert event.severity is AnnouncementSeverity.HIGH
    assert event.payload == {"foo": "bar"}


@pytest.mark.asyncio
async def test_get_event_returns_none_when_missing(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    event = await repository.get_event("missing")

    assert event is None


def test_require_pool_raises_before_initialize(repository):
    with pytest.raises(RuntimeError, match="not initialized"):
        repository._require_pool()


def test_event_from_row_handles_invalid_payload_json(repository, now):
    row = {
        "event_id": "evt-1",
        "source": "skills",
        "category": "skill.reminder",
        "severity": "normal",
        "tenant_id": None,
        "target_user_id": 42,
        "title": "Title",
        "body": "Body",
        "payload_json": "{bad-json",
        "fingerprint": None,
        "idempotency_key": None,
        "occurred_at": now,
        "created_at": now,
        "state": "digest",
    }

    event = repository._event_from_row(row)

    assert event.payload == {}


def test_event_from_row_preserves_structured_recipient(repository, now):
    row = {
        "event_id": "evt-2",
        "source": "tenant_app",
        "category": "build.completed",
        "severity": "normal",
        "tenant_id": "tenant-1",
        "target_user_id": 0,
        "recipient_key": "webhook:url:https://example.com/hooks/tenant-a",
        "recipient_json": {
            "channel": "webhook",
            "routing_key": "webhook:url:https://example.com/hooks/tenant-a",
            "webhook_url": "https://example.com/hooks/tenant-a",
            "metadata": {"subscription_id": "sub-1"},
        },
        "title": "Title",
        "body": "Body",
        "payload_json": {"ok": True},
        "fingerprint": None,
        "idempotency_key": None,
        "occurred_at": now,
        "created_at": now,
        "state": "immediate",
    }

    event = repository._event_from_row(row)

    assert event.recipient is not None
    assert event.recipient.channel == "webhook"
    assert event.recipient.webhook_url == "https://example.com/hooks/tenant-a"
    assert event.recipient.metadata == {"subscription_id": "sub-1"}


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
async def test_upsert_user_preferences_handles_missing_existing(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    repository.get_user_preferences = AsyncMock(
        side_effect=[
            None,
            AnnouncementUserPreferences(
                user_id=99,
                timezone="UTC",
                digest_enabled=True,
                digest_window_local="09:00",
                immediate_categories=[],
                muted_categories=[],
                max_immediate_per_hour=6,
                updated_at=None,
            ),
        ]
    )

    updated = await repository.upsert_user_preferences(
        99,
        AnnouncementPreferencePatch(timezone="UTC"),
    )

    assert updated.user_id == 99
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_personal_profile_preferences_handles_json_variants(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        {"timezone": "UTC", "preferences": '{"announcements":{"digest_enabled":false}}'},
        {"timezone": "UTC", "preferences": "{bad-json"},
    ]

    parsed = await repository.get_personal_profile_preferences(1)
    bad = await repository.get_personal_profile_preferences(2)

    assert parsed["timezone"] == "UTC"
    assert isinstance(parsed["preferences"], dict)
    assert bad["preferences"] == {}


@pytest.mark.asyncio
async def test_get_personal_profile_preferences_none_and_non_dict(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        None,
        {"timezone": "UTC", "preferences": '["not-a-dict"]'},
    ]

    missing = await repository.get_personal_profile_preferences(1)
    parsed = await repository.get_personal_profile_preferences(2)

    assert missing == {}
    assert parsed["preferences"] == {}


@pytest.mark.asyncio
async def test_get_personal_profile_preferences_keeps_existing_dict(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {
        "timezone": "Australia/Sydney",
        "preferences": {"announcements": {"digest_enabled": True}},
    }

    parsed = await repository.get_personal_profile_preferences(3)

    assert parsed["timezone"] == "Australia/Sydney"
    assert parsed["preferences"] == {"announcements": {"digest_enabled": True}}


@pytest.mark.asyncio
async def test_count_recent_events_returns_integer(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchval.return_value = 7

    count = await repository.count_recent_events(
        target_user_id=42,
        since=now - timedelta(hours=1),
        severities=["high"],
        categories=["provider.billing"],
    )

    assert count == 7
    conn.fetchval.assert_awaited_once()


@pytest.mark.asyncio
async def test_digest_state_read_and_upsert(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = {
        "user_id": 42,
        "last_digest_at": now,
        "last_window_key": "window-key",
        "updated_at": now,
    }

    state = await repository.get_digest_state(42)
    await repository.upsert_digest_state(
        user_id=42,
        last_digest_at=now,
        last_window_key="  next-window  ",
    )

    assert state is not None
    assert state["user_id"] == 42
    assert state["last_window_key"] == "window-key"
    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_get_digest_state_returns_none_when_absent(repository, mock_pool):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.return_value = None

    state = await repository.get_digest_state(42)

    assert state is None


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
async def test_get_suppression_state_handles_present_and_missing(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        {
            "id": 5,
            "source": "provider_monitor",
            "category": "provider.billing",
            "target_user_id": 42,
            "fingerprint": "provider:claude:billing",
            "state": "active",
            "occurrence_count": 2,
            "first_seen": now,
            "last_seen": now,
            "last_notified_at": None,
            "next_allowed_at": None,
            "resolved_at": None,
            "updated_at": now,
        },
        None,
    ]

    present = await repository.get_suppression_state(
        source="provider_monitor",
        category="provider.billing",
        target_user_id=42,
        fingerprint="provider:claude:billing",
    )
    missing = await repository.get_suppression_state(
        source="provider_monitor",
        category="provider.billing",
        target_user_id=42,
        fingerprint="provider:claude:billing",
    )

    assert present is not None
    assert present.id == 5
    assert missing is None


@pytest.mark.asyncio
async def test_mark_and_resolve_suppression_paths(repository, mock_pool, now):
    pool, conn = mock_pool
    repository._pool = pool
    conn.fetchrow.side_effect = [
        {
            "id": 9,
            "source": "provider_monitor",
            "category": "provider.auth",
            "target_user_id": 42,
            "fingerprint": "provider:claude:auth",
            "state": "active",
            "occurrence_count": 1,
            "first_seen": now,
            "last_seen": now,
            "last_notified_at": now,
            "next_allowed_at": now + timedelta(hours=1),
            "resolved_at": None,
            "updated_at": now,
        },
        None,
        {
            "id": 9,
            "source": "provider_monitor",
            "category": "provider.auth",
            "target_user_id": 42,
            "fingerprint": "provider:claude:auth",
            "state": "resolved",
            "occurrence_count": 1,
            "first_seen": now,
            "last_seen": now,
            "last_notified_at": now,
            "next_allowed_at": now + timedelta(hours=1),
            "resolved_at": now,
            "updated_at": now,
        },
        None,
    ]

    notified = await repository.mark_suppression_notified(
        suppression_id=9,
        notified_at=now,
        cooldown_seconds=60,
    )
    missing_notified = await repository.mark_suppression_notified(
        suppression_id=10,
        notified_at=now,
        cooldown_seconds=60,
    )
    resolved = await repository.resolve_suppression(suppression_id=9, resolved_at=now)
    missing_resolved = await repository.resolve_suppression(suppression_id=10, resolved_at=now)

    assert notified is not None
    assert notified.id == 9
    assert missing_notified is None
    assert resolved is not None
    assert resolved.state == "resolved"
    assert missing_resolved is None


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
