"""Provider adapter protocols for email, tasks, and calendars."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from zetherion_ai.routing.models import DestinationType, NormalizedEvent, NormalizedTask


@dataclass
class ProviderDestination:
    """Provider-native destination (calendar/task list/mailbox)."""

    destination_id: str
    destination_type: DestinationType
    display_name: str
    writable: bool = True
    is_primary: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderEvent:
    """Provider event model used for conflict checks."""

    event_id: str
    calendar_id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderTask:
    """Provider task model used for list/update flows."""

    task_id: str
    list_id: str
    title: str
    due_at: datetime | None = None
    completed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class EmailProviderAdapter(Protocol):
    """Email provider operations needed by routing."""

    async def list_sources(self, user_id: int) -> list[ProviderDestination]:
        """List connected mail sources for a user."""

    async def list_unread(self, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return unread email summaries."""


class TaskProviderAdapter(Protocol):
    """Task provider operations needed by routing."""

    async def list_task_lists(self, user_id: int) -> list[ProviderDestination]:
        """List task lists for a user."""

    async def create_task(
        self,
        user_id: int,
        task_list_id: str,
        task: NormalizedTask,
    ) -> ProviderTask:
        """Create a task on a provider task list."""


class CalendarProviderAdapter(Protocol):
    """Calendar provider operations needed by routing."""

    async def list_calendars(self, user_id: int) -> list[ProviderDestination]:
        """List calendar destinations for a user."""

    async def list_events(
        self,
        user_id: int,
        calendar_ids: list[str],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ProviderEvent]:
        """List provider events in a time window for conflict detection."""

    async def create_event(
        self,
        user_id: int,
        calendar_id: str,
        event: NormalizedEvent,
    ) -> ProviderEvent:
        """Create an event in a target calendar."""
