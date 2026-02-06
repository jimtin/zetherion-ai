"""Calendar Awareness Skill for SecureClaw.

Provides calendar and schedule awareness capabilities:
- Track known schedule patterns (work hours, recurring meetings)
- Understand availability from conversation context
- Timezone awareness from user profile
- Meeting prep summaries
- Morning briefings and end-of-day summaries

Note: This is awareness-based initially - it learns your schedule
from conversation. Full calendar API integration is a future feature.
"""

import contextlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.calendar")

# Collection name for calendar storage
CALENDAR_COLLECTION = "skill_calendar"


class EventType(Enum):
    """Types of calendar events."""

    MEETING = "meeting"
    DEADLINE = "deadline"
    REMINDER = "reminder"
    WORK_HOURS = "work_hours"
    BREAK = "break"
    FOCUS_TIME = "focus_time"
    PERSONAL = "personal"
    RECURRING = "recurring"


class RecurrencePattern(Enum):
    """Recurrence patterns for events."""

    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    WEEKDAYS = "weekdays"


@dataclass
class CalendarEvent:
    """A calendar event or schedule entry."""

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    event_type: EventType = EventType.MEETING
    title: str = ""
    description: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool = False
    recurrence: RecurrencePattern | None = None
    location: str | None = None
    participants: list[str] = field(default_factory=list)
    timezone: str = "UTC"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    source: str = "conversation"  # "conversation", "explicit", "calendar_api"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "event_type": self.event_type.value,
            "title": self.title,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "all_day": self.all_day,
            "recurrence": self.recurrence.value if self.recurrence else None,
            "location": self.location,
            "participants": self.participants,
            "timezone": self.timezone,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalendarEvent":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            event_type=EventType(data["event_type"])
            if data.get("event_type")
            else EventType.MEETING,
            title=data.get("title", ""),
            description=data.get("description", ""),
            start_time=datetime.fromisoformat(data["start_time"])
            if data.get("start_time")
            else None,
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            all_day=data.get("all_day", False),
            recurrence=RecurrencePattern(data["recurrence"]) if data.get("recurrence") else None,
            location=data.get("location"),
            participants=data.get("participants", []),
            timezone=data.get("timezone", "UTC"),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at")
            else datetime.now(),
            source=data.get("source", "conversation"),
        )

    def is_happening_now(self) -> bool:
        """Check if event is currently happening."""
        if not self.start_time:
            return False
        now = datetime.now()
        end = self.end_time or (self.start_time + timedelta(hours=1))
        return self.start_time <= now <= end

    def is_upcoming(self, within_hours: int = 24) -> bool:
        """Check if event is within the next N hours."""
        if not self.start_time:
            return False
        now = datetime.now()
        return now < self.start_time < now + timedelta(hours=within_hours)

    def is_today(self) -> bool:
        """Check if event is today."""
        if not self.start_time:
            return False
        return self.start_time.date() == date.today()


@dataclass
class SchedulePattern:
    """A learned schedule pattern."""

    user_id: str
    pattern_type: str  # "work_hours", "meeting_day", "focus_time"
    day_of_week: int | None = None  # 0=Monday, 6=Sunday
    start_time: time | None = None
    end_time: time | None = None
    confidence: float = 0.5
    occurrences: int = 1


class CalendarSkill(Skill):
    """Skill for calendar awareness and schedule management.

    Intents handled:
    - schedule_event: Add an event to the calendar
    - list_events: List upcoming events
    - check_availability: Check if a time slot is free
    - today_schedule: Get today's schedule
    - set_work_hours: Define work hours

    Heartbeat actions:
    - morning_briefing: Daily schedule summary
    - meeting_prep: Pre-meeting context
    - end_of_day: End of day summary
    """

    INTENTS = [
        "schedule_event",
        "list_events",
        "check_availability",
        "today_schedule",
        "set_work_hours",
    ]

    def __init__(self, memory: "QdrantMemory | None" = None):
        """Initialize the calendar skill."""
        super().__init__(memory=memory)
        self._events_cache: dict[str, dict[UUID, CalendarEvent]] = {}
        self._patterns_cache: dict[str, list[SchedulePattern]] = {}

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="calendar",
            description="Calendar awareness, schedule tracking, and meeting preparation",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_OWN_COLLECTION,
                    Permission.WRITE_OWN_COLLECTION,
                    Permission.SEND_MESSAGES,
                    Permission.READ_PROFILE,
                    Permission.READ_SCHEDULE,
                }
            ),
            collections=[CALENDAR_COLLECTION],
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        """Initialize the skill and create collection if needed."""
        if not self._memory:
            log.warning("calendar_no_memory", msg="No memory provided, using in-memory only")
            return True

        try:
            await self._memory.ensure_collection(
                CALENDAR_COLLECTION,
                vector_size=768,
            )
            log.info("calendar_initialized", collection=CALENDAR_COLLECTION)
            return True
        except Exception as e:
            log.error("calendar_init_failed", error=str(e))
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a calendar request."""
        handlers = {
            "schedule_event": self._handle_schedule,
            "list_events": self._handle_list,
            "check_availability": self._handle_availability,
            "today_schedule": self._handle_today,
            "set_work_hours": self._handle_work_hours,
        }

        handler = handlers.get(request.intent)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {request.intent}",
            )

        return await handler(request)

    async def _handle_schedule(self, request: SkillRequest) -> SkillResponse:
        """Handle event scheduling."""
        context = request.context

        event = CalendarEvent(
            user_id=request.user_id,
            title=context.get("title", request.message),
            description=context.get("description", ""),
            event_type=EventType(context.get("event_type", "meeting")),
            location=context.get("location"),
            participants=context.get("participants", []),
        )

        # Parse times
        if context.get("start_time"):
            with contextlib.suppress(ValueError):
                event.start_time = datetime.fromisoformat(context["start_time"])

        if context.get("end_time"):
            with contextlib.suppress(ValueError):
                event.end_time = datetime.fromisoformat(context["end_time"])

        if context.get("all_day"):
            event.all_day = True

        # Parse recurrence
        if context.get("recurrence"):
            with contextlib.suppress(ValueError):
                event.recurrence = RecurrencePattern(context["recurrence"])

        await self._store_event(event)

        log.info(
            "event_scheduled",
            event_id=str(event.id),
            user_id=request.user_id,
            title=event.title,
        )

        return SkillResponse(
            request_id=request.id,
            message=f"Scheduled: {event.title}",
            data={"event": event.to_dict()},
        )

    async def _handle_list(self, request: SkillRequest) -> SkillResponse:
        """Handle event listing."""
        context = request.context
        days_ahead = context.get("days", 7)

        events = await self._get_user_events(request.user_id)

        # Filter to upcoming events
        now = datetime.now()
        cutoff = now + timedelta(days=days_ahead)
        upcoming = [e for e in events if e.start_time and now <= e.start_time <= cutoff]

        # Sort by start time
        upcoming.sort(key=lambda e: e.start_time or datetime.max)

        return SkillResponse(
            request_id=request.id,
            message=f"Found {len(upcoming)} event(s) in the next {days_ahead} day(s)",
            data={
                "events": [e.to_dict() for e in upcoming],
                "count": len(upcoming),
            },
        )

    async def _handle_availability(self, request: SkillRequest) -> SkillResponse:
        """Check availability for a time slot."""
        context = request.context

        start_str = context.get("start_time")
        end_str = context.get("end_time")

        if not start_str:
            return SkillResponse.error_response(request.id, "No start_time provided")

        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str) if end_str else start + timedelta(hours=1)
        except ValueError:
            return SkillResponse.error_response(request.id, "Invalid time format")

        events = await self._get_user_events(request.user_id)

        # Check for conflicts
        conflicts = []
        for event in events:
            if not event.start_time:
                continue
            event_end = event.end_time or (event.start_time + timedelta(hours=1))
            # Check for overlap
            if event.start_time < end and event_end > start:
                conflicts.append(event)

        available = len(conflicts) == 0

        return SkillResponse(
            request_id=request.id,
            message="Available" if available else f"Busy - {len(conflicts)} conflict(s)",
            data={
                "available": available,
                "conflicts": [e.to_dict() for e in conflicts],
            },
        )

    async def _handle_today(self, request: SkillRequest) -> SkillResponse:
        """Get today's schedule."""
        events = await self._get_user_events(request.user_id)

        today = date.today()
        todays_events = [e for e in events if e.start_time and e.start_time.date() == today]

        # Sort by start time
        todays_events.sort(key=lambda e: e.start_time or datetime.max)

        if not todays_events:
            message = "No events scheduled for today"
        else:
            message = f"Today: {len(todays_events)} event(s)"

        return SkillResponse(
            request_id=request.id,
            message=message,
            data={
                "events": [e.to_dict() for e in todays_events],
                "count": len(todays_events),
            },
        )

    async def _handle_work_hours(self, request: SkillRequest) -> SkillResponse:
        """Set work hours pattern."""
        context = request.context

        start_hour = context.get("start_hour", 9)
        end_hour = context.get("end_hour", 17)
        days = context.get("days", [0, 1, 2, 3, 4])  # Default: Mon-Fri

        # Create work hours event (recurring)
        for day in days:
            pattern = SchedulePattern(
                user_id=request.user_id,
                pattern_type="work_hours",
                day_of_week=day,
                start_time=time(hour=start_hour),
                end_time=time(hour=end_hour),
                confidence=1.0,
            )
            if request.user_id not in self._patterns_cache:
                self._patterns_cache[request.user_id] = []
            self._patterns_cache[request.user_id].append(pattern)

        return SkillResponse(
            request_id=request.id,
            message=f"Work hours set: {start_hour}:00 - {end_hour}:00",
            data={
                "start_hour": start_hour,
                "end_hour": end_hour,
                "days": days,
            },
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for calendar-related actions."""
        actions: list[HeartbeatAction] = []
        now = datetime.now()

        for user_id in user_ids:
            events = await self._get_user_events(user_id)

            # Morning briefing (if it's morning and we haven't sent one)
            if 6 <= now.hour <= 9:
                todays_events = [e for e in events if e.is_today()]
                if todays_events:
                    actions.append(
                        HeartbeatAction(
                            skill_name=self.name,
                            action_type="morning_briefing",
                            user_id=user_id,
                            data={
                                "events": [e.to_dict() for e in todays_events],
                                "count": len(todays_events),
                            },
                            priority=7,
                        )
                    )

            # Meeting prep (15 minutes before)
            for event in events:
                if event.start_time:
                    time_until = event.start_time - now
                    if timedelta(minutes=10) < time_until < timedelta(minutes=20):
                        actions.append(
                            HeartbeatAction(
                                skill_name=self.name,
                                action_type="meeting_prep",
                                user_id=user_id,
                                data={
                                    "event": event.to_dict(),
                                    "minutes_until": int(time_until.total_seconds() / 60),
                                },
                                priority=9,
                            )
                        )

            # End of day summary (if it's end of work day)
            if 17 <= now.hour <= 18:
                tomorrows_date = date.today() + timedelta(days=1)
                tomorrows_events = [
                    e for e in events if e.start_time and e.start_time.date() == tomorrows_date
                ]
                if tomorrows_events:
                    actions.append(
                        HeartbeatAction(
                            skill_name=self.name,
                            action_type="end_of_day",
                            user_id=user_id,
                            data={
                                "tomorrow_events": [e.to_dict() for e in tomorrows_events],
                                "count": len(tomorrows_events),
                            },
                            priority=5,
                        )
                    )

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return context about user's schedule for the system prompt."""
        if user_id not in self._events_cache:
            return None

        events = list(self._events_cache[user_id].values())
        todays_events = [e for e in events if e.is_today()]
        current_event = next((e for e in events if e.is_happening_now()), None)

        if not todays_events and not current_event:
            return None

        parts = []
        if current_event:
            parts.append(f"Currently in: {current_event.title}")
        if todays_events:
            parts.append(f"{len(todays_events)} event(s) today")

        return " | ".join(parts)

    # Helper methods

    async def _store_event(self, event: CalendarEvent) -> None:
        """Store an event."""
        if event.user_id not in self._events_cache:
            self._events_cache[event.user_id] = {}
        self._events_cache[event.user_id][event.id] = event

        if self._memory:
            search_text = f"{event.title} {event.description}"
            if event.participants:
                search_text += " " + " ".join(event.participants)

            await self._memory.store_with_payload(
                collection_name=CALENDAR_COLLECTION,
                text=search_text,
                payload=event.to_dict(),
                point_id=str(event.id),
            )

    async def _get_user_events(self, user_id: str) -> list[CalendarEvent]:
        """Get all events for a user."""
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=CALENDAR_COLLECTION,
                field="user_id",
                value=user_id,
            )
            events = [CalendarEvent.from_dict(r) for r in results]
            self._events_cache[user_id] = {e.id: e for e in events}
            return events

        if user_id in self._events_cache:
            return list(self._events_cache[user_id].values())

        return []

    async def cleanup(self) -> None:
        """Clean up resources."""
        self._events_cache.clear()
        self._patterns_cache.clear()
        log.info("calendar_cleanup_complete")
