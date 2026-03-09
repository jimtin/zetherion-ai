"""Dispatch worker for queued announcement deliveries."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class AnnouncementChannelDefinition:
    """Registry metadata for one announcement delivery channel."""

    channel: str
    display_name: str
    description: str
    public_enabled: bool = False
    config_fields: tuple[str, ...] = field(default_factory=tuple)


class AnnouncementChannelAdapter(Protocol):
    """Channel adapter contract for announcement delivery."""

    async def send(self, event: AnnouncementEvent) -> None:
        """Send a single announcement event."""


class AnnouncementChannelRegistry:
    """Registry of delivery adapters keyed by channel name."""

    def __init__(self) -> None:
        self._adapters: dict[str, AnnouncementChannelAdapter] = {}
        self._definitions: dict[str, AnnouncementChannelDefinition] = {}

    def register(
        self,
        channel: str,
        adapter: AnnouncementChannelAdapter,
        *,
        definition: AnnouncementChannelDefinition | None = None,
    ) -> None:
        normalized = str(channel or "").strip().lower()
        if not normalized:
            raise ValueError("Announcement channel name is required")
        self._adapters[normalized] = adapter
        if definition is not None:
            self._definitions[normalized] = AnnouncementChannelDefinition(
                channel=normalized,
                display_name=definition.display_name,
                description=definition.description,
                public_enabled=bool(definition.public_enabled),
                config_fields=tuple(definition.config_fields),
            )
        elif normalized not in self._definitions:
            self._definitions[normalized] = AnnouncementChannelDefinition(
                channel=normalized,
                display_name=normalized.replace("_", " ").title(),
                description=f"{normalized} delivery channel",
            )

    def get(self, channel: str) -> AnnouncementChannelAdapter | None:
        return self._adapters.get(str(channel or "").strip().lower())

    def channels(self) -> list[str]:
        return sorted(self._adapters)

    def get_definition(self, channel: str) -> AnnouncementChannelDefinition | None:
        return self._definitions.get(str(channel or "").strip().lower())

    def definitions(self, *, public_only: bool = False) -> list[AnnouncementChannelDefinition]:
        definitions = sorted(self._definitions.values(), key=lambda item: item.channel)
        if not public_only:
            return definitions
        return [item for item in definitions if item.public_enabled]


class AnnouncementDispatcher:
    """Poll due deliveries and route them through one adapter path."""

    def __init__(
        self,
        repository: AnnouncementRepository,
        adapter: AnnouncementChannelAdapter | AnnouncementChannelRegistry,
        *,
        poll_interval_seconds: int = 15,
        batch_size: int = 25,
        max_retry_delay_seconds: int = 3600,
    ) -> None:
        self._repository: AnnouncementRepository = repository
        if isinstance(adapter, AnnouncementChannelRegistry):
            self._registry = adapter
        else:
            self._registry = AnnouncementChannelRegistry()
            self._registry.register("discord_dm", adapter)
        self._poll_interval_seconds: int = max(1, int(poll_interval_seconds))
        self._batch_size: int = max(1, min(500, int(batch_size)))
        self._max_retry_delay_seconds: int = max(30, int(max_retry_delay_seconds))
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

        adapter = self._registry.get(delivery.channel)
        if adapter is None:
            await self._repository.mark_delivery_failed(
                delivery_id=delivery.delivery_id,
                error_code="channel_not_registered",
                error_detail=f"No adapter registered for channel {delivery.channel}",
                terminal=True,
                retry_delay_seconds=self._retry_delay_seconds(delivery.retry_count + 1),
            )
            log.warning(
                "announcement_delivery_failed_unknown_channel",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
                channel=delivery.channel,
            )
            return

        try:
            await adapter.send(event)
            await self._repository.mark_delivery_sent(
                delivery_id=delivery.delivery_id,
                sent_at=datetime.now(UTC),
            )
            log.info(
                "announcement_delivery_sent",
                delivery_id=delivery.delivery_id,
                event_id=delivery.event_id,
                channel=delivery.channel,
                recipient_key=(event.recipient.routing_key if event.recipient else None),
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
        delay = 60 * (1 << (attempt - 1))
        if delay > self._max_retry_delay_seconds:
            return self._max_retry_delay_seconds
        return delay
