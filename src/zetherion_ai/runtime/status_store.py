"""Shared runtime status storage for long-lived services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg  # type: ignore[import-not-found,import-untyped]


def _encode_details(details: dict[str, Any] | None) -> str:
    return json.dumps(details or {}, sort_keys=True, separators=(",", ":"))


def _row_to_status(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    updated_at = row.get("updated_at")
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    raw_details = row.get("details")
    if isinstance(raw_details, str):
        try:
            details = json.loads(raw_details)
        except json.JSONDecodeError:
            details = {}
    elif isinstance(raw_details, dict):
        details = raw_details
    else:
        details = {}
    return {
        "service_name": row.get("service_name"),
        "status": row.get("status"),
        "summary": row.get("summary"),
        "details": details,
        "release_revision": row.get("release_revision"),
        "instance_id": row.get("instance_id"),
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
    }


class RuntimeStatusStore:
    """Persist cross-service runtime heartbeats in Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._pool = pool

    async def initialize(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_service_status (
                    service_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    summary TEXT,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    release_revision TEXT,
                    instance_id TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    async def upsert_status(
        self,
        *,
        service_name: str,
        status: str,
        summary: str,
        details: dict[str, Any] | None = None,
        release_revision: str | None = None,
        instance_id: str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO runtime_service_status (
                    service_name,
                    status,
                    summary,
                    details,
                    release_revision,
                    instance_id,
                    updated_at
                )
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, now())
                ON CONFLICT (service_name) DO UPDATE
                SET status = EXCLUDED.status,
                    summary = EXCLUDED.summary,
                    details = EXCLUDED.details,
                    release_revision = EXCLUDED.release_revision,
                    instance_id = EXCLUDED.instance_id,
                    updated_at = now()
                """,
                service_name,
                status,
                summary,
                _encode_details(details),
                release_revision,
                instance_id,
            )

    async def get_status(self, service_name: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT service_name,
                       status,
                       summary,
                       details,
                       release_revision,
                       instance_id,
                       updated_at
                FROM runtime_service_status
                WHERE service_name = $1
                """,
                service_name,
            )
        return _row_to_status(row)

    async def list_statuses(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT service_name,
                       status,
                       summary,
                       details,
                       release_revision,
                       instance_id,
                       updated_at
                FROM runtime_service_status
                ORDER BY service_name ASC
                """
            )
        return [status for row in rows if (status := _row_to_status(row)) is not None]
