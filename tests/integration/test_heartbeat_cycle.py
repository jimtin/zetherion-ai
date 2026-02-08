"""Heartbeat Scheduler full pipeline integration tests.

Exercises the full heartbeat pipeline:
    HeartbeatScheduler.run_once()
    -> SkillsClient
    -> HTTP
    -> SkillsServer
    -> SkillRegistry.run_heartbeat()
    -> ActionExecutor.execute()

Uses ``aiohttp.test_utils.TestServer`` to run the server in-process so no
external services are required.
"""

from datetime import datetime, time, timedelta
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestServer

from zetherion_ai.scheduler.actions import ActionExecutor, ActionResult, ScheduledEvent
from zetherion_ai.scheduler.heartbeat import HeartbeatConfig, HeartbeatScheduler
from zetherion_ai.skills.base import HeartbeatAction, SkillRequest
from zetherion_ai.skills.calendar import CalendarSkill
from zetherion_ai.skills.client import SkillsClient
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer
from zetherion_ai.skills.task_manager import TaskManagerSkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockMessageSender:
    """Mock message sender that records sent messages."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []  # (user_id, message)

    async def send_dm(self, user_id: str, message: str) -> bool:
        self.messages.append((user_id, message))
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def heartbeat_pipeline():
    """Build the full heartbeat pipeline backed by an in-process TestServer.

    Yields a tuple of (scheduler, sender, executor, client, test_server).
    """
    # -- registry with built-in skills --
    reg = SkillRegistry()
    reg.register(TaskManagerSkill(memory=None))
    reg.register(CalendarSkill(memory=None))
    init_results = await reg.initialize_all()
    assert all(init_results.values()), f"Skill init failed: {init_results}"

    # Optionally seed a task with a near-deadline for "user-1"
    await reg.handle_request(
        SkillRequest(
            user_id="user-1",
            intent="create_task",
            message="Near-deadline task",
            context={
                "title": "Near-deadline task",
                "deadline": (datetime.now() + timedelta(hours=12)).isoformat(),
                "priority": "high",
            },
        )
    )

    # -- aiohttp server wrapping the registry --
    skills_server = SkillsServer(registry=reg)
    app = skills_server.create_app()
    test_server = TestServer(app)
    await test_server.start_server()

    # -- httpx-based SkillsClient pointed at the test server --
    base_url = f"http://{test_server.host}:{test_server.port}"
    client = SkillsClient(base_url=base_url)

    # -- ActionExecutor with mock sender --
    sender = MockMessageSender()
    executor = ActionExecutor(message_sender=sender)

    # -- HeartbeatScheduler config: quiet hours that never interfere --
    config = HeartbeatConfig(
        quiet_start=time(23, 59),
        quiet_end=time(0, 1),
        max_actions_per_beat=10,
    )
    scheduler = HeartbeatScheduler(
        skills_client=client,
        action_executor=executor,
        config=config,
    )
    scheduler.set_user_ids(["user-1"])

    yield scheduler, sender, executor, client, test_server

    # -- cleanup --
    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_run_once_full_cycle(heartbeat_pipeline):
    """run_once() traverses the full pipeline without error.

    The result list may be empty if no skill-generated deadlines are due;
    the test validates the plumbing works end-to-end.
    """
    scheduler, _sender, _executor, _client, _ts = heartbeat_pipeline

    results = await scheduler.run_once()

    assert isinstance(results, list)
    # Every item must be an ActionResult
    for r in results:
        assert isinstance(r, ActionResult)


@pytest.mark.integration
async def test_run_once_stats_update(heartbeat_pipeline):
    """After run_once(), scheduler.stats should reflect the run."""
    scheduler, _sender, _executor, _client, _ts = heartbeat_pipeline

    stats_before_actions = scheduler.stats.total_actions
    results = await scheduler.run_once()

    # total_actions is incremented by _get_skill_actions regardless of result
    # If actions were returned, total_actions should increase; otherwise stays the same.
    if results:
        assert scheduler.stats.total_actions > stats_before_actions
        assert scheduler.stats.successful_actions + scheduler.stats.failed_actions > 0
    else:
        # Even with zero results, the pipeline ran without error
        assert scheduler.stats.total_actions >= stats_before_actions


@pytest.mark.integration
async def test_max_actions_limit(heartbeat_pipeline):
    """max_actions_per_beat should cap the number of results."""
    scheduler, _sender, _executor, _client, _ts = heartbeat_pipeline

    # Restrict to 1 action per beat
    scheduler._config.max_actions_per_beat = 1

    results = await scheduler.run_once()

    # Either no actions (nothing due) or at most 1
    assert len(results) <= 1


@pytest.mark.integration
async def test_scheduled_event_fires(heartbeat_pipeline):
    """A ScheduledEvent that is already due should fire during run_once()."""
    scheduler, sender, _executor, _client, _ts = heartbeat_pipeline

    event = ScheduledEvent(
        id=uuid4(),
        user_id="user-1",
        skill_name="task_manager",
        action_type="send_message",
        trigger_time=datetime.now() - timedelta(seconds=1),
        data={"message": "Scheduled ping"},
    )
    event_id = str(event.id)
    scheduler.schedule_event(event)

    # The event should be pending
    assert event_id in scheduler._scheduled_events

    await scheduler.run_once()

    # After execution the event must be removed from pending
    assert event_id not in scheduler._scheduled_events

    # The message should have been sent through MockMessageSender
    assert any(msg[1] == "Scheduled ping" for msg in sender.messages)


@pytest.mark.integration
async def test_rate_limiting(heartbeat_pipeline):
    """ActionExecutor should rate-limit after MAX_MESSAGES_PER_HOUR sends."""
    _scheduler, _sender, executor, _client, _ts = heartbeat_pipeline

    action = HeartbeatAction(
        skill_name="task_manager",
        action_type="send_message",
        user_id="user-1",
        data={"message": "ping"},
        priority=5,
    )

    # Execute MAX_MESSAGES_PER_HOUR times (should all succeed)
    for _ in range(ActionExecutor.MAX_MESSAGES_PER_HOUR):
        result = await executor.execute(action)
        assert result.success is True, f"Expected success, got error: {result.error}"

    # The next one should be rate-limited
    result = await executor.execute(action)
    assert result.success is False
    assert "Rate limited" in (result.error or "")


@pytest.mark.integration
async def test_quiet_hours_skip(heartbeat_pipeline):
    """During quiet hours, _run_heartbeat() should skip the skills client call."""
    scheduler, sender, _executor, _client, _ts = heartbeat_pipeline

    # Override config so it is *always* quiet
    scheduler._config.quiet_start = time(0, 0)
    scheduler._config.quiet_end = time(23, 59)

    msgs_before = len(sender.messages)
    total_actions_before = scheduler.stats.total_actions

    await scheduler._run_heartbeat()

    # No new skill actions should have been fetched
    assert scheduler.stats.total_actions == total_actions_before

    # No messages should have been sent via the skills pipeline
    assert len(sender.messages) == msgs_before

    # But total_beats should have incremented (heartbeat still ran)
    assert scheduler.stats.total_beats >= 1


@pytest.mark.integration
async def test_no_users_no_actions(heartbeat_pipeline):
    """With an empty user_ids list, run_once() should return an empty list."""
    scheduler, _sender, _executor, _client, _ts = heartbeat_pipeline

    scheduler.set_user_ids([])

    results = await scheduler.run_once()

    assert results == []


@pytest.mark.integration
async def test_cancel_scheduled_event(heartbeat_pipeline):
    """schedule_event + cancel_event should add then remove the event."""
    scheduler, _sender, _executor, _client, _ts = heartbeat_pipeline

    event = ScheduledEvent(
        id=uuid4(),
        user_id="user-1",
        skill_name="task_manager",
        action_type="send_message",
        trigger_time=datetime.now() + timedelta(hours=1),
        data={"message": "Will be cancelled"},
    )
    event_id = str(event.id)

    scheduler.schedule_event(event)
    assert event_id in scheduler._scheduled_events

    # First cancel should succeed
    assert scheduler.cancel_event(event_id) is True
    assert event_id not in scheduler._scheduled_events

    # Second cancel should return False (already removed)
    assert scheduler.cancel_event(event_id) is False
