"""Unit tests for Google provider adapter mailbox aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.integrations.providers.google import (
    GoogleProviderAdapter,
    _parse_google_datetime,
)
from zetherion_ai.routing.models import DestinationType, NormalizedEvent, NormalizedTask
from zetherion_ai.skills.gmail.accounts import GmailAccount


def _message(
    msg_id: str,
    *,
    sender: str,
    has_attachments: bool = False,
    attachment_filenames: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        gmail_id=msg_id,
        thread_id=f"thread-{msg_id}",
        subject=f"subject-{msg_id}",
        from_email=sender,
        to_emails=["me@example.com"],
        snippet=f"snippet-{msg_id}",
        received_at=datetime(2026, 2, 13, 12, 0, 0),
        has_attachments=has_attachments,
        attachment_filenames=attachment_filenames or [],
    )


@pytest.mark.asyncio
async def test_list_unread_scans_all_connected_accounts() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[
            GmailAccount(id=2, email="second@example.com", is_primary=False),
            GmailAccount(id=1, email="primary@example.com", is_primary=True),
        ]
    )
    account_manager.get_account = AsyncMock(
        side_effect=[
            GmailAccount(id=1, email="primary@example.com", access_token="token-primary"),
            GmailAccount(id=2, email="second@example.com", access_token="token-second"),
        ]
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(side_effect=["token-primary", "token-second"])  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(return_value=_message("p1", sender="boss@example.com"))

    client_second = MagicMock()
    client_second.list_messages = AsyncMock(return_value=([{"id": "s1"}], None))
    client_second.get_message = AsyncMock(return_value=_message("s1", sender="ops@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        side_effect=[client_primary, client_second],
    ):
        out = await adapter.list_unread(user_id=42, limit=2)

    assert len(out) == 2
    assert out[0]["account_ref"] == "1"
    assert out[1]["account_ref"] == "2"
    client_primary.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=1,
    )
    client_second.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=1,
    )


@pytest.mark.asyncio
async def test_list_unread_skips_accounts_without_valid_token() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[
            GmailAccount(id=1, email="one@example.com", is_primary=True),
            GmailAccount(id=2, email="two@example.com", is_primary=False),
        ]
    )
    account_manager.get_account = AsyncMock(
        side_effect=[
            GmailAccount(id=1, email="one@example.com", access_token="token-one"),
            GmailAccount(id=2, email="two@example.com", access_token="token-two"),
        ]
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(side_effect=[None, "token-two"])  # type: ignore[assignment]

    client_second = MagicMock()
    client_second.list_messages = AsyncMock(return_value=([{"id": "s1"}], None))
    client_second.get_message = AsyncMock(return_value=_message("s1", sender="ops@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_second,
    ):
        out = await adapter.list_unread(user_id=42, limit=2)

    assert len(out) == 1
    assert out[0]["account_ref"] == "2"
    client_second.list_messages.assert_awaited_once_with(
        query="in:inbox is:unread",
        max_results=2,
    )


@pytest.mark.asyncio
async def test_list_unread_returns_empty_when_no_accounts() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(return_value=[])
    account_manager.get_account = AsyncMock(return_value=None)
    adapter = GoogleProviderAdapter(account_manager=account_manager)

    out = await adapter.list_unread(user_id=42, limit=5)

    assert out == []


@pytest.mark.asyncio
async def test_list_unread_hydrates_account_tokens_before_fetch() -> None:
    account_manager = MagicMock()
    summary_account = GmailAccount(id=1, email="primary@example.com", is_primary=True)
    full_account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-primary",
        refresh_token="refresh-primary",
        is_primary=True,
    )
    account_manager.list_accounts = AsyncMock(return_value=[summary_account])
    account_manager.get_account = AsyncMock(return_value=full_account)

    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token-primary")  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(return_value=_message("p1", sender="boss@example.com"))

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_primary,
    ):
        out = await adapter.list_unread(user_id=42, limit=1)

    assert len(out) == 1
    account_manager.get_account.assert_awaited_once_with(1)
    hydrated_arg = adapter._ensure_access_token.await_args.args[0]
    assert hydrated_arg.access_token == "token-primary"


@pytest.mark.asyncio
async def test_list_unread_includes_attachment_metadata() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[GmailAccount(id=1, email="primary@example.com", is_primary=True)]
    )
    account_manager.get_account = AsyncMock(
        return_value=GmailAccount(id=1, email="primary@example.com", access_token="token-primary")
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token-primary")  # type: ignore[assignment]

    client_primary = MagicMock()
    client_primary.list_messages = AsyncMock(return_value=([{"id": "p1"}], None))
    client_primary.get_message = AsyncMock(
        return_value=_message(
            "p1",
            sender="boss@example.com",
            has_attachments=True,
            attachment_filenames=["agenda.pdf"],
        )
    )

    with patch(
        "zetherion_ai.integrations.providers.google.GmailClient",
        return_value=client_primary,
    ):
        out = await adapter.list_unread(user_id=42, limit=1)

    assert len(out) == 1
    assert out[0]["has_attachments"] is True
    assert out[0]["attachment_count"] == 1
    assert out[0]["attachment_filenames"] == ["agenda.pdf"]


@pytest.mark.asyncio
async def test_ensure_access_token_handles_aware_expiry() -> None:
    account_manager = MagicMock()
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-1",
        token_expiry=datetime.now(UTC) + timedelta(minutes=15),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "token-1"


@pytest.mark.asyncio
async def test_ensure_access_token_handles_naive_expiry() -> None:
    account_manager = MagicMock()
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="token-1",
        token_expiry=datetime.now() + timedelta(minutes=15),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "token-1"


@pytest.mark.asyncio
async def test_ensure_access_token_refresh_sets_timezone_aware_expiry() -> None:
    account_manager = MagicMock()
    account_manager.update_tokens = AsyncMock()
    auth = MagicMock()
    auth.refresh_access_token = AsyncMock(
        return_value={"access_token": "new-token", "expires_in": 3600}
    )
    adapter = GoogleProviderAdapter(account_manager=account_manager, auth=auth)
    account = GmailAccount(
        id=1,
        email="primary@example.com",
        access_token="old-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now(UTC) - timedelta(minutes=5),
    )

    token = await adapter._ensure_access_token(account)

    assert token == "new-token"
    account_manager.update_tokens.assert_awaited_once()
    updated_expiry = account_manager.update_tokens.await_args.kwargs["token_expiry"]
    assert updated_expiry.tzinfo is not None


@pytest.mark.asyncio
async def test_list_sources_and_preferred_account_paths() -> None:
    account_manager = MagicMock()
    account_manager.list_accounts = AsyncMock(
        return_value=[
            GmailAccount(id=1, email="primary@example.com", is_primary=True),
            GmailAccount(id=2, email="other@example.com", is_primary=False),
        ]
    )
    account_manager.get_primary_account = AsyncMock(return_value=None)
    account_manager.get_account = AsyncMock(return_value=GmailAccount(id=1, email="primary@example.com"))
    adapter = GoogleProviderAdapter(account_manager=account_manager)

    sources = await adapter.list_sources(user_id=7)
    assert [src.destination_type for src in sources] == [DestinationType.MAILBOX, DestinationType.MAILBOX]
    assert sources[0].is_primary is True
    assert sources[0].destination_id == "1"

    preferred = await adapter._get_preferred_account(7)
    assert preferred is not None
    assert preferred.email == "primary@example.com"


@pytest.mark.asyncio
async def test_list_task_lists_and_create_task_paths() -> None:
    account_manager = MagicMock()
    account = GmailAccount(id=1, email="primary@example.com", is_primary=True)
    account_manager.get_primary_account = AsyncMock(return_value=account)
    account_manager.list_accounts = AsyncMock(return_value=[account])
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token")  # type: ignore[assignment]
    adapter._get_json = AsyncMock(  # type: ignore[assignment]
        return_value={
            "items": [
                {"id": "@default", "title": "Default", "etag": "e1"},
                {"id": "list-2", "title": "Other", "etag": "e2"},
            ]
        }
    )
    adapter._post_json = AsyncMock(  # type: ignore[assignment]
        return_value={"id": "task-1", "title": "Call client", "status": "needsAction", "due": "2026-03-04T10:00:00Z"}
    )

    lists = await adapter.list_task_lists(user_id=7)
    assert len(lists) == 2
    assert lists[0].is_primary is True

    task = await adapter.create_task(
        user_id=7,
        task_list_id="@default",
        task=NormalizedTask(title="Call client", description="Follow up"),
    )
    assert task.task_id == "task-1"
    assert task.completed is False


@pytest.mark.asyncio
async def test_calendar_listing_and_event_create_list_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    account_manager = MagicMock()
    account = GmailAccount(id=1, email="primary@example.com", is_primary=True)
    account_manager.get_primary_account = AsyncMock(return_value=account)
    adapter = GoogleProviderAdapter(account_manager=account_manager)
    adapter._ensure_access_token = AsyncMock(return_value="token")  # type: ignore[assignment]
    adapter._get_json = AsyncMock(  # type: ignore[assignment]
        return_value={
            "items": [
                {"id": "cal-1", "summary": "Work", "accessRole": "owner", "primary": True, "timeZone": "UTC"}
            ]
        }
    )

    calendars = await adapter.list_calendars(user_id=7)
    assert calendars[0].destination_type == DestinationType.CALENDAR
    assert calendars[0].writable is True

    class _FakeCalendarItem:
        def __init__(self, *, event_id: str, start: datetime | None, end: datetime | None) -> None:
            self.event_id = event_id
            self.summary = "Meeting"
            self.start = start
            self.end = end
            self.all_day = False
            self.status = "confirmed"
            self.organizer = "owner@example.com"
            self.html_link = "https://calendar/event"

    class _FakeCalendarClient:
        def __init__(self, token: str, timeout: float) -> None:
            self.token = token
            self.timeout = timeout

        async def list_events(
            self,
            *,
            calendar_id: str,
            time_min: datetime,
            time_max: datetime,
            max_results: int,
        ) -> list[SimpleNamespace]:
            return [
                _FakeCalendarItem(event_id="evt-1", start=time_min, end=time_max),
                _FakeCalendarItem(event_id="evt-skip", start=None, end=time_max),
            ]

        async def create_event(
            self,
            *,
            summary: str,
            start: datetime,
            end: datetime,
            calendar_id: str,
            description: str,
            location: str,
            attendees: list[str],
        ) -> SimpleNamespace:
            return SimpleNamespace(
                event_id="evt-2",
                summary=summary,
                start=start,
                end=end,
                all_day=False,
                html_link="https://calendar/event/evt-2",
            )

    monkeypatch.setattr("zetherion_ai.integrations.providers.google.CalendarClient", _FakeCalendarClient)

    now = datetime.now(UTC)
    listed = await adapter.list_events(
        user_id=7,
        calendar_ids=["cal-1"],
        window_start=now,
        window_end=now + timedelta(hours=1),
    )
    assert len(listed) == 1
    assert listed[0].event_id == "evt-1"

    created = await adapter.create_event(
        user_id=7,
        calendar_id="cal-1",
        event=NormalizedEvent(
            title="Meeting",
            start=now,
            end=now + timedelta(hours=1),
            description="sync",
            location="Room 1",
            attendees=["a@example.com"],
        ),
    )
    assert created.event_id == "evt-2"


@pytest.mark.asyncio
async def test_create_event_requires_connected_account_and_token() -> None:
    account_manager = MagicMock()
    account_manager.get_primary_account = AsyncMock(return_value=None)
    account_manager.list_accounts = AsyncMock(return_value=[])
    adapter = GoogleProviderAdapter(account_manager=account_manager)

    now = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="No Google account connected"):
        await adapter.create_event(
            user_id=7,
            calendar_id="cal-1",
            event=NormalizedEvent(title="x", start=now, end=now + timedelta(hours=1)),
        )

    account = GmailAccount(id=1, email="primary@example.com")
    account_manager.get_primary_account = AsyncMock(return_value=account)
    adapter._ensure_access_token = AsyncMock(return_value=None)  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="token unavailable"):
        await adapter.create_event(
            user_id=7,
            calendar_id="cal-1",
            event=NormalizedEvent(title="x", start=now, end=now + timedelta(hours=1)),
        )


def test_parse_google_datetime_edges() -> None:
    assert _parse_google_datetime(None) is None
    assert _parse_google_datetime("not-a-date") is None
    parsed = _parse_google_datetime("2026-03-04T10:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is None
