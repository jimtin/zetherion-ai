"""SQLite storage for profile operational data.

Stores metadata and statistics about profiles (not the actual profile content,
which is encrypted in Qdrant). This includes:
- Profile stats per user
- Update history for debugging/rollback
- Pending confirmations queue
- Inference tier usage tracking
"""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.profile.storage")


@dataclass
class ProfileStats:
    """Statistics about a user's profile."""

    user_id: str
    profile_version: int
    last_updated: datetime
    total_entries: int
    high_confidence_entries: int
    pending_confirmations: int


@dataclass
class ProfileUpdateRecord:
    """Record of a profile update."""

    id: int
    timestamp: datetime
    user_id: str
    profile: str  # 'user' or 'employment'
    field: str
    old_value: Any
    new_value: Any
    confidence: float
    source_tier: int
    confirmed: bool | None  # None = pending


@dataclass
class PendingConfirmation:
    """A pending profile update awaiting user confirmation."""

    id: int
    user_id: str
    update_id: int
    created_at: datetime
    expires_at: datetime
    priority: int


class ProfileStorage:
    """SQLite storage for profile operational data."""

    SCHEMA = """
    -- Profile metadata and statistics (no actual profile content)
    CREATE TABLE IF NOT EXISTS profile_stats (
        user_id TEXT PRIMARY KEY,
        profile_version INTEGER DEFAULT 1,
        last_updated DATETIME,
        total_entries INTEGER DEFAULT 0,
        high_confidence_entries INTEGER DEFAULT 0,
        pending_confirmations INTEGER DEFAULT 0
    );

    -- Update history for debugging and rollback
    CREATE TABLE IF NOT EXISTS profile_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        user_id TEXT NOT NULL,
        profile TEXT NOT NULL,
        field TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        confidence REAL,
        source_tier INTEGER,
        confirmed INTEGER DEFAULT NULL
    );

    -- Pending confirmations queue
    CREATE TABLE IF NOT EXISTS pending_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        update_id INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME,
        priority INTEGER DEFAULT 0,
        FOREIGN KEY (update_id) REFERENCES profile_updates(id)
    );

    -- Inference tier usage tracking (for cost monitoring)
    CREATE TABLE IF NOT EXISTS inference_tier_usage (
        date DATE,
        tier INTEGER,
        invocation_count INTEGER DEFAULT 0,
        PRIMARY KEY (date, tier)
    );

    -- Indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_updates_user ON profile_updates(user_id);
    CREATE INDEX IF NOT EXISTS idx_updates_pending
        ON profile_updates(confirmed) WHERE confirmed IS NULL;
    CREATE INDEX IF NOT EXISTS idx_confirmations_user ON pending_confirmations(user_id);
    CREATE INDEX IF NOT EXISTS idx_confirmations_expires ON pending_confirmations(expires_at);
    """

    def __init__(self, db_path: str = "data/profiles.db"):
        """Initialize the profile storage.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Initialize the database schema."""
        with self._get_connection() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()
        log.info("profile_storage_initialized", db_path=str(self._db_path))

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection.

        Yields:
            SQLite connection with row factory enabled.
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # === Profile Stats ===

    def get_stats(self, user_id: str) -> ProfileStats | None:
        """Get profile stats for a user.

        Args:
            user_id: The user's ID.

        Returns:
            ProfileStats or None if not found.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_stats WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            if not row:
                return None

            return ProfileStats(
                user_id=row["user_id"],
                profile_version=row["profile_version"],
                last_updated=datetime.fromisoformat(row["last_updated"])
                if row["last_updated"]
                else datetime.now(),
                total_entries=row["total_entries"],
                high_confidence_entries=row["high_confidence_entries"],
                pending_confirmations=row["pending_confirmations"],
            )

    def upsert_stats(self, stats: ProfileStats) -> None:
        """Insert or update profile stats.

        Args:
            stats: The profile stats to upsert.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO profile_stats
                    (user_id, profile_version, last_updated, total_entries,
                     high_confidence_entries, pending_confirmations)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    profile_version = excluded.profile_version,
                    last_updated = excluded.last_updated,
                    total_entries = excluded.total_entries,
                    high_confidence_entries = excluded.high_confidence_entries,
                    pending_confirmations = excluded.pending_confirmations
                """,
                (
                    stats.user_id,
                    stats.profile_version,
                    stats.last_updated.isoformat(),
                    stats.total_entries,
                    stats.high_confidence_entries,
                    stats.pending_confirmations,
                ),
            )
            conn.commit()

    # === Profile Updates ===

    def record_update(
        self,
        user_id: str,
        profile: str,
        field: str,
        old_value: Any,
        new_value: Any,
        confidence: float,
        source_tier: int,
    ) -> int:
        """Record a profile update.

        Args:
            user_id: The user's ID.
            profile: 'user' or 'employment'.
            field: The field being updated.
            old_value: The previous value.
            new_value: The new value.
            confidence: Confidence score.
            source_tier: Which inference tier produced this.

        Returns:
            The ID of the inserted record.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO profile_updates
                    (user_id, profile, field, old_value, new_value,
                     confidence, source_tier)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    profile,
                    field,
                    json.dumps(old_value),
                    json.dumps(new_value),
                    confidence,
                    source_tier,
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_recent_updates(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[ProfileUpdateRecord]:
        """Get recent profile updates for a user.

        Args:
            user_id: The user's ID.
            limit: Maximum number of records to return.

        Returns:
            List of update records.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM profile_updates
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

            return [
                ProfileUpdateRecord(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    user_id=row["user_id"],
                    profile=row["profile"],
                    field=row["field"],
                    old_value=json.loads(row["old_value"]) if row["old_value"] else None,
                    new_value=json.loads(row["new_value"]) if row["new_value"] else None,
                    confidence=row["confidence"],
                    source_tier=row["source_tier"],
                    confirmed=row["confirmed"] if row["confirmed"] is not None else None,
                )
                for row in rows
            ]

    def get_pending_updates(self, user_id: str) -> list[ProfileUpdateRecord]:
        """Get pending (unconfirmed) updates for a user.

        Args:
            user_id: The user's ID.

        Returns:
            List of pending update records.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM profile_updates
                WHERE user_id = ? AND confirmed IS NULL
                ORDER BY timestamp DESC
                """,
                (user_id,),
            ).fetchall()

            return [
                ProfileUpdateRecord(
                    id=row["id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    user_id=row["user_id"],
                    profile=row["profile"],
                    field=row["field"],
                    old_value=json.loads(row["old_value"]) if row["old_value"] else None,
                    new_value=json.loads(row["new_value"]) if row["new_value"] else None,
                    confidence=row["confidence"],
                    source_tier=row["source_tier"],
                    confirmed=None,
                )
                for row in rows
            ]

    def confirm_update(self, update_id: int, confirmed: bool) -> None:
        """Mark an update as confirmed or rejected.

        Args:
            update_id: The update's ID.
            confirmed: Whether the update was confirmed.
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE profile_updates SET confirmed = ? WHERE id = ?",
                (1 if confirmed else 0, update_id),
            )
            conn.commit()

    # === Pending Confirmations ===

    def add_pending_confirmation(
        self,
        user_id: str,
        update_id: int,
        expires_at: datetime,
        priority: int = 0,
    ) -> int:
        """Add a pending confirmation.

        Args:
            user_id: The user's ID.
            update_id: The update to confirm.
            expires_at: When to auto-decline.
            priority: Higher = ask sooner.

        Returns:
            The ID of the inserted record.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_confirmations
                    (user_id, update_id, expires_at, priority)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, update_id, expires_at.isoformat(), priority),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_pending_confirmations(
        self,
        user_id: str,
        limit: int = 5,
    ) -> list[PendingConfirmation]:
        """Get pending confirmations for a user.

        Args:
            user_id: The user's ID.
            limit: Maximum number to return.

        Returns:
            List of pending confirmations, highest priority first.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_confirmations
                WHERE user_id = ? AND expires_at > ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (user_id, datetime.now().isoformat(), limit),
            ).fetchall()

            return [
                PendingConfirmation(
                    id=row["id"],
                    user_id=row["user_id"],
                    update_id=row["update_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    priority=row["priority"],
                )
                for row in rows
            ]

    def remove_pending_confirmation(self, confirmation_id: int) -> None:
        """Remove a pending confirmation.

        Args:
            confirmation_id: The confirmation's ID.
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pending_confirmations WHERE id = ?",
                (confirmation_id,),
            )
            conn.commit()

    def cleanup_expired_confirmations(self) -> int:
        """Remove expired confirmations and auto-decline their updates.

        Returns:
            Number of expired confirmations cleaned up.
        """
        with self._get_connection() as conn:
            # Get expired confirmations
            expired = conn.execute(
                """
                SELECT update_id FROM pending_confirmations
                WHERE expires_at <= ?
                """,
                (datetime.now().isoformat(),),
            ).fetchall()

            # Auto-decline the updates
            for row in expired:
                conn.execute(
                    "UPDATE profile_updates SET confirmed = 0 WHERE id = ?",
                    (row["update_id"],),
                )

            # Delete expired confirmations
            result = conn.execute(
                "DELETE FROM pending_confirmations WHERE expires_at <= ?",
                (datetime.now().isoformat(),),
            )
            conn.commit()

            if result.rowcount > 0:
                log.info("expired_confirmations_cleaned", count=result.rowcount)

            return result.rowcount

    # === Inference Tier Usage ===

    def record_tier_usage(self, tier: int) -> None:
        """Record an inference tier invocation.

        Args:
            tier: The tier used (1-4).
        """
        today = datetime.now().date().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO inference_tier_usage (date, tier, invocation_count)
                VALUES (?, ?, 1)
                ON CONFLICT(date, tier) DO UPDATE SET
                    invocation_count = invocation_count + 1
                """,
                (today, tier),
            )
            conn.commit()

    def get_tier_usage(self, days: int = 7) -> dict[int, int]:
        """Get inference tier usage for recent days.

        Args:
            days: Number of days to look back.

        Returns:
            Dictionary mapping tier to total invocations.
        """
        from datetime import timedelta

        start_date = (datetime.now() - timedelta(days=days)).date().isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tier, SUM(invocation_count) as total
                FROM inference_tier_usage
                WHERE date >= ?
                GROUP BY tier
                ORDER BY tier
                """,
                (start_date,),
            ).fetchall()

            return {row["tier"]: row["total"] for row in rows}

    def get_daily_tier_usage(self, days: int = 7) -> list[dict[str, Any]]:
        """Get daily inference tier usage breakdown.

        Args:
            days: Number of days to look back.

        Returns:
            List of daily usage records.
        """
        from datetime import timedelta

        start_date = (datetime.now() - timedelta(days=days)).date().isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT date, tier, invocation_count
                FROM inference_tier_usage
                WHERE date >= ?
                ORDER BY date DESC, tier
                """,
                (start_date,),
            ).fetchall()

            return [
                {
                    "date": row["date"],
                    "tier": row["tier"],
                    "count": row["invocation_count"],
                }
                for row in rows
            ]
