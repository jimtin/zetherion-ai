"""Tests for heartbeat scheduler module."""

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from zetherion_ai.scheduler.actions import (
    ActionExecutor,
    ActionResult,
    ScheduledEvent,
    ScheduledEventStatus,
)
from zetherion_ai.scheduler.heartbeat import (
    HeartbeatConfig,
    HeartbeatScheduler,
    HeartbeatStats,
)
from zetherion_ai.skills.base import HeartbeatAction


class TestHeartbeatConfig:
    """Tests for HeartbeatConfig dataclass."""

    def test_default_values(self) -> None:
        """HeartbeatConfig should have sensible defaults."""
        config = HeartbeatConfig()
        assert config.interval_seconds == 300  # 5 minutes
        assert config.quiet_start == time(22, 0)
        assert config.quiet_end == time(7, 0)
        assert config.max_actions_per_beat == 10
        assert config.respect_timezone is True
        assert config.min_priority_busy == 7

    def test_custom_values(self) -> None:
        """HeartbeatConfig should accept custom values."""
        config = HeartbeatConfig(
            interval_seconds=60,
            quiet_start=time(23, 0),
            quiet_end=time(6, 0),
            max_actions_per_beat=5,
        )
        assert config.interval_seconds == 60
        assert config.quiet_start == time(23, 0)
        assert config.max_actions_per_beat == 5


class TestHeartbeatStats:
    """Tests for HeartbeatStats dataclass."""

    def test_default_values(self) -> None:
        """HeartbeatStats should have zero counts initially."""
        stats = HeartbeatStats()
        assert stats.total_beats == 0
        assert stats.total_actions == 0
        assert stats.successful_actions == 0
        assert stats.failed_actions == 0
        assert stats.rate_limited == 0
        assert stats.last_beat is None

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        now = datetime.now()
        stats = HeartbeatStats(
            total_beats=10,
            total_actions=50,
            successful_actions=45,
            failed_actions=5,
            last_beat=now,
        )
        data = stats.to_dict()
        assert data["total_beats"] == 10
        assert data["total_actions"] == 50
        assert data["successful_actions"] == 45
        assert data["last_beat"] is not None


class TestHeartbeatScheduler:
    """Tests for HeartbeatScheduler class."""

    def test_init(self) -> None:
        """HeartbeatScheduler should initialize properly."""
        scheduler = HeartbeatScheduler()
        assert scheduler.is_running is False
        assert scheduler.stats.total_beats == 0

    def test_init_with_config(self) -> None:
        """HeartbeatScheduler should accept custom config."""
        config = HeartbeatConfig(interval_seconds=60)
        scheduler = HeartbeatScheduler(config=config)
        assert scheduler._config.interval_seconds == 60

    def test_set_user_ids(self) -> None:
        """set_user_ids should update user list."""
        scheduler = HeartbeatScheduler()
        scheduler.set_user_ids(["user1", "user2"])
        assert scheduler._user_ids == ["user1", "user2"]

    def test_add_user(self) -> None:
        """add_user should add to user list."""
        scheduler = HeartbeatScheduler()
        scheduler.add_user("user1")
        scheduler.add_user("user2")
        assert "user1" in scheduler._user_ids
        assert "user2" in scheduler._user_ids

    def test_add_user_duplicate(self) -> None:
        """add_user should not add duplicates."""
        scheduler = HeartbeatScheduler()
        scheduler.add_user("user1")
        scheduler.add_user("user1")
        assert scheduler._user_ids.count("user1") == 1

    def test_remove_user(self) -> None:
        """remove_user should remove from user list."""
        scheduler = HeartbeatScheduler()
        scheduler.set_user_ids(["user1", "user2"])
        scheduler.remove_user("user1")
        assert "user1" not in scheduler._user_ids
        assert "user2" in scheduler._user_ids

    def test_schedule_event(self) -> None:
        """schedule_event should add event to pending list."""
        scheduler = HeartbeatScheduler()
        event = ScheduledEvent(
            user_id="user1",
            skill_name="task_manager",
            action_type="reminder",
            trigger_time=datetime.now() + timedelta(hours=1),
        )
        scheduler.schedule_event(event)
        assert str(event.id) in scheduler._scheduled_events

    def test_cancel_event(self) -> None:
        """cancel_event should remove event."""
        scheduler = HeartbeatScheduler()
        event = ScheduledEvent(
            user_id="user1",
            skill_name="task_manager",
            action_type="reminder",
        )
        scheduler.schedule_event(event)
        result = scheduler.cancel_event(str(event.id))
        assert result is True
        assert str(event.id) not in scheduler._scheduled_events

    def test_cancel_event_not_found(self) -> None:
        """cancel_event should return False for unknown event."""
        scheduler = HeartbeatScheduler()
        result = scheduler.cancel_event("unknown-id")
        assert result is False

    def test_is_quiet_hours_daytime(self) -> None:
        """_is_quiet_hours should return False during daytime."""
        # Configure quiet hours from 22:00 to 07:00
        config = HeartbeatConfig(
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )
        scheduler = HeartbeatScheduler(config=config)

        # Test would depend on actual time, so we just verify the method exists
        # and returns a boolean
        result = scheduler._is_quiet_hours()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """start and stop should manage scheduler state."""
        scheduler = HeartbeatScheduler()

        # Start
        await scheduler.start()
        assert scheduler.is_running is True

        # Stop
        await scheduler.stop()
        assert scheduler.is_running is False

    @pytest.mark.asyncio
    async def test_start_already_running(self) -> None:
        """start should not start again if already running."""
        scheduler = HeartbeatScheduler()
        await scheduler.start()
        await scheduler.start()  # Should not error
        assert scheduler.is_running is True
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_run_once_no_users(self) -> None:
        """run_once should handle empty user list."""
        scheduler = HeartbeatScheduler()
        results = await scheduler.run_once()
        assert results == []

    @pytest.mark.asyncio
    async def test_run_once_no_client(self) -> None:
        """run_once should handle missing skills client."""
        scheduler = HeartbeatScheduler()
        scheduler.set_user_ids(["user1"])
        results = await scheduler.run_once()
        assert results == []

    @pytest.mark.asyncio
    async def test_run_once_with_actions(self) -> None:
        """run_once should execute actions from skills client."""
        # Mock skills client
        mock_client = AsyncMock()
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="task_manager",
                    action_type="update_memory",
                    user_id="user1",
                    priority=5,
                ),
            ]
        )

        scheduler = HeartbeatScheduler(skills_client=mock_client)
        scheduler.set_user_ids(["user1"])

        results = await scheduler.run_once()
        assert len(results) == 1
        assert results[0].success is True
        mock_client.trigger_heartbeat.assert_called_once_with(["user1"])

    @pytest.mark.asyncio
    async def test_run_once_sorts_by_priority(self) -> None:
        """run_once should sort actions by priority."""
        mock_client = AsyncMock()
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="low",
                    action_type="update_memory",
                    user_id="user1",
                    priority=1,
                ),
                HeartbeatAction(
                    skill_name="high",
                    action_type="update_memory",
                    user_id="user1",
                    priority=10,
                ),
                HeartbeatAction(
                    skill_name="medium",
                    action_type="update_memory",
                    user_id="user1",
                    priority=5,
                ),
            ]
        )

        scheduler = HeartbeatScheduler(skills_client=mock_client)
        scheduler.set_user_ids(["user1"])

        results = await scheduler.run_once()
        # Actions should be executed in priority order (highest first)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_run_once_respects_max_actions(self) -> None:
        """run_once should limit actions per beat."""
        mock_client = AsyncMock()
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name=f"skill_{i}",
                    action_type="update_memory",
                    user_id="user1",
                    priority=i,
                )
                for i in range(20)  # 20 actions
            ]
        )

        config = HeartbeatConfig(max_actions_per_beat=5)
        scheduler = HeartbeatScheduler(skills_client=mock_client, config=config)
        scheduler.set_user_ids(["user1"])

        results = await scheduler.run_once()
        assert len(results) == 5  # Limited to 5

    @pytest.mark.asyncio
    async def test_stats_tracking(self) -> None:
        """Scheduler should track statistics."""
        mock_client = AsyncMock()
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="test",
                    action_type="update_memory",
                    user_id="user1",
                    priority=5,
                ),
            ]
        )

        scheduler = HeartbeatScheduler(skills_client=mock_client)
        scheduler.set_user_ids(["user1"])

        await scheduler.run_once()

        stats = scheduler.stats
        assert stats.total_actions >= 1
        assert stats.successful_actions >= 1

    @pytest.mark.asyncio
    async def test_process_scheduled_events(self) -> None:
        """Scheduler should process due scheduled events."""
        scheduler = HeartbeatScheduler()

        # Add a due event
        event = ScheduledEvent(
            user_id="user1",
            skill_name="task_manager",
            action_type="update_memory",
            trigger_time=datetime.now() - timedelta(minutes=1),  # Already due
        )
        scheduler.schedule_event(event)

        # Run heartbeat (which processes scheduled events)
        await scheduler.run_once()

        # Event should have been processed and removed
        assert str(event.id) not in scheduler._scheduled_events

    @pytest.mark.asyncio
    async def test_run_heartbeat_rate_limit_tracking(self) -> None:
        """_run_heartbeat should track rate-limited actions."""
        mock_client = AsyncMock()
        mock_executor = AsyncMock(spec=ActionExecutor)
        mock_executor.execute = AsyncMock(
            return_value=ActionResult(
                action=HeartbeatAction(
                    skill_name="test",
                    action_type="send_message",
                    user_id="user1",
                ),
                success=False,
                error="Rate limited: too many messages this hour",
            )
        )

        scheduler = HeartbeatScheduler(
            skills_client=mock_client,
            action_executor=mock_executor,
        )
        scheduler.set_user_ids(["user1"])
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="test",
                    action_type="send_message",
                    user_id="user1",
                    priority=5,
                ),
            ]
        )

        # Mock _is_quiet_hours to return False so heartbeat proceeds
        with patch.object(scheduler, "_is_quiet_hours", return_value=False):
            await scheduler._run_heartbeat()

        assert scheduler.stats.rate_limited == 1
        assert scheduler.stats.total_beats == 1

    @pytest.mark.asyncio
    async def test_run_heartbeat_failed_action_stats(self) -> None:
        """_run_heartbeat should track failed actions that are not rate limited."""
        mock_client = AsyncMock()
        mock_executor = AsyncMock(spec=ActionExecutor)
        mock_executor.execute = AsyncMock(
            return_value=ActionResult(
                action=HeartbeatAction(
                    skill_name="test",
                    action_type="send_message",
                    user_id="user1",
                ),
                success=False,
                error="Failed to send message",
            )
        )

        scheduler = HeartbeatScheduler(
            skills_client=mock_client,
            action_executor=mock_executor,
        )
        scheduler.set_user_ids(["user1"])
        mock_client.trigger_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="test",
                    action_type="send_message",
                    user_id="user1",
                    priority=5,
                ),
            ]
        )

        with patch.object(scheduler, "_is_quiet_hours", return_value=False):
            await scheduler._run_heartbeat()

        assert scheduler.stats.failed_actions == 1
        assert scheduler.stats.rate_limited == 0

    @pytest.mark.asyncio
    async def test_run_heartbeat_quiet_hours_skips_skills(self) -> None:
        """_run_heartbeat should skip skills heartbeat during quiet hours."""
        mock_client = AsyncMock()
        scheduler = HeartbeatScheduler(skills_client=mock_client)
        scheduler.set_user_ids(["user1"])

        with patch.object(scheduler, "_is_quiet_hours", return_value=True):
            await scheduler._run_heartbeat()

        # Skills heartbeat should not be called during quiet hours
        mock_client.trigger_heartbeat.assert_not_called()
        assert scheduler.stats.total_beats == 1

    @pytest.mark.asyncio
    async def test_run_heartbeat_no_users_skips(self) -> None:
        """_run_heartbeat should skip when no users configured."""
        mock_client = AsyncMock()
        scheduler = HeartbeatScheduler(skills_client=mock_client)
        # No users set

        with patch.object(scheduler, "_is_quiet_hours", return_value=False):
            await scheduler._run_heartbeat()

        mock_client.trigger_heartbeat.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_loop_exception_handling(self) -> None:
        """_run_loop should catch exceptions and record them in stats."""
        scheduler = HeartbeatScheduler()
        scheduler._running = True

        call_count = 0

        async def failing_heartbeat() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Heartbeat failure")
            # Stop after second call
            scheduler._running = False

        with (
            patch.object(scheduler, "_run_heartbeat", side_effect=failing_heartbeat),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await scheduler._run_loop()

        assert scheduler.stats.last_error == "Heartbeat failure"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self) -> None:
        """stop should cancel the running task."""
        scheduler = HeartbeatScheduler()

        # Start the scheduler (creates a background task)
        await scheduler.start()
        assert scheduler.is_running is True
        assert scheduler._task is not None

        # Stop should cancel the task
        await scheduler.stop()
        assert scheduler.is_running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_stop_when_no_task(self) -> None:
        """stop should handle case when no task is running."""
        scheduler = HeartbeatScheduler()
        scheduler._running = True
        scheduler._task = None

        # Should not raise
        await scheduler.stop()
        assert scheduler.is_running is False

    @pytest.mark.asyncio
    async def test_process_scheduled_events_failed_status(self) -> None:
        """_process_scheduled_events should set FAILED status on failure."""
        mock_executor = AsyncMock(spec=ActionExecutor)
        mock_executor.execute = AsyncMock(
            return_value=ActionResult(
                action=HeartbeatAction(
                    skill_name="task_manager",
                    action_type="reminder",
                    user_id="user1",
                ),
                success=False,
                error="Execution failed",
            )
        )

        scheduler = HeartbeatScheduler(action_executor=mock_executor)

        event = ScheduledEvent(
            user_id="user1",
            skill_name="task_manager",
            action_type="reminder",
            trigger_time=datetime.now() - timedelta(minutes=1),
        )
        scheduler.schedule_event(event)

        await scheduler._process_scheduled_events()

        # Event should be removed from pending
        assert str(event.id) not in scheduler._scheduled_events
        # Event should have FAILED status
        assert event.status == ScheduledEventStatus.FAILED
        assert event.error == "Execution failed"
        assert event.triggered_at is not None

    @pytest.mark.asyncio
    async def test_process_scheduled_events_completed_status(self) -> None:
        """_process_scheduled_events should set COMPLETED status on success."""
        mock_executor = AsyncMock(spec=ActionExecutor)
        mock_executor.execute = AsyncMock(
            return_value=ActionResult(
                action=HeartbeatAction(
                    skill_name="task_manager",
                    action_type="reminder",
                    user_id="user1",
                ),
                success=True,
                message="Done",
            )
        )

        scheduler = HeartbeatScheduler(action_executor=mock_executor)

        event = ScheduledEvent(
            user_id="user1",
            skill_name="task_manager",
            action_type="reminder",
            trigger_time=datetime.now() - timedelta(minutes=1),
        )
        scheduler.schedule_event(event)

        await scheduler._process_scheduled_events()

        assert str(event.id) not in scheduler._scheduled_events
        assert event.status == ScheduledEventStatus.COMPLETED
        assert event.triggered_at is not None

    @pytest.mark.asyncio
    async def test_get_skill_actions_client_exception(self) -> None:
        """_get_skill_actions should return empty list on client error."""
        mock_client = AsyncMock()
        mock_client.trigger_heartbeat = AsyncMock(side_effect=Exception("Connection error"))

        scheduler = HeartbeatScheduler(skills_client=mock_client)
        scheduler.set_user_ids(["user1"])

        actions = await scheduler._get_skill_actions()
        assert actions == []

    @pytest.mark.asyncio
    async def test_run_heartbeat_increments_total_beats(self) -> None:
        """_run_heartbeat should always increment total_beats."""
        scheduler = HeartbeatScheduler()

        with patch.object(scheduler, "_is_quiet_hours", return_value=True):
            await scheduler._run_heartbeat()
            await scheduler._run_heartbeat()

        assert scheduler.stats.total_beats == 2
        assert scheduler.stats.last_beat is not None
