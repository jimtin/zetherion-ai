"""Google Calendar sync for Zetherion AI.

Provides calendar event listing, creation, and bidirectional sync
using the Google Calendar API via the same OAuth tokens as Gmail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.gmail.calendar_sync")

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class CalendarSyncError(Exception):
    """Raised when calendar operations fail."""


@dataclass
class CalendarEvent:
    """Represents a Google Calendar event."""

    event_id: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    attendees: list[str] = field(default_factory=list)
    status: str = "confirmed"  # confirmed, tentative, cancelled
    organizer: str = ""
    html_link: str = ""
    calendar_id: str = "primary"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "all_day": self.all_day,
            "attendees": self.attendees,
            "status": self.status,
            "organizer": self.organizer,
            "html_link": self.html_link,
            "calendar_id": self.calendar_id,
        }

    @property
    def duration_minutes(self) -> int:
        """Duration in minutes (0 if start or end is missing)."""
        if self.start and self.end:
            delta = self.end - self.start
            return int(delta.total_seconds() / 60)
        return 0


class CalendarClient:
    """Async Google Calendar API client.

    Supports listing events, creating events, and checking availability.
    Uses the same access token as Gmail (shared OAuth consent).
    """

    def __init__(self, access_token: str, *, timeout: float = 30.0) -> None:
        """Initialize the calendar client.

        Args:
            access_token: Google OAuth2 access token.
            timeout: HTTP request timeout.
        """
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        max_results: int = 50,
        single_events: bool = True,
    ) -> list[CalendarEvent]:
        """List calendar events within a time range.

        Args:
            calendar_id: Calendar ID (default "primary").
            time_min: Start of time range.
            time_max: End of time range.
            max_results: Maximum events.
            single_events: Expand recurring events.

        Returns:
            List of CalendarEvents.
        """
        url = f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events"
        params: dict[str, Any] = {
            "maxResults": max_results,
            "singleEvents": str(single_events).lower(),
            "orderBy": "startTime" if single_events else "updated",
        }

        if time_min:
            params["timeMin"] = time_min.isoformat() + "Z"
        if time_max:
            params["timeMax"] = time_max.isoformat() + "Z"

        data = await self._get(url, params)
        items = data.get("items", [])

        events = [self._parse_event(item, calendar_id) for item in items]
        log.debug("events_listed", calendar=calendar_id, count=len(events))
        return events

    async def get_event(self, event_id: str, *, calendar_id: str = "primary") -> CalendarEvent:
        """Get a single event by ID."""
        url = f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        data = await self._get(url)
        return self._parse_event(data, calendar_id)

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        *,
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> CalendarEvent:
        """Create a new calendar event.

        Args:
            summary: Event title.
            start: Start time.
            end: End time.
            calendar_id: Calendar ID.
            description: Event description.
            location: Event location.
            attendees: List of attendee email addresses.

        Returns:
            Created CalendarEvent.
        """
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }

        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        url = f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events"
        data = await self._post(url, body)

        event = self._parse_event(data, calendar_id)
        log.info("event_created", event_id=event.event_id, summary=summary)
        return event

    async def update_event(
        self,
        event_id: str,
        *,
        calendar_id: str = "primary",
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> CalendarEvent:
        """Update an existing calendar event."""
        body: dict[str, Any] = {}

        if summary is not None:
            body["summary"] = summary
        if start is not None:
            body["start"] = {"dateTime": start.isoformat(), "timeZone": "UTC"}
        if end is not None:
            body["end"] = {"dateTime": end.isoformat(), "timeZone": "UTC"}
        if description is not None:
            body["description"] = description
        if location is not None:
            body["location"] = location

        url = f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        data = await self._patch(url, body)

        event = self._parse_event(data, calendar_id)
        log.info("event_updated", event_id=event_id)
        return event

    async def delete_event(self, event_id: str, *, calendar_id: str = "primary") -> bool:
        """Delete a calendar event."""
        url = f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        await self._delete(url)
        log.info("event_deleted", event_id=event_id)
        return True

    async def get_today_events(self) -> list[CalendarEvent]:
        """Get all events for today."""
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = now + timedelta(days=1)
        return await self.list_events(time_min=now, time_max=end_of_day)

    async def get_upcoming_events(self, hours: int = 24) -> list[CalendarEvent]:
        """Get events in the next N hours."""
        now = datetime.now()
        end = now + timedelta(hours=hours)
        return await self.list_events(time_min=now, time_max=end)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            except httpx.HTTPStatusError as exc:
                raise CalendarSyncError(
                    f"Calendar API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise CalendarSyncError(f"Calendar API request failed: {exc}") from exc

    async def _post(self, url: str, json_data: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(url, headers=self._headers(), json=json_data)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            except httpx.HTTPStatusError as exc:
                raise CalendarSyncError(
                    f"Calendar API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise CalendarSyncError(f"Calendar API request failed: {exc}") from exc

    async def _patch(self, url: str, json_data: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.patch(url, headers=self._headers(), json=json_data)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            except httpx.HTTPStatusError as exc:
                raise CalendarSyncError(
                    f"Calendar API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise CalendarSyncError(f"Calendar API request failed: {exc}") from exc

    async def _delete(self, url: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.delete(url, headers=self._headers())
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise CalendarSyncError(
                    f"Calendar API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise CalendarSyncError(f"Calendar API request failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_event(self, data: dict[str, Any], calendar_id: str = "primary") -> CalendarEvent:
        """Parse a Google Calendar API event response."""
        start_data = data.get("start", {})
        end_data = data.get("end", {})

        # Handle all-day vs timed events
        all_day = "date" in start_data and "dateTime" not in start_data

        start = self._parse_datetime(start_data)
        end = self._parse_datetime(end_data)

        # Parse attendees
        attendees = [a.get("email", "") for a in data.get("attendees", []) if a.get("email")]

        # Parse organizer
        organizer = data.get("organizer", {}).get("email", "")

        return CalendarEvent(
            event_id=data.get("id", ""),
            summary=data.get("summary", ""),
            description=data.get("description", ""),
            location=data.get("location", ""),
            start=start,
            end=end,
            all_day=all_day,
            attendees=attendees,
            status=data.get("status", "confirmed"),
            organizer=organizer,
            html_link=data.get("htmlLink", ""),
            calendar_id=calendar_id,
        )

    def _parse_datetime(self, dt_data: dict[str, str]) -> datetime | None:
        """Parse datetime from Google Calendar format."""
        dt_str = dt_data.get("dateTime") or dt_data.get("date")
        if not dt_str:
            return None
        try:
            # Handle ISO format with timezone
            if "T" in dt_str:
                # Remove timezone suffix for naive datetime
                clean = dt_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean)
                return dt.replace(tzinfo=None)
            # Date-only (all-day events)
            return datetime.strptime(dt_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
