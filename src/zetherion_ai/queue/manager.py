"""Queue manager — worker pool orchestration and lifecycle.

Spawns ``asyncio.Task`` workers that poll :class:`QueueStorage` for items,
dispatch them to :class:`QueueProcessors`, and handle completion / failure.

Supports graceful shutdown: stop accepting, drain in-flight items, re-queue
anything still stuck in ``processing``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import UUID

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.queue.models import QueueItem, QueuePriority, QueueTaskType
from zetherion_ai.queue.processors import QueueProcessors
from zetherion_ai.queue.storage import QueueStorage

log = get_logger("zetherion_ai.queue.manager")

# Graceful shutdown: max seconds to wait for in-flight items before force-stop.
_DRAIN_TIMEOUT_SECONDS = 30

# How often the housekeeping loop runs (stale re-queue + purge).
_HOUSEKEEPING_INTERVAL_SECONDS = 60


class QueueManager:
    """Orchestrates worker tasks that consume from the priority queue.

    Workers are plain ``asyncio.Task`` objects — no separate processes.
    Two pools exist:

    * **Interactive** (P0 / P1) — fast poll interval, latency-sensitive.
    * **Background** (P2 / P3) — slower poll, throughput-oriented.
    """

    def __init__(
        self,
        storage: QueueStorage,
        processors: QueueProcessors,
    ) -> None:
        self._storage = storage
        self._processors = processors
        self._workers: list[asyncio.Task[None]] = []
        self._housekeeping_task: asyncio.Task[None] | None = None
        self._running = False
        self._draining = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn worker tasks and the housekeeping loop."""
        if self._running:
            log.warning("queue_manager_already_running")
            return

        settings = get_settings()
        self._running = True

        # Interactive workers (P0/P1)
        for i in range(settings.queue_interactive_workers):
            task = asyncio.create_task(
                self._worker_loop(
                    name=f"interactive-{i}",
                    priority_min=QueuePriority.INTERACTIVE,
                    priority_max=QueuePriority.NEAR_INTERACTIVE,
                    poll_ms=settings.queue_poll_interval_ms,
                ),
            )
            self._workers.append(task)

        # Background workers (P2/P3)
        for i in range(settings.queue_background_workers):
            task = asyncio.create_task(
                self._worker_loop(
                    name=f"background-{i}",
                    priority_min=QueuePriority.SCHEDULED,
                    priority_max=QueuePriority.BULK,
                    poll_ms=settings.queue_background_poll_ms,
                ),
            )
            self._workers.append(task)

        # Housekeeping
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop())

        log.info(
            "queue_manager_started",
            interactive_workers=settings.queue_interactive_workers,
            background_workers=settings.queue_background_workers,
        )

    async def stop(self) -> None:
        """Gracefully stop all workers.

        1. Stop accepting new dequeue attempts (``_draining = True``).
        2. Wait up to ``_DRAIN_TIMEOUT_SECONDS`` for in-flight items.
        3. Cancel remaining tasks.
        4. Re-queue any items stuck in ``processing``.
        """
        if not self._running:
            return

        log.info("queue_manager_stopping")
        self._draining = True
        self._running = False

        # Cancel housekeeping first
        if self._housekeeping_task and not self._housekeeping_task.done():
            self._housekeeping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._housekeeping_task

        # Give workers time to finish current items
        if self._workers:
            _, pending = await asyncio.wait(
                self._workers,
                timeout=_DRAIN_TIMEOUT_SECONDS,
            )
            # Cancel anything still running
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self._workers.clear()

        # Re-queue stale items that were mid-flight
        try:
            requeued = await self._storage.requeue_stale(timeout_seconds=0)
            if requeued:
                log.info("queue_shutdown_requeued", count=requeued)
        except Exception:
            log.exception("queue_shutdown_requeue_failed")

        self._draining = False
        log.info("queue_manager_stopped")

    async def enqueue(
        self,
        *,
        task_type: str | QueueTaskType,
        user_id: int,
        channel_id: int | None = None,
        payload: dict[str, Any],
        priority: int = QueuePriority.INTERACTIVE,
        correlation_id: str | None = None,
        parent_id: UUID | None = None,
    ) -> UUID:
        """Create and insert a queue item.

        This is the main entry point for code that wants to push work
        into the queue (e.g. ``bot.on_message``, heartbeat scheduler).

        Args:
            task_type: One of :class:`QueueTaskType` values.
            user_id: Discord user ID.
            channel_id: Discord channel ID (optional).
            payload: JSON-serialisable task data.
            priority: Queue priority (0 = highest).
            correlation_id: Optional correlation ID.
            parent_id: Optional parent item UUID.

        Returns:
            The UUID of the enqueued item.
        """
        settings = get_settings()
        item = QueueItem(
            priority=priority,
            task_type=str(task_type.value)
            if isinstance(task_type, QueueTaskType)
            else str(task_type),
            user_id=user_id,
            channel_id=channel_id,
            payload=payload,
            max_attempts=settings.queue_max_retry_attempts,
            correlation_id=correlation_id,
            parent_id=parent_id,
        )
        item_id = await self._storage.enqueue(item)
        log.debug(
            "queue_item_submitted",
            item_id=str(item_id),
            task_type=item.task_type,
            priority=priority,
        )
        return item_id

    @property
    def is_running(self) -> bool:
        """Whether the manager is running."""
        return self._running

    async def get_status(self) -> dict[str, Any]:
        """Return queue status counts + worker info."""
        counts = await self._storage.get_status_counts()
        return {
            "running": self._running,
            "draining": self._draining,
            "workers": len(self._workers),
            "status_counts": counts,
        }

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(
        self,
        name: str,
        priority_min: int,
        priority_max: int,
        poll_ms: int,
    ) -> None:
        """Poll for items and process them.

        Args:
            name: Human-readable worker name for logging.
            priority_min: Minimum priority value this worker handles.
            priority_max: Maximum priority value this worker handles.
            poll_ms: Milliseconds between polls when queue is empty.
        """
        log.debug(
            "worker_started",
            worker=name,
            priority_min=priority_min,
            priority_max=priority_max,
        )
        poll_seconds = poll_ms / 1000.0

        while self._running:
            try:
                item = await self._storage.dequeue(
                    priority_min=priority_min,
                    priority_max=priority_max,
                    worker_id=name,
                )

                if item is None:
                    # Queue empty — back off
                    await asyncio.sleep(poll_seconds)
                    continue

                await self._process_item(item, worker_name=name)

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("worker_error", worker=name)
                await asyncio.sleep(poll_seconds)

        log.debug("worker_stopped", worker=name)

    async def _process_item(self, item: QueueItem, worker_name: str) -> None:
        """Run a single item through the processor and record outcome."""
        log.debug(
            "processing_item",
            item_id=str(item.id),
            task_type=item.task_type,
            worker=worker_name,
        )

        result = await self._processors.process(item.task_type, item.payload)

        if result.success:
            await self._storage.complete(item.id)
            log.debug("item_completed", item_id=str(item.id), worker=worker_name)
        else:
            await self._storage.fail(item.id, result.error or "Unknown error")
            log.warning(
                "item_failed",
                item_id=str(item.id),
                error=result.error,
                worker=worker_name,
            )

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    async def _housekeeping_loop(self) -> None:
        """Periodically re-queue stale items and purge old completed ones."""
        while self._running:
            try:
                await asyncio.sleep(_HOUSEKEEPING_INTERVAL_SECONDS)
                if not self._running:
                    break

                settings = get_settings()

                # Re-queue items stuck in processing
                await self._storage.requeue_stale(
                    timeout_seconds=settings.queue_stale_timeout_seconds,
                )

                # Purge completed items older than 24h
                await self._storage.purge_completed(older_than_hours=24)

                # Purge dead-letter items older than 7 days
                await self._storage.purge_dead(older_than_days=7)

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("housekeeping_error")
