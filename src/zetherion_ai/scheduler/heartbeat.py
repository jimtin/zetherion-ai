"""Heartbeat scheduler for proactive behavior.

The scheduler runs inside the bot container and periodically:
1. Calls the skills service /heartbeat endpoint
2. Receives actions from all registered skills
3. Executes actions (send DMs, update memory, schedule follow-ups)
4. Respects rate limits and user preferences
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from zetherion_ai.config import get_dynamic
from zetherion_ai.logging import get_logger
from zetherion_ai.scheduler.actions import ActionExecutor, ActionResult, ScheduledEvent
from zetherion_ai.skills.base import HeartbeatAction

if TYPE_CHECKING:
    from zetherion_ai.queue.manager import QueueManager
    from zetherion_ai.skills.client import SkillsClient

log = get_logger("zetherion_ai.scheduler.heartbeat")


@dataclass
class HeartbeatConfig:
    """Configuration for the heartbeat scheduler."""

    # How often to run heartbeat (in seconds)
    interval_seconds: int = 300  # 5 minutes

    # Quiet hours (no proactive messages)
    quiet_start: time = field(default_factory=lambda: time(22, 0))  # 10 PM
    quiet_end: time = field(default_factory=lambda: time(7, 0))  # 7 AM

    # Maximum actions to execute per heartbeat
    max_actions_per_beat: int = 10

    # Whether to respect user timezone for quiet hours
    respect_timezone: bool = True

    # Minimum priority for actions during busy periods
    min_priority_busy: int = 7


@dataclass
class HeartbeatStats:
    """Statistics about heartbeat execution."""

    total_beats: int = 0
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    rate_limited: int = 0
    last_beat: datetime | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_beats": self.total_beats,
            "total_actions": self.total_actions,
            "successful_actions": self.successful_actions,
            "failed_actions": self.failed_actions,
            "rate_limited": self.rate_limited,
            "last_beat": self.last_beat.isoformat() if self.last_beat else None,
            "last_error": self.last_error,
        }


@dataclass
class QuietHoursWindow:
    """Quiet-hours window for a specific user."""

    start: time
    end: time
    timezone: str | None = None
    enabled: bool = True


class HeartbeatScheduler:
    """Async scheduler for periodic skill heartbeat.

    The scheduler:
    - Runs every N seconds (configurable, default 5 minutes)
    - Calls the skills service to get pending actions
    - Executes actions via the ActionExecutor
    - Handles scheduled events (one-time triggers)
    - Respects quiet hours and rate limits
    """

    def __init__(
        self,
        skills_client: "SkillsClient | None" = None,
        action_executor: ActionExecutor | None = None,
        config: HeartbeatConfig | None = None,
        queue_manager: "QueueManager | None" = None,
        quiet_hours_resolver: Callable[[str], Awaitable[QuietHoursWindow | None]] | None = None,
    ):
        """Initialize the heartbeat scheduler.

        Args:
            skills_client: Client for calling skills service.
            action_executor: Executor for actions.
            config: Scheduler configuration.
            queue_manager: Optional QueueManager for enqueuing actions.
            quiet_hours_resolver: Optional resolver for per-user quiet windows.
        """
        self._skills_client = skills_client
        self._action_executor = action_executor or ActionExecutor()
        self._config = config or HeartbeatConfig()
        self._queue_manager = queue_manager
        self._quiet_hours_resolver = quiet_hours_resolver
        self._stats = HeartbeatStats()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._user_ids: list[str] = []
        self._scheduled_events: dict[str, ScheduledEvent] = {}

        log.info("heartbeat_scheduler_initialized", interval=self._config.interval_seconds)

    @property
    def stats(self) -> HeartbeatStats:
        """Get scheduler statistics."""
        return self._stats

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    def set_user_ids(self, user_ids: list[str]) -> None:
        """Set the list of user IDs to check during heartbeat.

        Args:
            user_ids: List of user IDs.
        """
        self._user_ids = user_ids
        log.debug("user_ids_updated", count=len(user_ids))

    def add_user(self, user_id: str) -> None:
        """Add a user to the heartbeat list.

        Args:
            user_id: User ID to add.
        """
        if user_id not in self._user_ids:
            self._user_ids.append(user_id)
            log.debug("user_added", user_id=user_id)

    def remove_user(self, user_id: str) -> None:
        """Remove a user from the heartbeat list.

        Args:
            user_id: User ID to remove.
        """
        if user_id in self._user_ids:
            self._user_ids.remove(user_id)
            log.debug("user_removed", user_id=user_id)

    def schedule_event(self, event: ScheduledEvent) -> None:
        """Schedule a one-time event.

        Args:
            event: The event to schedule.
        """
        self._scheduled_events[str(event.id)] = event
        log.debug(
            "event_scheduled",
            event_id=str(event.id),
            trigger_time=event.trigger_time.isoformat(),
        )

    def cancel_event(self, event_id: str) -> bool:
        """Cancel a scheduled event.

        Args:
            event_id: The event ID to cancel.

        Returns:
            True if event was cancelled.
        """
        if event_id in self._scheduled_events:
            del self._scheduled_events[event_id]
            log.debug("event_cancelled", event_id=event_id)
            return True
        return False

    @staticmethod
    def _within_quiet_window(now: time, start: time, end: time) -> bool:
        """Check if ``now`` is inside a quiet-hours window."""
        # Handle overnight quiet hours (e.g., 22:00 - 07:00)
        if start > end:
            return now >= start or now <= end
        return start <= now <= end

    def _default_quiet_window(self) -> QuietHoursWindow:
        """Get system/default quiet-hours configuration."""
        quiet_start_hour = get_dynamic("scheduler", "quiet_start", None)
        quiet_end_hour = get_dynamic("scheduler", "quiet_end", None)
        start = (
            time(quiet_start_hour, 0) if quiet_start_hour is not None else self._config.quiet_start
        )
        end = time(quiet_end_hour, 0) if quiet_end_hour is not None else self._config.quiet_end
        enabled = bool(get_dynamic("scheduler", "quiet_hours_enabled", True))
        return QuietHoursWindow(start=start, end=end, enabled=enabled)

    def _is_quiet_hours(self) -> bool:
        """Check if current time is within system quiet hours."""
        window = self._default_quiet_window()
        if not window.enabled:
            return False
        return self._within_quiet_window(datetime.now().time(), window.start, window.end)

    async def _resolve_quiet_window(self, user_id: str) -> QuietHoursWindow:
        """Resolve quiet-hours window for a specific user."""
        if self._quiet_hours_resolver is None:
            return self._default_quiet_window()

        try:
            resolved = await self._quiet_hours_resolver(user_id)
        except Exception as exc:
            log.warning("quiet_hours_resolver_failed", user_id=user_id, error=str(exc))
            return self._default_quiet_window()

        if resolved is None:
            return self._default_quiet_window()
        return resolved

    async def _is_quiet_hours_for_user(self, user_id: str) -> bool:
        """Check quiet-hours state for a specific user."""
        window = await self._resolve_quiet_window(user_id)
        if not window.enabled:
            return False

        if self._config.respect_timezone and window.timezone:
            try:
                zone_now = datetime.now(UTC).astimezone(ZoneInfo(window.timezone)).time()
                return self._within_quiet_window(zone_now, window.start, window.end)
            except Exception:
                log.warning(
                    "quiet_hours_timezone_invalid",
                    user_id=user_id,
                    timezone=window.timezone,
                )

        return self._within_quiet_window(datetime.now().time(), window.start, window.end)

    async def _next_notification_time(self, user_id: str) -> datetime:
        """Compute next allowed notification time for a user."""
        window = await self._resolve_quiet_window(user_id)
        if not window.enabled:
            return datetime.now()

        # If timezone is available, compute the exact quiet-end instant there.
        if self._config.respect_timezone and window.timezone:
            try:
                zone = ZoneInfo(window.timezone)
                now_utc = datetime.now(UTC)
                now_local = now_utc.astimezone(zone)
                local_now_time = now_local.time()
                if not self._within_quiet_window(local_now_time, window.start, window.end):
                    return datetime.now()

                end_dt_local = datetime.combine(now_local.date(), window.end, tzinfo=zone)
                if window.start > window.end and local_now_time >= window.start:
                    end_dt_local += timedelta(days=1)
                if window.start <= window.end and local_now_time > window.end:
                    end_dt_local += timedelta(days=1)
                return end_dt_local.astimezone().replace(tzinfo=None)
            except Exception:
                log.warning("quiet_hours_next_time_timezone_failed", user_id=user_id)

        now_local = datetime.now()
        now_time = now_local.time()
        if not self._within_quiet_window(now_time, window.start, window.end):
            return now_local

        end_dt = datetime.combine(now_local.date(), window.end)
        if window.start > window.end and now_time >= window.start:
            end_dt += timedelta(days=1)
        if window.start <= window.end and now_time > window.end:
            end_dt += timedelta(days=1)
        return end_dt

    @staticmethod
    def _is_message_action(action: HeartbeatAction) -> bool:
        """Whether an action sends a user-facing notification."""
        return action.action_type in ActionExecutor.MESSAGE_ACTION_TYPES

    async def _should_defer_action(self, action: HeartbeatAction) -> bool:
        """Determine whether action execution should be deferred."""
        if not self._is_message_action(action):
            return False
        return await self._is_quiet_hours_for_user(action.user_id)

    async def start(self) -> None:
        """Start the heartbeat scheduler."""
        if self._running:
            log.warning("scheduler_already_running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info("heartbeat_scheduler_started")

    async def stop(self) -> None:
        """Stop the heartbeat scheduler."""
        self._running = False
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._task = None
        log.info("heartbeat_scheduler_stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._run_heartbeat()
            except Exception as e:
                log.error("heartbeat_error", error=str(e))
                self._stats.last_error = str(e)

            # Wait for next interval (dynamic override if available)
            interval = get_dynamic("scheduler", "interval_seconds", self._config.interval_seconds)
            await asyncio.sleep(interval)

    async def _run_heartbeat(self) -> None:
        """Run a single heartbeat cycle."""
        self._stats.total_beats += 1
        self._stats.last_beat = datetime.now()

        # Process scheduled events
        await self._process_scheduled_events()

        # Skip if no users to check
        if not self._user_ids:
            log.debug("skipping_heartbeat_no_users")
            return

        # Get actions from skills service
        actions = await self._get_skill_actions()
        if not actions:
            return

        # Sort by priority (highest first)
        actions.sort(key=lambda a: a.priority, reverse=True)

        # Limit actions per heartbeat
        actions = actions[: self._config.max_actions_per_beat]

        # Execute actions
        results = await self._execute_actions(actions)

        # Update stats
        for result in results:
            if result.success:
                self._stats.successful_actions += 1
            elif "rate limit" in (result.error or "").lower():
                self._stats.rate_limited += 1
            else:
                self._stats.failed_actions += 1

        log.debug(
            "heartbeat_complete",
            actions=len(actions),
            successful=sum(1 for r in results if r.success),
        )

    async def _get_skill_actions(self) -> list[HeartbeatAction]:
        """Get actions from the skills service."""
        if not self._skills_client:
            log.debug("no_skills_client")
            return []

        try:
            actions = await self._skills_client.trigger_heartbeat(self._user_ids)
            self._stats.total_actions += len(actions)
            return actions
        except Exception as e:
            log.error("skills_heartbeat_failed", error=str(e))
            return []

    async def _execute_actions(self, actions: list[HeartbeatAction]) -> list[ActionResult]:
        """Execute a list of actions (via queue if available, else direct)."""
        # If queue is available and running, enqueue at P2 priority
        if self._queue_manager is not None and self._queue_manager.is_running:
            return await self._enqueue_actions(actions)

        # Direct execution fallback
        results: list[ActionResult] = []
        for action in actions:
            if await self._should_defer_action(action):
                trigger_time = await self._next_notification_time(action.user_id)
                event = ScheduledEvent(
                    user_id=action.user_id,
                    skill_name=action.skill_name,
                    action_type=action.action_type,
                    trigger_time=trigger_time,
                    data=action.data,
                )
                self.schedule_event(event)
                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        message=f"Deferred until {trigger_time.isoformat()}",
                    )
                )
                continue
            result = await self._action_executor.execute(action)
            results.append(result)
        return results

    async def _enqueue_actions(self, actions: list[HeartbeatAction]) -> list[ActionResult]:
        """Enqueue heartbeat actions into the priority queue."""
        from zetherion_ai.queue.models import QueuePriority, QueueTaskType

        assert self._queue_manager is not None
        results: list[ActionResult] = []
        for action in actions:
            try:
                scheduled_for = None
                if await self._should_defer_action(action):
                    scheduled_for = await self._next_notification_time(action.user_id)

                await self._queue_manager.enqueue(
                    task_type=QueueTaskType.HEARTBEAT_ACTION,
                    user_id=int(action.user_id) if action.user_id.isdigit() else 0,
                    payload={
                        "skill_name": action.skill_name,
                        "action_type": action.action_type,
                        "user_id": action.user_id,
                        "data": action.data,
                        "priority": action.priority,
                    },
                    priority=QueuePriority.SCHEDULED,
                    scheduled_for=scheduled_for,
                )
                message = "Enqueued"
                if scheduled_for is not None:
                    message = f"Deferred until {scheduled_for.isoformat()}"
                results.append(ActionResult(action=action, success=True, message=message))
            except Exception as exc:
                log.warning("heartbeat_enqueue_failed", error=str(exc))
                # Fall back while still respecting quiet-hours delivery policy.
                if await self._should_defer_action(action):
                    trigger_time = await self._next_notification_time(action.user_id)
                    self.schedule_event(
                        ScheduledEvent(
                            user_id=action.user_id,
                            skill_name=action.skill_name,
                            action_type=action.action_type,
                            trigger_time=trigger_time,
                            data=action.data,
                        )
                    )
                    results.append(
                        ActionResult(
                            action=action,
                            success=True,
                            message=f"Deferred until {trigger_time.isoformat()}",
                        )
                    )
                else:
                    result = await self._action_executor.execute(action)
                    results.append(result)
        return results

    async def _process_scheduled_events(self) -> None:
        """Process any due scheduled events."""
        now = datetime.now()
        due_events = [e for e in self._scheduled_events.values() if e.is_due()]

        for event in due_events:
            # Convert to HeartbeatAction and execute
            action = HeartbeatAction(
                skill_name=event.skill_name,
                action_type=event.action_type,
                user_id=event.user_id,
                data=event.data,
                priority=5,  # Default priority for scheduled events
            )

            result = await self._action_executor.execute(action)

            # Update event status
            event.triggered_at = now
            if result.success:
                event.status = ScheduledEvent.from_dict(
                    {"status": "completed"}
                ).status  # Just use enum
                from zetherion_ai.scheduler.actions import ScheduledEventStatus

                event.status = ScheduledEventStatus.COMPLETED
            else:
                from zetherion_ai.scheduler.actions import ScheduledEventStatus

                event.status = ScheduledEventStatus.FAILED
                event.error = result.error

            # Remove from pending
            del self._scheduled_events[str(event.id)]

            log.debug(
                "scheduled_event_processed",
                event_id=str(event.id),
                success=result.success,
            )

    async def run_once(self) -> list[ActionResult]:
        """Run a single heartbeat manually (useful for testing).

        Returns:
            List of action results.
        """
        # Process scheduled events
        await self._process_scheduled_events()

        # Get and execute actions
        actions = await self._get_skill_actions()
        if not actions:
            return []

        actions.sort(key=lambda a: a.priority, reverse=True)
        actions = actions[: self._config.max_actions_per_beat]

        results = await self._execute_actions(actions)

        # Update stats (same as _run_heartbeat)
        for result in results:
            if result.success:
                self._stats.successful_actions += 1
            elif "rate limit" in (result.error or "").lower():
                self._stats.rate_limited += 1
            else:
                self._stats.failed_actions += 1

        return results
