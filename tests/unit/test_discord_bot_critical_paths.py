"""Critical production-path tests for Discord bot behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.memory.qdrant import QdrantMemory


@pytest.fixture
def bot_with_queue() -> tuple[ZetherionAIBot, AsyncMock]:
    """Create a bot instance configured with queue + allowlisted user manager."""
    memory = AsyncMock(spec=QdrantMemory)
    user_manager = AsyncMock()
    user_manager.is_allowed = AsyncMock(return_value=True)
    queue_manager = AsyncMock()
    queue_manager.is_running = True

    bot = ZetherionAIBot(memory=memory, user_manager=user_manager, queue_manager=queue_manager)
    bot_user = MagicMock(spec=discord.ClientUser)
    bot_user.id = 999999999
    bot_user.name = "ZetherionAIBot"
    bot._connection.user = bot_user
    return bot, queue_manager


@pytest.fixture
def queue_message() -> MagicMock:
    """Message fixture for queue path (typing called as awaitable)."""
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
    message.content = "hello from dm"
    message.webhook_id = None
    return message


@pytest.fixture
def inline_message() -> MagicMock:
    """Message fixture for inline path (typing used as async context manager)."""
    message = MagicMock(spec=discord.Message)
    message.id = 123
    message.author = MagicMock(spec=discord.User)
    message.author.id = 321
    message.channel = MagicMock(spec=discord.DMChannel)
    message.channel.id = 456
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock()
    typing_cm.__aexit__ = AsyncMock()
    message.channel.typing = MagicMock(return_value=typing_cm)
    message.reply = AsyncMock()
    message.mentions = []
    message.content = "inline content"
    return message


class TestDiscordBotCriticalPaths:
    """Tests for queue fallback, prompt-injection block, and shutdown."""

    @pytest.mark.asyncio
    async def test_on_message_routes_to_queue_when_running(
        self,
        bot_with_queue: tuple[ZetherionAIBot, AsyncMock],
        queue_message: MagicMock,
    ) -> None:
        bot, _ = bot_with_queue
        bot._agent = AsyncMock()

        with (
            patch.object(bot, "_enqueue_message", new=AsyncMock()) as mock_enqueue,
            patch.object(bot._rate_limiter, "check", return_value=(True, None)),
            patch(
                "zetherion_ai.discord.bot.detect_prompt_injection",
                return_value=False,
            ),
        ):
            await bot.on_message(queue_message)

        mock_enqueue.assert_awaited_once_with(queue_message, "hello from dm", False)

    @pytest.mark.asyncio
    async def test_on_message_blocks_prompt_injection(
        self,
        bot_with_queue: tuple[ZetherionAIBot, AsyncMock],
        queue_message: MagicMock,
    ) -> None:
        bot, _ = bot_with_queue
        bot._agent = AsyncMock()

        with (
            patch.object(bot, "_enqueue_message", new=AsyncMock()) as mock_enqueue,
            patch.object(bot._rate_limiter, "check", return_value=(True, None)),
            patch(
                "zetherion_ai.discord.bot.detect_prompt_injection",
                return_value=True,
            ),
        ):
            await bot.on_message(queue_message)

        mock_enqueue.assert_not_called()
        queue_message.reply.assert_awaited_once()
        assert "unusual patterns" in queue_message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_enqueue_message_failure_falls_back_inline(
        self,
        bot_with_queue: tuple[ZetherionAIBot, AsyncMock],
        queue_message: MagicMock,
    ) -> None:
        bot, queue_manager = bot_with_queue
        queue_manager.enqueue = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch.object(bot, "_process_message_inline", new=AsyncMock()) as mock_inline:
            await bot._enqueue_message(queue_message, "queued text", False)

        mock_inline.assert_awaited_once_with(queue_message, "queued text")

    @pytest.mark.asyncio
    async def test_process_message_inline_handles_generation_error(
        self,
        bot_with_queue: tuple[ZetherionAIBot, AsyncMock],
        inline_message: MagicMock,
    ) -> None:
        bot, _ = bot_with_queue
        bot._agent = AsyncMock()
        bot._agent.generate_response = AsyncMock(side_effect=RuntimeError("llm error"))

        await bot._process_message_inline(inline_message, "content")

        inline_message.reply.assert_awaited_once()
        assert "issue processing your message" in inline_message.reply.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_send_long_message_splits_chunks(self) -> None:
        bot = ZetherionAIBot(memory=AsyncMock(spec=QdrantMemory))
        channel = AsyncMock()

        content = "line1\nline2\nline3\nline4"
        await bot._send_long_message(channel, content, max_length=7)

        assert channel.send.await_count >= 2

    @pytest.mark.asyncio
    async def test_close_cleans_background_resources(
        self,
        bot_with_queue: tuple[ZetherionAIBot, AsyncMock],
    ) -> None:
        bot, queue_manager = bot_with_queue
        queue_manager.stop = AsyncMock()

        blocker = asyncio.Event()

        async def _pending() -> None:
            await blocker.wait()

        bot._keep_warm_task = asyncio.create_task(_pending())
        bot._agent = MagicMock()
        bot._agent._inference_broker = AsyncMock()

        with patch.object(discord.Client, "close", new=AsyncMock()) as mock_super_close:
            await bot.close()

        queue_manager.stop.assert_awaited_once()
        bot._agent._inference_broker.close.assert_awaited_once()
        mock_super_close.assert_awaited_once()
