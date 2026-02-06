"""Discord notification channel.

Sends notifications via Discord DMs to configured users.
"""

from typing import Any

import discord

from zetherion_ai.logging import get_logger
from zetherion_ai.notifications.dispatcher import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)

log = get_logger("zetherion_ai.notifications.discord")


class DiscordNotifier(NotificationChannel):
    """Sends notifications via Discord DMs.

    Requires a Discord bot client to be initialized and connected.
    """

    def __init__(
        self,
        bot: discord.Client,
        admin_user_ids: list[int] | None = None,
        min_priority: NotificationPriority = NotificationPriority.LOW,
    ):
        """Initialize the Discord notifier.

        Args:
            bot: Discord bot client (must be connected).
            admin_user_ids: List of user IDs to notify. If None, notifications
                           are logged but not sent.
            min_priority: Minimum priority level to send (default: LOW = all).
        """
        self._bot = bot
        self._admin_ids = admin_user_ids or []
        self._min_priority = min_priority

        # Priority ordering for comparison
        self._priority_order = {
            NotificationPriority.LOW: 0,
            NotificationPriority.MEDIUM: 1,
            NotificationPriority.HIGH: 2,
            NotificationPriority.CRITICAL: 3,
        }

    def supports_priority(self, priority: NotificationPriority) -> bool:
        """Check if this channel supports a priority level.

        Args:
            priority: The priority to check.

        Returns:
            True if priority meets minimum threshold.
        """
        return self._priority_order[priority] >= self._priority_order[self._min_priority]

    async def send(self, notification: Notification) -> bool:
        """Send a notification via Discord DM.

        Args:
            notification: The notification to send.

        Returns:
            True if sent successfully to at least one user.
        """
        if not self._admin_ids:
            log.debug(
                "discord_notification_skipped",
                reason="no admin users configured",
            )
            return False

        if not self._bot.is_ready():
            log.warning(
                "discord_notification_failed",
                reason="bot not ready",
            )
            return False

        # Format the message
        message = self._format_notification(notification)

        # Send to all admin users
        sent_count = 0
        for user_id in self._admin_ids:
            try:
                user = await self._bot.fetch_user(user_id)
                if user:
                    await user.send(message)
                    sent_count += 1
                    log.debug(
                        "discord_dm_sent",
                        user_id=user_id,
                        type=notification.type.value,
                    )
            except discord.Forbidden:
                log.warning(
                    "discord_dm_forbidden",
                    user_id=user_id,
                    reason="user has DMs disabled",
                )
            except discord.NotFound:
                log.warning(
                    "discord_user_not_found",
                    user_id=user_id,
                )
            except Exception as e:
                log.error(
                    "discord_dm_failed",
                    user_id=user_id,
                    error=str(e),
                )

        if sent_count > 0:
            log.info(
                "discord_notification_sent",
                type=notification.type.value,
                recipients=sent_count,
            )

        return sent_count > 0

    def _format_notification(self, notification: Notification) -> str:
        """Format a notification for Discord.

        Args:
            notification: The notification to format.

        Returns:
            Formatted message string.
        """
        # Priority emoji
        priority_emoji = {
            NotificationPriority.LOW: "",
            NotificationPriority.MEDIUM: "",
            NotificationPriority.HIGH: "",
            NotificationPriority.CRITICAL: "",
        }

        # Type emoji
        type_emoji = {
            NotificationType.MODEL_DISCOVERED: "",
            NotificationType.MODEL_DEPRECATED: "",
            NotificationType.MODEL_MISSING_PRICING: "",
            NotificationType.BUDGET_WARNING: "",
            NotificationType.BUDGET_EXCEEDED: "",
            NotificationType.DAILY_SUMMARY: "",
            NotificationType.MONTHLY_SUMMARY: "",
            NotificationType.RATE_LIMIT_HIT: "",
            NotificationType.RATE_LIMIT_FREQUENT: "",
            NotificationType.DISCOVERY_ERROR: "",
            NotificationType.SYSTEM_ERROR: "",
        }

        emoji = type_emoji.get(notification.type, "")
        priority = priority_emoji.get(notification.priority, "")

        # Build message
        lines = []

        # Title with emoji
        title = f"{emoji} **{notification.title}**"
        if priority:
            title = f"{priority} {title}"
        lines.append(title)

        # Message body
        lines.append("")
        lines.append(notification.message)

        # Timestamp
        lines.append("")
        lines.append(f"*{notification.timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    async def send_embed(
        self,
        notification: Notification,
        embed_data: dict[str, Any] | None = None,
    ) -> bool:
        """Send a notification with a Discord embed.

        Args:
            notification: The notification to send.
            embed_data: Optional additional embed fields.

        Returns:
            True if sent successfully.
        """
        if not self._admin_ids or not self._bot.is_ready():
            return False

        # Create embed
        embed = self._create_embed(notification, embed_data)

        # Send to all admin users
        sent_count = 0
        for user_id in self._admin_ids:
            try:
                user = await self._bot.fetch_user(user_id)
                if user:
                    await user.send(embed=embed)
                    sent_count += 1
            except Exception as e:
                log.error(
                    "discord_embed_failed",
                    user_id=user_id,
                    error=str(e),
                )

        return sent_count > 0

    def _create_embed(
        self,
        notification: Notification,
        extra_fields: dict[str, Any] | None = None,
    ) -> discord.Embed:
        """Create a Discord embed for a notification.

        Args:
            notification: The notification.
            extra_fields: Optional additional fields.

        Returns:
            Discord Embed object.
        """
        # Color based on priority
        colors = {
            NotificationPriority.LOW: discord.Color.light_grey(),
            NotificationPriority.MEDIUM: discord.Color.blue(),
            NotificationPriority.HIGH: discord.Color.orange(),
            NotificationPriority.CRITICAL: discord.Color.red(),
        }

        embed = discord.Embed(
            title=notification.title,
            description=notification.message,
            color=colors.get(notification.priority, discord.Color.default()),
            timestamp=notification.timestamp,
        )

        # Add metadata as fields
        if notification.metadata:
            for key, value in notification.metadata.items():
                if key not in ("model_id", "provider"):  # Skip duplicate info
                    embed.add_field(
                        name=key.replace("_", " ").title(),
                        value=str(value),
                        inline=True,
                    )

        # Add extra fields
        if extra_fields:
            for name, value in extra_fields.items():
                embed.add_field(
                    name=name,
                    value=str(value),
                    inline=True,
                )

        embed.set_footer(text="SecureClaw Cost Tracker")

        return embed
