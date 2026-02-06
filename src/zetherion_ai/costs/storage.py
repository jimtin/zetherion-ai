"""SQLite storage for cost tracking.

Stores usage records and model metadata in a lightweight SQLite database.
The database is created at `data/costs.db` by default.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.costs.storage")

# SQL schema
_SCHEMA = """
-- Usage records for each API call
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT,
    user_id TEXT,
    tokens_input INTEGER NOT NULL,
    tokens_output INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    cost_estimated BOOLEAN DEFAULT FALSE,
    latency_ms INTEGER,
    rate_limit_hit BOOLEAN DEFAULT FALSE,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT
);

-- Model metadata (discovered models)
CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    tier TEXT NOT NULL,
    context_window INTEGER,
    requests_per_minute INTEGER,
    tokens_per_minute INTEGER,
    created_at DATETIME,
    discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deprecated BOOLEAN DEFAULT FALSE,
    deprecated_at DATETIME
);

-- Daily aggregates for fast reporting
CREATE TABLE IF NOT EXISTS daily_costs (
    date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    total_tokens_input INTEGER DEFAULT 0,
    total_tokens_output INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0,
    request_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, provider, model)
);

-- Indices for common queries
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(user_id);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_costs(date);
"""


@dataclass
class UsageRecord:
    """A single API usage record."""

    provider: str
    model: str
    tokens_input: int
    tokens_output: int
    cost_usd: float
    cost_estimated: bool = False
    task_type: str | None = None
    user_id: str | None = None
    latency_ms: int | None = None
    rate_limit_hit: bool = False
    success: bool = True
    error_message: str | None = None
    timestamp: datetime | None = None
    id: int | None = None


class CostStorage:
    """SQLite storage for cost tracking data.

    Thread-safe via connection-per-operation pattern.
    """

    def __init__(self, db_path: str | Path = "data/costs.db"):
        """Initialize the cost storage.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        log.info("database_initialized", path=str(self._db_path))

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record_usage(self, record: UsageRecord) -> int:
        """Record a usage event.

        Args:
            record: The usage record to store.

        Returns:
            The ID of the inserted record.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO usage (
                    provider, model, task_type, user_id,
                    tokens_input, tokens_output, cost_usd, cost_estimated,
                    latency_ms, rate_limit_hit, success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.provider,
                    record.model,
                    record.task_type,
                    record.user_id,
                    record.tokens_input,
                    record.tokens_output,
                    record.cost_usd,
                    record.cost_estimated,
                    record.latency_ms,
                    record.rate_limit_hit,
                    record.success,
                    record.error_message,
                ),
            )
            conn.commit()

            # Update daily aggregate
            self._update_daily_aggregate(conn, record)

            return cursor.lastrowid or 0

    def _update_daily_aggregate(
        self,
        conn: sqlite3.Connection,
        record: UsageRecord,
    ) -> None:
        """Update the daily aggregate for this record."""
        date = datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO daily_costs (
                date, provider, model,
                total_tokens_input, total_tokens_output,
                total_cost_usd, request_count, error_count
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT (date, provider, model) DO UPDATE SET
                total_tokens_input = total_tokens_input + excluded.total_tokens_input,
                total_tokens_output = total_tokens_output + excluded.total_tokens_output,
                total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                request_count = request_count + 1,
                error_count = error_count + excluded.error_count
            """,
            (
                date,
                record.provider,
                record.model,
                record.tokens_input,
                record.tokens_output,
                record.cost_usd,
                0 if record.success else 1,
            ),
        )
        conn.commit()

    def get_usage_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        provider: str | None = None,
        user_id: str | None = None,
    ) -> list[UsageRecord]:
        """Get usage records within a date range.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            provider: Optional provider filter.
            user_id: Optional user filter.

        Returns:
            List of UsageRecord objects.
        """
        query = """
            SELECT * FROM usage
            WHERE timestamp >= ? AND timestamp <= ?
        """
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " ORDER BY timestamp DESC"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [self._row_to_record(row) for row in rows]

    def get_daily_summary(
        self,
        date: str,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get daily cost summary.

        Args:
            date: Date in YYYY-MM-DD format.
            provider: Optional provider filter.

        Returns:
            List of summary dicts with provider, model, costs, counts.
        """
        query = "SELECT * FROM daily_costs WHERE date = ?"
        params: list[Any] = [date]

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def get_total_cost_by_provider(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, float]:
        """Get total cost per provider for a date range.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.

        Returns:
            Dict mapping provider to total cost USD.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT provider, SUM(cost_usd) as total
                FROM usage
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY provider
                """,
                (start_date.isoformat(), end_date.isoformat()),
            )
            rows = cursor.fetchall()

        return {row["provider"]: row["total"] or 0.0 for row in rows}

    def get_total_cost(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> float:
        """Get total cost across all providers.

        Args:
            start_date: Optional start date filter.
            end_date: Optional end date filter.

        Returns:
            Total cost in USD.
        """
        query = "SELECT SUM(cost_usd) as total FROM usage"
        params: list[Any] = []

        if start_date:
            query += " WHERE timestamp >= ?"
            params.append(start_date.isoformat())

        if end_date:
            if start_date:
                query += " AND timestamp <= ?"
            else:
                query += " WHERE timestamp <= ?"
            params.append(end_date.isoformat())

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()

        return row["total"] or 0.0 if row else 0.0

    def get_rate_limit_count(
        self,
        start_date: datetime,
        end_date: datetime,
        provider: str | None = None,
    ) -> int:
        """Get count of rate limit hits in a date range.

        Args:
            start_date: Start of the date range.
            end_date: End of the date range.
            provider: Optional provider filter.

        Returns:
            Count of rate limit hits.
        """
        query = """
            SELECT COUNT(*) as count FROM usage
            WHERE timestamp >= ? AND timestamp <= ?
            AND rate_limit_hit = TRUE
        """
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()

        return row["count"] or 0 if row else 0

    def save_model(
        self,
        model_id: str,
        provider: str,
        tier: str,
        context_window: int | None = None,
        requests_per_minute: int | None = None,
        tokens_per_minute: int | None = None,
    ) -> None:
        """Save or update model metadata.

        Args:
            model_id: The model identifier.
            provider: The provider name.
            tier: The tier classification.
            context_window: Optional context window size.
            requests_per_minute: Optional rate limit.
            tokens_per_minute: Optional token rate limit.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO models (
                    model_id, provider, tier, context_window,
                    requests_per_minute, tokens_per_minute
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (model_id) DO UPDATE SET
                    provider = excluded.provider,
                    tier = excluded.tier,
                    context_window = excluded.context_window,
                    requests_per_minute = excluded.requests_per_minute,
                    tokens_per_minute = excluded.tokens_per_minute
                """,
                (
                    model_id,
                    provider,
                    tier,
                    context_window,
                    requests_per_minute,
                    tokens_per_minute,
                ),
            )
            conn.commit()

    def mark_model_deprecated(self, model_id: str) -> None:
        """Mark a model as deprecated.

        Args:
            model_id: The model to deprecate.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE models
                SET deprecated = TRUE, deprecated_at = CURRENT_TIMESTAMP
                WHERE model_id = ?
                """,
                (model_id,),
            )
            conn.commit()

    def get_models(
        self,
        provider: str | None = None,
        include_deprecated: bool = False,
    ) -> list[dict[str, Any]]:
        """Get stored model metadata.

        Args:
            provider: Optional provider filter.
            include_deprecated: Whether to include deprecated models.

        Returns:
            List of model metadata dicts.
        """
        query = "SELECT * FROM models WHERE 1=1"
        params: list[Any] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if not include_deprecated:
            query += " AND deprecated = FALSE"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> UsageRecord:
        """Convert a database row to a UsageRecord."""
        return UsageRecord(
            id=row["id"],
            provider=row["provider"],
            model=row["model"],
            task_type=row["task_type"],
            user_id=row["user_id"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_usd=row["cost_usd"],
            cost_estimated=bool(row["cost_estimated"]),
            latency_ms=row["latency_ms"],
            rate_limit_hit=bool(row["rate_limit_hit"]),
            success=bool(row["success"]),
            error_message=row["error_message"],
            timestamp=datetime.fromisoformat(row["timestamp"]) if row["timestamp"] else None,
        )

    def vacuum(self) -> None:
        """Optimize the database by running VACUUM."""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
        log.info("database_vacuumed")
