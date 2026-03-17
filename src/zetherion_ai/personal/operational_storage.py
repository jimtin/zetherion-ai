"""Owner-personal operational and review state persistence."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import (
    PersonalOperationalItem,
    PersonalOperationalItemStatus,
    PersonalReviewItem,
    PersonalReviewItemStatus,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.personal.operational_storage")

_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACTIVE_OPERATIONAL_STATUSES = (
    PersonalOperationalItemStatus.ACTIVE.value,
    PersonalOperationalItemStatus.IN_PROGRESS.value,
    PersonalOperationalItemStatus.BLOCKED.value,
)


def _validate_schema_identifier(schema: str) -> str:
    candidate = schema.strip()
    if not _SCHEMA_NAME_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema name: {schema!r}")
    return candidate


def _schema_sql(schema: str) -> str:
    validated = _validate_schema_identifier(schema)
    return f"""\
CREATE TABLE IF NOT EXISTS "{validated}".personal_operational_items (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT       NOT NULL,
    item_type       TEXT         NOT NULL,
    title_value     TEXT         NOT NULL,
    detail_value    TEXT,
    status          TEXT         NOT NULL DEFAULT 'active',
    due_at          TIMESTAMPTZ,
    tags            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    metadata_json   JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    source          TEXT         NOT NULL DEFAULT 'manual',
    external_ref    TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_personal_operational_items_user_status
    ON "{validated}".personal_operational_items (user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_operational_items_user_type
    ON "{validated}".personal_operational_items (user_id, item_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_operational_items_external_ref
    ON "{validated}".personal_operational_items (user_id, external_ref)
    WHERE external_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS "{validated}".personal_review_items (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT       NOT NULL,
    item_type         TEXT         NOT NULL,
    title_value       TEXT         NOT NULL,
    detail_value      TEXT,
    status            TEXT         NOT NULL DEFAULT 'pending',
    source            TEXT         NOT NULL DEFAULT 'system',
    related_resource  TEXT,
    priority          INT          NOT NULL DEFAULT 50,
    metadata_json     JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    due_at            TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    resolved_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_personal_review_items_user_status
    ON "{validated}".personal_review_items (user_id, status, priority DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_review_items_related_resource
    ON "{validated}".personal_review_items (user_id, related_resource)
    WHERE related_resource IS NOT NULL;
"""


class OwnerPersonalIntelligenceStorage:
    """Persist owner-personal operational state and review queue items."""

    def __init__(
        self,
        pool: asyncpg.Pool,  # type: ignore[type-arg]
        *,
        schema: str = "owner_personal",
        encryptor: FieldEncryptor | None = None,
    ) -> None:
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]
        self._schema = _validate_schema_identifier(schema)
        self._encryptor = encryptor

    async def ensure_schema(self) -> None:
        """Create owner-personal operational and review tables if needed."""
        ddl = _schema_sql(self._schema)
        async with self._pool.acquire() as conn:
            await conn.execute(ddl)
        log.info("owner_personal_intelligence_schema_ensured", schema=self._schema)

    def _encrypt_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._encryptor is None:
            return value
        return self._encryptor.encrypt_value(value)

    def _decrypt_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._encryptor is None:
            return value
        try:
            return self._encryptor.decrypt_value(value)
        except ValueError:
            return value

    def _operational_item_from_row(self, row: dict[str, Any]) -> PersonalOperationalItem:
        payload = dict(row)
        payload["title"] = self._decrypt_text(payload.pop("title_value", None)) or ""
        payload["detail"] = self._decrypt_text(payload.pop("detail_value", None))
        payload["metadata"] = payload.pop("metadata_json", {})
        return PersonalOperationalItem.from_db_row(payload)

    def _review_item_from_row(self, row: dict[str, Any]) -> PersonalReviewItem:
        payload = dict(row)
        payload["title"] = self._decrypt_text(payload.pop("title_value", None)) or ""
        payload["detail"] = self._decrypt_text(payload.pop("detail_value", None))
        payload["metadata"] = payload.pop("metadata_json", {})
        return PersonalReviewItem.from_db_row(payload)

    async def upsert_operational_item(
        self,
        item: PersonalOperationalItem,
    ) -> PersonalOperationalItem:
        """Insert or update one owner operational-state item."""
        data = item.to_db_row()
        # self._schema is regex-validated before interpolation into SQL identifiers.
        table = f'"{self._schema}".personal_operational_items'
        metadata_json = json.dumps(data["metadata"])
        tags_json = json.dumps(data["tags"])
        title_value = self._encrypt_text(data["title"])
        detail_value = self._encrypt_text(data["detail"])
        args: tuple[Any, ...]

        if item.id is None:
            sql = f"""
                INSERT INTO {table}
                    (user_id, item_type, title_value, detail_value, status, due_at,
                     tags, metadata_json, source, external_ref, completed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11)
                RETURNING *
            """  # nosec B608 - self._schema is regex-validated before interpolation
            args = (
                data["user_id"],
                data["item_type"],
                title_value,
                detail_value,
                data["status"],
                data["due_at"],
                tags_json,
                metadata_json,
                data["source"],
                data["external_ref"],
                data["completed_at"],
            )
        else:
            sql = f"""
                UPDATE {table}
                   SET item_type = $2,
                       title_value = $3,
                       detail_value = $4,
                       status = $5,
                       due_at = $6,
                       tags = $7::jsonb,
                       metadata_json = $8::jsonb,
                       source = $9,
                       external_ref = $10,
                       completed_at = $11,
                       updated_at = now()
                 WHERE id = $1 AND user_id = $12
                RETURNING *
            """  # nosec B608 - self._schema is regex-validated before interpolation
            args = (
                item.id,
                data["item_type"],
                title_value,
                detail_value,
                data["status"],
                data["due_at"],
                tags_json,
                metadata_json,
                data["source"],
                data["external_ref"],
                data["completed_at"],
                data["user_id"],
            )

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        if row is None:
            raise RuntimeError("Upsert personal operational item returned no row")
        return self._operational_item_from_row(dict(row))

    async def list_operational_items(
        self,
        user_id: int,
        *,
        item_type: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 25,
    ) -> list[PersonalOperationalItem]:
        """List owner operational-state items with optional filters."""
        # self._schema is regex-validated before interpolation into SQL identifiers.
        table = f'"{self._schema}".personal_operational_items'
        query = (
            f"SELECT * FROM {table} WHERE user_id = $1"
        )  # nosec B608 # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
        params: list[Any] = [user_id]
        idx = 2

        if item_type:
            query += f" AND item_type = ${idx}"
            params.append(item_type)
            idx += 1
        if status:
            query += f" AND status = ${idx}"
            params.append(status)
            idx += 1
        elif active_only:
            query += f" AND status = ANY(${idx}::text[])"
            params.append(list(_ACTIVE_OPERATIONAL_STATUSES))
            idx += 1

        query += (
            " ORDER BY"
            " CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,"
            " due_at ASC,"
            " updated_at DESC"
            f" LIMIT ${idx}"
        )
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._operational_item_from_row(dict(row)) for row in rows]

    async def upsert_review_item(self, item: PersonalReviewItem) -> PersonalReviewItem:
        """Insert or update one owner review-queue item."""
        data = item.to_db_row()
        # self._schema is regex-validated before interpolation into SQL identifiers.
        table = f'"{self._schema}".personal_review_items'
        metadata_json = json.dumps(data["metadata"])
        title_value = self._encrypt_text(data["title"])
        detail_value = self._encrypt_text(data["detail"])
        args: tuple[Any, ...]

        if item.id is None:
            sql = f"""
                INSERT INTO {table}
                    (user_id, item_type, title_value, detail_value, status, source,
                     related_resource, priority, metadata_json, due_at, resolved_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)
                RETURNING *
            """  # nosec B608 - self._schema is regex-validated before interpolation
            args = (
                data["user_id"],
                data["item_type"],
                title_value,
                detail_value,
                data["status"],
                data["source"],
                data["related_resource"],
                data["priority"],
                metadata_json,
                data["due_at"],
                data["resolved_at"],
            )
        else:
            sql = f"""
                UPDATE {table}
                   SET item_type = $2,
                       title_value = $3,
                       detail_value = $4,
                       status = $5,
                       source = $6,
                       related_resource = $7,
                       priority = $8,
                       metadata_json = $9::jsonb,
                       due_at = $10,
                       resolved_at = $11,
                       updated_at = now()
                 WHERE id = $1 AND user_id = $12
                RETURNING *
            """  # nosec B608 - self._schema is regex-validated before interpolation
            args = (
                item.id,
                data["item_type"],
                title_value,
                detail_value,
                data["status"],
                data["source"],
                data["related_resource"],
                data["priority"],
                metadata_json,
                data["due_at"],
                data["resolved_at"],
                data["user_id"],
            )

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        if row is None:
            raise RuntimeError("Upsert personal review item returned no row")
        return self._review_item_from_row(dict(row))

    async def get_review_item_by_related_resource(
        self,
        user_id: int,
        related_resource: str,
        *,
        source: str | None = None,
        pending_only: bool = False,
    ) -> PersonalReviewItem | None:
        """Fetch the newest review item for one related resource."""

        resource = str(related_resource or "").strip()
        if not resource:
            raise ValueError("related_resource is required")

        table = f'"{self._schema}".personal_review_items'
        query = (
            f"SELECT * FROM {table} WHERE user_id = $1 AND related_resource = $2"
        )  # nosec B608 # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
        params: list[Any] = [user_id, resource]
        idx = 3

        if source:
            query += f" AND source = ${idx}"
            params.append(source)
            idx += 1
        if pending_only:
            query += f" AND status = ${idx}"
            params.append(PersonalReviewItemStatus.PENDING.value)
            idx += 1

        query += " ORDER BY created_at DESC LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        if row is None:
            return None
        return self._review_item_from_row(dict(row))

    async def list_review_items(
        self,
        user_id: int,
        *,
        item_type: str | None = None,
        status: str | None = None,
        pending_only: bool = False,
        limit: int = 25,
    ) -> list[PersonalReviewItem]:
        """List owner review queue items with optional filters."""
        # self._schema is regex-validated before interpolation into SQL identifiers.
        table = f'"{self._schema}".personal_review_items'
        query = (
            f"SELECT * FROM {table} WHERE user_id = $1"
        )  # nosec B608 # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
        params: list[Any] = [user_id]
        idx = 2

        if item_type:
            query += f" AND item_type = ${idx}"
            params.append(item_type)
            idx += 1
        if status:
            query += f" AND status = ${idx}"
            params.append(status)
            idx += 1
        elif pending_only:
            query += f" AND status = ${idx}"
            params.append(PersonalReviewItemStatus.PENDING.value)
            idx += 1

        query += f" ORDER BY priority DESC, created_at DESC LIMIT ${idx}"
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._review_item_from_row(dict(row)) for row in rows]

    async def resolve_review_item(
        self,
        review_item_id: int,
        *,
        user_id: int,
        status: PersonalReviewItemStatus = PersonalReviewItemStatus.RESOLVED,
    ) -> PersonalReviewItem | None:
        """Resolve or dismiss a review item."""
        # self._schema is regex-validated before interpolation into SQL identifiers.
        table = f'"{self._schema}".personal_review_items'
        sql = f"""
            UPDATE {table}
               SET status = $1,
                   resolved_at = now(),
                   updated_at = now()
             WHERE id = $2 AND user_id = $3
            RETURNING *
        """  # nosec B608 - self._schema is regex-validated before interpolation
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, status.value, review_item_id, user_id)
        if row is None:
            return None
        return self._review_item_from_row(dict(row))


async def ensure_owner_personal_intelligence_schema(
    pool: Any,
    *,
    schema: str = "owner_personal",
) -> tuple[str, ...]:
    """Bootstrap owner-personal operational/review tables when a compatible pool is available."""
    if pool is None:
        return ()

    try:
        storage = OwnerPersonalIntelligenceStorage(pool, schema=schema)
        await storage.ensure_schema()
    except AttributeError:
        log.warning(
            "owner_personal_intelligence_schema_bootstrap_skipped",
            reason="pool_missing_acquire",
        )
        return ()
    except TypeError:
        log.warning(
            "owner_personal_intelligence_schema_bootstrap_skipped",
            reason="pool_not_async_context_manager",
        )
        return ()

    return ("personal_operational_items", "personal_review_items")
