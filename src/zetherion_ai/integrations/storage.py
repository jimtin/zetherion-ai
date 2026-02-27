"""Canonical storage for provider-agnostic integrations and routing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-not-found,import-untyped]

from zetherion_ai.logging import get_logger
from zetherion_ai.routing.models import DestinationType, RouteDecision

log = get_logger("zetherion_ai.integrations.storage")

INTEGRATIONS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS integration_accounts (
    id               SERIAL       PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL,
    email            TEXT,
    scopes           TEXT[]       DEFAULT '{}',
    is_primary       BOOLEAN      DEFAULT FALSE,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, account_ref)
);

CREATE TABLE IF NOT EXISTS integration_destinations (
    id               SERIAL       PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL,
    destination_id   TEXT         NOT NULL,
    destination_type TEXT         NOT NULL,
    display_name     TEXT,
    is_primary       BOOLEAN      DEFAULT FALSE,
    writable         BOOLEAN      DEFAULT TRUE,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, destination_type, destination_id)
);

CREATE TABLE IF NOT EXISTS integration_sync_state (
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL,
    cursor           TEXT,
    state            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider, account_ref)
);

CREATE TABLE IF NOT EXISTS integration_email_messages (
    id               SERIAL       PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL,
    external_id      TEXT         NOT NULL,
    thread_id        TEXT,
    subject          TEXT,
    from_email       TEXT,
    to_emails        TEXT[]       DEFAULT '{}',
    body_preview     TEXT,
    received_at      TIMESTAMPTZ,
    classification   TEXT,
    priority_score   FLOAT,
    security_action  TEXT,
    is_processed     BOOLEAN      DEFAULT FALSE,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, account_ref, external_id)
);

CREATE TABLE IF NOT EXISTS integration_object_links (
    id               SERIAL       PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    object_type      TEXT         NOT NULL,
    local_id         TEXT         NOT NULL,
    external_id      TEXT         NOT NULL,
    destination_id   TEXT,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, object_type, local_id),
    UNIQUE (user_id, provider, object_type, external_id)
);

CREATE TABLE IF NOT EXISTS routing_preferences (
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    key              TEXT         NOT NULL,
    value            JSONB        NOT NULL,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider, key)
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    id               BIGSERIAL    PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    source_type      TEXT         NOT NULL,
    route_tag        TEXT,
    mode             TEXT,
    reason           TEXT,
    decision_json    JSONB        NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_security_events (
    id               BIGSERIAL    PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    source_type      TEXT         NOT NULL,
    action           TEXT         NOT NULL,
    score            FLOAT        NOT NULL,
    reason           TEXT,
    payload_hash     TEXT,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_ingestion_queue (
    id               BIGSERIAL    PRIMARY KEY,
    queue_batch_id   TEXT         NOT NULL,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    source_type      TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL DEFAULT 'default',
    external_id      TEXT,
    payload          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status           TEXT         NOT NULL DEFAULT 'pending',
    error_code       TEXT,
    error_detail     TEXT,
    attempt_count    INT          NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_ingestion_dead_letter (
    id               BIGSERIAL    PRIMARY KEY,
    queue_item_id    BIGINT       NOT NULL,
    queue_batch_id   TEXT         NOT NULL,
    user_id          BIGINT       NOT NULL,
    provider         TEXT         NOT NULL,
    source_type      TEXT         NOT NULL,
    account_ref      TEXT         NOT NULL DEFAULT 'default',
    external_id      TEXT,
    payload          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error_code       TEXT,
    error_detail     TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_integration_accounts_user
    ON integration_accounts (user_id, provider);
CREATE INDEX IF NOT EXISTS idx_integration_destinations_user
    ON integration_destinations (user_id, provider, destination_type);
CREATE INDEX IF NOT EXISTS idx_integration_messages_user
    ON integration_email_messages (user_id, provider, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_routing_decisions_user
    ON routing_decisions (user_id, provider, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_user
    ON integration_security_events (user_id, provider, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_queue_lookup
    ON integration_ingestion_queue (user_id, provider, source_type, status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_ingestion_queue_batch
    ON integration_ingestion_queue (queue_batch_id);
"""


@dataclass
class IntegrationDestinationRecord:
    """Stored destination record."""

    provider: str
    account_ref: str
    destination_id: str
    destination_type: DestinationType
    display_name: str
    is_primary: bool
    writable: bool
    metadata: dict[str, Any]


@dataclass
class IngestionQueueRecord:
    """Stored ingestion queue record."""

    id: int
    queue_batch_id: str
    user_id: int
    provider: str
    source_type: str
    account_ref: str
    external_id: str
    payload: dict[str, Any]
    status: str
    error_code: str | None
    error_detail: str | None
    attempt_count: int
    created_at: datetime | None
    updated_at: datetime | None


class IntegrationStorage:
    """Storage adapter for provider-agnostic integration state."""

    def __init__(self, pool: asyncpg.Pool):  # type: ignore[type-arg]
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]

    async def ensure_schema(self) -> None:
        """Ensure canonical integration tables exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(INTEGRATIONS_SCHEMA_SQL)
        log.info("integration_schema_ensured")

    async def upsert_account(
        self,
        user_id: int,
        provider: str,
        account_ref: str,
        *,
        email: str | None = None,
        scopes: list[str] | None = None,
        is_primary: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert/update an integration account record."""
        await self._execute(
            """
            INSERT INTO integration_accounts
                (user_id, provider, account_ref, email, scopes, is_primary, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, provider, account_ref) DO UPDATE SET
                email = EXCLUDED.email,
                scopes = EXCLUDED.scopes,
                is_primary = EXCLUDED.is_primary,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            """,
            user_id,
            provider,
            account_ref,
            email,
            scopes or [],
            is_primary,
            self._json_param(metadata or {}),
        )

    async def upsert_destination(
        self,
        user_id: int,
        provider: str,
        account_ref: str,
        destination_id: str,
        destination_type: DestinationType,
        *,
        display_name: str,
        is_primary: bool = False,
        writable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert/update destination record."""
        await self._execute(
            """
            INSERT INTO integration_destinations
                (user_id, provider, account_ref, destination_id, destination_type,
                 display_name, is_primary, writable, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (user_id, provider, destination_type, destination_id) DO UPDATE SET
                account_ref = EXCLUDED.account_ref,
                display_name = EXCLUDED.display_name,
                is_primary = EXCLUDED.is_primary,
                writable = EXCLUDED.writable,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            """,
            user_id,
            provider,
            account_ref,
            destination_id,
            destination_type.value,
            display_name,
            is_primary,
            writable,
            self._json_param(metadata or {}),
        )

    async def list_destinations(
        self,
        user_id: int,
        provider: str,
        destination_type: DestinationType,
    ) -> list[IntegrationDestinationRecord]:
        """List known destinations for a user/provider/type."""
        rows = await self._fetch(
            """
            SELECT provider, account_ref, destination_id, destination_type,
                   display_name, is_primary, writable, metadata
            FROM integration_destinations
            WHERE user_id = $1 AND provider = $2 AND destination_type = $3
            ORDER BY is_primary DESC, display_name NULLS LAST, destination_id
            """,
            user_id,
            provider,
            destination_type.value,
        )
        return [
            IntegrationDestinationRecord(
                provider=row["provider"],
                account_ref=row["account_ref"],
                destination_id=row["destination_id"],
                destination_type=DestinationType(row["destination_type"]),
                display_name=row["display_name"] or row["destination_id"],
                is_primary=bool(row["is_primary"]),
                writable=bool(row["writable"]),
                metadata=self._json_object(row["metadata"]),
            )
            for row in rows
        ]

    async def get_primary_destination(
        self,
        user_id: int,
        provider: str,
        destination_type: DestinationType,
    ) -> IntegrationDestinationRecord | None:
        """Get primary destination for user/provider/type."""
        row = await self._fetchrow(
            """
            SELECT provider, account_ref, destination_id, destination_type,
                   display_name, is_primary, writable, metadata
            FROM integration_destinations
            WHERE user_id = $1
              AND provider = $2
              AND destination_type = $3
              AND is_primary = TRUE
            LIMIT 1
            """,
            user_id,
            provider,
            destination_type.value,
        )
        if row is None:
            return None
        return IntegrationDestinationRecord(
            provider=row["provider"],
            account_ref=row["account_ref"],
            destination_id=row["destination_id"],
            destination_type=DestinationType(row["destination_type"]),
            display_name=row["display_name"] or row["destination_id"],
            is_primary=bool(row["is_primary"]),
            writable=bool(row["writable"]),
            metadata=self._json_object(row["metadata"]),
        )

    async def set_primary_destination(
        self,
        user_id: int,
        provider: str,
        destination_type: DestinationType,
        destination_id: str,
    ) -> bool:
        """Set exactly one primary destination for a provider/type."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                UPDATE integration_destinations
                SET is_primary = FALSE, updated_at = now()
                WHERE user_id = $1 AND provider = $2 AND destination_type = $3
                """,
                user_id,
                provider,
                destination_type.value,
            )
            result = await conn.execute(
                """
                UPDATE integration_destinations
                SET is_primary = TRUE, updated_at = now()
                WHERE user_id = $1
                  AND provider = $2
                  AND destination_type = $3
                  AND destination_id = $4
                """,
                user_id,
                provider,
                destination_type.value,
                destination_id,
            )
        return str(result) == "UPDATE 1"

    async def delete_account(
        self,
        *,
        user_id: int,
        provider: str,
        account_ref: str,
    ) -> bool:
        """Delete an integration account and related destination/sync rows."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                DELETE FROM integration_sync_state
                WHERE user_id = $1 AND provider = $2 AND account_ref = $3
                """,
                user_id,
                provider,
                account_ref,
            )
            await conn.execute(
                """
                DELETE FROM integration_destinations
                WHERE user_id = $1 AND provider = $2 AND account_ref = $3
                """,
                user_id,
                provider,
                account_ref,
            )
            result = await conn.execute(
                """
                DELETE FROM integration_accounts
                WHERE user_id = $1 AND provider = $2 AND account_ref = $3
                """,
                user_id,
                provider,
                account_ref,
            )
        return str(result) == "DELETE 1"

    async def delete_destination(
        self,
        *,
        user_id: int,
        provider: str,
        destination_type: DestinationType,
        destination_id: str,
    ) -> bool:
        """Delete a known destination row for a user/provider/type."""
        result = await self._execute(
            """
            DELETE FROM integration_destinations
            WHERE user_id = $1
              AND provider = $2
              AND destination_type = $3
              AND destination_id = $4
            """,
            user_id,
            provider,
            destination_type.value,
            destination_id,
        )
        return str(result) == "DELETE 1"

    async def record_security_event(
        self,
        user_id: int,
        provider: str,
        source_type: str,
        *,
        action: str,
        score: float,
        reason: str,
        payload_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a security verdict for auditability."""
        await self._execute(
            """
            INSERT INTO integration_security_events
                (user_id, provider, source_type, action, score, reason, payload_hash, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            user_id,
            provider,
            source_type,
            action,
            score,
            reason,
            payload_hash,
            self._json_param(metadata or {}),
        )

    async def enqueue_ingestion_batch(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
        items: list[dict[str, Any]],
        status: str = "pending",
        error_code: str | None = None,
        error_detail: str | None = None,
        queue_batch_id: str | None = None,
    ) -> tuple[str, int]:
        """Enqueue provider payloads for deferred ingestion."""
        if not items:
            return (queue_batch_id or f"batch-{uuid4().hex}", 0)

        batch_id = queue_batch_id or f"batch-{uuid4().hex}"
        async with self._pool.acquire() as conn, conn.transaction():
            for item in items:
                account_ref = str(item.get("account_ref") or item.get("account_email") or "default")
                external_id = str(item.get("external_id") or item.get("id") or "")
                await conn.execute(
                    """
                    INSERT INTO integration_ingestion_queue
                        (queue_batch_id, user_id, provider, source_type, account_ref,
                         external_id, payload, status, error_code, error_detail)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    batch_id,
                    user_id,
                    provider,
                    source_type,
                    account_ref,
                    external_id,
                    self._json_param(item),
                    status,
                    error_code,
                    error_detail,
                )
        return batch_id, len(items)

    async def claim_ingestion_queue_items(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
        statuses: list[str],
        limit: int = 100,
    ) -> list[IngestionQueueRecord]:
        """Claim queue items and mark them as processing."""
        rows = await self._fetch(
            """
            WITH candidates AS (
                SELECT id
                FROM integration_ingestion_queue
                WHERE user_id = $1
                  AND provider = $2
                  AND source_type = $3
                  AND status = ANY($4::text[])
                ORDER BY created_at ASC
                LIMIT $5
                FOR UPDATE SKIP LOCKED
            )
            UPDATE integration_ingestion_queue q
            SET status = 'processing',
                attempt_count = q.attempt_count + 1,
                updated_at = now()
            FROM candidates
            WHERE q.id = candidates.id
            RETURNING q.id, q.queue_batch_id, q.user_id, q.provider, q.source_type,
                      q.account_ref, q.external_id, q.payload, q.status, q.error_code,
                      q.error_detail, q.attempt_count, q.created_at, q.updated_at
            """,
            user_id,
            provider,
            source_type,
            statuses,
            limit,
        )
        return [self._queue_record_from_row(row) for row in rows]

    async def mark_ingestion_items_done(self, queue_ids: list[int]) -> None:
        """Mark queue items as completed."""
        if not queue_ids:
            return
        await self._execute(
            """
            UPDATE integration_ingestion_queue
            SET status = 'done',
                error_code = NULL,
                error_detail = NULL,
                updated_at = now()
            WHERE id = ANY($1::bigint[])
            """,
            queue_ids,
        )

    async def mark_ingestion_items_blocked_unhealthy(
        self,
        *,
        queue_ids: list[int],
        error_code: str,
        error_detail: str | None = None,
    ) -> None:
        """Mark queue items as blocked by infrastructure health."""
        if not queue_ids:
            return
        await self._execute(
            """
            UPDATE integration_ingestion_queue
            SET status = 'blocked_unhealthy',
                error_code = $2,
                error_detail = $3,
                updated_at = now()
            WHERE id = ANY($1::bigint[])
            """,
            queue_ids,
            error_code,
            error_detail,
        )

    async def move_ingestion_item_to_dead_letter(
        self,
        *,
        queue_id: int,
        error_code: str,
        error_detail: str | None = None,
    ) -> None:
        """Move one queue item to dead letter storage and mark terminal."""
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, queue_batch_id, user_id, provider, source_type, account_ref,
                       external_id, payload
                FROM integration_ingestion_queue
                WHERE id = $1
                LIMIT 1
                """,
                queue_id,
            )
            if row is None:
                return
            await conn.execute(
                """
                INSERT INTO integration_ingestion_dead_letter
                    (queue_item_id, queue_batch_id, user_id, provider, source_type,
                     account_ref, external_id, payload, error_code, error_detail)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                row["id"],
                row["queue_batch_id"],
                row["user_id"],
                row["provider"],
                row["source_type"],
                row["account_ref"],
                row["external_id"],
                row["payload"],
                error_code,
                error_detail,
            )
            await conn.execute(
                """
                UPDATE integration_ingestion_queue
                SET status = 'dead_letter',
                    error_code = $2,
                    error_detail = $3,
                    updated_at = now()
                WHERE id = $1
                """,
                queue_id,
                error_code,
                error_detail,
            )

    async def get_ingestion_queue_counts(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
    ) -> dict[str, int]:
        """Get queue counts grouped by status."""
        rows = await self._fetch(
            """
            SELECT status, COUNT(*) AS cnt
            FROM integration_ingestion_queue
            WHERE user_id = $1
              AND provider = $2
              AND source_type = $3
            GROUP BY status
            """,
            user_id,
            provider,
            source_type,
        )
        out: dict[str, int] = {}
        for row in rows:
            status = str(row["status"])
            out[status] = int(row["cnt"] or 0)
        return out

    async def record_routing_decision(
        self,
        user_id: int,
        provider: str,
        source_type: str,
        decision: RouteDecision,
    ) -> None:
        """Store a full routing decision record."""
        data = decision.to_dict()
        await self._execute(
            """
            INSERT INTO routing_decisions
                (user_id, provider, source_type, route_tag, mode, reason, decision_json)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            user_id,
            provider,
            source_type,
            decision.route_tag.value,
            decision.mode.value,
            decision.reason,
            self._json_param(data),
        )

    async def upsert_object_link(
        self,
        user_id: int,
        provider: str,
        object_type: str,
        local_id: str,
        external_id: str,
        *,
        destination_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Upsert external/local object mapping for two-way sync."""
        await self._execute(
            """
            INSERT INTO integration_object_links
                (user_id, provider, object_type, local_id, external_id, destination_id, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, provider, object_type, local_id) DO UPDATE SET
                external_id = EXCLUDED.external_id,
                destination_id = EXCLUDED.destination_id,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            """,
            user_id,
            provider,
            object_type,
            local_id,
            external_id,
            destination_id,
            self._json_param(metadata or {}),
        )

    async def get_object_link_by_external(
        self,
        *,
        user_id: int,
        provider: str,
        object_type: str,
        external_id: str,
    ) -> dict[str, Any] | None:
        """Get object-link record by external id for deduplication."""
        row = await self._fetchrow(
            """
            SELECT local_id, external_id, destination_id, metadata, created_at, updated_at
            FROM integration_object_links
            WHERE user_id = $1 AND provider = $2 AND object_type = $3 AND external_id = $4
            LIMIT 1
            """,
            user_id,
            provider,
            object_type,
            external_id,
        )
        if row is None:
            return None
        return {
            "local_id": row["local_id"],
            "external_id": row["external_id"],
            "destination_id": row["destination_id"],
            "metadata": self._json_object(row["metadata"]),
            "created_at": row["created_at"].isoformat()
            if isinstance(row["created_at"], datetime)
            else None,
            "updated_at": row["updated_at"].isoformat()
            if isinstance(row["updated_at"], datetime)
            else None,
        }

    async def set_sync_state(
        self,
        user_id: int,
        provider: str,
        account_ref: str,
        *,
        cursor: str | None,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Persist sync cursor/state for a provider account."""
        await self._execute(
            """
            INSERT INTO integration_sync_state (user_id, provider, account_ref, cursor, state)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, provider, account_ref) DO UPDATE SET
                cursor = EXCLUDED.cursor,
                state = EXCLUDED.state,
                updated_at = now()
            """,
            user_id,
            provider,
            account_ref,
            cursor,
            self._json_param(state or {}),
        )

    async def get_sync_state(
        self,
        user_id: int,
        provider: str,
        account_ref: str,
    ) -> dict[str, Any] | None:
        """Load sync cursor/state for a provider account."""
        row = await self._fetchrow(
            """
            SELECT cursor, state, updated_at
            FROM integration_sync_state
            WHERE user_id = $1 AND provider = $2 AND account_ref = $3
            """,
            user_id,
            provider,
            account_ref,
        )
        if row is None:
            return None
        return {
            "cursor": row["cursor"],
            "state": self._json_object(row["state"]),
            "updated_at": row["updated_at"].isoformat()
            if isinstance(row["updated_at"], datetime)
            else None,
        }

    async def set_routing_preference(
        self,
        *,
        user_id: int,
        provider: str,
        key: str,
        value: dict[str, Any],
    ) -> None:
        """Store a provider routing preference."""
        await self._execute(
            """
            INSERT INTO routing_preferences (user_id, provider, key, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, provider, key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = now()
            """,
            user_id,
            provider,
            key,
            self._json_param(value),
        )

    async def get_routing_preference(
        self,
        *,
        user_id: int,
        provider: str,
        key: str,
    ) -> dict[str, Any] | None:
        """Read a provider routing preference if present."""
        row = await self._fetchrow(
            """
            SELECT value
            FROM routing_preferences
            WHERE user_id = $1 AND provider = $2 AND key = $3
            LIMIT 1
            """,
            user_id,
            provider,
            key,
        )
        if row is None:
            return None
        value = self._json_object(row["value"])
        return value

    async def store_email_message(
        self,
        user_id: int,
        provider: str,
        account_ref: str,
        external_id: str,
        *,
        thread_id: str | None,
        subject: str,
        from_email: str,
        to_emails: list[str],
        body_preview: str,
        received_at: datetime | None,
        classification: str | None = None,
        priority_score: float | None = None,
        security_action: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store canonical email message row."""
        await self._execute(
            """
            INSERT INTO integration_email_messages
                (user_id, provider, account_ref, external_id, thread_id,
                 subject, from_email, to_emails, body_preview, received_at,
                 classification, priority_score, security_action, metadata)
            VALUES ($1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10,
                    $11, $12, $13, $14)
            ON CONFLICT (user_id, provider, account_ref, external_id) DO UPDATE SET
                thread_id = EXCLUDED.thread_id,
                subject = EXCLUDED.subject,
                from_email = EXCLUDED.from_email,
                to_emails = EXCLUDED.to_emails,
                body_preview = EXCLUDED.body_preview,
                received_at = EXCLUDED.received_at,
                classification = EXCLUDED.classification,
                priority_score = EXCLUDED.priority_score,
                security_action = EXCLUDED.security_action,
                metadata = EXCLUDED.metadata
            """,
            user_id,
            provider,
            account_ref,
            external_id,
            thread_id,
            subject,
            from_email,
            to_emails,
            body_preview,
            received_at,
            classification,
            priority_score,
            security_action,
            self._json_param(metadata or {}),
        )

    @staticmethod
    def _json_param(value: dict[str, Any]) -> str:
        """Encode JSON objects for asyncpg JSONB parameters."""
        return json.dumps(value)

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        """Decode JSON/JSONB values from asyncpg rows into a dict."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            result: list[asyncpg.Record] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(query, *args)
            return result

    def _queue_record_from_row(self, row: asyncpg.Record) -> IngestionQueueRecord:
        return IngestionQueueRecord(
            id=int(row["id"]),
            queue_batch_id=str(row["queue_batch_id"]),
            user_id=int(row["user_id"]),
            provider=str(row["provider"]),
            source_type=str(row["source_type"]),
            account_ref=str(row["account_ref"]),
            external_id=str(row["external_id"] or ""),
            payload=self._json_object(row["payload"]),
            status=str(row["status"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_detail=str(row["error_detail"]) if row["error_detail"] is not None else None,
            attempt_count=int(row["attempt_count"] or 0),
            created_at=row["created_at"] if isinstance(row["created_at"], datetime) else None,
            updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else None,
        )
