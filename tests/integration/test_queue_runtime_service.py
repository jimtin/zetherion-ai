"""Docker-backed queue/runtime integration coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import asyncpg  # type: ignore[import-not-found]
import pytest
import pytest_asyncio

from tests.integration.e2e_runtime import get_runtime
from zetherion_ai.announcements.storage import (
    AnnouncementEventInput,
    AnnouncementRecipient,
    AnnouncementRepository,
)
from zetherion_ai.discord.user_manager import _SCHEMA_SQL as USER_MANAGER_SCHEMA_SQL
from zetherion_ai.queue.manager import QueueManager
from zetherion_ai.queue.models import QueueTaskType
from zetherion_ai.queue.processors import QueueProcessors
from zetherion_ai.queue.storage import QueueStorage
from zetherion_ai.runtime.status_store import RuntimeStatusStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.service_integration,
]


async def _wait_for(predicate, *, timeout: float = 8.0, interval: float = 0.1) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for integration state to converge")


@pytest_asyncio.fixture()
async def postgres_pool():
    runtime = get_runtime()
    pool = await asyncpg.create_pool(runtime.postgres_dsn, min_size=1, max_size=4)

    async with pool.acquire() as conn:
        await conn.execute(USER_MANAGER_SCHEMA_SQL)
    announcement_repository = AnnouncementRepository()
    await announcement_repository.initialize(pool)
    runtime_status_store = RuntimeStatusStore(pool)
    await runtime_status_store.initialize()

    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE message_queue RESTART IDENTITY")
        await conn.execute("TRUNCATE TABLE runtime_service_status")
        await conn.execute(
            "TRUNCATE TABLE announcement_deliveries, announcement_events RESTART IDENTITY CASCADE"
        )

    try:
        yield pool
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE TABLE message_queue, runtime_service_status RESTART IDENTITY CASCADE"
            )
            await conn.execute(
                "TRUNCATE TABLE announcement_deliveries, announcement_events "
                "RESTART IDENTITY CASCADE"
            )
        await pool.close()


class _FakeMessage:
    def __init__(self, channel: _FakeChannel) -> None:
        self.channel = channel
        self.replies: list[str] = []

    async def reply(self, content: str, mention_author: bool = True) -> None:
        self.replies.append(content)


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.message = _FakeMessage(self)

    async def fetch_message(self, _message_id: int) -> _FakeMessage:
        return self.message

    def get_partial_message(self, _message_id: int) -> _FakeMessage:
        return self.message

    async def send(self, content: str) -> None:
        self.sent.append(content)


class _FallbackChannel(_FakeChannel):
    async def fetch_message(self, _message_id: int) -> None:
        raise RuntimeError("message unavailable")

    def get_partial_message(self, _message_id: int) -> None:
        return None


class _FakeBot:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, _channel_id: int):
        return self._channel

    async def fetch_channel(self, _channel_id: int):
        return self._channel


class _FakeAgent:
    def __init__(self, response: str) -> None:
        self._response = response

    async def generate_response(self, **_: object) -> str:
        return self._response


@pytest.mark.service_e2e
@pytest.mark.delivery_canary
@pytest.mark.asyncio
async def test_queue_manager_processes_dm_items_without_stranding(
    postgres_pool, monkeypatch
) -> None:
    monkeypatch.setenv("QUEUE_INTERACTIVE_WORKERS", "1")
    monkeypatch.setenv("QUEUE_BACKGROUND_WORKERS", "0")
    monkeypatch.setenv("QUEUE_POLL_INTERVAL_MS", "20")
    monkeypatch.setenv("QUEUE_STALE_TIMEOUT_SECONDS", "1")

    from zetherion_ai.config import get_settings

    get_settings.cache_clear()

    storage = QueueStorage(postgres_pool)
    channel = _FakeChannel()
    manager = QueueManager(
        storage=storage,
        processors=QueueProcessors(bot=_FakeBot(channel), agent=_FakeAgent("hello from queue")),
    )
    await manager.start()

    try:
        await manager.enqueue(
            task_type=QueueTaskType.DISCORD_MESSAGE,
            user_id=1234,
            channel_id=5678,
            payload={
                "channel_id": 5678,
                "message_id": 42,
                "content": "hello",
                "user_id": 1234,
            },
        )

        async def _completed() -> bool:
            counts = await storage.get_status_counts()
            return counts.get("completed", 0) == 1 and counts.get("processing", 0) == 0

        await _wait_for(_completed)
        assert channel.message.replies == ["hello from queue"]
    finally:
        await manager.stop()
        get_settings.cache_clear()


@pytest.mark.service_e2e
@pytest.mark.delivery_canary
@pytest.mark.asyncio
async def test_queue_manager_falls_back_to_channel_send_when_reply_reference_is_missing(
    postgres_pool, monkeypatch
) -> None:
    monkeypatch.setenv("QUEUE_INTERACTIVE_WORKERS", "1")
    monkeypatch.setenv("QUEUE_BACKGROUND_WORKERS", "0")
    monkeypatch.setenv("QUEUE_POLL_INTERVAL_MS", "20")

    from zetherion_ai.config import get_settings

    get_settings.cache_clear()

    storage = QueueStorage(postgres_pool)
    channel = _FallbackChannel()
    manager = QueueManager(
        storage=storage,
        processors=QueueProcessors(bot=_FakeBot(channel), agent=_FakeAgent("fallback works")),
    )
    await manager.start()

    try:
        await manager.enqueue(
            task_type=QueueTaskType.DISCORD_MESSAGE,
            user_id=2233,
            channel_id=7788,
            payload={
                "channel_id": 7788,
                "message_id": 7,
                "content": "hello channel",
                "user_id": 2233,
            },
        )

        async def _completed() -> bool:
            counts = await storage.get_status_counts()
            return counts.get("completed", 0) == 1 and counts.get("processing", 0) == 0

        await _wait_for(_completed)
        assert channel.sent == ["fallback works"]
    finally:
        await manager.stop()
        get_settings.cache_clear()


@pytest.mark.service_e2e
@pytest.mark.asyncio
async def test_queue_manager_dead_letters_failed_discord_work_without_processing_leaks(
    postgres_pool, monkeypatch
) -> None:
    monkeypatch.setenv("QUEUE_INTERACTIVE_WORKERS", "1")
    monkeypatch.setenv("QUEUE_BACKGROUND_WORKERS", "0")
    monkeypatch.setenv("QUEUE_POLL_INTERVAL_MS", "20")
    monkeypatch.setenv("QUEUE_MAX_RETRY_ATTEMPTS", "1")

    from zetherion_ai.config import get_settings

    get_settings.cache_clear()

    storage = QueueStorage(postgres_pool)
    manager = QueueManager(storage=storage, processors=QueueProcessors())
    await manager.start()

    try:
        await manager.enqueue(
            task_type=QueueTaskType.DISCORD_MESSAGE,
            user_id=55,
            channel_id=66,
            payload={
                "channel_id": 66,
                "message_id": 99,
                "content": "this should dead-letter",
                "user_id": 55,
            },
        )

        async def _dead_lettered() -> bool:
            counts = await storage.get_status_counts()
            return counts.get("dead", 0) == 1 and counts.get("processing", 0) == 0

        await _wait_for(_dead_lettered)
    finally:
        await manager.stop()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_runtime_status_store_and_announcement_claim_probe_round_trip(postgres_pool) -> None:
    status_store = RuntimeStatusStore(postgres_pool)
    await status_store.initialize()
    await status_store.upsert_status(
        service_name="discord_bot",
        status="healthy",
        summary="worker green",
        details={"workers": 1},
        release_revision="sha-local",
        instance_id="local-slot-a",
    )

    statuses = await status_store.list_statuses()
    assert any(
        entry["service_name"] == "discord_bot" and entry["status"] == "healthy"
        for entry in statuses
    )

    repository = AnnouncementRepository()
    await repository.initialize(postgres_pool)
    receipt = await repository.create_event(
        AnnouncementEventInput(
            source="integration-test",
            category="runtime",
            severity="high",
            title="Queue ready",
            body="Queue runtime integration ready.",
            recipient=AnnouncementRecipient(
                channel="discord_dm",
                routing_key="discord_dm:user:1234",
                target_user_id=1234,
            ),
        )
    )
    delivery = await repository.create_delivery(
        event_id=receipt.event_id,
        channel="discord_dm",
        scheduled_for=datetime.now(UTC),
    )

    await repository.probe_claim_due_deliveries()
    claimed = await repository.claim_due_deliveries(limit=10)

    assert [item.delivery_id for item in claimed] == [delivery.delivery_id]
