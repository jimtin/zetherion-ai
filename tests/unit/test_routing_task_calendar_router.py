"""Unit tests for conflict routing defaults in TaskCalendarRouter."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.discord.security.models import ThreatAction, ThreatVerdict
from zetherion_ai.integrations.providers.base import ProviderDestination, ProviderEvent
from zetherion_ai.routing.models import (
    DestinationType,
    IngestionEnvelope,
    IngestionSource,
    NormalizedEvent,
    RouteMode,
    RouteTag,
)
from zetherion_ai.routing.registry import ProviderAdapters, ProviderCapabilities, ProviderRegistry
from zetherion_ai.routing.task_calendar_router import TaskCalendarRouter


class _StorageStub:
    def __init__(self) -> None:
        self.upsert_destination = AsyncMock()
        self.get_primary_destination = AsyncMock(
            return_value=SimpleNamespace(
                provider="google",
                account_ref="default",
                destination_id="primary-cal",
                destination_type=DestinationType.CALENDAR,
                display_name="Primary",
            )
        )
        self.record_routing_decision = AsyncMock()
        self.upsert_object_link = AsyncMock()
        self.record_security_event = AsyncMock()


class _SecurityStub:
    def __init__(self) -> None:
        self.analyze = AsyncMock(
            return_value=SimpleNamespace(
                verdict=ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1),
                payload_hash="abc",
            )
        )


class _CalendarAdapter:
    def __init__(self, *, conflicts: list[ProviderEvent]) -> None:
        self._conflicts = conflicts
        self.list_calendars = AsyncMock(
            return_value=[
                ProviderDestination(
                    destination_id="primary-cal",
                    destination_type=DestinationType.CALENDAR,
                    display_name="Primary",
                    writable=True,
                    is_primary=True,
                    metadata={"account_ref": "default"},
                ),
                ProviderDestination(
                    destination_id="shared-cal",
                    destination_type=DestinationType.CALENDAR,
                    display_name="Shared",
                    writable=True,
                    is_primary=False,
                    metadata={"account_ref": "default"},
                ),
            ]
        )
        self.list_events = AsyncMock(return_value=self._conflicts)
        self.create_event = AsyncMock(
            return_value=ProviderEvent(
                event_id="ev1",
                calendar_id="primary-cal",
                title="Created",
                start=datetime(2026, 2, 14, 10, 0),
                end=datetime(2026, 2, 14, 11, 0),
            )
        )


def _registry(calendar_adapter: _CalendarAdapter) -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register(
        "google",
        adapters=ProviderAdapters(calendar=calendar_adapter),
        capabilities=ProviderCapabilities(calendar_read=True, calendar_write=True),
    )
    return reg


def _envelope() -> IngestionEnvelope:
    return IngestionEnvelope(
        source_type=IngestionSource.EMAIL,
        provider="google",
        account_ref="default",
        payload={"subject": "Meeting"},
    )


def _event(*, attendees: bool = False, priority: str = "medium") -> NormalizedEvent:
    return NormalizedEvent(
        title="New Meeting",
        start=datetime(2026, 2, 14, 10, 0),
        end=datetime(2026, 2, 14, 11, 0),
        attendees=["a@example.com"] if attendees else [],
        metadata={"priority": priority},
    )


def _existing_conflict(*, overlap_minutes: int) -> list[ProviderEvent]:
    start = datetime(2026, 2, 14, 10, 0)
    end = start + timedelta(minutes=overlap_minutes)
    return [
        ProviderEvent(
            event_id="busy-1",
            calendar_id="shared-cal",
            title="Busy",
            start=start,
            end=end,
        )
    ]


@pytest.mark.asyncio
async def test_moderate_conflict_defaults_to_draft_without_write() -> None:
    storage = _StorageStub()
    security = _SecurityStub()
    adapter = _CalendarAdapter(conflicts=_existing_conflict(overlap_minutes=30))
    router = TaskCalendarRouter(storage=storage, providers=_registry(adapter), security=security)

    decision = await router.route_event(
        user_id=123,
        provider="google",
        envelope=_envelope(),
        event=_event(priority="medium", attendees=False),
    )

    assert decision.mode == RouteMode.DRAFT
    assert decision.route_tag == RouteTag.CALENDAR_CANDIDATE
    adapter.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_moderate_conflict_high_priority_asks_confirmation() -> None:
    storage = _StorageStub()
    security = _SecurityStub()
    adapter = _CalendarAdapter(conflicts=_existing_conflict(overlap_minutes=30))
    router = TaskCalendarRouter(storage=storage, providers=_registry(adapter), security=security)

    decision = await router.route_event(
        user_id=123,
        provider="google",
        envelope=_envelope(),
        event=_event(priority="high", attendees=False),
    )

    assert decision.mode == RouteMode.ASK
    adapter.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_conflict_auto_writes_event() -> None:
    storage = _StorageStub()
    security = _SecurityStub()
    adapter = _CalendarAdapter(conflicts=_existing_conflict(overlap_minutes=10))
    router = TaskCalendarRouter(storage=storage, providers=_registry(adapter), security=security)

    decision = await router.route_event(
        user_id=123,
        provider="google",
        envelope=_envelope(),
        event=_event(priority="medium", attendees=False),
    )

    assert decision.mode == RouteMode.AUTO
    adapter.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_conflict_scan_uses_all_connected_calendars() -> None:
    storage = _StorageStub()
    security = _SecurityStub()
    adapter = _CalendarAdapter(conflicts=[])
    router = TaskCalendarRouter(storage=storage, providers=_registry(adapter), security=security)

    await router.route_event(
        user_id=123,
        provider="google",
        envelope=_envelope(),
        event=_event(priority="medium", attendees=False),
    )

    args = adapter.list_events.await_args
    calendar_ids = args.args[1]
    assert "primary-cal" in calendar_ids
    assert "shared-cal" in calendar_ids


@pytest.mark.asyncio
async def test_low_conflict_auto_write_records_canonical_trust_audit(monkeypatch) -> None:
    storage = _StorageStub()
    security = _SecurityStub()
    adapter = _CalendarAdapter(conflicts=_existing_conflict(overlap_minutes=10))
    recorded = AsyncMock()
    monkeypatch.setattr(
        "zetherion_ai.routing.task_calendar_router.record_routing_trust_decision",
        recorded,
    )
    trust_storage = object()
    router = TaskCalendarRouter(
        storage=storage,
        providers=_registry(adapter),
        security=security,
        trust_storage=trust_storage,
    )

    decision = await router.route_event(
        user_id=123,
        provider="google",
        envelope=_envelope(),
        event=_event(priority="medium", attendees=False),
    )

    recorded.assert_awaited_once_with(
        trust_storage,
        user_id=123,
        action="routing.calendar.route",
        source_type="email",
        decision=decision,
        source_system="task_calendar_router",
    )
