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
