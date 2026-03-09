"""Unit tests for tenant notification service behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.announcements.dispatcher import (
    AnnouncementChannelDefinition,
    AnnouncementChannelRegistry,
)
from zetherion_ai.announcements.policy import AnnouncementPolicyDecision
from zetherion_ai.announcements.storage import AnnouncementReceipt, AnnouncementSeverity
from zetherion_ai.api.notification_service import TenantNotificationService


class _NoopAdapter:
    async def send(self, event) -> None:  # pragma: no cover - not used in service tests
        return None


@pytest.fixture()
def notification_service_parts():
    tenant_manager = AsyncMock()
    announcement_repository = AsyncMock()
    announcement_policy_engine = AsyncMock()
    tenant_admin_manager = AsyncMock()

    registry = AnnouncementChannelRegistry()
    registry.register(
        "webhook",
        _NoopAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="webhook",
            display_name="Webhook",
            description="POST notifications to a tenant webhook.",
            public_enabled=True,
            config_fields=("webhook_url",),
        ),
    )
    registry.register(
        "email",
        _NoopAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="email",
            display_name="Email",
            description="Send notifications by email.",
            public_enabled=True,
            config_fields=("email", "account_id"),
        ),
    )
    registry.register(
        "discord_dm",
        _NoopAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="discord_dm",
            display_name="Discord DM",
            description="Internal owner DM delivery.",
            public_enabled=False,
            config_fields=("target_user_id",),
        ),
    )

    service = TenantNotificationService(
        tenant_manager=tenant_manager,
        announcement_repository=announcement_repository,
        announcement_policy_engine=announcement_policy_engine,
        channel_registry=registry,
        tenant_admin_manager=tenant_admin_manager,
    )
    return (
        service,
        tenant_manager,
        announcement_repository,
        announcement_policy_engine,
        tenant_admin_manager,
    )


@pytest.mark.asyncio
async def test_list_channels_marks_email_unconfigured_without_connected_account(
    notification_service_parts,
) -> None:
    service, _, _, _, tenant_admin_manager = notification_service_parts
    tenant_admin_manager.list_email_accounts = AsyncMock(
        return_value=[
            {"account_id": "acct-ignored", "email_address": "ops@example.com", "status": "revoked"}
        ]
    )

    channels = await service.list_channels("tenant-1")

    assert [item["channel_id"] for item in channels] == ["email", "webhook"]
    email = channels[0]
    assert email["status"] == "unconfigured"
    assert email["metadata"]["reason"] == "no_connected_tenant_mailbox"
    webhook = channels[1]
    assert webhook["status"] == "available"


def test_validate_subscription_normalizes_webhook_and_email_payloads(
    notification_service_parts,
) -> None:
    service, _, _, _, _ = notification_service_parts

    webhook = service.validate_subscription(
        channel_id="Webhook",
        event_types=["order.failed", "order.failed", "  "],
        channel_config={"webhook_url": " https://example.com/hook "},
    )
    assert webhook == {
        "channel_id": "webhook",
        "event_types": ["order.failed"],
        "channel_config": {"webhook_url": "https://example.com/hook"},
    }

    email = service.validate_subscription(
        channel_id="EMAIL",
        event_types=["order.failed", "order.refunded"],
        channel_config={"email": " ALERTS@Example.com ", "account_id": "acct-1"},
    )
    assert email == {
        "channel_id": "email",
        "event_types": ["order.failed", "order.refunded"],
        "channel_config": {"email": "alerts@example.com", "account_id": "acct-1"},
    }


@pytest.mark.parametrize(
    ("channel_id", "event_types", "channel_config", "message"),
    [
        (
            "",
            ["order.failed"],
            {"webhook_url": "https://example.com/hook"},
            "channel_id is required",
        ),
        ("sms", ["order.failed"], {"number": "+61400000000"}, "Unsupported channel_id"),
        (
            "webhook",
            [],
            {"webhook_url": "https://example.com/hook"},
            "event_types must contain at least one event type",
        ),
        ("webhook", ["order.failed"], {}, "channel_config.webhook_url is required for webhook"),
        ("email", ["order.failed"], {}, "channel_config.email is required for email"),
    ],
)
def test_validate_subscription_rejects_invalid_payloads(
    notification_service_parts,
    channel_id: str,
    event_types: list[str],
    channel_config: dict[str, str],
    message: str,
) -> None:
    service, _, _, _, _ = notification_service_parts

    with pytest.raises(ValueError, match=message):
        service.validate_subscription(
            channel_id=channel_id,
            event_types=event_types,
            channel_config=channel_config,
        )


@pytest.mark.asyncio
async def test_publish_event_schedules_delivery_for_matching_subscription(
    notification_service_parts,
) -> None:
    (
        service,
        tenant_manager,
        announcement_repository,
        announcement_policy_engine,
        _,
    ) = notification_service_parts
    tenant_manager.match_notification_subscriptions = AsyncMock(
        return_value=[
            {
                "subscription_id": "sub-1",
                "channel_id": "webhook",
                "channel_config": {"webhook_url": "https://example.com/hook"},
                "template": {"title": "Alert: {title}", "body": "{payload_json}"},
            }
        ]
    )
    announcement_policy_engine.evaluate_event = AsyncMock(
        return_value=AnnouncementPolicyDecision(
            status="scheduled",
            delivery_mode="immediate",
            severity=AnnouncementSeverity.HIGH,
            scheduled_for=None,
            reason_code="recipient_channel_immediate_default",
        )
    )
    announcement_repository.create_event = AsyncMock(
        return_value=AnnouncementReceipt(
            status="accepted",
            event_id="evt-1",
            scheduled_for=None,
            reason_code="accepted_new",
        )
    )
    announcement_repository.create_delivery = AsyncMock()

    result = await service.publish_event(
        tenant_id="tenant-1",
        source_app="checkout",
        event_type="order.failed",
        severity="high",
        title="Payment failed",
        body="A payment failed",
        payload={"order_id": "o-1"},
        occurred_at=datetime(2026, 3, 10, 1, 0, tzinfo=UTC),
        dedupe_key="evt-key",
    )

    assert result["matched_subscriptions"] == 1
    assert result["deliveries"][0]["receipt"]["status"] == "scheduled"
    create_event_args = announcement_repository.create_event.await_args.args
    event = create_event_args[0]
    assert event.title == "Alert: Payment failed"
    assert event.body == '{"order_id": "o-1"}'
    assert event.recipient is not None
    assert event.recipient.routing_key == "webhook:url:https://example.com/hook"
    assert event.idempotency_key == "evt-key:sub-1"
    announcement_repository.create_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_event_handles_deferred_and_deduped_receipts(
    notification_service_parts,
) -> None:
    (
        service,
        tenant_manager,
        announcement_repository,
        announcement_policy_engine,
        _,
    ) = notification_service_parts
    tenant_manager.match_notification_subscriptions = AsyncMock(
        return_value=[
            {
                "subscription_id": "sub-email",
                "channel_id": "email",
                "channel_config": {"email": "alerts@example.com", "account_id": "acct-1"},
                "template": {"body": "{payload.details}", "title": "{missing.value}"},
            }
        ]
    )
    announcement_policy_engine.evaluate_event = AsyncMock(
        side_effect=[
            AnnouncementPolicyDecision(
                status="deferred",
                delivery_mode="deferred",
                severity=AnnouncementSeverity.NORMAL,
                scheduled_for=None,
                reason_code="muted_category",
            ),
            AnnouncementPolicyDecision(
                status="scheduled",
                delivery_mode="digest",
                severity=AnnouncementSeverity.NORMAL,
                scheduled_for=datetime(2026, 3, 10, 2, 0, tzinfo=UTC),
                reason_code="digest_window",
            ),
        ]
    )
    announcement_repository.create_event = AsyncMock(
        side_effect=[
            AnnouncementReceipt(
                status="accepted",
                event_id="evt-deferred",
                reason_code="accepted_new",
            ),
            AnnouncementReceipt(
                status="deduped",
                event_id="evt-deduped",
                reason_code="duplicate_fingerprint",
            ),
        ]
    )
    announcement_repository.create_delivery = AsyncMock()

    deferred = await service.publish_event(
        tenant_id="tenant-1",
        source_app="checkout",
        event_type="order.failed",
        severity="normal",
        title="Payment failed",
        body="Fallback body",
        payload={"details": ["one", "two"]},
        occurred_at=None,
        dedupe_key=None,
    )
    deduped = await service.publish_event(
        tenant_id="tenant-1",
        source_app="checkout",
        event_type="order.failed",
        severity="normal",
        title="Payment failed",
        body="Fallback body",
        payload={"details": {"order_id": "o-1"}},
        occurred_at=None,
        dedupe_key="dedupe-key",
    )

    assert deferred["deliveries"][0]["receipt"]["status"] == "deferred"
    assert deduped["deliveries"][0]["receipt"]["status"] == "deduped"
    assert announcement_repository.create_delivery.await_count == 0

    deferred_event = announcement_repository.create_event.await_args_list[0].args[0]
    assert deferred_event.title == "Payment failed"
    assert deferred_event.body == '["one", "two"]'
    deduped_event = announcement_repository.create_event.await_args_list[1].args[0]
    assert deduped_event.recipient is not None
    assert deduped_event.recipient.email == "alerts@example.com"
    assert deduped_event.recipient.metadata["account_id"] == "acct-1"


def test_subscription_recipient_and_render_template_helpers(notification_service_parts) -> None:
    service, _, _, _, _ = notification_service_parts

    webhook = TenantNotificationService._subscription_recipient(
        tenant_id="tenant-1",
        subscription={
            "subscription_id": "sub-1",
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
        },
    )
    assert webhook.routing_key == "webhook:url:https://example.com/hook"

    email = TenantNotificationService._subscription_recipient(
        tenant_id="tenant-1",
        subscription={
            "subscription_id": "sub-2",
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com", "account_id": "acct-9"},
        },
    )
    assert email.routing_key == "email:alerts@example.com"
    assert email.metadata["account_id"] == "acct-9"

    rendered_list = service._render_template(
        "{payload.items}",
        default="fallback",
        context={"payload": {"items": ["one", "two"]}},
    )
    assert rendered_list == '["one", "two"]'

    rendered_default = service._render_template(
        "{payload.missing}",
        default="fallback",
        context={"payload": {"items": ["one"]}},
    )
    assert rendered_default == "fallback"

    with pytest.raises(ValueError, match="Unsupported subscription channel"):
        TenantNotificationService._subscription_recipient(
            tenant_id="tenant-1",
            subscription={
                "subscription_id": "sub-3",
                "channel_id": "sms",
                "channel_config": {"number": "+61400000000"},
            },
        )
