"""Unit tests for the notifications package (Phase 5B.1)."""

import pytest

from zetherion_ai.notifications.dispatcher import (
    Notification,
    NotificationChannel,
    NotificationDispatcher,
    NotificationPriority,
    NotificationType,
)


class MockChannel(NotificationChannel):
    """Mock notification channel for testing."""

    def __init__(self, min_priority: NotificationPriority = NotificationPriority.LOW):
        self.min_priority = min_priority
        self.sent: list[Notification] = []
        self.should_fail = False

    async def send(self, notification: Notification) -> bool:
        if self.should_fail:
            raise RuntimeError("Channel failed")
        self.sent.append(notification)
        return True

    def supports_priority(self, priority: NotificationPriority) -> bool:
        priority_order = {
            NotificationPriority.LOW: 0,
            NotificationPriority.MEDIUM: 1,
            NotificationPriority.HIGH: 2,
            NotificationPriority.CRITICAL: 3,
        }
        return priority_order[priority] >= priority_order[self.min_priority]


class TestNotification:
    """Tests for Notification dataclass."""

    def test_notification_creation(self):
        """Test creating a notification."""
        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="New Model",
            message="A new model was discovered",
        )
        assert notification.type == NotificationType.MODEL_DISCOVERED
        assert notification.title == "New Model"
        assert notification.priority == NotificationPriority.MEDIUM  # default

    def test_notification_with_priority(self):
        """Test notification with explicit priority."""
        notification = Notification(
            type=NotificationType.BUDGET_EXCEEDED,
            title="Budget Alert",
            message="Over budget!",
            priority=NotificationPriority.CRITICAL,
        )
        assert notification.priority == NotificationPriority.CRITICAL

    def test_notification_with_metadata(self):
        """Test notification with metadata."""
        notification = Notification(
            type=NotificationType.RATE_LIMIT_HIT,
            title="Rate Limited",
            message="Hit rate limit",
            metadata={"provider": "openai", "model": "gpt-4o"},
        )
        assert notification.metadata["provider"] == "openai"
        assert notification.metadata["model"] == "gpt-4o"


class TestNotificationTypes:
    """Tests for NotificationType enum."""

    def test_model_notification_types(self):
        """Test model-related notification types."""
        assert NotificationType.MODEL_DISCOVERED.value == "model_discovered"
        assert NotificationType.MODEL_DEPRECATED.value == "model_deprecated"
        assert NotificationType.MODEL_MISSING_PRICING.value == "model_missing_pricing"

    def test_cost_notification_types(self):
        """Test cost-related notification types."""
        assert NotificationType.BUDGET_WARNING.value == "budget_warning"
        assert NotificationType.BUDGET_EXCEEDED.value == "budget_exceeded"
        assert NotificationType.DAILY_SUMMARY.value == "daily_summary"

    def test_rate_limit_types(self):
        """Test rate limit notification types."""
        assert NotificationType.RATE_LIMIT_HIT.value == "rate_limit_hit"


class TestNotificationPriority:
    """Tests for NotificationPriority enum."""

    def test_priority_values(self):
        """Test priority enum values."""
        assert NotificationPriority.LOW.value == "low"
        assert NotificationPriority.MEDIUM.value == "medium"
        assert NotificationPriority.HIGH.value == "high"
        assert NotificationPriority.CRITICAL.value == "critical"


class TestNotificationDispatcher:
    """Tests for NotificationDispatcher."""

    def test_register_channel(self):
        """Test registering a notification channel."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)
        assert len(dispatcher._channels) == 1

    @pytest.mark.asyncio
    async def test_dispatch_to_channel(self):
        """Test dispatching a notification."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="Test",
            message="Test message",
        )
        count = await dispatcher.dispatch(notification)

        assert count == 1
        assert len(channel.sent) == 1
        assert channel.sent[0].title == "Test"

    @pytest.mark.asyncio
    async def test_dispatch_respects_priority(self):
        """Test that dispatch respects channel priority filters."""
        dispatcher = NotificationDispatcher()
        # Channel only accepts HIGH and CRITICAL
        channel = MockChannel(min_priority=NotificationPriority.HIGH)
        dispatcher.register_channel(channel)

        # LOW priority - should not be sent
        low_notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="Low Priority",
            message="Test",
            priority=NotificationPriority.LOW,
        )
        count = await dispatcher.dispatch(low_notification)
        assert count == 0

        # HIGH priority - should be sent
        high_notification = Notification(
            type=NotificationType.BUDGET_WARNING,
            title="High Priority",
            message="Test",
            priority=NotificationPriority.HIGH,
        )
        count = await dispatcher.dispatch(high_notification)
        assert count == 1

    @pytest.mark.asyncio
    async def test_dispatch_to_multiple_channels(self):
        """Test dispatching to multiple channels."""
        dispatcher = NotificationDispatcher()
        channel1 = MockChannel()
        channel2 = MockChannel()
        dispatcher.register_channel(channel1)
        dispatcher.register_channel(channel2)

        notification = Notification(
            type=NotificationType.DAILY_SUMMARY,
            title="Summary",
            message="Daily summary",
        )
        count = await dispatcher.dispatch(notification)

        assert count == 2
        assert len(channel1.sent) == 1
        assert len(channel2.sent) == 1

    @pytest.mark.asyncio
    async def test_dispatch_handles_channel_failure(self):
        """Test that dispatch handles channel failures gracefully."""
        dispatcher = NotificationDispatcher()
        good_channel = MockChannel()
        bad_channel = MockChannel()
        bad_channel.should_fail = True

        dispatcher.register_channel(good_channel)
        dispatcher.register_channel(bad_channel)

        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="Test",
            message="Test",
        )
        count = await dispatcher.dispatch(notification)

        # Only good channel should succeed
        assert count == 1
        assert len(good_channel.sent) == 1

    def test_type_filtering(self):
        """Test enabling/disabling notification types."""
        dispatcher = NotificationDispatcher()

        # Disable a type
        dispatcher.set_type_enabled(NotificationType.MODEL_DISCOVERED, False)
        assert dispatcher.is_type_enabled(NotificationType.MODEL_DISCOVERED) is False

        # Other types should still be enabled by default
        assert dispatcher.is_type_enabled(NotificationType.BUDGET_WARNING) is True

    @pytest.mark.asyncio
    async def test_dispatch_respects_type_filter(self):
        """Test that disabled types are not dispatched."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        # Disable the type
        dispatcher.set_type_enabled(NotificationType.MODEL_DISCOVERED, False)

        notification = Notification(
            type=NotificationType.MODEL_DISCOVERED,
            title="Test",
            message="Test",
        )
        count = await dispatcher.dispatch(notification)

        assert count == 0
        assert len(channel.sent) == 0

    @pytest.mark.asyncio
    async def test_notify_model_discovered(self):
        """Test convenience method for model discovered."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_model_discovered(
            model_id="gpt-5",
            provider="openai",
            tier="quality",
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.MODEL_DISCOVERED
        assert "gpt-5" in channel.sent[0].message

    @pytest.mark.asyncio
    async def test_notify_model_deprecated(self):
        """Test convenience method for model deprecated."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_model_deprecated(
            model_id="old-model",
            provider="anthropic",
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.MODEL_DEPRECATED

    @pytest.mark.asyncio
    async def test_notify_budget_warning(self):
        """Test convenience method for budget warning."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_budget_warning(
            current=8.0,
            threshold=10.0,
            period="daily",
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.BUDGET_WARNING
        assert channel.sent[0].priority == NotificationPriority.HIGH
        assert "80%" in channel.sent[0].message

    @pytest.mark.asyncio
    async def test_notify_budget_exceeded(self):
        """Test convenience method for budget exceeded."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_budget_exceeded(
            current=12.0,
            threshold=10.0,
            period="daily",
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.BUDGET_EXCEEDED
        assert channel.sent[0].priority == NotificationPriority.CRITICAL

    @pytest.mark.asyncio
    async def test_notify_rate_limit(self):
        """Test convenience method for rate limit hit."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_rate_limit(
            provider="openai",
            model="gpt-4o",
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.RATE_LIMIT_HIT
        assert "gpt-4o" in channel.sent[0].message

    @pytest.mark.asyncio
    async def test_notify_daily_summary(self):
        """Test convenience method for daily summary."""
        dispatcher = NotificationDispatcher()
        channel = MockChannel()
        dispatcher.register_channel(channel)

        count = await dispatcher.notify_daily_summary(
            report_text="Total: $5.00",
            total_cost=5.0,
        )

        assert count == 1
        assert channel.sent[0].type == NotificationType.DAILY_SUMMARY
        assert channel.sent[0].priority == NotificationPriority.LOW
