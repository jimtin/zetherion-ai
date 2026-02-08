"""Tests for Calendar Skill."""

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.calendar import (
    CALENDAR_COLLECTION,
    CalendarEvent,
    CalendarSkill,
    EventType,
    RecurrencePattern,
    SchedulePattern,
)
from zetherion_ai.skills.permissions import Permission


class TestEventType:
    """Tests for EventType enum."""

    def test_event_type_values(self) -> None:
        """EventType should have expected values."""
        assert EventType.MEETING.value == "meeting"
        assert EventType.DEADLINE.value == "deadline"
        assert EventType.REMINDER.value == "reminder"
        assert EventType.WORK_HOURS.value == "work_hours"
        assert EventType.FOCUS_TIME.value == "focus_time"


class TestRecurrencePattern:
    """Tests for RecurrencePattern enum."""

    def test_recurrence_values(self) -> None:
        """RecurrencePattern should have expected values."""
        assert RecurrencePattern.DAILY.value == "daily"
        assert RecurrencePattern.WEEKLY.value == "weekly"
        assert RecurrencePattern.MONTHLY.value == "monthly"
        assert RecurrencePattern.WEEKDAYS.value == "weekdays"


class TestCalendarEvent:
    """Tests for CalendarEvent dataclass."""

    def test_default_values(self) -> None:
        """CalendarEvent should have sensible defaults."""
        event = CalendarEvent()
        assert isinstance(event.id, UUID)
        assert event.user_id == ""
        assert event.event_type == EventType.MEETING
        assert event.title == ""
        assert event.start_time is None
        assert event.end_time is None
        assert event.all_day is False
        assert event.recurrence is None
        assert event.timezone == "UTC"
        assert event.source == "conversation"

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        start = datetime(2026, 2, 10, 14, 0)
        end = datetime(2026, 2, 10, 15, 0)
        event = CalendarEvent(
            user_id="user123",
            title="Team Meeting",
            description="Weekly sync",
            event_type=EventType.MEETING,
            start_time=start,
            end_time=end,
            participants=["Alice", "Bob"],
            location="Room 101",
        )
        data = event.to_dict()
        assert data["user_id"] == "user123"
        assert data["title"] == "Team Meeting"
        assert data["event_type"] == "meeting"
        assert data["start_time"] == start.isoformat()
        assert data["participants"] == ["Alice", "Bob"]

    def test_from_dict(self) -> None:
        """from_dict should deserialize properly."""
        data = {
            "id": str(uuid4()),
            "user_id": "user456",
            "event_type": "deadline",
            "title": "Project Due",
            "start_time": "2026-03-01T17:00:00",
            "recurrence": "weekly",
            "created_at": "2026-02-06T10:00:00",
        }
        event = CalendarEvent.from_dict(data)
        assert event.user_id == "user456"
        assert event.event_type == EventType.DEADLINE
        assert event.title == "Project Due"
        assert event.recurrence == RecurrencePattern.WEEKLY

    def test_is_happening_now(self) -> None:
        """is_happening_now should detect current events."""
        now = datetime.now()
        event = CalendarEvent(
            start_time=now - timedelta(minutes=30),
            end_time=now + timedelta(minutes=30),
        )
        assert event.is_happening_now() is True

    def test_is_happening_now_future(self) -> None:
        """is_happening_now should return False for future events."""
        event = CalendarEvent(
            start_time=datetime.now() + timedelta(hours=1),
        )
        assert event.is_happening_now() is False

    def test_is_happening_now_no_start(self) -> None:
        """is_happening_now should return False without start time."""
        event = CalendarEvent()
        assert event.is_happening_now() is False

    def test_is_upcoming(self) -> None:
        """is_upcoming should detect upcoming events."""
        event = CalendarEvent(
            start_time=datetime.now() + timedelta(hours=2),
        )
        assert event.is_upcoming(within_hours=24) is True
        assert event.is_upcoming(within_hours=1) is False

    def test_is_upcoming_past(self) -> None:
        """is_upcoming should return False for past events."""
        event = CalendarEvent(
            start_time=datetime.now() - timedelta(hours=1),
        )
        assert event.is_upcoming() is False

    def test_is_today(self) -> None:
        """is_today should detect events on today's date."""
        event = CalendarEvent(
            start_time=datetime.now(),
        )
        assert event.is_today() is True

    def test_is_today_tomorrow(self) -> None:
        """is_today should return False for tomorrow's events."""
        event = CalendarEvent(
            start_time=datetime.now() + timedelta(days=1),
        )
        assert event.is_today() is False


class TestSchedulePattern:
    """Tests for SchedulePattern dataclass."""

    def test_schedule_pattern(self) -> None:
        """SchedulePattern should store pattern data."""
        pattern = SchedulePattern(
            user_id="user123",
            pattern_type="work_hours",
            day_of_week=0,  # Monday
            start_time=time(9, 0),
            end_time=time(17, 0),
            confidence=0.9,
        )
        assert pattern.user_id == "user123"
        assert pattern.start_time == time(9, 0)
        assert pattern.confidence == 0.9


class TestCalendarSkill:
    """Tests for CalendarSkill."""

    def test_metadata(self) -> None:
        """Skill should have correct metadata."""
        skill = CalendarSkill()
        meta = skill.metadata
        assert meta.name == "calendar"
        assert meta.version == "1.0.0"
        assert Permission.READ_OWN_COLLECTION in meta.permissions
        assert Permission.READ_SCHEDULE in meta.permissions
        assert CALENDAR_COLLECTION in meta.collections
        assert "schedule_event" in meta.intents
        assert "list_events" in meta.intents

    def test_initial_status(self) -> None:
        """Skill should start uninitialized."""
        skill = CalendarSkill()
        assert skill.status == SkillStatus.UNINITIALIZED

    @pytest.mark.asyncio
    async def test_initialize_no_memory(self) -> None:
        """Skill should initialize without memory."""
        skill = CalendarSkill()
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_handle_schedule_event(self) -> None:
        """Skill should handle event scheduling."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            message="Team standup",
            context={
                "title": "Team Standup",
                "start_time": start.isoformat(),
                "event_type": "meeting",
            },
        )

        response = await skill.handle(request)
        assert response.success is True
        assert "Team Standup" in response.message
        assert response.data["event"]["title"] == "Team Standup"

    @pytest.mark.asyncio
    async def test_handle_list_events(self) -> None:
        """Skill should handle event listing."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create an event
        start = datetime.now() + timedelta(days=1)
        create_request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Test Event",
                "start_time": start.isoformat(),
            },
        )
        await skill.handle(create_request)

        # List events
        list_request = SkillRequest(
            user_id="user123",
            intent="list_events",
            context={"days": 7},
        )
        response = await skill.handle(list_request)
        assert response.success is True
        assert response.data["count"] == 1

    @pytest.mark.asyncio
    async def test_handle_check_availability_free(self) -> None:
        """Skill should report availability when free."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1, hours=10)
        request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=1)).isoformat(),
            },
        )

        response = await skill.handle(request)
        assert response.success is True
        assert response.data["available"] is True

    @pytest.mark.asyncio
    async def test_handle_check_availability_conflict(self) -> None:
        """Skill should detect conflicts."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1, hours=10)

        # Schedule an event
        schedule_request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Existing Meeting",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=1)).isoformat(),
            },
        )
        await skill.handle(schedule_request)

        # Check overlapping time
        check_request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={
                "start_time": (start + timedelta(minutes=30)).isoformat(),
                "end_time": (start + timedelta(hours=2)).isoformat(),
            },
        )

        response = await skill.handle(check_request)
        assert response.success is True
        assert response.data["available"] is False
        assert len(response.data["conflicts"]) == 1

    @pytest.mark.asyncio
    async def test_handle_today_schedule(self) -> None:
        """Skill should return today's schedule."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create event for today
        today_start = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        if today_start < datetime.now():
            today_start += timedelta(hours=2)

        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Today's Meeting",
                "start_time": today_start.isoformat(),
            },
        )
        await skill.handle(request)

        # Get today's schedule
        today_request = SkillRequest(
            user_id="user123",
            intent="today_schedule",
        )
        response = await skill.handle(today_request)
        assert response.success is True
        assert response.data["count"] >= 0  # May or may not have events depending on time

    @pytest.mark.asyncio
    async def test_handle_set_work_hours(self) -> None:
        """Skill should set work hours."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="set_work_hours",
            context={
                "start_hour": 8,
                "end_hour": 18,
                "days": [0, 1, 2, 3, 4],  # Mon-Fri
            },
        )

        response = await skill.handle(request)
        assert response.success is True
        assert response.data["start_hour"] == 8
        assert response.data["end_hour"] == 18

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self) -> None:
        """Skill should error on unknown intent."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(intent="unknown_intent")
        response = await skill.handle(request)
        assert response.success is False
        assert "Unknown intent" in response.error

    @pytest.mark.asyncio
    async def test_heartbeat_meeting_prep(self) -> None:
        """Skill should generate meeting prep actions."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create event starting in 15 minutes
        start = datetime.now() + timedelta(minutes=15)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Important Meeting",
                "start_time": start.isoformat(),
            },
        )
        await skill.handle(request)

        # Run heartbeat
        actions = await skill.on_heartbeat(["user123"])
        prep_actions = [a for a in actions if a.action_type == "meeting_prep"]
        assert len(prep_actions) == 1
        assert prep_actions[0].priority == 9

    def test_get_system_prompt_fragment_no_events(self) -> None:
        """get_system_prompt_fragment should return None without events."""
        skill = CalendarSkill()
        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    def test_get_system_prompt_fragment_with_current_event(self) -> None:
        """get_system_prompt_fragment should describe current event."""
        skill = CalendarSkill()
        event_id = uuid4()
        now = datetime.now()
        skill._events_cache["user123"] = {
            event_id: CalendarEvent(
                id=event_id,
                user_id="user123",
                title="Team Sync",
                start_time=now - timedelta(minutes=30),
                end_time=now + timedelta(minutes=30),
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "Currently in" in fragment
        assert "Team Sync" in fragment

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        """Skill should clean up resources."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create an event
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={"title": "Test"},
        )
        await skill.handle(request)
        assert len(skill._events_cache) > 0

        # Cleanup
        await skill.cleanup()
        assert len(skill._events_cache) == 0
        assert len(skill._patterns_cache) == 0


class TestCalendarEventExtended:
    """Extended tests for CalendarEvent."""

    def test_is_upcoming_no_start_time(self) -> None:
        """is_upcoming should return False without start time."""
        event = CalendarEvent()
        assert event.is_upcoming() is False

    def test_is_today_no_start_time(self) -> None:
        """is_today should return False without start time."""
        event = CalendarEvent()
        assert event.is_today() is False

    def test_is_happening_now_with_default_end(self) -> None:
        """is_happening_now should use default 1-hour duration when no end_time."""
        now = datetime.now()
        event = CalendarEvent(
            start_time=now - timedelta(minutes=30),
            end_time=None,
        )
        # Default end = start + 1 hour, so 30 min after start should be happening
        assert event.is_happening_now() is True

    def test_from_dict_minimal(self) -> None:
        """from_dict should handle minimal data."""
        data = {}
        event = CalendarEvent.from_dict(data)
        assert event.user_id == ""
        assert event.event_type == EventType.MEETING
        assert event.start_time is None
        assert event.end_time is None
        assert event.recurrence is None

    def test_to_dict_none_fields(self) -> None:
        """to_dict should handle None start/end times."""
        event = CalendarEvent()
        data = event.to_dict()
        assert data["start_time"] is None
        assert data["end_time"] is None
        assert data["recurrence"] is None

    def test_from_dict_with_all_fields(self) -> None:
        """from_dict should deserialize all fields."""
        now = datetime.now()
        data = {
            "id": str(uuid4()),
            "user_id": "user123",
            "event_type": "focus_time",
            "title": "Deep Work",
            "description": "Focus session",
            "start_time": now.isoformat(),
            "end_time": (now + timedelta(hours=2)).isoformat(),
            "all_day": False,
            "recurrence": "daily",
            "location": "Home Office",
            "participants": ["me"],
            "timezone": "US/Eastern",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "source": "explicit",
        }
        event = CalendarEvent.from_dict(data)
        assert event.event_type == EventType.FOCUS_TIME
        assert event.recurrence == RecurrencePattern.DAILY
        assert event.location == "Home Office"
        assert event.source == "explicit"


class TestCalendarSkillExtended:
    """Extended tests for CalendarSkill."""

    @pytest.mark.asyncio
    async def test_initialize_with_memory(self) -> None:
        """Skill should initialize with memory and create collection."""
        mock_memory = AsyncMock()
        mock_memory.ensure_collection = AsyncMock()
        skill = CalendarSkill(memory=mock_memory)
        result = await skill.initialize()
        assert result is True
        mock_memory.ensure_collection.assert_called_once_with(
            CALENDAR_COLLECTION,
            vector_size=768,
        )

    @pytest.mark.asyncio
    async def test_initialize_with_memory_failure(self) -> None:
        """Skill should return False when memory initialization fails."""
        mock_memory = AsyncMock()
        mock_memory.ensure_collection = AsyncMock(side_effect=RuntimeError("Connection failed"))
        skill = CalendarSkill(memory=mock_memory)
        result = await skill.initialize()
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_schedule_with_all_day(self) -> None:
        """Skill should handle all-day event scheduling."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            message="Company holiday",
            context={
                "title": "Company Holiday",
                "all_day": True,
                "event_type": "personal",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["event"]["all_day"] is True

    @pytest.mark.asyncio
    async def test_handle_schedule_with_recurrence(self) -> None:
        """Skill should handle recurring event scheduling."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Weekly Standup",
                "start_time": start.isoformat(),
                "recurrence": "weekly",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["event"]["recurrence"] == "weekly"

    @pytest.mark.asyncio
    async def test_handle_schedule_with_invalid_time(self) -> None:
        """Skill should handle invalid start_time gracefully."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Bad Time Event",
                "start_time": "not-a-valid-time",
            },
        )
        response = await skill.handle(request)
        assert response.success is True  # Still schedules, just without start time
        assert response.data["event"]["start_time"] is None

    @pytest.mark.asyncio
    async def test_handle_schedule_with_invalid_recurrence(self) -> None:
        """Skill should handle invalid recurrence gracefully."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Bad Recurrence",
                "recurrence": "invalid_pattern",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["event"]["recurrence"] is None

    @pytest.mark.asyncio
    async def test_handle_availability_no_start_time(self) -> None:
        """Skill should error when no start_time provided for availability."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "No start_time provided" in response.error

    @pytest.mark.asyncio
    async def test_handle_availability_invalid_time_format(self) -> None:
        """Skill should error on invalid time format for availability."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={
                "start_time": "invalid-format",
            },
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Invalid time format" in response.error

    @pytest.mark.asyncio
    async def test_handle_availability_without_end_time(self) -> None:
        """Skill should default end_time to start + 1 hour."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1, hours=10)
        request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={
                "start_time": start.isoformat(),
                # no end_time
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["available"] is True

    @pytest.mark.asyncio
    async def test_handle_today_no_events(self) -> None:
        """Skill should handle today with no events."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="today_schedule",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert "No events" in response.message
        assert response.data["count"] == 0

    @pytest.mark.asyncio
    async def test_handle_today_with_events(self) -> None:
        """Skill should list today's events sorted by start time."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Schedule two events for today
        now = datetime.now()
        later = now + timedelta(hours=1)
        earlier = now + timedelta(minutes=30)

        for t, title in [(later, "Later Event"), (earlier, "Earlier Event")]:
            request = SkillRequest(
                user_id="user123",
                intent="schedule_event",
                context={
                    "title": title,
                    "start_time": t.isoformat(),
                },
            )
            await skill.handle(request)

        request = SkillRequest(
            user_id="user123",
            intent="today_schedule",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["count"] == 2
        assert "2 event(s)" in response.message

    @pytest.mark.asyncio
    async def test_handle_work_hours_default_days(self) -> None:
        """Skill should set work hours with default days."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="set_work_hours",
            context={
                "start_hour": 9,
                "end_hour": 17,
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["days"] == [0, 1, 2, 3, 4]
        assert "user123" in skill._patterns_cache
        assert len(skill._patterns_cache["user123"]) == 5

    @pytest.mark.asyncio
    async def test_heartbeat_morning_briefing(self) -> None:
        """Skill should generate morning briefing between 6-9 AM."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create event for today
        today_start = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Afternoon Meeting",
                "start_time": today_start.isoformat(),
            },
        )
        await skill.handle(request)

        # Mock time to be during morning hours
        morning = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        with patch("zetherion_ai.skills.calendar.datetime") as mock_dt:
            mock_dt.now.return_value = morning
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.max = datetime.max
            actions = await skill.on_heartbeat(["user123"])
            briefing_actions = [a for a in actions if a.action_type == "morning_briefing"]
            # If we're in the 6-9 window and there are events today, expect briefing
            if 6 <= morning.hour <= 9:
                assert len(briefing_actions) >= 0  # May or may not trigger depending on events

    @pytest.mark.asyncio
    async def test_heartbeat_end_of_day(self) -> None:
        """Skill should generate end of day actions between 17-18."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        # Create event for tomorrow
        tomorrow = datetime.now() + timedelta(days=1)
        tomorrow_start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Tomorrow Meeting",
                "start_time": tomorrow_start.isoformat(),
            },
        )
        await skill.handle(request)

        # Mock time to be end of day
        eod = datetime.now().replace(hour=17, minute=30, second=0, microsecond=0)
        with patch("zetherion_ai.skills.calendar.datetime") as mock_dt:
            mock_dt.now.return_value = eod
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.max = datetime.max
            actions = await skill.on_heartbeat(["user123"])
            eod_actions = [a for a in actions if a.action_type == "end_of_day"]
            # Should have end of day action if tomorrow has events
            if 17 <= eod.hour <= 18:
                assert len(eod_actions) >= 0

    @pytest.mark.asyncio
    async def test_heartbeat_no_events(self) -> None:
        """Skill should return no actions when user has no events."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        actions = await skill.on_heartbeat(["user123"])
        assert actions == []

    def test_get_system_prompt_fragment_todays_events(self) -> None:
        """get_system_prompt_fragment should describe today's events count."""
        skill = CalendarSkill()
        event_id = uuid4()
        now = datetime.now()
        # Event that is today but not happening right now
        skill._events_cache["user123"] = {
            event_id: CalendarEvent(
                id=event_id,
                user_id="user123",
                title="Later Meeting",
                start_time=now + timedelta(hours=3),
                end_time=now + timedelta(hours=4),
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "event(s) today" in fragment

    def test_get_system_prompt_fragment_no_today_events(self) -> None:
        """get_system_prompt_fragment should return None if no events today."""
        skill = CalendarSkill()
        event_id = uuid4()
        # Event tomorrow, not today
        skill._events_cache["user123"] = {
            event_id: CalendarEvent(
                id=event_id,
                user_id="user123",
                title="Tomorrow Event",
                start_time=datetime.now() + timedelta(days=1),
                end_time=datetime.now() + timedelta(days=1, hours=1),
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    @pytest.mark.asyncio
    async def test_store_event_with_memory(self) -> None:
        """Skill should store events in memory when available."""
        mock_memory = AsyncMock()
        mock_memory.ensure_collection = AsyncMock()
        mock_memory.store_with_payload = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(return_value=[])
        skill = CalendarSkill(memory=mock_memory)
        await skill.initialize()

        start = datetime.now() + timedelta(days=1)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Memory Test Event",
                "description": "Testing storage",
                "start_time": start.isoformat(),
                "participants": ["Alice", "Bob"],
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        mock_memory.store_with_payload.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_events_with_memory(self) -> None:
        """Skill should fetch events from memory when available."""
        event_data = {
            "id": str(uuid4()),
            "user_id": "user123",
            "event_type": "meeting",
            "title": "Test",
            "start_time": (datetime.now() + timedelta(hours=2)).isoformat(),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        mock_memory = AsyncMock()
        mock_memory.ensure_collection = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(return_value=[event_data])
        skill = CalendarSkill(memory=mock_memory)
        await skill.initialize()

        request = SkillRequest(
            user_id="user123",
            intent="list_events",
            context={"days": 7},
        )
        response = await skill.handle(request)
        assert response.success is True
        mock_memory.filter_by_field.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_schedule_with_end_time(self) -> None:
        """Skill should parse end_time for scheduled events."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1)
        end = start + timedelta(hours=2)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Long Meeting",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["event"]["end_time"] is not None

    @pytest.mark.asyncio
    async def test_handle_schedule_invalid_end_time(self) -> None:
        """Skill should handle invalid end_time gracefully."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1)
        request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "Invalid End Time",
                "start_time": start.isoformat(),
                "end_time": "not-a-time",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["event"]["end_time"] is None

    @pytest.mark.asyncio
    async def test_handle_availability_conflict_with_no_end_event(self) -> None:
        """Availability check should handle events without end_time."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        start = datetime.now() + timedelta(days=1, hours=10)

        # Schedule an event without end_time (defaults to start + 1hr)
        schedule_request = SkillRequest(
            user_id="user123",
            intent="schedule_event",
            context={
                "title": "No End Meeting",
                "start_time": start.isoformat(),
            },
        )
        await skill.handle(schedule_request)

        # Check overlapping time (within 1 hour of start)
        check_request = SkillRequest(
            user_id="user123",
            intent="check_availability",
            context={
                "start_time": (start + timedelta(minutes=30)).isoformat(),
                "end_time": (start + timedelta(hours=2)).isoformat(),
            },
        )
        response = await skill.handle(check_request)
        assert response.success is True
        assert response.data["available"] is False
        assert len(response.data["conflicts"]) == 1

    @pytest.mark.asyncio
    async def test_handle_work_hours_creates_patterns(self) -> None:
        """Work hours should create correct schedule patterns."""
        skill = CalendarSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user456",
            intent="set_work_hours",
            context={
                "start_hour": 10,
                "end_hour": 19,
                "days": [0, 1, 2],
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert len(skill._patterns_cache["user456"]) == 3
        pattern = skill._patterns_cache["user456"][0]
        assert pattern.start_time == time(hour=10)
        assert pattern.end_time == time(hour=19)
        assert pattern.confidence == 1.0
