"""Outlook provider scaffold (feature-flagged, not yet enabled)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from zetherion_ai.integrations.providers.base import (
    CalendarProviderAdapter,
    EmailProviderAdapter,
    ProviderDestination,
    ProviderEvent,
    ProviderTask,
    TaskProviderAdapter,
)
from zetherion_ai.routing.models import NormalizedEvent, NormalizedTask


class OutlookProviderAdapter(EmailProviderAdapter, TaskProviderAdapter, CalendarProviderAdapter):
    """Placeholder Outlook adapter for future Microsoft Graph support."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    async def list_sources(self, user_id: int) -> list[ProviderDestination]:
        self._raise_if_disabled()
        return []

    async def list_unread(self, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        self._raise_if_disabled()
        return []

    async def list_task_lists(self, user_id: int) -> list[ProviderDestination]:
        self._raise_if_disabled()
        return []

    async def create_task(
        self,
        user_id: int,
        task_list_id: str,
        task: NormalizedTask,
    ) -> ProviderTask:
        self._raise_if_disabled()
        raise NotImplementedError("Outlook task creation is not implemented")

    async def list_calendars(self, user_id: int) -> list[ProviderDestination]:
        self._raise_if_disabled()
        return []

    async def list_events(
        self,
        user_id: int,
        calendar_ids: list[str],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ProviderEvent]:
        self._raise_if_disabled()
        return []

    async def create_event(
        self,
        user_id: int,
        calendar_id: str,
        event: NormalizedEvent,
    ) -> ProviderEvent:
        self._raise_if_disabled()
        raise NotImplementedError("Outlook event creation is not implemented")

    def _raise_if_disabled(self) -> None:
        if not self._enabled:
            raise RuntimeError("Outlook provider is disabled")
