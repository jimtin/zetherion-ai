"""Tests for Google Calendar sync client."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.gmail.calendar_sync import (
    CalendarClient,
    CalendarEvent,
    CalendarSyncError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
) -> MagicMock:
    """Create a mock ``httpx.Response``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        http_error = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
        resp.raise_for_status.side_effect = http_error
    return resp


def _sample_event_data(
    *,
    event_id: str = "evt1",
    summary: str = "Team meeting",
    description: str = "Weekly sync",
    location: str = "Room 42",
    start_dt: str = "2026-03-15T10:00:00Z",
    end_dt: str = "2026-03-15T11:00:00Z",
    all_day: bool = False,
    attendees: list[dict] | None = None,
    status: str = "confirmed",
    organizer_email: str = "org@example.com",
    html_link: str = "https://calendar.google.com/event/evt1",
) -> dict:
    """Build a sample Google Calendar API event response payload."""
    if all_day:
        start_block = {"date": start_dt.split("T")[0]}
        end_block = {"date": end_dt.split("T")[0]}
    else:
        start_block = {"dateTime": start_dt}
        end_block = {"dateTime": end_dt}

    data: dict = {
        "id": event_id,
        "summary": summary,
        "description": description,
        "location": location,
        "start": start_block,
        "end": end_block,
        "status": status,
        "organizer": {"email": organizer_email},
        "htmlLink": html_link,
    }
    if attendees is not None:
        data["attendees"] = attendees
    return data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a CalendarClient with a test access token."""
    return CalendarClient("test-access-token")


@pytest.fixture
def mock_httpx():
    """Mock httpx.AsyncClient so that no real HTTP calls are made.

    Yields the mock client instance that ``async with httpx.AsyncClient(...)``
    resolves to, allowing callers to set ``mock_httpx.get.return_value``, etc.
    """
    with patch("zetherion_ai.skills.gmail.calendar_sync.httpx.AsyncClient") as mock_cls:
        client_instance = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        yield client_instance


# ===================================================================
# 1. CalendarEvent dataclass tests
# ===================================================================


class TestCalendarEvent:
    """Tests for the CalendarEvent dataclass."""

    def test_default_values(self):
        event = CalendarEvent()
        assert event.event_id == ""
        assert event.summary == ""
        assert event.description == ""
        assert event.location == ""
        assert event.start is None
        assert event.end is None
        assert event.all_day is False
        assert event.attendees == []
        assert event.status == "confirmed"
        assert event.organizer == ""
        assert event.html_link == ""
        assert event.calendar_id == "primary"

    def test_to_dict_with_start_and_end(self):
        start = datetime(2026, 3, 15, 10, 0, 0)
        end = datetime(2026, 3, 15, 11, 0, 0)
        event = CalendarEvent(
            event_id="e1",
            summary="Meeting",
            description="A meeting",
            location="Office",
            start=start,
            end=end,
            all_day=False,
            attendees=["a@b.com"],
            status="confirmed",
            organizer="org@b.com",
            html_link="https://cal.example.com/e1",
            calendar_id="work",
        )
        d = event.to_dict()
        assert d["event_id"] == "e1"
        assert d["summary"] == "Meeting"
        assert d["description"] == "A meeting"
        assert d["location"] == "Office"
        assert d["start"] == start.isoformat()
        assert d["end"] == end.isoformat()
        assert d["all_day"] is False
        assert d["attendees"] == ["a@b.com"]
        assert d["status"] == "confirmed"
        assert d["organizer"] == "org@b.com"
        assert d["html_link"] == "https://cal.example.com/e1"
        assert d["calendar_id"] == "work"

    def test_to_dict_without_start_and_end(self):
        event = CalendarEvent(summary="No dates")
        d = event.to_dict()
        assert d["start"] is None
        assert d["end"] is None

    def test_duration_minutes_both_set(self):
        event = CalendarEvent(
            start=datetime(2026, 1, 1, 10, 0),
            end=datetime(2026, 1, 1, 11, 30),
        )
        assert event.duration_minutes == 90

    def test_duration_minutes_start_missing(self):
        event = CalendarEvent(end=datetime(2026, 1, 1, 11, 0))
        assert event.duration_minutes == 0

    def test_duration_minutes_end_missing(self):
        event = CalendarEvent(start=datetime(2026, 1, 1, 10, 0))
        assert event.duration_minutes == 0

    def test_duration_minutes_both_missing(self):
        event = CalendarEvent()
        assert event.duration_minutes == 0

    def test_attendees_list_is_independent(self):
        """Each CalendarEvent should have its own attendees list."""
        e1 = CalendarEvent()
        e2 = CalendarEvent()
        e1.attendees.append("x@y.com")
        assert e2.attendees == []


# ===================================================================
# 2. CalendarClient.__init__ tests
# ===================================================================


class TestCalendarClientInit:
    """Tests for CalendarClient constructor."""

    def test_valid_initialization(self):
        c = CalendarClient(access_token="tok-abc")
        assert c._access_token == "tok-abc"
        assert c._timeout == 30.0

    def test_empty_access_token_raises_value_error(self):
        with pytest.raises(ValueError, match="access_token is required"):
            CalendarClient(access_token="")

    def test_custom_timeout(self):
        c = CalendarClient(access_token="tok", timeout=60.0)
        assert c._timeout == 60.0

    def test_headers(self):
        c = CalendarClient(access_token="my-token")
        assert c._headers() == {"Authorization": "Bearer my-token"}


# ===================================================================
# 3. CalendarClient.list_events tests
# ===================================================================


class TestListEvents:
    """Tests for CalendarClient.list_events."""

    async def test_basic_listing_with_items(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data={
                "items": [
                    _sample_event_data(event_id="e1"),
                    _sample_event_data(event_id="e2"),
                ],
            }
        )
        events = await client.list_events()
        assert len(events) == 2
        assert events[0].event_id == "e1"
        assert events[1].event_id == "e2"

    async def test_empty_response(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={})
        events = await client.list_events()
        assert events == []

    async def test_with_time_min_and_time_max(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        t_min = datetime(2026, 3, 1, 0, 0, 0)
        t_max = datetime(2026, 3, 31, 23, 59, 59)
        await client.list_events(time_min=t_min, time_max=t_max)
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["timeMin"] == t_min.isoformat() + "Z"
        assert params["timeMax"] == t_max.isoformat() + "Z"

    async def test_without_time_min_time_max(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        await client.list_events()
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert "timeMin" not in params
        assert "timeMax" not in params

    async def test_single_events_false_changes_order_by(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        await client.list_events(single_events=False)
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["singleEvents"] == "false"
        assert params["orderBy"] == "updated"

    async def test_single_events_true_order_by_start_time(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        await client.list_events(single_events=True)
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["singleEvents"] == "true"
        assert params["orderBy"] == "startTime"

    async def test_custom_calendar_id(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        await client.list_events(calendar_id="work@group.calendar.google.com")
        call_kwargs = mock_httpx.get.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("url")
        assert "work@group.calendar.google.com" in url

    async def test_custom_max_results(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"items": []})
        await client.list_events(max_results=10)
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["maxResults"] == 10


# ===================================================================
# 4. CalendarClient.get_event tests
# ===================================================================


class TestGetEvent:
    """Tests for CalendarClient.get_event."""

    async def test_successful_retrieval(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(
            json_data=_sample_event_data(event_id="evt42", summary="Standup")
        )
        event = await client.get_event("evt42")
        assert event.event_id == "evt42"
        assert event.summary == "Standup"

    async def test_api_error_raises_calendar_sync_error(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(status_code=404, text="Not Found")
        with pytest.raises(CalendarSyncError, match="404"):
            await client.get_event("nonexistent")

    async def test_custom_calendar_id(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data=_sample_event_data(event_id="evt99"))
        event = await client.get_event("evt99", calendar_id="secondary")
        assert event.calendar_id == "secondary"


# ===================================================================
# 5. CalendarClient.create_event tests
# ===================================================================


class TestCreateEvent:
    """Tests for CalendarClient.create_event."""

    async def test_minimal_creation(self, client, mock_httpx):
        start = datetime(2026, 4, 1, 9, 0)
        end = datetime(2026, 4, 1, 10, 0)
        mock_httpx.post.return_value = _make_response(
            json_data=_sample_event_data(
                event_id="new1",
                summary="Quick call",
                start_dt=start.isoformat(),
                end_dt=end.isoformat(),
            )
        )
        event = await client.create_event("Quick call", start, end)
        assert event.event_id == "new1"
        assert event.summary == "Quick call"
        mock_httpx.post.assert_awaited_once()

    async def test_with_all_optional_params(self, client, mock_httpx):
        start = datetime(2026, 4, 1, 9, 0)
        end = datetime(2026, 4, 1, 10, 0)
        mock_httpx.post.return_value = _make_response(json_data=_sample_event_data(event_id="new2"))
        await client.create_event(
            "All params",
            start,
            end,
            description="Full description",
            location="Room A",
            attendees=["a@b.com", "c@d.com"],
            calendar_id="work",
        )
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["summary"] == "All params"
        assert json_body["description"] == "Full description"
        assert json_body["location"] == "Room A"
        assert json_body["attendees"] == [{"email": "a@b.com"}, {"email": "c@d.com"}]

    async def test_without_optional_params(self, client, mock_httpx):
        start = datetime(2026, 4, 1, 9, 0)
        end = datetime(2026, 4, 1, 10, 0)
        mock_httpx.post.return_value = _make_response(json_data=_sample_event_data(event_id="new3"))
        await client.create_event("Minimal", start, end)
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "description" not in json_body
        assert "location" not in json_body
        assert "attendees" not in json_body


# ===================================================================
# 6. CalendarClient.update_event tests
# ===================================================================


class TestUpdateEvent:
    """Tests for CalendarClient.update_event."""

    async def test_update_some_fields(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(
            json_data=_sample_event_data(event_id="upd1", summary="Updated title")
        )
        event = await client.update_event("upd1", summary="Updated title")
        assert event.summary == "Updated title"
        call_kwargs = mock_httpx.patch.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["summary"] == "Updated title"
        assert "start" not in json_body
        assert "end" not in json_body

    async def test_update_all_fields(self, client, mock_httpx):
        new_start = datetime(2026, 5, 1, 14, 0)
        new_end = datetime(2026, 5, 1, 15, 0)
        mock_httpx.patch.return_value = _make_response(
            json_data=_sample_event_data(event_id="upd2")
        )
        await client.update_event(
            "upd2",
            summary="New summary",
            start=new_start,
            end=new_end,
            description="New desc",
            location="New loc",
        )
        call_kwargs = mock_httpx.patch.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["summary"] == "New summary"
        assert json_body["start"]["dateTime"] == new_start.isoformat()
        assert json_body["end"]["dateTime"] == new_end.isoformat()
        assert json_body["description"] == "New desc"
        assert json_body["location"] == "New loc"

    async def test_update_no_fields(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(
            json_data=_sample_event_data(event_id="upd3")
        )
        event = await client.update_event("upd3")
        assert event.event_id == "upd3"
        call_kwargs = mock_httpx.patch.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body == {}

    async def test_update_custom_calendar_id(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(
            json_data=_sample_event_data(event_id="upd4")
        )
        event = await client.update_event("upd4", calendar_id="secondary")
        assert event.calendar_id == "secondary"
        call_kwargs = mock_httpx.patch.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("url")
        assert "secondary" in url


# ===================================================================
# 7. CalendarClient.delete_event tests
# ===================================================================


class TestDeleteEvent:
    """Tests for CalendarClient.delete_event."""

    async def test_successful_delete(self, client, mock_httpx):
        mock_httpx.delete.return_value = _make_response(status_code=204)
        result = await client.delete_event("del1")
        assert result is True
        mock_httpx.delete.assert_awaited_once()

    async def test_delete_api_error(self, client, mock_httpx):
        mock_httpx.delete.return_value = _make_response(status_code=403, text="Forbidden")
        with pytest.raises(CalendarSyncError, match="403"):
            await client.delete_event("del2")

    async def test_delete_custom_calendar_id(self, client, mock_httpx):
        mock_httpx.delete.return_value = _make_response(status_code=204)
        await client.delete_event("del3", calendar_id="other")
        call_kwargs = mock_httpx.delete.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("url")
        assert "other" in url


# ===================================================================
# 8. get_today_events and get_upcoming_events tests
# ===================================================================


class TestConvenienceMethods:
    """Tests for get_today_events and get_upcoming_events."""

    async def test_get_today_events(self, client):
        fake_now = datetime(2026, 6, 15, 14, 30, 0)
        expected_min = datetime(2026, 6, 15, 0, 0, 0)
        expected_max = datetime(2026, 6, 16, 0, 0, 0)

        with patch.object(client, "list_events", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            with patch("zetherion_ai.skills.gmail.calendar_sync.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                # Allow timedelta to work normally
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await client.get_today_events()

            mock_list.assert_awaited_once()
            call_kwargs = mock_list.call_args.kwargs
            assert call_kwargs["time_min"] == expected_min
            assert call_kwargs["time_max"] == expected_max

    async def test_get_upcoming_events_default_hours(self, client):
        fake_now = datetime(2026, 6, 15, 14, 30, 0)
        expected_end = fake_now + timedelta(hours=24)

        with patch.object(client, "list_events", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            with patch("zetherion_ai.skills.gmail.calendar_sync.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                await client.get_upcoming_events()

            mock_list.assert_awaited_once()
            call_kwargs = mock_list.call_args.kwargs
            assert call_kwargs["time_min"] == fake_now
            assert call_kwargs["time_max"] == expected_end

    async def test_get_upcoming_events_custom_hours(self, client):
        fake_now = datetime(2026, 6, 15, 14, 30, 0)
        expected_end = fake_now + timedelta(hours=8)

        with patch.object(client, "list_events", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            with patch("zetherion_ai.skills.gmail.calendar_sync.datetime") as mock_dt:
                mock_dt.now.return_value = fake_now
                await client.get_upcoming_events(hours=8)

            mock_list.assert_awaited_once()
            call_kwargs = mock_list.call_args.kwargs
            assert call_kwargs["time_min"] == fake_now
            assert call_kwargs["time_max"] == expected_end


# ===================================================================
# 9. HTTP error handling tests
# ===================================================================


class TestHTTPErrorHandling:
    """Tests for HTTP error handling across all methods."""

    async def test_get_http_status_error(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(status_code=500, text="Internal Server Error")
        with pytest.raises(CalendarSyncError, match="500"):
            await client._get("https://example.com/test")

    async def test_get_request_error(self, client, mock_httpx):
        mock_httpx.get.side_effect = httpx.RequestError(
            "Connection refused", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(CalendarSyncError, match="request failed"):
            await client._get("https://example.com/test")

    async def test_post_http_status_error(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(status_code=429, text="Rate limited")
        with pytest.raises(CalendarSyncError, match="429"):
            await client._post("https://example.com/test", {})

    async def test_post_request_error(self, client, mock_httpx):
        mock_httpx.post.side_effect = httpx.RequestError(
            "Timeout", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(CalendarSyncError, match="request failed"):
            await client._post("https://example.com/test", {})

    async def test_patch_http_status_error(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(status_code=400, text="Bad Request")
        with pytest.raises(CalendarSyncError, match="400"):
            await client._patch("https://example.com/test", {})

    async def test_patch_request_error(self, client, mock_httpx):
        mock_httpx.patch.side_effect = httpx.RequestError(
            "DNS error", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(CalendarSyncError, match="request failed"):
            await client._patch("https://example.com/test", {})

    async def test_delete_http_status_error(self, client, mock_httpx):
        mock_httpx.delete.return_value = _make_response(status_code=401, text="Unauthorized")
        with pytest.raises(CalendarSyncError, match="401"):
            await client._delete("https://example.com/test")

    async def test_delete_request_error(self, client, mock_httpx):
        mock_httpx.delete.side_effect = httpx.RequestError(
            "Connection reset", request=MagicMock(spec=httpx.Request)
        )
        with pytest.raises(CalendarSyncError, match="request failed"):
            await client._delete("https://example.com/test")


# ===================================================================
# 10. _parse_event tests
# ===================================================================


class TestParseEvent:
    """Tests for CalendarClient._parse_event."""

    def test_timed_event(self, client):
        data = _sample_event_data(
            event_id="pe1",
            summary="Timed",
            start_dt="2026-03-15T10:00:00Z",
            end_dt="2026-03-15T11:00:00Z",
        )
        event = client._parse_event(data, "primary")
        assert event.event_id == "pe1"
        assert event.summary == "Timed"
        assert event.all_day is False
        assert event.start == datetime(2026, 3, 15, 10, 0, 0)
        assert event.end == datetime(2026, 3, 15, 11, 0, 0)

    def test_all_day_event(self, client):
        data = _sample_event_data(
            event_id="pe2",
            summary="All day",
            all_day=True,
            start_dt="2026-03-15T00:00:00",
            end_dt="2026-03-16T00:00:00",
        )
        event = client._parse_event(data, "primary")
        assert event.all_day is True
        assert event.start == datetime(2026, 3, 15, 0, 0, 0)
        assert event.end == datetime(2026, 3, 16, 0, 0, 0)

    def test_with_attendees(self, client):
        data = _sample_event_data(
            attendees=[
                {"email": "a@b.com"},
                {"email": "c@d.com"},
                {"email": "e@f.com"},
            ]
        )
        event = client._parse_event(data)
        assert event.attendees == ["a@b.com", "c@d.com", "e@f.com"]

    def test_attendees_skip_entries_without_email(self, client):
        data = _sample_event_data(
            attendees=[
                {"email": "a@b.com"},
                {"displayName": "No Email"},
                {"email": ""},
                {"email": "c@d.com"},
            ]
        )
        event = client._parse_event(data)
        assert event.attendees == ["a@b.com", "c@d.com"]

    def test_no_attendees(self, client):
        data = _sample_event_data()
        # No attendees key
        event = client._parse_event(data)
        assert event.attendees == []

    def test_missing_optional_fields(self, client):
        data = {"id": "pe5"}
        event = client._parse_event(data, "cal123")
        assert event.event_id == "pe5"
        assert event.summary == ""
        assert event.description == ""
        assert event.location == ""
        assert event.start is None
        assert event.end is None
        assert event.all_day is False
        assert event.attendees == []
        assert event.status == "confirmed"
        assert event.organizer == ""
        assert event.html_link == ""
        assert event.calendar_id == "cal123"

    def test_organizer_email_parsed(self, client):
        data = _sample_event_data(organizer_email="boss@corp.com")
        event = client._parse_event(data)
        assert event.organizer == "boss@corp.com"

    def test_html_link_parsed(self, client):
        data = _sample_event_data(html_link="https://cal.google.com/e/abc")
        event = client._parse_event(data)
        assert event.html_link == "https://cal.google.com/e/abc"

    def test_calendar_id_passed_through(self, client):
        data = _sample_event_data()
        event = client._parse_event(data, "my-special-calendar")
        assert event.calendar_id == "my-special-calendar"


# ===================================================================
# 11. _parse_datetime tests
# ===================================================================


class TestParseDatetime:
    """Tests for CalendarClient._parse_datetime."""

    def test_iso_datetime_with_z_suffix(self, client):
        result = client._parse_datetime({"dateTime": "2026-03-15T10:00:00Z"})
        assert result == datetime(2026, 3, 15, 10, 0, 0)
        assert result.tzinfo is None

    def test_iso_datetime_with_timezone_offset(self, client):
        result = client._parse_datetime({"dateTime": "2026-03-15T10:00:00+05:00"})
        assert result is not None
        assert result.tzinfo is None

    def test_date_only_string(self, client):
        result = client._parse_datetime({"date": "2026-03-15"})
        assert result == datetime(2026, 3, 15, 0, 0, 0)

    def test_empty_dict_returns_none(self, client):
        result = client._parse_datetime({})
        assert result is None

    def test_invalid_string_returns_none(self, client):
        result = client._parse_datetime({"dateTime": "not-a-date"})
        assert result is None

    def test_invalid_date_format_returns_none(self, client):
        result = client._parse_datetime({"date": "15/03/2026"})
        assert result is None

    def test_datetime_preferred_over_date(self, client):
        """When both dateTime and date are present, dateTime is used."""
        result = client._parse_datetime(
            {
                "dateTime": "2026-03-15T10:00:00Z",
                "date": "2026-03-15",
            }
        )
        assert result == datetime(2026, 3, 15, 10, 0, 0)


# ===================================================================
# 12. CalendarSyncError tests
# ===================================================================


class TestCalendarSyncError:
    """Tests for the CalendarSyncError exception."""

    def test_is_exception(self):
        assert issubclass(CalendarSyncError, Exception)

    def test_message(self):
        exc = CalendarSyncError("something went wrong")
        assert str(exc) == "something went wrong"


# ===================================================================
# 13. Successful HTTP helper tests (happy paths)
# ===================================================================


class TestHTTPHappyPaths:
    """Tests for successful HTTP helper methods."""

    async def test_get_returns_json(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={"key": "value"})
        result = await client._get("https://example.com/api")
        assert result == {"key": "value"}

    async def test_get_passes_params(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={})
        await client._get("https://example.com/api", params={"q": "test"})
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params == {"q": "test"}

    async def test_post_returns_json(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={"id": "new"})
        result = await client._post("https://example.com/api", {"data": 1})
        assert result == {"id": "new"}

    async def test_patch_returns_json(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(json_data={"updated": True})
        result = await client._patch("https://example.com/api", {"field": "val"})
        assert result == {"updated": True}

    async def test_delete_returns_none(self, client, mock_httpx):
        mock_httpx.delete.return_value = _make_response(status_code=204)
        result = await client._delete("https://example.com/api")
        assert result is None

    async def test_post_sends_json_body(self, client, mock_httpx):
        mock_httpx.post.return_value = _make_response(json_data={})
        payload = {"summary": "Test", "start": {"dateTime": "2026-01-01T00:00:00"}}
        await client._post("https://example.com/api", payload)
        call_kwargs = mock_httpx.post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body == payload

    async def test_patch_sends_json_body(self, client, mock_httpx):
        mock_httpx.patch.return_value = _make_response(json_data={})
        payload = {"summary": "Updated"}
        await client._patch("https://example.com/api", payload)
        call_kwargs = mock_httpx.patch.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body == payload

    async def test_get_sends_auth_header(self, client, mock_httpx):
        mock_httpx.get.return_value = _make_response(json_data={})
        await client._get("https://example.com/api")
        call_kwargs = mock_httpx.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer test-access-token"
