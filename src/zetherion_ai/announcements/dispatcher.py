"""Dispatch worker for queued announcement deliveries."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from zetherion_ai.announcements.storage import (
    AnnouncementDelivery,
    AnnouncementEvent,
    AnnouncementRepository,
)
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.announcements.dispatcher")


@dataclass(slots=True)
class AnnouncementDispatchError(RuntimeError):
    """Structured delivery failure for retry/terminal routing."""

    code: str
    detail: str | None = None
    retryable: bool = True


class AnnouncementChannelAdapter(Protocol):
    """Channel adapter contract for announcement delivery."""

    async def send(self, event: AnnouncementEvent) -> None:
        """Send a single announcement event."""


class AnnouncementDispatcher:
    """Poll due deliveries and route them through one adapter path."""

    def __init__(
        self,
        repository: AnnouncementRepository,
        adapter: AnnouncementChannelAdapter,
        *,
        poll_interval_seconds: int = 15,
        batch_size: int = 25,
        max_retry_delay_seconds: int = 3600,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._batch_size = max(1, min(500, int(batch_size)))
        self._max_retry_delay_seconds = max(30, int(max_retry_delay_seconds))
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(),
            name="announcement-dispatcher",
        )
        log.info(
            "announcement_dispatcher_started",
            poll_interval_seconds=self._poll_interval_seconds,
            batch_size=self._batch_size,
        )

    async def stop(self) -> None:
        self._running = False
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None
        log.info("announcement_dispatcher_stopped")

    async def run_once(self) -> int:
        """Run one dispatch tick and return claimed delivery count."""
        claimed = await self._repository.claim_due_deliveries(limit=self._batch_size)
        if not claimed:
            return 0
        for delivery in claimed:
            await self._dispatch_delivery(delivery)
        return len(claimed)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                processed = await self.run_once()
                if processed <= 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("announcement_dispatch_cycle_failed")
                await asyncio.sleep(self._poll_interval_seconds)

    async def _dispatch_delivery(self, delivery: AnnouncementDelivery) -> None:
        event = await self._repository.get_event(delivery.event_id)
        if event is None:
            await self._repository.mark_delivery_failed(
                delivery_id=delivery.delivery_id,
                error_code="event_not_found",
                error_detail=f"Missing event {delivery.event_id}",
                terminal=True,
                retry_delay_seconds=self._retry_delay_seconds(delivery.retry_count + 1),
            )
            log.warning(
                "announcement_delivery_failed_missing_event",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
            )
            return

        try:
            await self._adapter.send(event)
            await self._repository.mark_delivery_sent(
                delivery_id=delivery.delivery_id,
                sent_at=datetime.now(UTC),
            )
            log.info(
                "announcement_delivery_sent",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
                channel=delivery.channel,
                target_user_id=event.target_user_id,
            )
        except AnnouncementDispatchError as exc:
            delay = self._retry_delay_seconds(delivery.retry_count + 1)
            terminal = not exc.retryable
            await self._repository.mark_delivery_failed(
                delivery_id=delivery.delivery_id,
                error_code=exc.code,
                error_detail=exc.detail,
                terminal=terminal,
                retry_delay_seconds=delay,
            )
            log.warning(
                "announcement_delivery_failed",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
                code=exc.code,
                retryable=exc.retryable,
                terminal=terminal,
                retry_delay_seconds=delay,
            )
        except Exception as exc:
            delay = self._retry_delay_seconds(delivery.retry_count + 1)
            await self._repository.mark_delivery_failed(
                delivery_id=delivery.delivery_id,
                error_code="dispatch_exception",
                error_detail=str(exc),
                terminal=False,
                retry_delay_seconds=delay,
            )
            log.exception(
                "announcement_delivery_failed_unhandled",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
            )

    def _retry_delay_seconds(self, retry_count: int) -> int:
        attempt = max(1, int(retry_count))
        delay = 60 * (2 ** (attempt - 1))
        return min(self._max_retry_delay_seconds, delay)

