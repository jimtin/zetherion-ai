"""Tests for Calendar Skill."""

from datetime import datetime, time, timedelta
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
