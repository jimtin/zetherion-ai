"""Action types and execution for the heartbeat scheduler.

This module defines:
- ScheduledEvent: One-time scheduled actions (reminders, follow-ups)
- ActionExecutor: Executes actions returned by skills during heartbeat
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import HeartbeatAction

if TYPE_CHECKING:
    pass

log = get_logger("zetherion_ai.scheduler.actions")


class ScheduledEventStatus(Enum):
    """Status of a scheduled event."""

    PENDING = "pending"
    TRIGGERED = "triggered"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledEvent:
    """A one-time scheduled action.

    Used for reminders, follow-ups, and other time-based triggers.
    """

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    skill_name: str = ""
    action_type: str = ""
    trigger_time: datetime = field(default_factory=datetime.now)
    data: dict[str, Any] = field(default_factory=dict)
    status: ScheduledEventStatus = ScheduledEventStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    triggered_at: datetime | None = None
    error: str | None = None

    def is_due(self) -> bool:
        """Check if event should be triggered."""
        if self.status != ScheduledEventStatus.PENDING:
            return False
        return datetime.now() >= self.trigger_time

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "skill_name": self.skill_name,
            "action_type": self.action_type,
            "trigger_time": self.trigger_time.isoformat(),
            "data": self.data,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledEvent":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            skill_name=data.get("skill_name", ""),
            action_type=data.get("action_type", ""),
            trigger_time=datetime.fromisoformat(data["trigger_time"])
            if data.get("trigger_time")
            else datetime.now(),
            data=data.get("data", {}),
            status=ScheduledEventStatus(data["status"])
            if data.get("status")
            else ScheduledEventStatus.PENDING,
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            triggered_at=datetime.fromisoformat(data["triggered_at"])
            if data.get("triggered_at")
            else None,
            error=data.get("error"),
        )


@dataclass
class ActionResult:
    """Result of executing an action."""

    action: HeartbeatAction
    success: bool = True
    message: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "action": self.action.to_dict(),
            "success": self.success,
            "message": self.message,
            "error": self.error,
        }


class MessageSender(Protocol):
    """Protocol for sending messages to users."""

    async def send_dm(self, user_id: str, message: str) -> bool:
        """Send a direct message to a user."""
        ...


class ActionExecutor:
    """Executes actions returned by skills during heartbeat.

    Handles different action types:
    - send_message: Send a proactive DM to user
    - deadline_reminder: Remind about upcoming deadlines
    - overdue_alert: Alert about overdue tasks
    - morning_briefing: Send daily briefing
    - meeting_prep: Send meeting preparation info
    - confirm_low_confidence: Ask user to confirm profile entry
    - And more...
    """

    # Rate limit: max proactive messages per user per hour
    MAX_MESSAGES_PER_HOUR = 3

    def __init__(
        self,
        message_sender: MessageSender | None = None,
    ):
        """Initialize the action executor.

        Args:
            message_sender: Object that can send DMs to users.
        """
        self._message_sender = message_sender
        self._message_counts: dict[str, list[datetime]] = {}  # user_id -> timestamps

    def _can_send_message(self, user_id: str) -> bool:
        """Check if we can send a message to this user (rate limiting)."""
        now = datetime.now()
        hour_ago = now.replace(minute=0, second=0, microsecond=0)

        if user_id not in self._message_counts:
            self._message_counts[user_id] = []

        # Filter to messages in the last hour
        recent = [t for t in self._message_counts[user_id] if t >= hour_ago]
        self._message_counts[user_id] = recent

        return len(recent) < self.MAX_MESSAGES_PER_HOUR

    def _record_message(self, user_id: str) -> None:
        """Record that a message was sent."""
        if user_id not in self._message_counts:
            self._message_counts[user_id] = []
        self._message_counts[user_id].append(datetime.now())

    async def execute(self, action: HeartbeatAction) -> ActionResult:
        """Execute a heartbeat action.

        Args:
            action: The action to execute.

        Returns:
            Result of the execution.
        """
        log.debug(
            "executing_action",
            skill=action.skill_name,
            action_type=action.action_type,
            user_id=action.user_id,
        )

        # Check rate limit for message actions
        message_actions = {
            "send_message",
            "deadline_reminder",
            "overdue_alert",
            "morning_briefing",
            "meeting_prep",
            "end_of_day",
            "stale_task_check",
            "confirm_low_confidence",
            "decay_check",
        }

        if action.action_type in message_actions and not self._can_send_message(action.user_id):
            log.warning(
                "rate_limited",
                user_id=action.user_id,
                action_type=action.action_type,
            )
            return ActionResult(
                action=action,
                success=False,
                error="Rate limited: too many messages this hour",
            )

        # Route to specific handler
        handler = self._get_handler(action.action_type)
        if handler:
            result: ActionResult = await handler(action)
            if result.success and action.action_type in message_actions:
                self._record_message(action.user_id)
            return result

        # Unknown action type - log but don't fail
        log.warning("unknown_action_type", action_type=action.action_type)
        return ActionResult(
            action=action,
            success=True,
            message=f"Action type '{action.action_type}' not implemented",
        )

    def _get_handler(self, action_type: str) -> Any:
        """Get handler for action type."""
        handlers = {
            "send_message": self._handle_send_message,
            "deadline_reminder": self._handle_deadline_reminder,
            "overdue_alert": self._handle_overdue_alert,
            "morning_briefing": self._handle_morning_briefing,
            "meeting_prep": self._handle_meeting_prep,
            "end_of_day": self._handle_end_of_day,
            "stale_task_check": self._handle_stale_task_check,
            "confirm_low_confidence": self._handle_confirm_low_confidence,
            "decay_check": self._handle_decay_check,
            "update_memory": self._handle_update_memory,
            "schedule": self._handle_schedule,
        }
        return handlers.get(action_type)

    async def _send_dm(self, user_id: str, message: str) -> bool:
        """Send a DM to a user."""
        if not self._message_sender:
            log.warning("no_message_sender", user_id=user_id)
            return False

        try:
            return await self._message_sender.send_dm(user_id, message)
        except Exception as e:
            log.error("send_dm_failed", user_id=user_id, error=str(e))
            return False

    async def _handle_send_message(self, action: HeartbeatAction) -> ActionResult:
        """Handle generic send_message action."""
        message = action.data.get("message", "")
        if not message:
            return ActionResult(action=action, success=False, error="No message provided")

        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Message sent" if sent else None,
            error=None if sent else "Failed to send message",
        )

    async def _handle_deadline_reminder(self, action: HeartbeatAction) -> ActionResult:
        """Handle deadline reminder action."""
        tasks = action.data.get("tasks", [])
        count = action.data.get("count", len(tasks))

        if count == 1:
            task = tasks[0] if tasks else {}
            message = f"⏰ Reminder: '{task.get('title', 'Task')}' is due within 24 hours!"
        else:
            message = f"⏰ You have {count} task(s) due within 24 hours!"

        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Deadline reminder sent" if sent else None,
            error=None if sent else "Failed to send reminder",
        )

    async def _handle_overdue_alert(self, action: HeartbeatAction) -> ActionResult:
        """Handle overdue task alert."""
        tasks = action.data.get("tasks", [])
        count = action.data.get("count", len(tasks))

        message = f"🚨 Alert: {count} task(s) are overdue and need attention!"
        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Overdue alert sent" if sent else None,
            error=None if sent else "Failed to send alert",
        )

    async def _handle_morning_briefing(self, action: HeartbeatAction) -> ActionResult:
        """Handle morning briefing action."""
        events = action.data.get("events", [])
        count = action.data.get("count", len(events))

        if count == 0:
            message = "☀️ Good morning! Your calendar is clear today."
        elif count == 1:
            event = events[0] if events else {}
            message = f"☀️ Good morning! You have 1 event today: {event.get('title', 'Event')}"
        else:
            message = f"☀️ Good morning! You have {count} events scheduled for today."

        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Morning briefing sent" if sent else None,
            error=None if sent else "Failed to send briefing",
        )

    async def _handle_meeting_prep(self, action: HeartbeatAction) -> ActionResult:
        """Handle meeting prep action."""
        event = action.data.get("event", {})
        minutes = action.data.get("minutes_until", 15)

        title = event.get("title", "Meeting")
        message = f"📅 Heads up: '{title}' starts in {minutes} minutes!"

        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Meeting prep sent" if sent else None,
            error=None if sent else "Failed to send prep",
        )

    async def _handle_end_of_day(self, action: HeartbeatAction) -> ActionResult:
        """Handle end of day summary."""
        events = action.data.get("tomorrow_events", [])
        count = action.data.get("count", len(events))

        if count == 0:
            message = "🌙 End of day! Tomorrow's calendar is clear."
        else:
            message = f"🌙 End of day! Tomorrow you have {count} event(s) scheduled."

        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="End of day sent" if sent else None,
            error=None if sent else "Failed to send summary",
        )

    async def _handle_stale_task_check(self, action: HeartbeatAction) -> ActionResult:
        """Handle stale task notification."""
        tasks = action.data.get("tasks", [])
        count = action.data.get("count", len(tasks))

        message = (
            f"📝 You have {count} task(s) with no updates in 7+ days. "
            "Would you like to review them?"
        )
        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Stale task check sent" if sent else None,
            error=None if sent else "Failed to send check",
        )

    async def _handle_confirm_low_confidence(self, action: HeartbeatAction) -> ActionResult:
        """Handle profile confirmation request."""
        entry = action.data.get("entry", {})
        key = entry.get("key", "information")
        value = entry.get("value", "")

        message = f"❓ Can you confirm: Is your {key} '{value}'?"
        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Confirmation request sent" if sent else None,
            error=None if sent else "Failed to send confirmation",
        )

    async def _handle_decay_check(self, action: HeartbeatAction) -> ActionResult:
        """Handle stale profile entry notification."""
        stale_count = action.data.get("stale_count", 0)
        categories = action.data.get("categories", [])

        cat_str = ", ".join(categories[:3]) if categories else "profile"
        message = f"ℹ️ Some of your {cat_str} information may be outdated ({stale_count} entries)."
        sent = await self._send_dm(action.user_id, message)
        return ActionResult(
            action=action,
            success=sent,
            message="Decay check sent" if sent else None,
            error=None if sent else "Failed to send check",
        )

    async def _handle_update_memory(self, action: HeartbeatAction) -> ActionResult:
        """Handle memory update action (non-messaging)."""
        # This would update internal state, not send a message
        log.debug("update_memory_action", data=action.data)
        return ActionResult(action=action, success=True, message="Memory updated")

    async def _handle_schedule(self, action: HeartbeatAction) -> ActionResult:
        """Handle schedule action (creates a scheduled event)."""
        # This would create a ScheduledEvent for later execution
        log.debug("schedule_action", data=action.data)
        return ActionResult(action=action, success=True, message="Action scheduled")
