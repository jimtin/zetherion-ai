"""PostgreSQL storage for the central telemetry receiver.

Tables are only created when ``TELEMETRY_CENTRAL_MODE=true``.
Follows the same asyncpg.Pool pattern as HealthStorage.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.telemetry.models import (
    InstanceRegistration,
    TelemetryConsent,
    TelemetryReport,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

log = get_logger("zetherion_ai.telemetry.storage")

# ------------------------------------------------------------------
# DDL
# ------------------------------------------------------------------

_CREATE_INSTANCES_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry_instances (
    instance_id  TEXT PRIMARY KEY,
    api_key_hash TEXT NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_version TEXT NOT NULL DEFAULT '',
    consent_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""

_CREATE_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry_reports (
    id           BIGSERIAL PRIMARY KEY,
    instance_id  TEXT NOT NULL REFERENCES telemetry_instances(instance_id) ON DELETE CASCADE,
    timestamp    TIMESTAMPTZ NOT NULL,
    version      TEXT NOT NULL,
    report_json  JSONB NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_reports_instance
    ON telemetry_reports (instance_id, timestamp DESC);
"""

_CREATE_AGGREGATES_TABLE = """
CREATE TABLE IF NOT EXISTS fleet_aggregates (
    id              BIGSERIAL PRIMARY KEY,
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    metric_name     TEXT NOT NULL,
    aggregation_json JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fleet_aggregates_period
    ON fleet_aggregates (period_start, metric_name);
"""


class TelemetryStorage:
    """Central-instance storage for telemetry data."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def initialize(self, pool: asyncpg.Pool) -> None:
        """Create tables and store the connection pool."""
        self._pool = pool
        async with pool.acquire() as conn:
            await conn.execute(_CREATE_INSTANCES_TABLE)
            await conn.execute(_CREATE_REPORTS_TABLE)
            await conn.execute(_CREATE_AGGREGATES_TABLE)
        log.info("telemetry_storage_initialized")

    # ------------------------------------------------------------------
    # Instance management
    # ------------------------------------------------------------------

    async def register_instance(self, registration: InstanceRegistration) -> None:
        """Insert or update an instance registration."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO telemetry_instances
                    (instance_id, api_key_hash, first_seen, last_seen,
                     current_version, consent_json)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_seen = EXCLUDED.last_seen,
                    current_version = EXCLUDED.current_version,
                    consent_json = EXCLUDED.consent_json
                """,
                registration.instance_id,
                registration.api_key_hash,
                registration.first_seen,
                registration.last_seen,
                registration.current_version,
                json.dumps(registration.consent.to_dict()),
            )

    async def get_instance(self, instance_id: str) -> InstanceRegistration | None:
        """Fetch a single instance registration."""
        if self._pool is None:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM telemetry_instances WHERE instance_id = $1",
                instance_id,
            )
        if row is None:
            return None
        return InstanceRegistration(
            instance_id=row["instance_id"],
            api_key_hash=row["api_key_hash"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            current_version=row["current_version"],
            consent=TelemetryConsent.from_dict(
                json.loads(row["consent_json"]) if row["consent_json"] else {}
            ),
        )

    async def list_instances(self) -> list[dict[str, Any]]:
        """Return all registered instances as dicts."""
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM telemetry_instances ORDER BY last_seen DESC")
        return [dict(r) for r in rows]

    async def delete_instance(self, instance_id: str) -> bool:
        """Remove an instance and all its reports (CASCADE)."""
        if self._pool is None:
            return False
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM telemetry_instances WHERE instance_id = $1",
                instance_id,
            )
        return bool(result == "DELETE 1")

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def save_report(self, report: TelemetryReport) -> None:
        """Persist an inbound telemetry report."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO telemetry_reports
                    (instance_id, timestamp, version, report_json)
                VALUES ($1, $2, $3, $4)
                """,
                report.instance_id,
                datetime.fromisoformat(report.timestamp),
                report.version,
                json.dumps(report.metrics),
            )
        # Touch last_seen on the instance
        await self._touch_instance(report.instance_id, report.version)

    async def get_reports(
        self,
        instance_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query stored reports with optional filters."""
        if self._pool is None:
            return []

        clauses: list[str] = []
        params: list[Any] = []
        idx = 1

        if instance_id:
            clauses.append(f"instance_id = ${idx}")
            params.append(instance_id)
            idx += 1

        if since:
            clauses.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM telemetry_reports {where} ORDER BY timestamp DESC LIMIT ${idx}"  # nosec B608
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Fleet Aggregates
    # ------------------------------------------------------------------

    async def save_aggregate(
        self,
        period_start: datetime,
        period_end: datetime,
        metric_name: str,
        aggregation: dict[str, Any],
    ) -> None:
        """Store a fleet-wide aggregate."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fleet_aggregates
                    (period_start, period_end, metric_name, aggregation_json)
                VALUES ($1, $2, $3, $4)
                """,
                period_start,
                period_end,
                metric_name,
                json.dumps(aggregation),
            )

    async def get_aggregates(
        self,
        metric_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query fleet aggregates."""
        if self._pool is None:
            return []

        if metric_name:
            query = """
                SELECT * FROM fleet_aggregates
                WHERE metric_name = $1
                ORDER BY period_start DESC
                LIMIT $2
            """
            params: list[Any] = [metric_name, limit]
        else:
            query = """
                SELECT * FROM fleet_aggregates
                ORDER BY period_start DESC
                LIMIT $1
            """
            params = [limit]

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _touch_instance(self, instance_id: str, version: str) -> None:
        """Update last_seen and version on an instance."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE telemetry_instances
                SET last_seen = NOW(), current_version = $2
                WHERE instance_id = $1
                """,
                instance_id,
                version,
            )
