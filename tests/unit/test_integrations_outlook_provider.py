"""Unit tests for OutlookProviderAdapter scaffold behavior."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from zetherion_ai.integrations.providers.outlook import OutlookProviderAdapter
from zetherion_ai.routing.models import NormalizedEvent, NormalizedTask


@pytest.mark.asyncio
async def test_outlook_adapter_disabled_rejects_all_operations() -> None:
    adapter = OutlookProviderAdapter(enabled=False)
    now = datetime.now()

    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.list_sources(user_id=1)
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.list_unread(user_id=1, limit=5)
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.list_task_lists(user_id=1)
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.create_task(
            user_id=1,
            task_list_id="list-1",
            task=NormalizedTask(title="Task"),
        )
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.list_calendars(user_id=1)
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.list_events(
            user_id=1,
            calendar_ids=["cal-1"],
            window_start=now,
            window_end=now + timedelta(hours=1),
        )
    with pytest.raises(RuntimeError, match="disabled"):
        await adapter.create_event(
            user_id=1,
            calendar_id="cal-1",
            event=NormalizedEvent(
                title="Event",
                start=now,
                end=now + timedelta(hours=1),
            ),
        )


@pytest.mark.asyncio
async def test_outlook_adapter_enabled_returns_scaffold_defaults() -> None:
    adapter = OutlookProviderAdapter(enabled=True)
    now = datetime.now()

    assert await adapter.list_sources(user_id=1) == []
    assert await adapter.list_unread(user_id=1) == []
    assert await adapter.list_task_lists(user_id=1) == []
    assert await adapter.list_calendars(user_id=1) == []
    assert (
        await adapter.list_events(
            user_id=1,
            calendar_ids=["cal-1"],
            window_start=now,
            window_end=now + timedelta(hours=1),
        )
        == []
    )

    with pytest.raises(NotImplementedError, match="task creation"):
        await adapter.create_task(
            user_id=1,
            task_list_id="list-1",
            task=NormalizedTask(title="Task"),
        )
    with pytest.raises(NotImplementedError, match="event creation"):
        await adapter.create_event(
            user_id=1,
            calendar_id="cal-1",
            event=NormalizedEvent(
                title="Event",
                start=now,
                end=now + timedelta(hours=1),
            ),
        )
