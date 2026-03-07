"""Owner-portfolio storage for tenant-derived datasets and owner snapshots."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.portfolio.storage")

_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_schema_identifier(value: str) -> str:
    candidate = value.strip()
    if not _SCHEMA_NAME_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema identifier: {value!r}")
    return candidate


def _schema_sql(owner_portfolio_schema: str) -> str:
    schema = _validate_schema_identifier(owner_portfolio_schema)
    return f"""
CREATE SCHEMA IF NOT EXISTS \"{schema}\";
CREATE TABLE IF NOT EXISTS \"{schema}\".tenant_derived_datasets (
    dataset_id             VARCHAR(64) PRIMARY KEY,
    zetherion_tenant_id    UUID NOT NULL,
    tenant_name            TEXT NOT NULL,
    derivation_kind        TEXT NOT NULL,
    trust_domain           TEXT NOT NULL DEFAULT 'tenant_derived',
    source                 TEXT NOT NULL,
    summary                JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    provenance             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (zetherion_tenant_id, derivation_kind)
);

CREATE INDEX IF NOT EXISTS idx_tenant_derived_datasets_tenant
    ON \"{schema}\".tenant_derived_datasets (zetherion_tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS \"{schema}\".owner_portfolio_tenant_snapshots (
    snapshot_id            VARCHAR(64) PRIMARY KEY,
    zetherion_tenant_id    UUID NOT NULL,
    tenant_name            TEXT NOT NULL,
    derivation_kind        TEXT NOT NULL,
    trust_domain           TEXT NOT NULL DEFAULT 'owner_portfolio',
    source_dataset_id      VARCHAR(64) NOT NULL,
    source                 TEXT NOT NULL,
    summary                JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    provenance             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (zetherion_tenant_id, derivation_kind)
);

CREATE INDEX IF NOT EXISTS idx_owner_portfolio_snapshots_tenant
    ON \"{schema}\".owner_portfolio_tenant_snapshots (zetherion_tenant_id, updated_at DESC);
"""


class PortfolioStorage:
    """Generic storage for tenant-derived datasets and owner-portfolio snapshots."""

    def __init__(
        self,
        *,
        dsn: str,
        min_size: int = 1,
        max_size: int = 5,
        owner_portfolio_schema: str = "owner_portfolio",
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._owner_portfolio_schema = _validate_schema_identifier(owner_portfolio_schema)
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=max(self._min_size, self._max_size),
        )
        await self._ensure_schema()
        log.info(
            "portfolio_storage_initialized",
            owner_portfolio_schema=self._owner_portfolio_schema,
            min_size=self._min_size,
            max_size=self._max_size,
        )

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        log.info("portfolio_storage_closed")

    async def _ensure_schema(self) -> None:
        await self._execute(_schema_sql(self._owner_portfolio_schema))

    async def _execute(self, query: str, *args: Any) -> str:
        if self._pool is None:
            raise RuntimeError("PortfolioStorage not initialized")
        async with self._pool.acquire() as conn:
            status = await conn.execute(query, *args)
            return cast(str, status)

    async def _fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None:
        if self._pool is None:
            raise RuntimeError("PortfolioStorage not initialized")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return cast(Mapping[str, Any] | None, row)

    async def _fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]:
        if self._pool is None:
            raise RuntimeError("PortfolioStorage not initialized")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return cast(list[Mapping[str, Any]], rows)

    async def upsert_tenant_derived_dataset(
        self,
        *,
        zetherion_tenant_id: str,
        tenant_name: str,
        derivation_kind: str,
        source: str,
        summary: dict[str, Any],
        provenance: dict[str, Any] | None,
    ) -> dict[str, Any]:
        schema = self._owner_portfolio_schema
        dataset_id = f"tds_{uuid.uuid4().hex[:24]}"
        row = await self._fetchrow(
            f"""
            INSERT INTO \"{schema}\".tenant_derived_datasets (
                dataset_id,
                zetherion_tenant_id,
                tenant_name,
                derivation_kind,
                source,
                summary,
                provenance
            ) VALUES (
                $1,
                $2::uuid,
                $3,
                $4,
                $5,
                $6::jsonb,
                $7::jsonb
            )
            ON CONFLICT (zetherion_tenant_id, derivation_kind)
            DO UPDATE SET
                tenant_name = EXCLUDED.tenant_name,
                source = EXCLUDED.source,
                summary = EXCLUDED.summary,
                provenance = EXCLUDED.provenance,
                updated_at = now()
            RETURNING dataset_id, zetherion_tenant_id, tenant_name, derivation_kind,
                      trust_domain, source, summary, provenance, created_at, updated_at
            """,  # nosec B608
            dataset_id,
            zetherion_tenant_id,
            tenant_name,
            derivation_kind,
            source,
            json.dumps(summary or {}),
            json.dumps(provenance or {}),
        )
        if row is None:
            raise RuntimeError("Upsert tenant derived dataset returned no row")
        return dict(row)

    async def get_tenant_derived_dataset(
        self,
        *,
        zetherion_tenant_id: str,
        derivation_kind: str,
    ) -> dict[str, Any] | None:
        schema = self._owner_portfolio_schema
        row = await self._fetchrow(
            f"""
            SELECT dataset_id, zetherion_tenant_id, tenant_name, derivation_kind,
                   trust_domain, source, summary, provenance, created_at, updated_at
            FROM \"{schema}\".tenant_derived_datasets
            WHERE zetherion_tenant_id = $1::uuid
              AND derivation_kind = $2
            """,  # nosec B608
            zetherion_tenant_id,
            derivation_kind,
        )
        return dict(row) if row is not None else None

    async def list_tenant_derived_datasets(
        self,
        *,
        derivation_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        schema = self._owner_portfolio_schema
        params: list[Any] = []
        where = ""
        if derivation_kind is not None:
            params.append(derivation_kind)
            where = "WHERE derivation_kind = $1"
        rows = await self._fetch(
            f"""
            SELECT dataset_id, zetherion_tenant_id, tenant_name, derivation_kind,
                   trust_domain, source, summary, provenance, created_at, updated_at
            FROM \"{schema}\".tenant_derived_datasets
            {where}
            ORDER BY tenant_name ASC, updated_at DESC
            """,  # nosec B608
            *params,
        )
        return [dict(row) for row in rows]

    async def upsert_owner_portfolio_snapshot(
        self,
        *,
        zetherion_tenant_id: str,
        tenant_name: str,
        derivation_kind: str,
        source_dataset_id: str,
        source: str,
        summary: dict[str, Any],
        provenance: dict[str, Any] | None,
    ) -> dict[str, Any]:
        schema = self._owner_portfolio_schema
        snapshot_id = f"ops_{uuid.uuid4().hex[:24]}"
        row = await self._fetchrow(
            f"""
            INSERT INTO \"{schema}\".owner_portfolio_tenant_snapshots (
                snapshot_id,
                zetherion_tenant_id,
                tenant_name,
                derivation_kind,
                source_dataset_id,
                source,
                summary,
                provenance
            ) VALUES (
                $1,
                $2::uuid,
                $3,
                $4,
                $5,
                $6,
                $7::jsonb,
                $8::jsonb
            )
            ON CONFLICT (zetherion_tenant_id, derivation_kind)
            DO UPDATE SET
                tenant_name = EXCLUDED.tenant_name,
                source_dataset_id = EXCLUDED.source_dataset_id,
                source = EXCLUDED.source,
                summary = EXCLUDED.summary,
                provenance = EXCLUDED.provenance,
                updated_at = now()
            RETURNING snapshot_id, zetherion_tenant_id, tenant_name, derivation_kind,
                      trust_domain, source_dataset_id, source, summary, provenance,
                      created_at, updated_at
            """,  # nosec B608
            snapshot_id,
            zetherion_tenant_id,
            tenant_name,
            derivation_kind,
            source_dataset_id,
            source,
            json.dumps(summary or {}),
            json.dumps(provenance or {}),
        )
        if row is None:
            raise RuntimeError("Upsert owner portfolio snapshot returned no row")
        return dict(row)

    async def get_owner_portfolio_snapshot(
        self,
        *,
        zetherion_tenant_id: str,
        derivation_kind: str,
    ) -> dict[str, Any] | None:
        schema = self._owner_portfolio_schema
        row = await self._fetchrow(
            f"""
            SELECT snapshot_id, zetherion_tenant_id, tenant_name, derivation_kind,
                   trust_domain, source_dataset_id, source, summary, provenance,
                   created_at, updated_at
            FROM \"{schema}\".owner_portfolio_tenant_snapshots
            WHERE zetherion_tenant_id = $1::uuid
              AND derivation_kind = $2
            """,  # nosec B608
            zetherion_tenant_id,
            derivation_kind,
        )
        return dict(row) if row is not None else None

    async def list_owner_portfolio_snapshots(
        self,
        *,
        derivation_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        schema = self._owner_portfolio_schema
        params: list[Any] = []
        where = ""
        if derivation_kind is not None:
            params.append(derivation_kind)
            where = "WHERE derivation_kind = $1"
        rows = await self._fetch(
            f"""
            SELECT snapshot_id, zetherion_tenant_id, tenant_name, derivation_kind,
                   trust_domain, source_dataset_id, source, summary, provenance,
                   created_at, updated_at
            FROM \"{schema}\".owner_portfolio_tenant_snapshots
            {where}
            ORDER BY tenant_name ASC, updated_at DESC
            """,  # nosec B608
            *params,
        )
        return [dict(row) for row in rows]
