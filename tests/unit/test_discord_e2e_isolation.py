"""Unit tests for Discord synthetic E2E isolation behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.discord.e2e_lease import DiscordE2ELease
from zetherion_ai.memory.qdrant import QdrantMemory


@pytest.fixture
def bot() -> ZetherionAIBot:
    memory = AsyncMock(spec=QdrantMemory)
    user_manager = AsyncMock()
    user_manager.is_allowed = AsyncMock(return_value=True)
    user_manager.get_role = AsyncMock(return_value="user")
    bot = ZetherionAIBot(memory=memory, user_manager=user_manager)
    mock_user = MagicMock(spec=discord.ClientUser)
    mock_user.id = 2222
    mock_user.name = "ZetherionAIBot"
    bot._connection.user = mock_user
    return bot


def _settings(**overrides) -> SimpleNamespace:
    defaults = {
        "allow_bot_messages": False,
        "discord_e2e_enabled": True,
        "discord_e2e_allowed_author_ids": [1111],
        "discord_e2e_guild_id": 123,
        "discord_e2e_category_id": 456,
        "discord_e2e_parent_channel_id": None,
        "discord_e2e_channel_prefix": "zeth-e2e",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _message(
    *, bot_user: discord.ClientUser, lease: DiscordE2ELease | None, author_id: int = 1111
) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock(spec=discord.User)
    message.author.id = author_id
    message.author.bot = True
    message.author.name = "discord-test-bot"
    message.channel = MagicMock(spec=discord.TextChannel)
    message.channel.id = 987654321
    message.channel.name = "zeth-e2e-run"
    message.channel.category_id = 456
    message.channel.topic = lease.to_topic() if lease is not None else None
    message.channel.typing = MagicMock()
    message.guild = MagicMock(spec=discord.Guild)
    message.guild.id = 123
    message.mentions = [bot_user]
    message.content = f"<@{bot_user.id}> ping"
    message.reply = AsyncMock()
    message.webhook_id = None
    return message


def _thread_message(
    *, bot_user: discord.ClientUser, lease: DiscordE2ELease | None, author_id: int = 1111
) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock(spec=discord.User)
    message.author.id = author_id
    message.author.bot = True
    message.author.name = "discord-test-bot"
    message.channel = MagicMock(spec=discord.Thread)
    message.channel.id = 987654322
    message.channel.parent_id = 1179752957579907102
    message.channel.name = lease.to_thread_name() if lease is not None else "zeth-e2e-run"
    message.channel.topic = None
    message.channel.typing = MagicMock()
    message.guild = MagicMock(spec=discord.Guild)
    message.guild.id = 123
    message.mentions = [bot_user]
    message.content = f"<@{bot_user.id}> ping"
    message.reply = AsyncMock()
    message.webhook_id = None
    return message


def _interaction(*, lease: DiscordE2ELease | None, author_id: int = 1111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.User)
    interaction.user.id = author_id
    interaction.user.bot = True
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.id = 987654321
    interaction.channel.name = "zeth-e2e-run"
    interaction.channel.category_id = 456
    interaction.channel.topic = lease.to_topic() if lease is not None else None
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = 123
    interaction.channel_id = 987654321
    interaction.response = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_on_message_bypasses_rate_limit_for_active_synthetic_run(bot: ZetherionAIBot) -> None:
    lease = DiscordE2ELease(
        run_id="discord-run-1",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )
    message = _message(bot_user=bot.user, lease=lease)
    bot._agent = AsyncMock()

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=_settings()),
        patch.object(bot, "_is_message_user_allowed", new=AsyncMock(return_value=True)),
        patch.object(
            bot, "_maybe_handle_worker_operator_command", new=AsyncMock(return_value=False)
        ),
        patch.object(bot, "_maybe_handle_dev_watcher_dm", new=AsyncMock(return_value=False)),
        patch.object(bot, "_maybe_handle_presence_quick_reply", new=AsyncMock(return_value=False)),
        patch.object(bot, "_is_security_blocked", new=AsyncMock(return_value=False)),
        patch.object(bot, "_process_message_inline", new=AsyncMock()) as process_inline,
        patch.object(bot._rate_limiter, "check", return_value=(False, "slow down")) as rate_check,
    ):
        await bot.on_message(message)

    rate_check.assert_not_called()
    process_inline.assert_awaited_once()
    message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_bypasses_allowlist_for_active_synthetic_run(bot: ZetherionAIBot) -> None:
    lease = DiscordE2ELease(
        run_id="discord-run-allowlist",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )
    message = _message(bot_user=bot.user, lease=lease)
    bot._agent = AsyncMock()

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=_settings()),
        patch.object(
            bot, "_is_message_user_allowed", new=AsyncMock(return_value=False)
        ) as allow_check,
        patch.object(
            bot, "_maybe_handle_worker_operator_command", new=AsyncMock(return_value=False)
        ),
        patch.object(bot, "_maybe_handle_dev_watcher_dm", new=AsyncMock(return_value=False)),
        patch.object(bot, "_maybe_handle_presence_quick_reply", new=AsyncMock(return_value=False)),
        patch.object(bot, "_is_security_blocked", new=AsyncMock(return_value=False)),
        patch.object(bot, "_process_message_inline", new=AsyncMock()) as process_inline,
        patch.object(bot._rate_limiter, "check", return_value=(False, "slow down")) as rate_check,
    ):
        await bot.on_message(message)

    allow_check.assert_not_called()
    rate_check.assert_not_called()
    process_inline.assert_awaited_once()
    message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_bypasses_rate_limit_for_active_synthetic_thread_run(
    bot: ZetherionAIBot,
) -> None:
    lease = DiscordE2ELease(
        run_id="discord-run-thread",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        guild_id=123,
        category_id=None,
        channel_prefix="zeth-e2e",
    )
    message = _thread_message(bot_user=bot.user, lease=lease)
    bot._agent = AsyncMock()

    with (
        patch(
            "zetherion_ai.discord.bot.get_settings",
            return_value=_settings(
                discord_e2e_category_id=None, discord_e2e_parent_channel_id=1179752957579907102
            ),
        ),
        patch.object(bot, "_is_message_user_allowed", new=AsyncMock(return_value=True)),
        patch.object(
            bot, "_maybe_handle_worker_operator_command", new=AsyncMock(return_value=False)
        ),
        patch.object(bot, "_maybe_handle_dev_watcher_dm", new=AsyncMock(return_value=False)),
        patch.object(bot, "_maybe_handle_presence_quick_reply", new=AsyncMock(return_value=False)),
        patch.object(bot, "_is_security_blocked", new=AsyncMock(return_value=False)),
        patch.object(bot, "_process_message_inline", new=AsyncMock()) as process_inline,
        patch.object(bot._rate_limiter, "check", return_value=(False, "slow down")) as rate_check,
    ):
        await bot.on_message(message)

    rate_check.assert_not_called()
    process_inline.assert_awaited_once()
    message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_check_security_bypasses_allowlist_for_active_synthetic_interaction(
    bot: ZetherionAIBot,
) -> None:
    lease = DiscordE2ELease(
        run_id="discord-run-interaction",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )
    interaction = _interaction(lease=lease)

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=_settings()),
        patch.object(
            bot, "_is_interaction_user_allowed", new=AsyncMock(return_value=False)
        ) as allow_check,
        patch.object(bot, "_is_security_blocked", new=AsyncMock(return_value=False)),
        patch.object(bot._rate_limiter, "check", return_value=(False, "slow down")) as rate_check,
    ):
        allowed = await bot._check_security(interaction, content="ping")

    assert allowed is True
    allow_check.assert_not_called()
    rate_check.assert_not_called()
    interaction.response.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_ignores_bot_when_synthetic_lease_is_expired(
    bot: ZetherionAIBot,
) -> None:
    lease = DiscordE2ELease(
        run_id="discord-run-2",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC) - timedelta(minutes=10),
        expires_at=datetime.now(tz=UTC) - timedelta(minutes=1),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )
    message = _message(bot_user=bot.user, lease=lease)

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=_settings()),
        patch.object(
            bot, "_is_message_user_allowed", new=AsyncMock(return_value=True)
        ) as allow_check,
        patch.object(bot._rate_limiter, "check", return_value=(False, "slow down")) as rate_check,
    ):
        await bot.on_message(message)

    allow_check.assert_not_called()
    rate_check.assert_not_called()
    message.reply.assert_not_called()
