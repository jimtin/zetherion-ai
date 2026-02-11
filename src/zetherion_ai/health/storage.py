"""PostgreSQL storage for health monitoring data.

Follows the same async pattern as SettingsManager and UserManager:
initialise with an asyncpg.Pool, then use async methods for reads/writes.

Tables are created via ``initialize(pool)`` and live in the same
PostgreSQL database as RBAC, settings, and personal understanding data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

log = get_logger("zetherion_ai.health.storage")


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------


class IncidentSeverity(Enum):
    """Severity levels for health incidents."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UpdateStatus(Enum):
    """Status of an update attempt."""

    CHECKING = "checking"
    DOWNLOADING = "downloading"
    APPLYING = "applying"
    VALIDATING = "validating"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class MetricsSnapshot:
    """A point-in-time snapshot of system metrics."""

    timestamp: datetime
    metrics: dict[str, Any]
    anomalies: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "metrics": self.metrics,
            "anomalies": self.anomalies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricsSnapshot:
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if isinstance(data["timestamp"], str)
            else data["timestamp"],
            metrics=data.get("metrics", {}),
            anomalies=data.get("anomalies", {}),
        )


@dataclass
class DailyReport:
    """A daily health analysis report."""

    date: str  # YYYY-MM-DD
    summary: dict[str, Any]
    recommendations: dict[str, Any]
    overall_score: float  # 0-100
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "date": self.date,
            "summary": self.summary,
            "recommendations": self.recommendations,
            "overall_score": self.overall_score,
        }


@dataclass
class HealingAction:
    """A record of a self-healing action taken."""

    timestamp: datetime
    action_type: str
    trigger: str
    result: str  # "success", "failed", "skipped"
    details: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action_type": self.action_type,
            "trigger": self.trigger,
            "result": self.result,
            "details": self.details,
        }


@dataclass
class Incident:
    """A health incident (period of degradation)."""

    start_time: datetime
    severity: IncidentSeverity
    description: str
    end_time: datetime | None = None
    resolved: bool = False
    resolution: str | None = None
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "severity": self.severity.value,
            "description": self.description,
            "resolved": self.resolved,
            "resolution": self.resolution,
        }


@dataclass
class UpdateRecord:
    """A record of an update attempt."""

    timestamp: datetime
    version: str
    previous_version: str
    git_sha: str
    status: UpdateStatus
    health_check_result: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "version": self.version,
            "previous_version": self.previous_version,
            "git_sha": self.git_sha,
            "status": self.status.value,
            "health_check_result": self.health_check_result,
        }


# ------------------------------------------------------------------
# SQL schema
# ------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metrics JSONB NOT NULL,
    anomalies JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS health_daily_reports (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    summary JSONB NOT NULL,
    recommendations JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_score REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS health_healing_actions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action_type TEXT NOT NULL,
    trigger TEXT NOT NULL,
    result TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS health_incidents (
    id SERIAL PRIMARY KEY,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    severity TEXT NOT NULL DEFAULT 'low',
    description TEXT NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolution TEXT
);

CREATE TABLE IF NOT EXISTS update_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version TEXT NOT NULL,
    previous_version TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    health_check_result JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_health_snapshots_ts
    ON health_snapshots (timestamp);
CREATE INDEX IF NOT EXISTS idx_health_daily_reports_date
    ON health_daily_reports (date);
CREATE INDEX IF NOT EXISTS idx_health_healing_ts
    ON health_healing_actions (timestamp);
CREATE INDEX IF NOT EXISTS idx_health_incidents_resolved
    ON health_incidents (resolved);
CREATE INDEX IF NOT EXISTS idx_update_history_ts
    ON update_history (timestamp);
"""


# ------------------------------------------------------------------
# Storage class
# ------------------------------------------------------------------


class HealthStorage:
    """PostgreSQL storage for health monitoring data.

    Follows the same lifecycle as SettingsManager::

        storage = HealthStorage()
        await storage.initialize(pool)
        await storage.save_snapshot(snapshot)
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def initialize(self, pool: asyncpg.Pool) -> None:
        """Create tables and store the connection pool reference."""
        self._pool = pool
        async with pool.acquire() as conn:
            await conn.execute(_SCHEMA)
        log.info("health_storage.initialized")

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def save_snapshot(self, snapshot: MetricsSnapshot) -> int:
        """Insert a metrics snapshot. Returns the row id."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO health_snapshots (timestamp, metrics, anomalies)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                snapshot.timestamp,
                json.dumps(snapshot.metrics),
                json.dumps(snapshot.anomalies),
            )
        return row["id"]  # type: ignore[index,no-any-return]

    async def get_snapshots(
        self,
        start: datetime,
        end: datetime,
        limit: int = 1000,
    ) -> list[MetricsSnapshot]:
        """Get snapshots within a time range."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT id, timestamp, metrics, anomalies
                FROM health_snapshots
                WHERE timestamp >= $1 AND timestamp <= $2
                ORDER BY timestamp DESC
                LIMIT $3
                """,
                start,
                end,
                limit,
            )
        return [
            MetricsSnapshot(
                id=row["id"],
                timestamp=row["timestamp"],
                metrics=json.loads(row["metrics"]),
                anomalies=json.loads(row["anomalies"]),
            )
            for row in rows
        ]

    async def get_latest_snapshot(self) -> MetricsSnapshot | None:
        """Get the most recent snapshot."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT id, timestamp, metrics, anomalies
                FROM health_snapshots
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )
        if row is None:
            return None
        return MetricsSnapshot(
            id=row["id"],
            timestamp=row["timestamp"],
            metrics=json.loads(row["metrics"]),
            anomalies=json.loads(row["anomalies"]),
        )

    # ------------------------------------------------------------------
    # Daily reports
    # ------------------------------------------------------------------

    async def save_daily_report(self, report: DailyReport) -> None:
        """Upsert a daily health report."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                INSERT INTO health_daily_reports (date, summary, recommendations, overall_score)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (date) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    recommendations = EXCLUDED.recommendations,
                    overall_score = EXCLUDED.overall_score
                """,
                report.date,
                json.dumps(report.summary),
                json.dumps(report.recommendations),
                report.overall_score,
            )

    async def get_daily_report(self, date: str) -> DailyReport | None:
        """Get a daily report by date (YYYY-MM-DD)."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                "SELECT id, date, summary, recommendations, overall_score "
                "FROM health_daily_reports WHERE date = $1",
                date,
            )
        if row is None:
            return None
        return DailyReport(
            id=row["id"],
            date=row["date"],
            summary=json.loads(row["summary"]),
            recommendations=json.loads(row["recommendations"]),
            overall_score=row["overall_score"],
        )

    async def get_daily_reports(
        self,
        start_date: str,
        end_date: str,
    ) -> list[DailyReport]:
        """Get daily reports within a date range."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                "SELECT id, date, summary, recommendations, overall_score "
                "FROM health_daily_reports "
                "WHERE date >= $1 AND date <= $2 "
                "ORDER BY date DESC",
                start_date,
                end_date,
            )
        return [
            DailyReport(
                id=row["id"],
                date=row["date"],
                summary=json.loads(row["summary"]),
                recommendations=json.loads(row["recommendations"]),
                overall_score=row["overall_score"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Healing actions
    # ------------------------------------------------------------------

    async def save_healing_action(self, action: HealingAction) -> int:
        """Record a self-healing action. Returns the row id."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO health_healing_actions
                    (timestamp, action_type, trigger, result, details)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                action.timestamp,
                action.action_type,
                action.trigger,
                action.result,
                json.dumps(action.details),
            )
        return row["id"]  # type: ignore[index,no-any-return]

    async def get_healing_actions(
        self,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> list[HealingAction]:
        """Get healing actions within a time range."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT id, timestamp, action_type, trigger, result, details
                FROM health_healing_actions
                WHERE timestamp >= $1 AND timestamp <= $2
                ORDER BY timestamp DESC
                LIMIT $3
                """,
                start,
                end,
                limit,
            )
        return [
            HealingAction(
                id=row["id"],
                timestamp=row["timestamp"],
                action_type=row["action_type"],
                trigger=row["trigger"],
                result=row["result"],
                details=json.loads(row["details"]),
            )
            for row in rows
        ]

    async def get_recent_healing_action(
        self,
        action_type: str,
        within_seconds: int = 300,
    ) -> HealingAction | None:
        """Check if a healing action of this type was taken recently."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT id, timestamp, action_type, trigger, result, details
                FROM health_healing_actions
                WHERE action_type = $1
                  AND timestamp >= NOW() - INTERVAL '1 second' * $2
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                action_type,
                within_seconds,
            )
        if row is None:
            return None
        return HealingAction(
            id=row["id"],
            timestamp=row["timestamp"],
            action_type=row["action_type"],
            trigger=row["trigger"],
            result=row["result"],
            details=json.loads(row["details"]),
        )

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    async def create_incident(self, incident: Incident) -> int:
        """Create a new incident. Returns the row id."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO health_incidents
                    (start_time, severity, description, resolved)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                incident.start_time,
                incident.severity.value,
                incident.description,
                incident.resolved,
            )
        return row["id"]  # type: ignore[index,no-any-return]

    async def resolve_incident(self, incident_id: int, resolution: str) -> None:
        """Mark an incident as resolved."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                """
                UPDATE health_incidents
                SET resolved = TRUE, end_time = NOW(), resolution = $1
                WHERE id = $2
                """,
                resolution,
                incident_id,
            )

    async def get_open_incidents(self) -> list[Incident]:
        """Get all unresolved incidents."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT id, start_time, end_time, severity, description, resolved, resolution
                FROM health_incidents
                WHERE resolved = FALSE
                ORDER BY start_time DESC
                """
            )
        return [
            Incident(
                id=row["id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                severity=IncidentSeverity(row["severity"]),
                description=row["description"],
                resolved=row["resolved"],
                resolution=row["resolution"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Update history
    # ------------------------------------------------------------------

    async def save_update_record(self, record: UpdateRecord) -> int:
        """Record an update attempt. Returns the row id."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                INSERT INTO update_history
                    (timestamp, version, previous_version, git_sha, status, health_check_result)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                record.timestamp,
                record.version,
                record.previous_version,
                record.git_sha,
                record.status.value,
                json.dumps(record.health_check_result),
            )
        return row["id"]  # type: ignore[index,no-any-return]

    async def update_update_status(
        self,
        record_id: int,
        status: UpdateStatus,
        health_check_result: dict[str, Any] | None = None,
    ) -> None:
        """Update the status of an update record."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            if health_check_result is not None:
                await conn.execute(
                    """
                    UPDATE update_history
                    SET status = $1, health_check_result = $2
                    WHERE id = $3
                    """,
                    status.value,
                    json.dumps(health_check_result),
                    record_id,
                )
            else:
                await conn.execute(
                    "UPDATE update_history SET status = $1 WHERE id = $2",
                    status.value,
                    record_id,
                )

    async def get_latest_update(self) -> UpdateRecord | None:
        """Get the most recent update record."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                """
                SELECT id, timestamp, version, previous_version, git_sha,
                       status, health_check_result
                FROM update_history
                ORDER BY timestamp DESC
                LIMIT 1
                """
            )
        if row is None:
            return None
        return UpdateRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            version=row["version"],
            previous_version=row["previous_version"],
            git_sha=row["git_sha"],
            status=UpdateStatus(row["status"]),
            health_check_result=json.loads(row["health_check_result"]),
        )

    async def get_update_history(self, limit: int = 20) -> list[UpdateRecord]:
        """Get recent update history."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                """
                SELECT id, timestamp, version, previous_version, git_sha,
                       status, health_check_result
                FROM update_history
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            UpdateRecord(
                id=row["id"],
                timestamp=row["timestamp"],
                version=row["version"],
                previous_version=row["previous_version"],
                git_sha=row["git_sha"],
                status=UpdateStatus(row["status"]),
                health_check_result=json.loads(row["health_check_result"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def prune_old_snapshots(self, days: int = 30) -> int:
        """Delete snapshots older than N days. Returns count deleted."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result = await conn.execute(
                "DELETE FROM health_snapshots WHERE timestamp < NOW() - INTERVAL '1 day' * $1",
                days,
            )
        count = int(result.split()[-1]) if result else 0
        log.info("health_storage.pruned_snapshots", days=days, deleted=count)
        return count
