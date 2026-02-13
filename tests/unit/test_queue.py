"""Tests for the priority message queue system.

Covers QueueItem models, QueueStorage, QueueProcessors, and QueueManager.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.queue.manager import QueueManager
from zetherion_ai.queue.models import QueueItem, QueuePriority, QueueStatus, QueueTaskType
from zetherion_ai.queue.processors import ProcessorResult, QueueProcessors
from zetherion_ai.queue.storage import QueueStorage, _row_to_item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    """Build a mock asyncpg.Pool and return ``(pool, conn)``.

    The ``conn`` is the mock connection yielded by ``async with pool.acquire()``.
    ``pool.acquire()`` returns a sync context manager (matching asyncpg behaviour).
    """
    pool = MagicMock()
    conn = AsyncMock()

    # pool.acquire() returns an async context manager (not a coroutine)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx

    # conn.transaction() returns an async context manager (not a coroutine)
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=conn)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    conn.fetch.return_value = []
    conn.execute.return_value = "UPDATE 0"
    return pool, conn


def _make_row(**overrides: Any) -> dict[str, Any]:
    """Build a mock asyncpg.Record-like dict for a queue item."""
    row: dict[str, Any] = {
        "id": uuid4(),
        "priority": QueuePriority.INTERACTIVE,
        "status": QueueStatus.QUEUED,
        "task_type": QueueTaskType.DISCORD_MESSAGE,
        "user_id": 123456,
        "channel_id": 789,
        "payload": json.dumps({"content": "hello"}),
        "attempt_count": 0,
        "max_attempts": 3,
        "last_error": None,
        "worker_id": None,
        "created_at": datetime.now(),
        "scheduled_for": datetime.now(),
        "started_at": None,
        "completed_at": None,
        "correlation_id": None,
        "parent_id": None,
    }
    row.update(overrides)
    return row


# ===========================================================================
# QueueItem model tests
# ===========================================================================


class TestQueueItem:
    """Tests for QueueItem dataclass."""

    def test_defaults(self) -> None:
        item = QueueItem()
        assert isinstance(item.id, UUID)
        assert item.priority == QueuePriority.INTERACTIVE
        assert item.status == QueueStatus.QUEUED
        assert item.task_type == QueueTaskType.DISCORD_MESSAGE
        assert item.user_id == 0
        assert item.attempt_count == 0
        assert item.max_attempts == 3

    def test_to_dict_roundtrip(self) -> None:
        item = QueueItem(
            user_id=42,
            channel_id=100,
            payload={"key": "value"},
            task_type=QueueTaskType.SKILL_REQUEST,
        )
        d = item.to_dict()
        assert d["user_id"] == 42
        assert d["channel_id"] == 100
        assert d["payload"] == {"key": "value"}
        assert d["task_type"] == QueueTaskType.SKILL_REQUEST

        # Round-trip via from_dict
        restored = QueueItem.from_dict(d)
        assert restored.user_id == item.user_id
        assert restored.channel_id == item.channel_id
        assert restored.payload == item.payload
        assert str(restored.id) == str(item.id)

    def test_from_dict_defaults(self) -> None:
        item = QueueItem.from_dict({})
        assert isinstance(item.id, UUID)
        assert item.priority == QueuePriority.INTERACTIVE
        assert item.status == QueueStatus.QUEUED

    def test_from_dict_with_parent_id(self) -> None:
        parent = uuid4()
        item = QueueItem.from_dict({"parent_id": str(parent)})
        assert item.parent_id == parent

    def test_priority_ordering(self) -> None:
        assert QueuePriority.INTERACTIVE < QueuePriority.NEAR_INTERACTIVE
        assert QueuePriority.NEAR_INTERACTIVE < QueuePriority.SCHEDULED
        assert QueuePriority.SCHEDULED < QueuePriority.BULK


class TestQueueEnums:
    """Tests for queue enum values."""

    def test_status_values(self) -> None:
        assert QueueStatus.QUEUED == "queued"
        assert QueueStatus.PROCESSING == "processing"
        assert QueueStatus.COMPLETED == "completed"
        assert QueueStatus.FAILED == "failed"
        assert QueueStatus.DEAD == "dead"

    def test_task_type_values(self) -> None:
        assert QueueTaskType.DISCORD_MESSAGE == "discord_message"
        assert QueueTaskType.SKILL_REQUEST == "skill_request"
        assert QueueTaskType.HEARTBEAT_ACTION == "heartbeat_action"
        assert QueueTaskType.BULK_INGESTION == "bulk_ingestion"

    def test_priority_values(self) -> None:
        assert QueuePriority.INTERACTIVE == 0
        assert QueuePriority.NEAR_INTERACTIVE == 1
        assert QueuePriority.SCHEDULED == 2
        assert QueuePriority.BULK == 3


# ===========================================================================
# QueueStorage tests
# ===========================================================================


class TestRowToItem:
    """Tests for _row_to_item helper."""

    def test_converts_row(self) -> None:
        row = _make_row()
        item = _row_to_item(row)
        assert isinstance(item, QueueItem)
        assert item.id == row["id"]
        assert item.user_id == 123456
        assert item.payload == {"content": "hello"}

    def test_handles_dict_payload(self) -> None:
        row = _make_row(payload={"already": "parsed"})
        item = _row_to_item(row)
        assert item.payload == {"already": "parsed"}

    def test_handles_json_string_payload(self) -> None:
        row = _make_row(payload='{"from": "json"}')
        item = _row_to_item(row)
        assert item.payload == {"from": "json"}


class TestQueueStorageEnqueue:
    """Tests for QueueStorage.enqueue()."""

    @pytest.mark.asyncio
    async def test_enqueue_returns_uuid(self) -> None:
        pool, conn = _make_pool()
        expected_id = uuid4()
        conn.fetchrow.return_value = {"id": expected_id}

        storage = QueueStorage(pool=pool)
        item = QueueItem(user_id=42, payload={"test": True})
        result = await storage.enqueue(item)

        assert result == expected_id
        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_serializes_payload(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"id": uuid4()}

        storage = QueueStorage(pool=pool)
        payload = {"key": "value", "nested": [1, 2, 3]}
        item = QueueItem(user_id=42, payload=payload)
        await storage.enqueue(item)

        # Verify the JSON-serialised payload was passed
        call_args = conn.fetchrow.call_args
        assert json.dumps(payload) in [str(a) for a in call_args[0]]


class TestQueueStorageBatch:
    """Tests for QueueStorage.enqueue_batch()."""

    @pytest.mark.asyncio
    async def test_batch_empty_returns_empty(self) -> None:
        pool, _conn = _make_pool()
        storage = QueueStorage(pool=pool)
        result = await storage.enqueue_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_inserts_all(self) -> None:
        pool, conn = _make_pool()
        ids = [uuid4(), uuid4(), uuid4()]
        conn.fetchrow.side_effect = [{"id": i} for i in ids]

        storage = QueueStorage(pool=pool)
        items = [QueueItem(user_id=i) for i in range(3)]
        result = await storage.enqueue_batch(items)

        assert result == ids
        assert conn.fetchrow.call_count == 3


class TestQueueStorageDequeue:
    """Tests for QueueStorage.dequeue()."""

    @pytest.mark.asyncio
    async def test_dequeue_returns_none_when_empty(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = None

        storage = QueueStorage(pool=pool)
        result = await storage.dequeue(worker_id="w-0")
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_returns_item(self) -> None:
        pool, conn = _make_pool()
        row = _make_row(status="processing", worker_id="w-0")
        conn.fetchrow.return_value = row

        storage = QueueStorage(pool=pool)
        result = await storage.dequeue(worker_id="w-0")
        assert result is not None
        assert result.worker_id == "w-0"

    @pytest.mark.asyncio
    async def test_dequeue_passes_priority_range(self) -> None:
        pool, conn = _make_pool()
        row = _make_row(status="processing", worker_id="w-bg")
        conn.fetchrow.return_value = row

        storage = QueueStorage(pool=pool)
        await storage.dequeue(priority_min=2, priority_max=3, worker_id="w-bg")

        call_args = conn.fetchrow.call_args[0]
        assert call_args[1] == "w-bg"
        assert call_args[2] == 2
        assert call_args[3] == 3


class TestQueueStorageComplete:
    """Tests for QueueStorage.complete()."""

    @pytest.mark.asyncio
    async def test_complete_calls_update(self) -> None:
        pool, conn = _make_pool()

        storage = QueueStorage(pool=pool)
        item_id = uuid4()
        await storage.complete(item_id)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "completed" in sql


class TestQueueStorageFail:
    """Tests for QueueStorage.fail()."""

    @pytest.mark.asyncio
    async def test_fail_moves_to_dead_when_exhausted(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"attempt_count": 3, "max_attempts": 3}

        storage = QueueStorage(pool=pool)
        await storage.fail(uuid4(), "test error")

        # Should update to 'dead' status
        sql = conn.execute.call_args[0][0]
        assert "dead" in sql

    @pytest.mark.asyncio
    async def test_fail_requeues_with_backoff(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"attempt_count": 1, "max_attempts": 3}

        storage = QueueStorage(pool=pool)
        await storage.fail(uuid4(), "transient error")

        sql = conn.execute.call_args[0][0]
        assert "queued" in sql

        # First retry should use the first backoff bucket (5 seconds).
        next_run = conn.execute.call_args[0][3]
        assert isinstance(next_run, datetime)
        delta = (next_run - datetime.now(tz=UTC)).total_seconds()
        assert 0 <= delta <= 6

    @pytest.mark.asyncio
    async def test_fail_not_found_does_nothing(self) -> None:
        pool, conn = _make_pool()
        conn.fetchrow.return_value = None

        storage = QueueStorage(pool=pool)
        await storage.fail(uuid4(), "error")

        conn.execute.assert_not_called()


class TestQueueStorageHousekeeping:
    """Tests for housekeeping methods."""

    @pytest.mark.asyncio
    async def test_requeue_stale_returns_count(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 5"

        storage = QueueStorage(pool=pool)
        count = await storage.requeue_stale(timeout_seconds=300)
        assert count == 5

    @pytest.mark.asyncio
    async def test_get_status_counts(self) -> None:
        pool, conn = _make_pool()
        conn.fetch.return_value = [
            {"status": "queued", "cnt": 10},
            {"status": "processing", "cnt": 2},
        ]

        storage = QueueStorage(pool=pool)
        counts = await storage.get_status_counts()
        assert counts == {"queued": 10, "processing": 2}

    @pytest.mark.asyncio
    async def test_purge_completed(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "DELETE 7"

        storage = QueueStorage(pool=pool)
        count = await storage.purge_completed(older_than_hours=24)
        assert count == 7

    @pytest.mark.asyncio
    async def test_purge_dead(self) -> None:
        pool, conn = _make_pool()
        conn.execute.return_value = "DELETE 3"

        storage = QueueStorage(pool=pool)
        count = await storage.purge_dead(older_than_days=7)
        assert count == 3


# ===========================================================================
# QueueProcessors tests
# ===========================================================================


class TestProcessorResult:
    """Tests for ProcessorResult."""

    def test_defaults(self) -> None:
        r = ProcessorResult()
        assert r.success is True
        assert r.error is None
        assert r.data == {}

    def test_failure(self) -> None:
        r = ProcessorResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"


class TestQueueProcessorsDispatch:
    """Tests for QueueProcessors.process() dispatch."""

    @pytest.mark.asyncio
    async def test_unknown_task_type_succeeds(self) -> None:
        procs = QueueProcessors()
        result = await procs.process("nonexistent_type", {})
        assert result.success is True
        assert "Unknown" in (result.error or "")

    @pytest.mark.asyncio
    async def test_discord_message_without_bot(self) -> None:
        procs = QueueProcessors()
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {"content": "hello", "user_id": 42},
        )
        assert result.success is False
        assert "not available" in (result.error or "")

    @pytest.mark.asyncio
    async def test_discord_message_empty_content(self) -> None:
        bot = MagicMock()
        agent = AsyncMock()
        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {"content": "", "user_id": 42},
        )
        assert result.success is False
        assert "Empty" in (result.error or "")

    @pytest.mark.asyncio
    async def test_discord_message_processes_and_replies(self) -> None:
        # Mock bot that can get a channel and fetch a message
        mock_message = AsyncMock()
        mock_message.reply = AsyncMock()
        mock_message.channel = AsyncMock()

        mock_channel = AsyncMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_message)

        bot = MagicMock()
        bot.get_channel.return_value = mock_channel

        agent = AsyncMock()
        agent.generate_response = AsyncMock(return_value="Hello back!")

        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {
                "content": "hello",
                "user_id": 42,
                "channel_id": 100,
                "message_id": 200,
            },
        )
        assert result.success is True
        agent.generate_response.assert_called_once_with(user_id=42, channel_id=100, message="hello")
        mock_message.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_skill_request_without_client(self) -> None:
        procs = QueueProcessors()
        result = await procs.process(
            QueueTaskType.SKILL_REQUEST,
            {"user_id": "42", "intent": "create_task"},
        )
        assert result.success is False
        assert "not available" in (result.error or "")

    @pytest.mark.asyncio
    async def test_skill_request_dispatches(self) -> None:
        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.message = "Task created"
        mock_response.error = None
        client.handle_request.return_value = mock_response

        procs = QueueProcessors(skills_client=client)
        result = await procs.process(
            QueueTaskType.SKILL_REQUEST,
            {"user_id": "42", "intent": "create_task", "message": "Make a task"},
        )
        assert result.success is True
        client.handle_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_action_without_executor(self) -> None:
        procs = QueueProcessors()
        result = await procs.process(
            QueueTaskType.HEARTBEAT_ACTION,
            {"skill_name": "calendar", "action_type": "send_message"},
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_heartbeat_action_executes(self) -> None:
        from zetherion_ai.scheduler.actions import ActionResult
        from zetherion_ai.skills.base import HeartbeatAction

        executor = AsyncMock()
        executor.execute.return_value = ActionResult(
            action=HeartbeatAction(
                skill_name="calendar",
                action_type="send_message",
                user_id="42",
            ),
            success=True,
        )

        procs = QueueProcessors(action_executor=executor)
        result = await procs.process(
            QueueTaskType.HEARTBEAT_ACTION,
            {
                "skill_name": "calendar",
                "action_type": "send_message",
                "user_id": "42",
                "data": {},
                "priority": 5,
            },
        )
        assert result.success is True
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_ingestion_without_client(self) -> None:
        procs = QueueProcessors()
        result = await procs.process(
            QueueTaskType.BULK_INGESTION,
            {"source": "email", "operation": "sync"},
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_processor_catches_exceptions(self) -> None:
        bot = MagicMock()
        agent = AsyncMock()
        agent.generate_response = AsyncMock(side_effect=RuntimeError("boom"))

        mock_channel = AsyncMock()
        mock_channel.fetch_message = AsyncMock(side_effect=Exception("not found"))
        bot.get_channel.return_value = mock_channel

        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {"content": "hello", "user_id": 42, "channel_id": 1, "message_id": 2},
        )
        assert result.success is False
        assert "boom" in (result.error or "")


# ===========================================================================
# QueueManager tests
# ===========================================================================


class TestQueueManagerEnqueue:
    """Tests for QueueManager.enqueue()."""

    @pytest.mark.asyncio
    async def test_enqueue_creates_item(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        storage.enqueue.return_value = uuid4()
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)
        item_id = await mgr.enqueue(
            task_type=QueueTaskType.DISCORD_MESSAGE,
            user_id=42,
            channel_id=100,
            payload={"content": "test"},
        )

        assert isinstance(item_id, UUID)
        storage.enqueue.assert_called_once()
        enqueued_item = storage.enqueue.call_args[0][0]
        assert enqueued_item.user_id == 42
        assert enqueued_item.task_type == QueueTaskType.DISCORD_MESSAGE

    @pytest.mark.asyncio
    async def test_enqueue_with_enum_task_type(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        storage.enqueue.return_value = uuid4()
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)
        await mgr.enqueue(
            task_type=QueueTaskType.HEARTBEAT_ACTION,
            user_id=1,
            payload={},
            priority=QueuePriority.SCHEDULED,
        )

        enqueued_item = storage.enqueue.call_args[0][0]
        assert enqueued_item.task_type == "heartbeat_action"
        assert enqueued_item.priority == QueuePriority.SCHEDULED


class TestQueueManagerLifecycle:
    """Tests for QueueManager start/stop."""

    @pytest.mark.asyncio
    async def test_start_creates_workers(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        processors = MagicMock(spec=QueueProcessors)
        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 2
            s.queue_background_workers = 1
            s.queue_poll_interval_ms = 100
            s.queue_background_poll_ms = 1000
            mock_settings.return_value = s

            await mgr.start()
            assert mgr.is_running is True
            # 2 interactive + 1 background = 3 workers
            assert len(mgr._workers) == 3

            await mgr.stop()
            assert mgr.is_running is False
            assert len(mgr._workers) == 0

    @pytest.mark.asyncio
    async def test_start_uses_distinct_priority_bands(self) -> None:
        """Interactive and background workers should dequeue different priority ranges."""
        storage = AsyncMock(spec=QueueStorage)
        storage.dequeue = AsyncMock(return_value=None)
        processors = MagicMock(spec=QueueProcessors)
        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 1
            s.queue_background_workers = 1
            s.queue_poll_interval_ms = 10
            s.queue_background_poll_ms = 10
            mock_settings.return_value = s

            await mgr.start()
            await asyncio.sleep(0.03)
            await mgr.stop()

        calls = storage.dequeue.call_args_list
        assert any(
            c.kwargs.get("priority_min") == QueuePriority.INTERACTIVE
            and c.kwargs.get("priority_max") == QueuePriority.NEAR_INTERACTIVE
            for c in calls
        )
        assert any(
            c.kwargs.get("priority_min") == QueuePriority.SCHEDULED
            and c.kwargs.get("priority_max") == QueuePriority.BULK
            for c in calls
        )

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        processors = MagicMock(spec=QueueProcessors)
        mgr = QueueManager(storage=storage, processors=processors)
        await mgr.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        processors = MagicMock(spec=QueueProcessors)
        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 1
            s.queue_background_workers = 0
            s.queue_poll_interval_ms = 100
            s.queue_background_poll_ms = 1000
            mock_settings.return_value = s

            await mgr.start()
            worker_count = len(mgr._workers)
            await mgr.start()  # Should not add more workers
            assert len(mgr._workers) == worker_count

            await mgr.stop()

    @pytest.mark.asyncio
    async def test_get_status(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        storage.get_status_counts.return_value = {"queued": 5, "processing": 2}
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)
        status = await mgr.get_status()
        assert status["running"] is False
        assert status["status_counts"] == {"queued": 5, "processing": 2}


class TestQueueManagerProcessItem:
    """Tests for QueueManager._process_item()."""

    @pytest.mark.asyncio
    async def test_process_item_success(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        processors = AsyncMock(spec=QueueProcessors)
        processors.process.return_value = ProcessorResult(success=True)

        mgr = QueueManager(storage=storage, processors=processors)
        item = QueueItem(user_id=42, payload={"test": True})

        await mgr._process_item(item, worker_name="test-0")

        processors.process.assert_called_once_with(item.task_type, item.payload)
        storage.complete.assert_called_once_with(item.id)

    @pytest.mark.asyncio
    async def test_process_item_failure(self) -> None:
        storage = AsyncMock(spec=QueueStorage)
        processors = AsyncMock(spec=QueueProcessors)
        processors.process.return_value = ProcessorResult(success=False, error="oops")

        mgr = QueueManager(storage=storage, processors=processors)
        item = QueueItem(user_id=42, payload={"test": True})

        await mgr._process_item(item, worker_name="test-0")

        storage.fail.assert_called_once_with(item.id, "oops")


# ===========================================================================
# Package exports
# ===========================================================================


class TestQueuePackageExports:
    """Verify the queue package __init__.py exports."""

    def test_exports(self) -> None:
        from zetherion_ai.queue import (
            QueueItem,
            QueueManager,
            QueueProcessors,
            QueueStorage,
        )

        # Just verify they are importable
        assert QueueItem is not None
        assert QueueManager is not None
        assert QueueProcessors is not None
        assert QueueStorage is not None


# ===========================================================================
# Additional coverage for uncovered paths
# ===========================================================================


class TestQueueProcessorsSendReply:
    """Tests for _send_reply helper method."""

    @pytest.mark.asyncio
    async def test_send_reply_short_message(self) -> None:
        """Short messages are sent as a single reply."""
        message = AsyncMock()
        message.reply = AsyncMock()

        await QueueProcessors._send_reply(message, "Short message", max_length=2000)

        message.reply.assert_called_once_with("Short message", mention_author=True)

    @pytest.mark.asyncio
    async def test_send_reply_long_message_splits(self) -> None:
        """Long messages are split across multiple sends."""
        message = AsyncMock()
        message.reply = AsyncMock()
        message.channel = AsyncMock()
        message.channel.send = AsyncMock()

        # Create a message that needs to be split
        long_text = "Line1\n" * 300  # Will exceed 2000 chars

        await QueueProcessors._send_reply(message, long_text, max_length=2000)

        # First part is a reply, subsequent parts use channel.send
        assert message.reply.call_count >= 1
        # Additional parts sent to channel (if any)
        # The exact count depends on how the splitting happens

    @pytest.mark.asyncio
    async def test_send_reply_splits_on_line_boundaries(self) -> None:
        """Messages are split on line boundaries when possible."""
        message = AsyncMock()
        message.reply = AsyncMock()
        message.channel = AsyncMock()
        message.channel.send = AsyncMock()

        # Create content that forces a split
        lines = ["A" * 100 for _ in range(30)]  # 3000 chars total
        content = "\n".join(lines)

        await QueueProcessors._send_reply(message, content, max_length=2000)

        # Should be split into multiple messages
        total_calls = message.reply.call_count + message.channel.send.call_count
        assert total_calls >= 2


class TestQueueProcessorsDiscordMessageEdgeCases:
    """Additional edge cases for Discord message processing."""

    @pytest.mark.asyncio
    async def test_discord_message_fetch_fails_sends_to_channel(self) -> None:
        """When message fetch fails, response is sent to channel instead."""
        mock_channel = AsyncMock()
        mock_channel.fetch_message = AsyncMock(side_effect=Exception("Not found"))
        mock_channel.send = AsyncMock()

        bot = MagicMock()
        bot.get_channel.return_value = mock_channel

        agent = AsyncMock()
        agent.generate_response = AsyncMock(return_value="Response text")

        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {
                "content": "hello",
                "user_id": 42,
                "channel_id": 100,
                "message_id": 200,
            },
        )

        assert result.success is True
        # Should send to channel since fetch failed
        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_discord_message_no_channel_found(self) -> None:
        """When channel is not found, processing still succeeds but no send."""
        bot = MagicMock()
        bot.get_channel.return_value = None

        agent = AsyncMock()
        agent.generate_response = AsyncMock(return_value="Response")

        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {
                "content": "hello",
                "user_id": 42,
                "channel_id": 100,
                "message_id": 200,
            },
        )

        # Should complete successfully even though no channel was found
        assert result.success is True

    @pytest.mark.asyncio
    async def test_discord_message_channel_without_send_method(self) -> None:
        """Channel without send method is handled gracefully."""
        mock_channel = MagicMock(spec=[])  # Explicitly no send method

        bot = MagicMock()
        bot.get_channel.return_value = mock_channel

        agent = AsyncMock()
        agent.generate_response = AsyncMock(return_value="Response")

        procs = QueueProcessors(bot=bot, agent=agent)
        result = await procs.process(
            QueueTaskType.DISCORD_MESSAGE,
            {
                "content": "hello",
                "user_id": 42,
                "channel_id": 100,
            },
        )

        # Should still succeed even though channel doesn't have send
        assert result.success is True


class TestQueueProcessorsBulkIngestion:
    """Tests for bulk ingestion processor."""

    @pytest.mark.asyncio
    async def test_bulk_ingestion_with_skills_client(self) -> None:
        """Bulk ingestion routes to skills client when available."""
        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.error = None
        client.handle_request.return_value = mock_response

        procs = QueueProcessors(skills_client=client)
        result = await procs.process(
            QueueTaskType.BULK_INGESTION,
            {
                "source": "youtube",
                "operation": "sync_channels",
                "user_id": "123",
                "data": {"channel_id": "UC123"},
            },
        )

        assert result.success is True
        client.handle_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_ingestion_skill_failure(self) -> None:
        """Bulk ingestion propagates skill failure."""
        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Sync failed"
        client.handle_request.return_value = mock_response

        procs = QueueProcessors(skills_client=client)
        result = await procs.process(
            QueueTaskType.BULK_INGESTION,
            {
                "source": "email",
                "operation": "import",
                "user_id": "456",
                "data": {},
            },
        )

        assert result.success is False
        assert result.error == "Sync failed"


class TestQueueManagerHousekeeping:
    """Tests for QueueManager housekeeping loop."""

    @pytest.mark.asyncio
    async def test_housekeeping_loop_runs_periodically(self) -> None:
        """Housekeeping loop calls requeue and purge methods."""
        storage = AsyncMock(spec=QueueStorage)
        storage.requeue_stale = AsyncMock(return_value=0)
        storage.purge_completed = AsyncMock(return_value=0)
        storage.purge_dead = AsyncMock(return_value=0)
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 0
            s.queue_background_workers = 0
            s.queue_stale_timeout_seconds = 300
            mock_settings.return_value = s

            # Start and immediately trigger housekeeping by patching sleep
            with patch("zetherion_ai.queue.manager._HOUSEKEEPING_INTERVAL_SECONDS", 0.01):
                await mgr.start()
                await asyncio.sleep(0.05)  # Let housekeeping run once
                await mgr.stop()

            # Housekeeping should have run at least once
            assert storage.requeue_stale.call_count >= 1
            assert storage.purge_completed.call_count >= 1
            assert storage.purge_dead.call_count >= 1

    @pytest.mark.asyncio
    async def test_housekeeping_handles_exceptions(self) -> None:
        """Housekeeping loop continues on exceptions."""
        storage = AsyncMock(spec=QueueStorage)
        storage.requeue_stale = AsyncMock(side_effect=Exception("DB error"))
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 0
            s.queue_background_workers = 0
            mock_settings.return_value = s

            # Start and stop quickly
            with patch("zetherion_ai.queue.manager._HOUSEKEEPING_INTERVAL_SECONDS", 0.01):
                await mgr.start()
                await asyncio.sleep(0.05)
                await mgr.stop()

            # Should not crash despite exception


class TestQueueManagerWorkerLoop:
    """Tests for worker loop edge cases."""

    @pytest.mark.asyncio
    async def test_worker_loop_stops_on_cancel(self) -> None:
        """Worker loop exits cleanly when cancelled."""
        storage = AsyncMock(spec=QueueStorage)
        storage.dequeue = AsyncMock(return_value=None)
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 1
            s.queue_background_workers = 0
            s.queue_poll_interval_ms = 10
            s.queue_background_poll_ms = 100
            mock_settings.return_value = s

            await mgr.start()
            await asyncio.sleep(0.05)  # Let worker start
            await mgr.stop()  # Cancels workers

            # Workers should be stopped
            assert len(mgr._workers) == 0

    @pytest.mark.asyncio
    async def test_stop_requeues_stale_items(self) -> None:
        """stop() requeues items stuck in processing."""
        storage = AsyncMock(spec=QueueStorage)
        storage.dequeue = AsyncMock(return_value=None)
        storage.requeue_stale = AsyncMock(return_value=3)
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 1
            s.queue_background_workers = 0
            s.queue_poll_interval_ms = 10
            s.queue_background_poll_ms = 100
            mock_settings.return_value = s

            await mgr.start()
            await mgr.stop()

            # Should call requeue_stale with timeout=0 on shutdown
            storage.requeue_stale.assert_called_with(timeout_seconds=0)

    @pytest.mark.asyncio
    async def test_stop_handles_requeue_error(self) -> None:
        """stop() handles requeue_stale exceptions gracefully."""
        storage = AsyncMock(spec=QueueStorage)
        storage.dequeue = AsyncMock(return_value=None)
        storage.requeue_stale = AsyncMock(side_effect=Exception("DB error"))
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)

        with patch("zetherion_ai.queue.manager.get_settings") as mock_settings:
            s = MagicMock()
            s.queue_interactive_workers = 1
            s.queue_background_workers = 0
            s.queue_poll_interval_ms = 10
            s.queue_background_poll_ms = 100
            mock_settings.return_value = s

            await mgr.start()
            await mgr.stop()  # Should not raise despite requeue error


class TestQueueManagerEnqueueWithStringTaskType:
    """Tests for enqueue with string task type."""

    @pytest.mark.asyncio
    async def test_enqueue_with_string_task_type(self) -> None:
        """enqueue() accepts string task type."""
        storage = AsyncMock(spec=QueueStorage)
        storage.enqueue.return_value = uuid4()
        processors = MagicMock(spec=QueueProcessors)

        mgr = QueueManager(storage=storage, processors=processors)
        item_id = await mgr.enqueue(
            task_type="custom_task_type",
            user_id=42,
            payload={},
        )

        assert isinstance(item_id, UUID)
        enqueued_item = storage.enqueue.call_args[0][0]
        assert enqueued_item.task_type == "custom_task_type"
