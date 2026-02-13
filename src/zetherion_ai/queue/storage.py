"""PostgreSQL-backed storage layer for the priority message queue.

Provides atomic enqueue, dequeue (with ``FOR UPDATE SKIP LOCKED``), retry
with exponential back-off, stale-item recovery, and housekeeping purges.
Follows the same ``asyncpg.Pool`` patterns used by
:class:`~zetherion_ai.discord.user_manager.UserManager`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-not-found,import-untyped]

from zetherion_ai.logging import get_logger
from zetherion_ai.queue.models import QueueItem

log = get_logger("zetherion_ai.queue.storage")

# Exponential back-off delays (seconds) indexed by attempt number (0-based).
_RETRY_BACKOFF_SECONDS: list[int] = [5, 30, 300]


def _row_to_item(row: asyncpg.Record) -> QueueItem:
    """Convert an ``asyncpg.Record`` to a :class:`QueueItem`."""
    return QueueItem(
        id=row["id"],
        priority=row["priority"],
        status=row["status"],
        task_type=row["task_type"],
        user_id=row["user_id"],
        channel_id=row["channel_id"],
        payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        last_error=row["last_error"],
        worker_id=row["worker_id"],
        created_at=row["created_at"],
        scheduled_for=row["scheduled_for"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        correlation_id=row["correlation_id"],
        parent_id=row["parent_id"],
    )


class QueueStorage:
    """PostgreSQL storage backend for the priority message queue.

    All public methods acquire connections from the pool and release them
    automatically.  Mutations use explicit transactions where atomicity
    across multiple statements is required.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        """Initialise with an existing asyncpg connection pool.

        Args:
            pool: An ``asyncpg.Pool`` instance (shared with the rest of the
                application).
        """
        self._pool = pool

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(self, item: QueueItem) -> UUID:
        """Insert a single item into the queue.

        Args:
            item: The queue item to insert.

        Returns:
            The UUID of the inserted item.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO message_queue
                    (id, priority, status, task_type, user_id, channel_id,
                     payload, attempt_count, max_attempts, last_error,
                     worker_id, scheduled_for, correlation_id, parent_id)
                VALUES ($1, $2, $3, $4, $5, $6,
                        $7::jsonb, $8, $9, $10,
                        $11, $12, $13, $14)
                RETURNING id
                """,
                item.id,
                item.priority,
                item.status,
                item.task_type,
                item.user_id,
                item.channel_id,
                json.dumps(item.payload),
                item.attempt_count,
                item.max_attempts,
                item.last_error,
                item.worker_id,
                item.scheduled_for,
                item.correlation_id,
                item.parent_id,
            )
        item_id: UUID = row["id"]  # type: ignore[index]
        log.debug(
            "queue_item_enqueued",
            item_id=str(item_id),
            priority=item.priority,
            task_type=item.task_type,
        )
        return item_id

    async def enqueue_batch(self, items: list[QueueItem]) -> list[UUID]:
        """Insert multiple items into the queue in a single transaction.

        Args:
            items: List of queue items to insert.

        Returns:
            List of UUIDs for the inserted items.
        """
        if not items:
            return []

        ids: list[UUID] = []
        async with self._pool.acquire() as conn, conn.transaction():
            for item in items:
                row = await conn.fetchrow(
                    """
                    INSERT INTO message_queue
                        (id, priority, status, task_type, user_id, channel_id,
                         payload, attempt_count, max_attempts, last_error,
                         worker_id, scheduled_for, correlation_id, parent_id)
                    VALUES ($1, $2, $3, $4, $5, $6,
                            $7::jsonb, $8, $9, $10,
                            $11, $12, $13, $14)
                    RETURNING id
                    """,
                    item.id,
                    item.priority,
                    item.status,
                    item.task_type,
                    item.user_id,
                    item.channel_id,
                    json.dumps(item.payload),
                    item.attempt_count,
                    item.max_attempts,
                    item.last_error,
                    item.worker_id,
                    item.scheduled_for,
                    item.correlation_id,
                    item.parent_id,
                )
                ids.append(row["id"])  # type: ignore[index]
        log.debug("queue_batch_enqueued", count=len(ids))
        return ids

    # ------------------------------------------------------------------
    # Dequeue
    # ------------------------------------------------------------------

    async def dequeue(
        self,
        priority_min: int = 0,
        priority_max: int = 3,
        worker_id: str = "",
    ) -> QueueItem | None:
        """Atomically claim the highest-priority ready item.

        Uses ``FOR UPDATE SKIP LOCKED`` so multiple workers can dequeue
        concurrently without contention.

        Args:
            priority_min: Minimum priority value to consider (inclusive).
            priority_max: Maximum priority value to consider (inclusive).
            worker_id: Identifier of the claiming worker.

        Returns:
            The claimed :class:`QueueItem`, or ``None`` if the queue is empty.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE message_queue
                SET status       = 'processing',
                    worker_id    = $1,
                    started_at   = now(),
                    attempt_count = attempt_count + 1
                WHERE id = (
                    SELECT id FROM message_queue
                    WHERE status = 'queued'
                      AND scheduled_for <= now()
                      AND priority >= $2
                      AND priority <= $3
                    ORDER BY priority ASC, scheduled_for ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                worker_id,
                priority_min,
                priority_max,
            )
        if row is None:
            return None
        item = _row_to_item(row)
        log.debug(
            "queue_item_dequeued",
            item_id=str(item.id),
            priority=item.priority,
            worker_id=worker_id,
        )
        return item

    # ------------------------------------------------------------------
    # Complete / Fail
    # ------------------------------------------------------------------

    async def complete(self, item_id: UUID) -> None:
        """Mark an item as successfully completed.

        Args:
            item_id: The UUID of the item to complete.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE message_queue
                SET status       = 'completed',
                    completed_at = now()
                WHERE id = $1
                """,
                item_id,
            )
        log.debug("queue_item_completed", item_id=str(item_id))

    async def fail(self, item_id: UUID, error: str) -> None:
        """Record a processing failure for an item.

        If the item has exhausted its maximum attempts it is moved to
        ``DEAD`` status.  Otherwise it is re-queued with an exponential
        back-off delay applied to ``scheduled_for``.

        Args:
            item_id: The UUID of the failed item.
            error: Human-readable error description.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT attempt_count, max_attempts FROM message_queue WHERE id = $1",
                item_id,
            )
            if row is None:
                log.warning("queue_fail_item_not_found", item_id=str(item_id))
                return

            attempt_count: int = row["attempt_count"]
            max_attempts: int = row["max_attempts"]

            if attempt_count >= max_attempts:
                # Move to dead-letter state
                await conn.execute(
                    """
                    UPDATE message_queue
                    SET status     = 'dead',
                        last_error = $2,
                        completed_at = now()
                    WHERE id = $1
                    """,
                    item_id,
                    error,
                )
                log.info(
                    "queue_item_dead",
                    item_id=str(item_id),
                    attempts=attempt_count,
                    error=error,
                )
            else:
                # Exponential back-off: index clamped to length of delay list
                backoff_idx = min(max(attempt_count - 1, 0), len(_RETRY_BACKOFF_SECONDS) - 1)
                delay = _RETRY_BACKOFF_SECONDS[backoff_idx]
                next_run = datetime.now(tz=UTC) + timedelta(seconds=delay)

                await conn.execute(
                    """
                    UPDATE message_queue
                    SET status        = 'queued',
                        last_error    = $2,
                        scheduled_for = $3,
                        worker_id     = NULL,
                        started_at    = NULL
                    WHERE id = $1
                    """,
                    item_id,
                    error,
                    next_run,
                )
                log.info(
                    "queue_item_requeued",
                    item_id=str(item_id),
                    attempt=attempt_count,
                    backoff_seconds=delay,
                )

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    async def requeue_stale(self, timeout_seconds: int = 300) -> int:
        """Re-queue items stuck in ``processing`` beyond the timeout.

        Args:
            timeout_seconds: Seconds after which a processing item is
                considered stale.

        Returns:
            Number of items re-queued.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=timeout_seconds)
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(
                """
                UPDATE message_queue
                SET status     = 'queued',
                    worker_id  = NULL,
                    started_at = NULL
                WHERE status = 'processing'
                  AND started_at < $1
                """,
                cutoff,
            )
        # asyncpg returns e.g. "UPDATE 3"
        count = int(result.split()[-1])
        if count > 0:
            log.info("queue_stale_requeued", count=count, timeout_seconds=timeout_seconds)
        return count

    async def get_status_counts(self) -> dict[str, int]:
        """Return a mapping of ``{status: count}`` for all queue items.

        Returns:
            Dictionary with status strings as keys and counts as values.
        """
        async with self._pool.acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(
                "SELECT status, count(*)::int AS cnt FROM message_queue GROUP BY status"
            )
        return {row["status"]: row["cnt"] for row in rows}

    async def purge_completed(self, older_than_hours: int = 24) -> int:
        """Delete completed items older than the given threshold.

        Args:
            older_than_hours: Age in hours after which completed items are
                purged.

        Returns:
            Number of rows deleted.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(hours=older_than_hours)
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(
                """
                DELETE FROM message_queue
                WHERE status = 'completed'
                  AND completed_at < $1
                """,
                cutoff,
            )
        count = int(result.split()[-1])
        if count > 0:
            log.info("queue_completed_purged", count=count, older_than_hours=older_than_hours)
        return count

    async def purge_dead(self, older_than_days: int = 7) -> int:
        """Delete dead-letter items older than the given threshold.

        Args:
            older_than_days: Age in days after which dead items are purged.

        Returns:
            Number of rows deleted.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=older_than_days)
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(
                """
                DELETE FROM message_queue
                WHERE status = 'dead'
                  AND completed_at < $1
                """,
                cutoff,
            )
        count = int(result.split()[-1])
        if count > 0:
            log.info("queue_dead_purged", count=count, older_than_days=older_than_days)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Execute *query* and return the first column of the first row."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)
