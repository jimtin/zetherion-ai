"""Unit tests for the Discord notification module."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from zetherion_ai.notifications.discord import DiscordNotifier
from zetherion_ai.notifications.dispatcher import (
    Notification,
    NotificationPriority,
    NotificationType,
)


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot."""
    bot = MagicMock(spec=discord.Client)
    bot.is_ready.return_value = True
    bot.fetch_user = AsyncMock()
    return bot


@pytest.fixture
def sample_notification():
    """Create a sample notification."""
    return Notification(
        type=NotificationType.MODEL_DISCOVERED,
        priority=NotificationPriority.MEDIUM,
        title="New Model Discovered",
        message="GPT-5 has been discovered",
        metadata={"model_id": "gpt-5", "provider": "openai"},
    )


class TestDiscordNotifierInit:
    """Tests for DiscordNotifier initialization."""

    def test_init_basic(self, mock_bot):
        """Test basic initialization."""
        notifier = DiscordNotifier(mock_bot)
        assert notifier._bot == mock_bot
        assert notifier._admin_ids == []
        assert notifier._min_priority == NotificationPriority.LOW

    def test_init_with_admin_ids(self, mock_bot):
        """Test initialization with admin IDs."""
        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123, 456])
        assert notifier._admin_ids == [123, 456]

    def test_init_with_min_priority(self, mock_bot):
        """Test initialization with minimum priority."""
        notifier = DiscordNotifier(mock_bot, min_priority=NotificationPriority.HIGH)
        assert notifier._min_priority == NotificationPriority.HIGH


class TestDiscordNotifierSupportsPriority:
    """Tests for supports_priority method."""

    def test_supports_low_priority(self, mock_bot):
        """Test that LOW priority is supported by default."""
        notifier = DiscordNotifier(mock_bot)
        assert notifier.supports_priority(NotificationPriority.LOW) is True
        assert notifier.supports_priority(NotificationPriority.MEDIUM) is True
        assert notifier.supports_priority(NotificationPriority.HIGH) is True
        assert notifier.supports_priority(NotificationPriority.CRITICAL) is True

    def test_supports_high_priority_only(self, mock_bot):
        """Test with HIGH minimum priority."""
        notifier = DiscordNotifier(mock_bot, min_priority=NotificationPriority.HIGH)
        assert notifier.supports_priority(NotificationPriority.LOW) is False
        assert notifier.supports_priority(NotificationPriority.MEDIUM) is False
        assert notifier.supports_priority(NotificationPriority.HIGH) is True
        assert notifier.supports_priority(NotificationPriority.CRITICAL) is True

    def test_supports_critical_only(self, mock_bot):
        """Test with CRITICAL minimum priority."""
        notifier = DiscordNotifier(mock_bot, min_priority=NotificationPriority.CRITICAL)
        assert notifier.supports_priority(NotificationPriority.LOW) is False
        assert notifier.supports_priority(NotificationPriority.MEDIUM) is False
        assert notifier.supports_priority(NotificationPriority.HIGH) is False
        assert notifier.supports_priority(NotificationPriority.CRITICAL) is True


class TestDiscordNotifierSend:
    """Tests for send method."""

    @pytest.mark.asyncio
    async def test_send_no_admin_ids(self, mock_bot, sample_notification):
        """Test send with no admin IDs configured."""
        notifier = DiscordNotifier(mock_bot, admin_user_ids=[])
        result = await notifier.send(sample_notification)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_bot_not_ready(self, mock_bot, sample_notification):
        """Test send when bot is not ready."""
        mock_bot.is_ready.return_value = False
        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send(sample_notification)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_success(self, mock_bot, sample_notification):
        """Test successful send."""
        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        mock_bot.fetch_user.return_value = mock_user

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send(sample_notification)

        assert result is True
        mock_bot.fetch_user.assert_called_once_with(123)
        mock_user.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_multiple_admins(self, mock_bot, sample_notification):
        """Test sending to multiple admins."""
        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        mock_bot.fetch_user.return_value = mock_user

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123, 456, 789])
        result = await notifier.send(sample_notification)

        assert result is True
        assert mock_bot.fetch_user.call_count == 3
        assert mock_user.send.call_count == 3

    @pytest.mark.asyncio
    async def test_send_forbidden_error(self, mock_bot, sample_notification):
        """Test send when user has DMs disabled."""
        mock_bot.fetch_user.side_effect = discord.Forbidden(MagicMock(), "DMs disabled")

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send(sample_notification)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_user_not_found(self, mock_bot, sample_notification):
        """Test send when user is not found."""
        mock_bot.fetch_user.side_effect = discord.NotFound(MagicMock(), "User not found")

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send(sample_notification)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_partial_success(self, mock_bot, sample_notification):
        """Test send with partial success (some users fail)."""
        mock_user_success = AsyncMock()
        mock_user_success.send = AsyncMock()

        call_count = 0

        async def fetch_user_side_effect(user_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise discord.NotFound(MagicMock(), "User not found")
            return mock_user_success

        mock_bot.fetch_user.side_effect = fetch_user_side_effect

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123, 456, 789])
        result = await notifier.send(sample_notification)

        # Should return True because at least one message was sent
        assert result is True
        assert mock_user_success.send.call_count == 2


class TestDiscordNotifierFormatNotification:
    """Tests for _format_notification method."""

    def test_format_basic(self, mock_bot, sample_notification):
        """Test basic formatting."""
        notifier = DiscordNotifier(mock_bot)
        message = notifier._format_notification(sample_notification)

        assert "New Model Discovered" in message
        assert "GPT-5 has been discovered" in message

    def test_format_with_priority(self, mock_bot):
        """Test formatting with different priorities."""
        notifier = DiscordNotifier(mock_bot)

        for priority in NotificationPriority:
            notification = Notification(
                type=NotificationType.BUDGET_WARNING,
                priority=priority,
                title="Test",
                message="Test message",
            )
            message = notifier._format_notification(notification)
            assert "Test" in message

    def test_format_with_all_types(self, mock_bot):
        """Test formatting with all notification types."""
        notifier = DiscordNotifier(mock_bot)

        for ntype in NotificationType:
            notification = Notification(
                type=ntype,
                priority=NotificationPriority.MEDIUM,
                title=f"Test {ntype.value}",
                message="Test message",
            )
            message = notifier._format_notification(notification)
            assert f"Test {ntype.value}" in message


class TestDiscordNotifierSendEmbed:
    """Tests for send_embed method."""

    @pytest.mark.asyncio
    async def test_send_embed_no_admins(self, mock_bot, sample_notification):
        """Test send_embed with no admin IDs."""
        notifier = DiscordNotifier(mock_bot, admin_user_ids=[])
        result = await notifier.send_embed(sample_notification)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_embed_bot_not_ready(self, mock_bot, sample_notification):
        """Test send_embed when bot is not ready."""
        mock_bot.is_ready.return_value = False
        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send_embed(sample_notification)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_embed_success(self, mock_bot, sample_notification):
        """Test successful embed send."""
        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        mock_bot.fetch_user.return_value = mock_user

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send_embed(sample_notification)

        assert result is True
        mock_user.send.assert_called_once()
        # Verify embed was passed
        call_args = mock_user.send.call_args
        assert "embed" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_send_embed_with_extra_data(self, mock_bot, sample_notification):
        """Test send_embed with extra data."""
        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        mock_bot.fetch_user.return_value = mock_user

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send_embed(
            sample_notification,
            embed_data={"Extra Field": "Extra Value"},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_send_embed_error(self, mock_bot, sample_notification):
        """Test send_embed with error."""
        mock_bot.fetch_user.side_effect = Exception("Connection error")

        notifier = DiscordNotifier(mock_bot, admin_user_ids=[123])
        result = await notifier.send_embed(sample_notification)

        assert result is False


class TestDiscordNotifierCreateEmbed:
    """Tests for _create_embed method."""

    def test_create_embed_basic(self, mock_bot, sample_notification):
        """Test basic embed creation."""
        notifier = DiscordNotifier(mock_bot)
        embed = notifier._create_embed(sample_notification)

        assert isinstance(embed, discord.Embed)
        assert embed.title == sample_notification.title
        assert embed.description == sample_notification.message

    def test_create_embed_colors_by_priority(self, mock_bot):
        """Test that embed colors vary by priority."""
        notifier = DiscordNotifier(mock_bot)
        embeds = {}

        for priority in NotificationPriority:
            notification = Notification(
                type=NotificationType.SYSTEM_ERROR,
                priority=priority,
                title="Test",
                message="Test",
            )
            embeds[priority] = notifier._create_embed(notification)

        # Different priorities should have different colors
        colors = [embed.color for embed in embeds.values()]
        # At least some should be different
        assert len(set(colors)) > 1

    def test_create_embed_with_metadata(self, mock_bot):
        """Test embed creation with metadata."""
        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            priority=NotificationPriority.MEDIUM,
            title="Test",
            message="Test",
            metadata={"cost": "$0.50", "tokens": 1000},
        )
        notifier = DiscordNotifier(mock_bot)
        embed = notifier._create_embed(notification)

        # Should have fields for metadata
        assert len(embed.fields) >= 2

    def test_create_embed_with_extra_fields(self, mock_bot, sample_notification):
        """Test embed creation with extra fields."""
        notifier = DiscordNotifier(mock_bot)
        embed = notifier._create_embed(
            sample_notification,
            extra_fields={"Field1": "Value1", "Field2": "Value2"},
        )

        # Should have extra fields
        field_names = [f.name for f in embed.fields]
        assert "Field1" in field_names
        assert "Field2" in field_names

    def test_create_embed_footer(self, mock_bot, sample_notification):
        """Test that embed has footer."""
        notifier = DiscordNotifier(mock_bot)
        embed = notifier._create_embed(sample_notification)

        assert embed.footer is not None
        assert "SecureClaw" in embed.footer.text
