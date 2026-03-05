"""PostgreSQL storage for the unified announcement domain."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

log = get_logger("zetherion_ai.announcements.storage")


class AnnouncementSeverity(Enum):
    """Announcement severity levels."""

    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def coerce(cls, raw: str | AnnouncementSeverity) -> AnnouncementSeverity:
        if isinstance(raw, AnnouncementSeverity):
            return raw
        normalized = str(raw or "").strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return cls.NORMAL


@dataclass
class AnnouncementEventInput:
    """Input payload for announcement event persistence."""

    source: str
    category: str
    severity: AnnouncementSeverity | str
    target_user_id: int
    title: str
    body: str
    tenant_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str | None = None
    idempotency_key: str | None = None
    occurred_at: datetime | None = None
    state: str = "accepted"


@dataclass
class AnnouncementEvent:
    """Persisted announcement event record."""

    event_id: str
    source: str
    category: str
    severity: AnnouncementSeverity
    tenant_id: str | None
    target_user_id: int
    title: str
    body: str
    payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str | None = None
    idempotency_key: str | None = None
    occurred_at: datetime | None = None
    created_at: datetime | None = None
    state: str = "accepted"


@dataclass
class AnnouncementReceipt:
    """Persistence receipt for event ingestion."""

    status: str  # accepted | deduped | scheduled | deferred
    event_id: str
    scheduled_for: datetime | None = None
    reason_code: str | None = None


@dataclass
class AnnouncementDelivery:
    """Queued or sent channel delivery record."""

    delivery_id: int
    event_id: str
    channel: str
    scheduled_for: datetime
    sent_at: datetime | None
    status: str
    error_code: str | None
    error_detail: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime


@dataclass
class AnnouncementUserPreferences:
    """Per-user announcement preferences."""

    user_id: int
    timezone: str = "UTC"
    digest_enabled: bool = True
    digest_window_local: str = "09:00"
    immediate_categories: list[str] = field(default_factory=list)
    muted_categories: list[str] = field(default_factory=list)
    max_immediate_per_hour: int = 6
    updated_at: datetime | None = None


@dataclass
class AnnouncementPreferencePatch:
    """Patch payload for per-user preferences."""

    timezone: str | None = None
    digest_enabled: bool | None = None
    digest_window_local: str | None = None
    immediate_categories: list[str] | None = None
    muted_categories: list[str] | None = None
    max_immediate_per_hour: int | None = None


@dataclass
class AnnouncementSuppressionState:
    """Persistent suppression state for repeated events (e.g. provider issues)."""

    id: int
    source: str
    category: str
    target_user_id: int
    fingerprint: str
    state: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    last_notified_at: datetime | None
    next_allowed_at: datetime | None
    resolved_at: datetime | None
    updated_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS announcement_events (
    event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'normal',
    tenant_id TEXT,
    target_user_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    fingerprint TEXT,
    idempotency_key TEXT,
    dedupe_bucket TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state TEXT NOT NULL DEFAULT 'accepted'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_announcement_events_source_idempotency
    ON announcement_events (source, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND btrim(idempotency_key) <> '';

CREATE INDEX IF NOT EXISTS idx_announcement_events_target_fingerprint_bucket
    ON announcement_events (target_user_id, fingerprint, category, dedupe_bucket);
CREATE INDEX IF NOT EXISTS idx_announcement_events_created
    ON announcement_events (created_at DESC);

CREATE TABLE IF NOT EXISTS announcement_deliveries (
    delivery_id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES announcement_events(event_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'scheduled',
    error_code TEXT,
    error_detail TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_announcement_deliveries_due
    ON announcement_deliveries (status, scheduled_for ASC);
CREATE INDEX IF NOT EXISTS idx_announcement_deliveries_event
    ON announcement_deliveries (event_id, created_at DESC);

CREATE TABLE IF NOT EXISTS announcement_user_preferences (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    digest_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    digest_window_local TEXT NOT NULL DEFAULT '09:00',
    immediate_categories_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    muted_categories_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    max_immediate_per_hour INTEGER NOT NULL DEFAULT 6,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS announcement_digest_state (
    user_id BIGINT PRIMARY KEY,
    last_digest_at TIMESTAMPTZ,
    last_window_key TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS announcement_suppression_state (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    target_user_id BIGINT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_notified_at TIMESTAMPTZ,
    next_allowed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source, category, target_user_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_announcement_suppression_lookup
    ON announcement_suppression_state (
        source,
        category,
        target_user_id,
        fingerprint,
        state,
        updated_at DESC
    );
"""


def _as_json_text(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _row_count(status: str | None) -> int:
    if not status:
        return 0
    parts = status.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def _dedupe_bucket(occurred_at: datetime, window_minutes: int) -> str:
    window_seconds = max(60, int(window_minutes) * 60)
    bucket = int(occurred_at.timestamp()) // window_seconds
    return str(bucket)


class AnnouncementRepository:
    """Persistence interface for unified announcement events and deliveries."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def initialize(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA)
        log.info("announcement_repository.initialized")

    async def create_event(
        self,
        event: AnnouncementEventInput,
        *,
        dedupe_window_minutes: int = 10,
    ) -> AnnouncementReceipt:
        pool = self._require_pool()
        occurred_at = event.occurred_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        source = str(event.source).strip()
        category = str(event.category).strip()
        title = str(event.title).strip()
        body = str(event.body).strip()
        fingerprint = str(event.fingerprint or "").strip() or None
        idempotency_key = str(event.idempotency_key or "").strip() or None
        severity = AnnouncementSeverity.coerce(event.severity).value
        dedupe_bucket = (
            _dedupe_bucket(occurred_at, dedupe_window_minutes)
            if fingerprint and event.target_user_id > 0
            else None
        )

        async with pool.acquire() as conn, conn.transaction():
            if idempotency_key:
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO announcement_events (
                        event_id,
                        source,
                        category,
                        severity,
                        tenant_id,
                        target_user_id,
                        title,
                        body,
                        payload_json,
                        fingerprint,
                        idempotency_key,
                        dedupe_bucket,
                        occurred_at,
                        state
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb,
                        $10, $11, $12, $13, $14
                    )
                    ON CONFLICT (source, idempotency_key)
                    WHERE idempotency_key IS NOT NULL AND btrim(idempotency_key) <> ''
                    DO NOTHING
                    RETURNING event_id, occurred_at
                    """,
                    str(uuid4()),
                    source,
                    category,
                    severity,
                    event.tenant_id,
                    event.target_user_id,
                    title,
                    body,
                    _as_json_text(event.payload),
                    fingerprint,
                    idempotency_key,
                    dedupe_bucket,
                    occurred_at,
                    str(event.state or "accepted"),
                )
                if inserted is not None:
                    return AnnouncementReceipt(
                        status="accepted",
                        event_id=str(inserted["event_id"]),
                        reason_code="accepted_new",
                    )
                existing = await conn.fetchrow(
                    """
                    SELECT event_id
                    FROM announcement_events
                    WHERE source = $1 AND idempotency_key = $2
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    source,
                    idempotency_key,
                )
                if existing is not None:
                    return AnnouncementReceipt(
                        status="deduped",
                        event_id=str(existing["event_id"]),
                        reason_code="idempotency_key_conflict",
                    )

            if dedupe_bucket is not None:
                fallback = await conn.fetchrow(
                    """
                    SELECT event_id
                    FROM announcement_events
                    WHERE target_user_id = $1
                      AND fingerprint = $2
                      AND category = $3
                      AND dedupe_bucket = $4
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    event.target_user_id,
                    fingerprint,
                    category,
                    dedupe_bucket,
                )
                if fallback is not None:
                    return AnnouncementReceipt(
                        status="deduped",
                        event_id=str(fallback["event_id"]),
                        reason_code="fingerprint_bucket_conflict",
                    )

            event_id = str(uuid4())
            await conn.execute(
                """
                INSERT INTO announcement_events (
                    event_id,
                    source,
                    category,
                    severity,
                    tenant_id,
                    target_user_id,
                    title,
                    body,
                    payload_json,
                    fingerprint,
                    idempotency_key,
                    dedupe_bucket,
                    occurred_at,
                    state
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14)
                """,
                event_id,
                source,
                category,
                severity,
                event.tenant_id,
                event.target_user_id,
                title,
                body,
                _as_json_text(event.payload),
                fingerprint,
                idempotency_key,
                dedupe_bucket,
                occurred_at,
                str(event.state or "accepted"),
            )
            return AnnouncementReceipt(
                status="accepted",
                event_id=event_id,
                reason_code="accepted_new",
            )

    async def create_delivery(
        self,
        *,
        event_id: str,
        channel: str,
        scheduled_for: datetime,
        status: str = "scheduled",
    ) -> AnnouncementDelivery:
        pool = self._require_pool()
        when = (
            scheduled_for
            if scheduled_for.tzinfo is not None
            else scheduled_for.replace(tzinfo=UTC)
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO announcement_deliveries (
                    event_id,
                    channel,
                    scheduled_for,
                    status
                )
                VALUES ($1, $2, $3, $4)
                RETURNING
                    delivery_id,
                    event_id,
                    channel,
                    scheduled_for,
                    sent_at,
                    status,
                    error_code,
                    error_detail,
                    retry_count,
                    created_at,
                    updated_at
                """,
                event_id,
                str(channel).strip(),
                when,
                str(status).strip() or "scheduled",
            )
        return self._delivery_from_row(row)

    async def claim_due_deliveries(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[AnnouncementDelivery]:
        """Claim due deliveries for dispatch processing."""
        pool = self._require_pool()
        cursor_time = as_of or datetime.now(UTC)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH due AS (
                    SELECT delivery_id
                    FROM announcement_deliveries
                    WHERE status IN ('scheduled', 'retry')
                      AND scheduled_for <= $1
                    ORDER BY scheduled_for ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                )
                UPDATE announcement_deliveries AS deliveries
                SET status = 'processing',
                    updated_at = NOW()
                FROM due
                WHERE deliveries.delivery_id = due.delivery_id
                RETURNING
                    deliveries.delivery_id,
                    deliveries.event_id,
                    deliveries.channel,
                    deliveries.scheduled_for,
                    deliveries.sent_at,
                    deliveries.status,
                    deliveries.error_code,
                    deliveries.error_detail,
                    deliveries.retry_count,
                    deliveries.created_at,
                    deliveries.updated_at
                """,
                cursor_time,
                max(1, min(1000, int(limit))),
            )
        return [self._delivery_from_row(row) for row in rows]

    async def list_due_deliveries(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[AnnouncementDelivery]:
        pool = self._require_pool()
        cursor_time = as_of or datetime.now(UTC)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    delivery_id,
                    event_id,
                    channel,
                    scheduled_for,
                    sent_at,
                    status,
                    error_code,
                    error_detail,
                    retry_count,
                    created_at,
                    updated_at
                FROM announcement_deliveries
                WHERE status IN ('scheduled', 'retry')
                  AND scheduled_for <= $1
                ORDER BY scheduled_for ASC
                LIMIT $2
                """,
                cursor_time,
                max(1, min(1000, int(limit))),
            )
        return [self._delivery_from_row(row) for row in rows]

    async def mark_delivery_sent(
        self,
        *,
        delivery_id: int,
        sent_at: datetime | None = None,
    ) -> bool:
        pool = self._require_pool()
        now = sent_at or datetime.now(UTC)
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE announcement_deliveries
                SET status = 'sent',
                    sent_at = $2,
                    updated_at = NOW(),
                    error_code = NULL,
                    error_detail = NULL
                WHERE delivery_id = $1
                """,
                delivery_id,
                now,
            )
        return _row_count(status) > 0

    async def mark_delivery_failed(
        self,
        *,
        delivery_id: int,
        error_code: str,
        error_detail: str | None = None,
        retry_delay_seconds: int = 300,
        terminal: bool = False,
    ) -> bool:
        pool = self._require_pool()
        next_attempt = datetime.now(UTC) + timedelta(seconds=max(1, retry_delay_seconds))
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE announcement_deliveries
                SET status = $2,
                    error_code = $3,
                    error_detail = $4,
                    retry_count = retry_count + 1,
                    scheduled_for = $5,
                    updated_at = NOW()
                WHERE delivery_id = $1
                """,
                delivery_id,
                "failed" if terminal else "retry",
                str(error_code).strip() or "unknown_error",
                (str(error_detail).strip() or None) if error_detail else None,
                next_attempt,
            )
        return _row_count(status) > 0

    async def get_user_preferences(
        self,
        user_id: int,
        *,
        with_defaults: bool = True,
    ) -> AnnouncementUserPreferences | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    user_id,
                    timezone,
                    digest_enabled,
                    digest_window_local,
                    immediate_categories_json,
                    muted_categories_json,
                    max_immediate_per_hour,
                    updated_at
                FROM announcement_user_preferences
                WHERE user_id = $1
                """,
                user_id,
            )
        if row is None:
            if with_defaults:
                return AnnouncementUserPreferences(user_id=user_id)
            return None
        return AnnouncementUserPreferences(
            user_id=int(row["user_id"]),
            timezone=str(row["timezone"] or "UTC"),
            digest_enabled=bool(row["digest_enabled"]),
            digest_window_local=str(row["digest_window_local"] or "09:00"),
            immediate_categories=_parse_json_list(row["immediate_categories_json"]),
            muted_categories=_parse_json_list(row["muted_categories_json"]),
            max_immediate_per_hour=max(1, int(row["max_immediate_per_hour"] or 6)),
            updated_at=_coerce_datetime(row["updated_at"]),
        )

    async def upsert_user_preferences(
        self,
        user_id: int,
        patch: AnnouncementPreferencePatch,
    ) -> AnnouncementUserPreferences:
        existing = await self.get_user_preferences(user_id)
        if existing is None:
            existing = AnnouncementUserPreferences(user_id=user_id)
        merged = AnnouncementUserPreferences(
            user_id=user_id,
            timezone=(patch.timezone or existing.timezone or "UTC").strip() or "UTC",
            digest_enabled=(
                existing.digest_enabled
                if patch.digest_enabled is None
                else bool(patch.digest_enabled)
            ),
            digest_window_local=(
                (patch.digest_window_local or existing.digest_window_local or "09:00").strip()
                or "09:00"
            ),
            immediate_categories=(
                existing.immediate_categories
                if patch.immediate_categories is None
                else [str(item).strip() for item in patch.immediate_categories if str(item).strip()]
            ),
            muted_categories=(
                existing.muted_categories
                if patch.muted_categories is None
                else [str(item).strip() for item in patch.muted_categories if str(item).strip()]
            ),
            max_immediate_per_hour=max(
                1,
                patch.max_immediate_per_hour
                if patch.max_immediate_per_hour is not None
                else existing.max_immediate_per_hour,
            ),
        )
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO announcement_user_preferences (
                    user_id,
                    timezone,
                    digest_enabled,
                    digest_window_local,
                    immediate_categories_json,
                    muted_categories_json,
                    max_immediate_per_hour,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    timezone = EXCLUDED.timezone,
                    digest_enabled = EXCLUDED.digest_enabled,
                    digest_window_local = EXCLUDED.digest_window_local,
                    immediate_categories_json = EXCLUDED.immediate_categories_json,
                    muted_categories_json = EXCLUDED.muted_categories_json,
                    max_immediate_per_hour = EXCLUDED.max_immediate_per_hour,
                    updated_at = NOW()
                """,
                merged.user_id,
                merged.timezone,
                merged.digest_enabled,
                merged.digest_window_local,
                json.dumps(merged.immediate_categories),
                json.dumps(merged.muted_categories),
                merged.max_immediate_per_hour,
            )
        updated = await self.get_user_preferences(user_id)
        if updated is None:  # pragma: no cover
            return merged
        return updated

    async def get_personal_profile_preferences(self, user_id: int) -> dict[str, Any]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT timezone, preferences
                FROM personal_profile
                WHERE user_id = $1
                LIMIT 1
                """,
                user_id,
            )
        if row is None:
            return {}
        raw_preferences = row.get("preferences")
        if isinstance(raw_preferences, str):
            try:
                raw_preferences = json.loads(raw_preferences)
            except json.JSONDecodeError:
                raw_preferences = {}
        if not isinstance(raw_preferences, dict):
            raw_preferences = {}
        timezone = row.get("timezone")
        timezone_value = str(timezone).strip() if timezone else ""
        return {
            "timezone": timezone_value or None,
            "preferences": raw_preferences,
        }

    async def count_recent_events(
        self,
        *,
        target_user_id: int,
        since: datetime,
        severities: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> int:
        pool = self._require_pool()
        severity_filter = [str(item).strip() for item in severities or [] if str(item).strip()]
        category_filter = [str(item).strip() for item in categories or [] if str(item).strip()]
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*)::int AS event_count
                FROM announcement_events
                WHERE target_user_id = $1
                  AND occurred_at >= $2
                  AND ($3::text[] IS NULL OR severity = ANY($3::text[]))
                  AND ($4::text[] IS NULL OR category = ANY($4::text[]))
                """,
                target_user_id,
                since,
                severity_filter or None,
                category_filter or None,
            )
        return int(count or 0)

    async def get_event(self, event_id: str) -> AnnouncementEvent | None:
        """Look up an announcement event by ID."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    event_id,
                    source,
                    category,
                    severity,
                    tenant_id,
                    target_user_id,
                    title,
                    body,
                    payload_json,
                    fingerprint,
                    idempotency_key,
                    occurred_at,
                    created_at,
                    state
                FROM announcement_events
                WHERE event_id = $1
                """,
                event_id,
            )
        if row is None:
            return None
        return self._event_from_row(row)

    async def get_digest_state(self, user_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, last_digest_at, last_window_key, updated_at
                FROM announcement_digest_state
                WHERE user_id = $1
                """,
                user_id,
            )
        if row is None:
            return None
        return {
            "user_id": int(row["user_id"]),
            "last_digest_at": _coerce_datetime(row["last_digest_at"]),
            "last_window_key": str(row["last_window_key"] or ""),
            "updated_at": _coerce_datetime(row["updated_at"]),
        }

    async def upsert_digest_state(
        self,
        *,
        user_id: int,
        last_digest_at: datetime | None,
        last_window_key: str | None,
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO announcement_digest_state (
                    user_id,
                    last_digest_at,
                    last_window_key,
                    updated_at
                )
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    last_digest_at = EXCLUDED.last_digest_at,
                    last_window_key = EXCLUDED.last_window_key,
                    updated_at = NOW()
                """,
                user_id,
                last_digest_at,
                (str(last_window_key).strip() or None) if last_window_key else None,
            )

    async def get_suppression_state(
        self,
        *,
        source: str,
        category: str,
        target_user_id: int,
        fingerprint: str,
    ) -> AnnouncementSuppressionState | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    source,
                    category,
                    target_user_id,
                    fingerprint,
                    state,
                    occurrence_count,
                    first_seen,
                    last_seen,
                    last_notified_at,
                    next_allowed_at,
                    resolved_at,
                    updated_at
                FROM announcement_suppression_state
                WHERE source = $1
                  AND category = $2
                  AND target_user_id = $3
                  AND fingerprint = $4
                LIMIT 1
                """,
                source,
                category,
                target_user_id,
                fingerprint,
            )
        if row is None:
            return None
        return self._suppression_from_row(row)

    async def upsert_suppression_observation(
        self,
        *,
        source: str,
        category: str,
        target_user_id: int,
        fingerprint: str,
        seen_at: datetime | None = None,
    ) -> AnnouncementSuppressionState:
        pool = self._require_pool()
        observed_at = seen_at or datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO announcement_suppression_state (
                    source,
                    category,
                    target_user_id,
                    fingerprint,
                    state,
                    first_seen,
                    last_seen,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, 'active', $5, $5, NOW())
                ON CONFLICT (source, category, target_user_id, fingerprint)
                DO UPDATE SET
                    state = 'active',
                    last_seen = EXCLUDED.last_seen,
                    occurrence_count = announcement_suppression_state.occurrence_count + 1,
                    updated_at = NOW()
                RETURNING
                    id,
                    source,
                    category,
                    target_user_id,
                    fingerprint,
                    state,
                    occurrence_count,
                    first_seen,
                    last_seen,
                    last_notified_at,
                    next_allowed_at,
                    resolved_at,
                    updated_at
                """,
                source,
                category,
                target_user_id,
                fingerprint,
                observed_at,
            )
        return self._suppression_from_row(row)

    async def mark_suppression_notified(
        self,
        *,
        suppression_id: int,
        notified_at: datetime | None = None,
        cooldown_seconds: int = 3600,
    ) -> AnnouncementSuppressionState | None:
        pool = self._require_pool()
        now = notified_at or datetime.now(UTC)
        next_allowed = now + timedelta(seconds=max(1, cooldown_seconds))
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE announcement_suppression_state
                SET last_notified_at = $2,
                    next_allowed_at = $3,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING
                    id,
                    source,
                    category,
                    target_user_id,
                    fingerprint,
                    state,
                    occurrence_count,
                    first_seen,
                    last_seen,
                    last_notified_at,
                    next_allowed_at,
                    resolved_at,
                    updated_at
                """,
                suppression_id,
                now,
                next_allowed,
            )
        if row is None:
            return None
        return self._suppression_from_row(row)

    async def resolve_suppression(
        self,
        *,
        suppression_id: int,
        resolved_at: datetime | None = None,
    ) -> AnnouncementSuppressionState | None:
        pool = self._require_pool()
        now = resolved_at or datetime.now(UTC)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE announcement_suppression_state
                SET state = 'resolved',
                    resolved_at = $2,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING
                    id,
                    source,
                    category,
                    target_user_id,
                    fingerprint,
                    state,
                    occurrence_count,
                    first_seen,
                    last_seen,
                    last_notified_at,
                    next_allowed_at,
                    resolved_at,
                    updated_at
                """,
                suppression_id,
                now,
            )
        if row is None:
            return None
        return self._suppression_from_row(row)

    async def purge_expired_events(self, retention_days: int = 30) -> int:
        pool = self._require_pool()
        cutoff = datetime.now(UTC) - timedelta(days=max(1, retention_days))
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                DELETE FROM announcement_events
                WHERE created_at < $1
                """,
                cutoff,
            )
        return _row_count(status)

    async def purge_expired_deliveries(self, retention_days: int = 30) -> int:
        pool = self._require_pool()
        cutoff = datetime.now(UTC) - timedelta(days=max(1, retention_days))
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                DELETE FROM announcement_deliveries
                WHERE created_at < $1
                """,
                cutoff,
            )
        return _row_count(status)

    async def purge_resolved_suppressions(self, retention_days: int = 30) -> int:
        pool = self._require_pool()
        cutoff = datetime.now(UTC) - timedelta(days=max(1, retention_days))
        async with pool.acquire() as conn:
            status = await conn.execute(
                """
                DELETE FROM announcement_suppression_state
                WHERE state = 'resolved'
                  AND resolved_at IS NOT NULL
                  AND resolved_at < $1
                """,
                cutoff,
            )
        return _row_count(status)

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("AnnouncementRepository is not initialized")
        return self._pool

    @staticmethod
    def _delivery_from_row(row: Any) -> AnnouncementDelivery:
        return AnnouncementDelivery(
            delivery_id=int(row["delivery_id"]),
            event_id=str(row["event_id"]),
            channel=str(row["channel"]),
            scheduled_for=_coerce_datetime(row["scheduled_for"]) or datetime.now(UTC),
            sent_at=_coerce_datetime(row["sent_at"]),
            status=str(row["status"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_detail=str(row["error_detail"]) if row["error_detail"] is not None else None,
            retry_count=int(row["retry_count"] or 0),
            created_at=_coerce_datetime(row["created_at"]) or datetime.now(UTC),
            updated_at=_coerce_datetime(row["updated_at"]) or datetime.now(UTC),
        )

    @staticmethod
    def _event_from_row(row: Any) -> AnnouncementEvent:
        payload_raw = row.get("payload_json", {})
        if isinstance(payload_raw, str):
            try:
                payload_raw = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload_raw = {}
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        return AnnouncementEvent(
            event_id=str(row["event_id"]),
            source=str(row["source"]),
            category=str(row["category"]),
            severity=AnnouncementSeverity.coerce(row.get("severity", "normal")),
            tenant_id=str(row["tenant_id"]).strip() if row["tenant_id"] is not None else None,
            target_user_id=int(row["target_user_id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            payload=payload,
            fingerprint=str(row["fingerprint"]).strip() if row["fingerprint"] is not None else None,
            idempotency_key=(
                str(row["idempotency_key"]).strip() if row["idempotency_key"] is not None else None
            ),
            occurred_at=_coerce_datetime(row.get("occurred_at", None)),
            created_at=_coerce_datetime(row.get("created_at", None)),
            state=str(row["state"] if "state" in row and row["state"] else "accepted"),
        )

    @staticmethod
    def _suppression_from_row(row: Any) -> AnnouncementSuppressionState:
        return AnnouncementSuppressionState(
            id=int(row["id"]),
            source=str(row["source"]),
            category=str(row["category"]),
            target_user_id=int(row["target_user_id"]),
            fingerprint=str(row["fingerprint"]),
            state=str(row["state"]),
            occurrence_count=int(row["occurrence_count"] or 0),
            first_seen=_coerce_datetime(row["first_seen"]) or datetime.now(UTC),
            last_seen=_coerce_datetime(row["last_seen"]) or datetime.now(UTC),
            last_notified_at=_coerce_datetime(row["last_notified_at"]),
            next_allowed_at=_coerce_datetime(row["next_allowed_at"]),
            resolved_at=_coerce_datetime(row["resolved_at"]),
            updated_at=_coerce_datetime(row["updated_at"]) or datetime.now(UTC),
        )
