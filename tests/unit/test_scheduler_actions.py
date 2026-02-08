"""Tests for scheduler actions module."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from zetherion_ai.scheduler.actions import (
    ActionExecutor,
    ActionResult,
    ScheduledEvent,
    ScheduledEventStatus,
)
from zetherion_ai.skills.base import HeartbeatAction


class TestScheduledEventStatus:
    """Tests for ScheduledEventStatus enum."""

    def test_status_values(self) -> None:
        """ScheduledEventStatus should have expected values."""
        assert ScheduledEventStatus.PENDING.value == "pending"
        assert ScheduledEventStatus.TRIGGERED.value == "triggered"
        assert ScheduledEventStatus.COMPLETED.value == "completed"
        assert ScheduledEventStatus.FAILED.value == "failed"
        assert ScheduledEventStatus.CANCELLED.value == "cancelled"


class TestScheduledEvent:
    """Tests for ScheduledEvent dataclass."""

    def test_default_values(self) -> None:
        """ScheduledEvent should have sensible defaults."""
        event = ScheduledEvent()
        assert isinstance(event.id, UUID)
        assert event.user_id == ""
        assert event.skill_name == ""
        assert event.status == ScheduledEventStatus.PENDING
        assert event.triggered_at is None
        assert event.error is None

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        trigger = datetime.now() + timedelta(hours=1)
        event = ScheduledEvent(
            user_id="user123",
            skill_name="task_manager",
            action_type="reminder",
            trigger_time=trigger,
            data={"task_id": "abc"},
        )
        data = event.to_dict()
        assert data["user_id"] == "user123"
        assert data["skill_name"] == "task_manager"
        assert data["action_type"] == "reminder"
        assert data["status"] == "pending"
        assert data["data"] == {"task_id": "abc"}

    def test_from_dict(self) -> None:
        """from_dict should deserialize properly."""
        trigger = datetime.now() + timedelta(hours=1)
        data = {
            "id": str(uuid4()),
            "user_id": "user456",
            "skill_name": "calendar",
            "action_type": "meeting_prep",
            "trigger_time": trigger.isoformat(),
            "status": "completed",
            "created_at": datetime.now().isoformat(),
        }
        event = ScheduledEvent.from_dict(data)
        assert event.user_id == "user456"
        assert event.skill_name == "calendar"
        assert event.status == ScheduledEventStatus.COMPLETED

    def test_is_due_pending_future(self) -> None:
        """is_due should return False for future pending events."""
        event = ScheduledEvent(
            trigger_time=datetime.now() + timedelta(hours=1),
        )
        assert event.is_due() is False

    def test_is_due_pending_past(self) -> None:
        """is_due should return True for past pending events."""
        event = ScheduledEvent(
            trigger_time=datetime.now() - timedelta(minutes=1),
        )
        assert event.is_due() is True

    def test_is_due_not_pending(self) -> None:
        """is_due should return False for non-pending events."""
        event = ScheduledEvent(
            trigger_time=datetime.now() - timedelta(minutes=1),
            status=ScheduledEventStatus.COMPLETED,
        )
        assert event.is_due() is False


class TestActionResult:
    """Tests for ActionResult dataclass."""

    def test_success_result(self) -> None:
        """ActionResult should default to success."""
        action = HeartbeatAction(
            skill_name="test",
            action_type="test",
            user_id="user123",
        )
        result = ActionResult(action=action)
        assert result.success is True
        assert result.error is None

    def test_failure_result(self) -> None:
        """ActionResult should handle failures."""
        action = HeartbeatAction(
            skill_name="test",
            action_type="test",
            user_id="user123",
        )
        result = ActionResult(
            action=action,
            success=False,
            error="Something went wrong",
        )
        assert result.success is False
        assert result.error == "Something went wrong"

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        action = HeartbeatAction(
            skill_name="test",
            action_type="test",
            user_id="user123",
        )
        result = ActionResult(action=action, message="Done!")
        data = result.to_dict()
        assert data["success"] is True
        assert data["message"] == "Done!"
        assert "action" in data


class MockMessageSender:
    """Mock message sender for testing."""

    def __init__(self, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.sent_messages: list[tuple[str, str]] = []

    async def send_dm(self, user_id: str, message: str) -> bool:
        self.sent_messages.append((user_id, message))
        return self.should_succeed


class TestActionExecutor:
    """Tests for ActionExecutor class."""

    def test_init(self) -> None:
        """ActionExecutor should initialize properly."""
        executor = ActionExecutor()
        assert executor._message_sender is None

    def test_init_with_sender(self) -> None:
        """ActionExecutor should accept message sender."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)
        assert executor._message_sender is sender

    def test_rate_limiting(self) -> None:
        """ActionExecutor should track messages per user."""
        executor = ActionExecutor()
        # Initially can send
        assert executor._can_send_message("user123") is True

        # Record some messages
        for _ in range(3):
            executor._record_message("user123")

        # Now rate limited
        assert executor._can_send_message("user123") is False

        # Different user not affected
        assert executor._can_send_message("user456") is True

    @pytest.mark.asyncio
    async def test_execute_send_message_success(self) -> None:
        """execute should send messages successfully."""
        sender = MockMessageSender(should_succeed=True)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )

        result = await executor.execute(action)
        assert result.success is True
        assert len(sender.sent_messages) == 1
        assert sender.sent_messages[0] == ("user123", "Hello!")

    @pytest.mark.asyncio
    async def test_execute_send_message_no_sender(self) -> None:
        """execute should handle missing message sender."""
        executor = ActionExecutor()  # No sender

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )

        result = await executor.execute(action)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_execute_rate_limited(self) -> None:
        """execute should respect rate limits."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        # Fill up the rate limit
        for _ in range(3):
            executor._record_message("user123")

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )

        result = await executor.execute(action)
        assert result.success is False
        assert "rate limit" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_deadline_reminder(self) -> None:
        """execute should handle deadline reminders."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="deadline_reminder",
            user_id="user123",
            data={
                "tasks": [{"title": "Buy groceries"}],
                "count": 1,
            },
        )

        result = await executor.execute(action)
        assert result.success is True
        assert len(sender.sent_messages) == 1
        assert "groceries" in sender.sent_messages[0][1]
        assert "⏰" in sender.sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_execute_overdue_alert(self) -> None:
        """execute should handle overdue alerts."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="overdue_alert",
            user_id="user123",
            data={"count": 2},
        )

        result = await executor.execute(action)
        assert result.success is True
        assert "🚨" in sender.sent_messages[0][1]
        assert "2" in sender.sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_execute_morning_briefing(self) -> None:
        """execute should handle morning briefings."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user123",
            data={"events": [], "count": 0},
        )

        result = await executor.execute(action)
        assert result.success is True
        assert "☀️" in sender.sent_messages[0][1]
        assert "morning" in sender.sent_messages[0][1].lower()

    @pytest.mark.asyncio
    async def test_execute_meeting_prep(self) -> None:
        """execute should handle meeting prep."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="meeting_prep",
            user_id="user123",
            data={
                "event": {"title": "Team Standup"},
                "minutes_until": 15,
            },
        )

        result = await executor.execute(action)
        assert result.success is True
        assert "📅" in sender.sent_messages[0][1]
        assert "Team Standup" in sender.sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self) -> None:
        """execute should handle unknown action types gracefully."""
        executor = ActionExecutor()

        action = HeartbeatAction(
            skill_name="test",
            action_type="unknown_action_xyz",
            user_id="user123",
        )

        result = await executor.execute(action)
        assert result.success is True  # Unknown actions don't fail
        assert "not implemented" in result.message.lower()

    @pytest.mark.asyncio
    async def test_execute_update_memory(self) -> None:
        """execute should handle non-messaging actions."""
        executor = ActionExecutor()

        action = HeartbeatAction(
            skill_name="test",
            action_type="update_memory",
            user_id="user123",
            data={"key": "value"},
        )

        result = await executor.execute(action)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_confirm_low_confidence(self) -> None:
        """execute should handle profile confirmation requests."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="profile_manager",
            action_type="confirm_low_confidence",
            user_id="user123",
            data={
                "entry": {
                    "key": "timezone",
                    "value": "America/New_York",
                }
            },
        )

        result = await executor.execute(action)
        assert result.success is True
        assert "❓" in sender.sent_messages[0][1]
        assert "timezone" in sender.sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_morning_briefing_count_zero(self) -> None:
        """_handle_morning_briefing should say calendar is clear for count=0."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user123",
            data={"events": [], "count": 0},
        )
        result = await executor.execute(action)
        assert result.success is True
        assert "clear" in sender.sent_messages[0][1].lower()

    @pytest.mark.asyncio
    async def test_morning_briefing_count_one(self) -> None:
        """_handle_morning_briefing should mention single event title for count=1."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user123",
            data={
                "events": [{"title": "Team Standup"}],
                "count": 1,
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "1 event" in msg
        assert "Team Standup" in msg

    @pytest.mark.asyncio
    async def test_morning_briefing_count_multiple(self) -> None:
        """_handle_morning_briefing should show count for multiple events."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user123",
            data={
                "events": [{"title": "E1"}, {"title": "E2"}, {"title": "E3"}],
                "count": 3,
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "3" in msg
        assert "events" in msg.lower()

    @pytest.mark.asyncio
    async def test_morning_briefing_count_one_empty_events_list(self) -> None:
        """_handle_morning_briefing with count=1 but empty events list uses fallback."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="morning_briefing",
            user_id="user123",
            data={"events": [], "count": 1},
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "1 event" in msg
        # Should fall back to 'Event' when list is empty
        assert "Event" in msg

    @pytest.mark.asyncio
    async def test_deadline_reminder_count_multiple(self) -> None:
        """_handle_deadline_reminder should use generic message for count > 1."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="deadline_reminder",
            user_id="user123",
            data={
                "tasks": [{"title": "T1"}, {"title": "T2"}],
                "count": 2,
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "2" in msg
        assert "due within 24 hours" in msg

    @pytest.mark.asyncio
    async def test_deadline_reminder_count_one_empty_tasks(self) -> None:
        """_handle_deadline_reminder with count=1 but no tasks uses fallback title."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="deadline_reminder",
            user_id="user123",
            data={"tasks": [], "count": 1},
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "Task" in msg
        assert "due within 24 hours" in msg

    @pytest.mark.asyncio
    async def test_end_of_day_count_zero(self) -> None:
        """_handle_end_of_day should say calendar is clear for count=0."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="end_of_day",
            user_id="user123",
            data={"tomorrow_events": [], "count": 0},
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "clear" in msg.lower()

    @pytest.mark.asyncio
    async def test_end_of_day_count_multiple(self) -> None:
        """_handle_end_of_day should show count of tomorrow's events."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="end_of_day",
            user_id="user123",
            data={
                "tomorrow_events": [{"title": "E1"}, {"title": "E2"}],
                "count": 2,
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "2" in msg
        assert "event(s)" in msg

    @pytest.mark.asyncio
    async def test_decay_check_empty_data(self) -> None:
        """_handle_decay_check should handle empty categories."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="profile_manager",
            action_type="decay_check",
            user_id="user123",
            data={"stale_count": 0, "categories": []},
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        # When categories is empty, should fall back to "profile"
        assert "profile" in msg.lower()

    @pytest.mark.asyncio
    async def test_decay_check_with_categories(self) -> None:
        """_handle_decay_check should list categories."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="profile_manager",
            action_type="decay_check",
            user_id="user123",
            data={
                "stale_count": 5,
                "categories": ["preferences", "work", "personal"],
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "preferences" in msg
        assert "5 entries" in msg

    @pytest.mark.asyncio
    async def test_handle_schedule_empty_data(self) -> None:
        """_handle_schedule should succeed even with empty data."""
        executor = ActionExecutor()

        action = HeartbeatAction(
            skill_name="test",
            action_type="schedule",
            user_id="user123",
            data={},
        )
        result = await executor.execute(action)
        assert result.success is True
        assert "scheduled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_handle_send_message_empty_message(self) -> None:
        """_handle_send_message should fail when message is empty."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": ""},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "No message" in result.error

    @pytest.mark.asyncio
    async def test_handle_send_message_no_message_key(self) -> None:
        """_handle_send_message should fail when message key is missing."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "No message" in result.error

    @pytest.mark.asyncio
    async def test_send_message_failure(self) -> None:
        """_handle_send_message should report failure when sender fails."""
        sender = MockMessageSender(should_succeed=False)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "Failed to send" in result.error

    @pytest.mark.asyncio
    async def test_stale_task_check(self) -> None:
        """_handle_stale_task_check should send stale task notification."""
        sender = MockMessageSender()
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="stale_task_check",
            user_id="user123",
            data={
                "tasks": [{"title": "Old task"}],
                "count": 3,
            },
        )
        result = await executor.execute(action)
        assert result.success is True
        msg = sender.sent_messages[0][1]
        assert "3" in msg
        assert "7+ days" in msg

    @pytest.mark.asyncio
    async def test_deadline_reminder_failure(self) -> None:
        """_handle_deadline_reminder should report failure when send fails."""
        sender = MockMessageSender(should_succeed=False)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="deadline_reminder",
            user_id="user123",
            data={"tasks": [{"title": "T1"}], "count": 1},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "Failed to send" in result.error

    @pytest.mark.asyncio
    async def test_overdue_alert_failure(self) -> None:
        """_handle_overdue_alert should report failure when send fails."""
        sender = MockMessageSender(should_succeed=False)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="task_manager",
            action_type="overdue_alert",
            user_id="user123",
            data={"count": 1},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "Failed to send" in result.error

    @pytest.mark.asyncio
    async def test_end_of_day_failure(self) -> None:
        """_handle_end_of_day should report failure when send fails."""
        sender = MockMessageSender(should_succeed=False)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="calendar",
            action_type="end_of_day",
            user_id="user123",
            data={"tomorrow_events": [], "count": 0},
        )
        result = await executor.execute(action)
        assert result.success is False
        assert "Failed to send" in result.error

    @pytest.mark.asyncio
    async def test_send_dm_exception_handling(self) -> None:
        """_send_dm should handle exceptions from message sender."""
        sender = MockMessageSender()

        async def failing_send(user_id: str, message: str) -> bool:
            raise ConnectionError("Network error")

        sender.send_dm = failing_send
        executor = ActionExecutor(message_sender=sender)

        result = await executor._send_dm("user123", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_message_recording_on_success(self) -> None:
        """execute should record message on successful send for rate limiting."""
        sender = MockMessageSender(should_succeed=True)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )

        await executor.execute(action)
        assert len(executor._message_counts.get("user123", [])) == 1

    @pytest.mark.asyncio
    async def test_no_message_recording_on_failure(self) -> None:
        """execute should not record message on failed send."""
        sender = MockMessageSender(should_succeed=False)
        executor = ActionExecutor(message_sender=sender)

        action = HeartbeatAction(
            skill_name="test",
            action_type="send_message",
            user_id="user123",
            data={"message": "Hello!"},
        )

        await executor.execute(action)
        assert len(executor._message_counts.get("user123", [])) == 0
