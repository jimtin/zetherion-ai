"""Notification dispatcher for routing alerts to appropriate channels."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.notifications.dispatcher")


class NotificationType(Enum):
    """Types of notifications."""

    # Model notifications
    MODEL_DISCOVERED = "model_discovered"
    MODEL_DEPRECATED = "model_deprecated"
    MODEL_MISSING_PRICING = "model_missing_pricing"

    # Cost notifications
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXCEEDED = "budget_exceeded"
    DAILY_SUMMARY = "daily_summary"
    MONTHLY_SUMMARY = "monthly_summary"

    # Rate limit notifications
    RATE_LIMIT_HIT = "rate_limit_hit"
    RATE_LIMIT_FREQUENT = "rate_limit_frequent"

    # System notifications
    DISCOVERY_ERROR = "discovery_error"
    SYSTEM_ERROR = "system_error"


class NotificationPriority(Enum):
    """Priority levels for notifications."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Notification:
    """A notification to be sent."""

    type: NotificationType
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.MEDIUM
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class NotificationChannel(ABC):
    """Abstract base class for notification channels."""

    @abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Send a notification.

        Args:
            notification: The notification to send.

        Returns:
            True if sent successfully, False otherwise.
        """
        pass

    @abstractmethod
    def supports_priority(self, priority: NotificationPriority) -> bool:
        """Check if this channel supports a priority level.

        Args:
            priority: The priority to check.

        Returns:
            True if supported.
        """
        pass


class NotificationDispatcher:
    """Dispatches notifications to registered channels.

    Supports multiple channels and priority-based routing.
    """

    def __init__(self) -> None:
        """Initialize the dispatcher."""
        self._channels: list[NotificationChannel] = []
        self._type_filters: dict[NotificationType, bool] = {}

    def register_channel(self, channel: NotificationChannel) -> None:
        """Register a notification channel.

        Args:
            channel: The channel to register.
        """
        self._channels.append(channel)
        log.info("channel_registered", channel=channel.__class__.__name__)

    def set_type_enabled(
        self,
        notification_type: NotificationType,
        enabled: bool,
    ) -> None:
        """Enable or disable a notification type.

        Args:
            notification_type: The type to configure.
            enabled: Whether to enable it.
        """
        self._type_filters[notification_type] = enabled

    def is_type_enabled(self, notification_type: NotificationType) -> bool:
        """Check if a notification type is enabled.

        Args:
            notification_type: The type to check.

        Returns:
            True if enabled (default is True).
        """
        return self._type_filters.get(notification_type, True)

    async def dispatch(self, notification: Notification) -> int:
        """Dispatch a notification to all appropriate channels.

        Args:
            notification: The notification to dispatch.

        Returns:
            Number of channels that successfully received the notification.
        """
        if not self.is_type_enabled(notification.type):
            log.debug(
                "notification_filtered",
                type=notification.type.value,
            )
            return 0

        sent_count = 0
        for channel in self._channels:
            if channel.supports_priority(notification.priority):
                try:
                    success = await channel.send(notification)
                    if success:
                        sent_count += 1
                except Exception as e:
                    log.error(
                        "channel_send_failed",
                        channel=channel.__class__.__name__,
                        error=str(e),
                    )

        if sent_count > 0:
            log.info(
                "notification_dispatched",
                type=notification.type.value,
                priority=notification.priority.value,
                channels=sent_count,
            )
        else:
            log.warning(
                "notification_not_sent",
                type=notification.type.value,
                reason="no channels available",
            )

        return sent_count

    async def notify_model_discovered(
        self,
        model_id: str,
        provider: str,
        tier: str,
    ) -> int:
        """Send a notification about a newly discovered model.

        Args:
            model_id: The model ID.
            provider: The provider name.
            tier: The model tier.

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="New Model Discovered",
            message=f"New {tier} model from {provider}: **{model_id}**",
            priority=NotificationPriority.LOW,
            metadata={"model_id": model_id, "provider": provider, "tier": tier},
        )
        return await self.dispatch(notification)

    async def notify_model_deprecated(
        self,
        model_id: str,
        provider: str,
    ) -> int:
        """Send a notification about a deprecated model.

        Args:
            model_id: The model ID.
            provider: The provider name.

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.MODEL_DEPRECATED,
            title="Model Deprecated",
            message=f"Model **{model_id}** from {provider} has been deprecated.",
            priority=NotificationPriority.MEDIUM,
            metadata={"model_id": model_id, "provider": provider},
        )
        return await self.dispatch(notification)

    async def notify_missing_pricing(
        self,
        model_id: str,
        provider: str,
    ) -> int:
        """Send a notification about a model missing pricing data.

        Args:
            model_id: The model ID.
            provider: The provider name.

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.MODEL_MISSING_PRICING,
            title="Missing Pricing Data",
            message=f"No pricing data for **{model_id}** ({provider}). Using fallback estimates.",
            priority=NotificationPriority.LOW,
            metadata={"model_id": model_id, "provider": provider},
        )
        return await self.dispatch(notification)

    async def notify_budget_warning(
        self,
        current: float,
        threshold: float,
        period: str = "daily",
    ) -> int:
        """Send a budget warning notification.

        Args:
            current: Current spending.
            threshold: Budget threshold.
            period: Budget period (daily, monthly).

        Returns:
            Number of channels notified.
        """
        pct = (current / threshold) * 100
        notification = Notification(
            type=NotificationType.BUDGET_WARNING,
            title=f"{period.title()} Budget Warning",
            message=(
                f"Spending has reached **${current:.2f}** "
                f"({pct:.0f}% of ${threshold:.2f} {period} budget)."
            ),
            priority=NotificationPriority.HIGH,
            metadata={"current": current, "threshold": threshold, "period": period},
        )
        return await self.dispatch(notification)

    async def notify_budget_exceeded(
        self,
        current: float,
        threshold: float,
        period: str = "daily",
    ) -> int:
        """Send a budget exceeded notification.

        Args:
            current: Current spending.
            threshold: Budget threshold.
            period: Budget period (daily, monthly).

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.BUDGET_EXCEEDED,
            title=f"{period.title()} Budget Exceeded",
            message=(
                f"**Budget exceeded!** Spending is **${current:.2f}** "
                f"(${threshold:.2f} {period} limit)."
            ),
            priority=NotificationPriority.CRITICAL,
            metadata={"current": current, "threshold": threshold, "period": period},
        )
        return await self.dispatch(notification)

    async def notify_rate_limit(
        self,
        provider: str,
        model: str,
    ) -> int:
        """Send a rate limit notification.

        Args:
            provider: The provider that rate limited.
            model: The model that hit the limit.

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.RATE_LIMIT_HIT,
            title="Rate Limit Hit",
            message=f"Rate limit hit on **{model}** ({provider}).",
            priority=NotificationPriority.MEDIUM,
            metadata={"provider": provider, "model": model},
        )
        return await self.dispatch(notification)

    async def notify_daily_summary(
        self,
        report_text: str,
        total_cost: float,
    ) -> int:
        """Send a daily summary notification.

        Args:
            report_text: Formatted report text.
            total_cost: Total daily cost.

        Returns:
            Number of channels notified.
        """
        notification = Notification(
            type=NotificationType.DAILY_SUMMARY,
            title="Daily Cost Summary",
            message=report_text,
            priority=NotificationPriority.LOW,
            metadata={"total_cost": total_cost},
        )
        return await self.dispatch(notification)
