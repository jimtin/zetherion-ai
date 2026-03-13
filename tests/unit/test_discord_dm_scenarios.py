"""Deterministic local Discord DM scenarios for the sharded local gate."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.queue.manager import QueueManager
from zetherion_ai.queue.processors import QueueProcessors
from zetherion_ai.queue.storage import QueueStorage


@dataclass(frozen=True)
class DmScenario:
    """Minimal scripted DM scenario for local reliability checks."""

    name: str
    queue_heartbeat_at: float
    now: float
    expected_path: str


class FakeDiscordScenarioRunner:
    """Drive one DM scenario through the real bot routing logic."""

    def __init__(self) -> None:
        memory = AsyncMock(spec=QdrantMemory)
        user_manager = AsyncMock()
        user_manager.is_allowed = AsyncMock(return_value=True)
        storage = AsyncMock(spec=QueueStorage)
        processors = MagicMock(spec=QueueProcessors)
        self.queue_manager = QueueManager(storage=storage, processors=processors)
        self.queue_manager._running = True
        self.bot = ZetherionAIBot(
            memory=memory,
            user_manager=user_manager,
            queue_manager=self.queue_manager,
        )
        self.bot._agent = AsyncMock()
        bot_user = MagicMock(spec=discord.ClientUser)
        bot_user.id = 999999999
        bot_user.name = "ZetherionAIBot"
        self.bot._connection.user = bot_user

    @staticmethod
    def message() -> MagicMock:
        message = MagicMock(spec=discord.Message)
        message.id = 123
        message.author = MagicMock(spec=discord.User)
        message.author.id = 321
        message.author.bot = False
        message.channel = MagicMock(spec=discord.DMChannel)
        message.channel.id = 456
        message.channel.typing = AsyncMock()
        message.reply = AsyncMock()
        message.mentions = []
        message.content = "hello from deterministic dm scenario"
        message.webhook_id = None
        return message

    async def run(self, scenario: DmScenario) -> str:
        self.queue_manager._worker_last_heartbeat["interactive-0"] = scenario.queue_heartbeat_at
        self.queue_manager._worker_idle_timeout_seconds["interactive-0"] = 5.0
        message = self.message()

        with (
            patch("zetherion_ai.queue.manager.time.monotonic", return_value=scenario.now),
            patch.object(self.bot, "_enqueue_message", new=AsyncMock()) as mock_enqueue,
            patch.object(self.bot, "_process_message_inline", new=AsyncMock()) as mock_inline,
            patch.object(self.bot._rate_limiter, "check", return_value=(True, None)),
            patch("zetherion_ai.discord.bot.detect_prompt_injection", return_value=False),
        ):
            await self.bot.on_message(message)

        if mock_enqueue.await_count:
            return "queued"
        if mock_inline.await_count:
            return "inline"
        return "dropped"


@pytest.mark.asyncio
async def test_dm_scenario_routes_inline_when_queue_worker_is_stale() -> None:
    runner = FakeDiscordScenarioRunner()
    scenario = DmScenario(
        name="stale-worker-fallback",
        queue_heartbeat_at=100.0,
        now=107.0,
        expected_path="inline",
    )

    result = await runner.run(scenario)

    assert result == scenario.expected_path


@pytest.mark.asyncio
async def test_dm_scenario_routes_queue_when_worker_heartbeat_is_fresh() -> None:
    runner = FakeDiscordScenarioRunner()
    scenario = DmScenario(
        name="fresh-worker-queue",
        queue_heartbeat_at=100.0,
        now=102.0,
        expected_path="queued",
    )

    result = await runner.run(scenario)

    assert result == scenario.expected_path
