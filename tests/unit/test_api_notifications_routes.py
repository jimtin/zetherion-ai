"""Unit tests for tenant notification API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from zetherion_ai.announcements.dispatcher import (
    AnnouncementChannelDefinition,
    AnnouncementChannelRegistry,
)
from zetherion_ai.announcements.policy import AnnouncementPolicyEngine
from zetherion_ai.announcements.storage import AnnouncementRepository
from zetherion_ai.api.notification_service import TenantNotificationService
from zetherion_ai.api.routes.notifications import (
    _get_notification_service,
    _parse_occurred_at,
    _parse_subscription_status,
    _serialise,
    handle_create_notification_subscription,
    handle_delete_notification_subscription,
    handle_list_notification_channels,
    handle_list_notification_subscriptions,
    handle_patch_notification_subscription,
    handle_publish_notification_event,
)


class _NoopAdapter:
    async def send(self, event) -> None:  # pragma: no cover - not invoked by route tests
        return None


@pytest_asyncio.fixture()
async def notifications_routes_client():
    tenant = {"tenant_id": "tenant-1"}
    tenant_manager = AsyncMock()
    tenant_admin_manager = AsyncMock()
    tenant_admin_manager.list_email_accounts = AsyncMock(return_value=[])
    announcement_repository = AsyncMock()
    announcement_policy_engine = AsyncMock()

    registry = AnnouncementChannelRegistry()
    registry.register(
        "webhook",
        _NoopAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="webhook",
            display_name="Webhook",
            description="POST notifications to a webhook",
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
            description="Send notifications by email",
            public_enabled=True,
            config_fields=("email", "account_id"),
        ),
    )
    service = TenantNotificationService(
        tenant_manager=tenant_manager,
        announcement_repository=announcement_repository,
        announcement_policy_engine=announcement_policy_engine,
        channel_registry=registry,
        tenant_admin_manager=tenant_admin_manager,
    )

    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["tenant"] = tenant
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["tenant_manager"] = tenant_manager
    app["tenant_notification_service"] = service
    app.router.add_get("/api/v1/notifications/channels", handle_list_notification_channels)
    app.router.add_get(
        "/api/v1/notifications/subscriptions",
        handle_list_notification_subscriptions,
    )
    app.router.add_post(
        "/api/v1/notifications/subscriptions",
        handle_create_notification_subscription,
    )
    app.router.add_patch(
        "/api/v1/notifications/subscriptions/{subscription_id}",
        handle_patch_notification_subscription,
    )
    app.router.add_delete(
        "/api/v1/notifications/subscriptions/{subscription_id}",
        handle_delete_notification_subscription,
    )
    app.router.add_post("/api/v1/notifications/events", handle_publish_notification_event)

    async with TestClient(TestServer(app)) as client:
        yield (
            client,
            tenant_manager,
            tenant_admin_manager,
            announcement_repository,
            announcement_policy_engine,
        )


@pytest.mark.asyncio
async def test_list_notification_subscriptions_serializes_datetimes(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.list_notification_subscriptions = AsyncMock(
        return_value=[
            {
                "subscription_id": "sub-1",
                "tenant_id": "tenant-1",
                "source_app": "checkout",
                "event_types": ["order.failed"],
                "channel_id": "webhook",
                "channel_config": {"webhook_url": "https://example.com/hook"},
                "template": {},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        ]
    )

    response = await client.get("/api/v1/notifications/subscriptions")

    assert response.status == 200
    body = await response.json()
    assert body["count"] == 1
    assert body["subscriptions"][0]["created_at"] == now.isoformat()


def test_notification_route_helper_functions_cover_parsing() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    assert _serialise({"created_at": now, "status": "active"}) == {
        "created_at": now.isoformat(),
        "status": "active",
    }
    assert _parse_occurred_at("2026-03-10T12:00:00") == datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assert _parse_subscription_status("PAUSED") == "paused"

    with pytest.raises(ValueError, match="Invalid occurred_at timestamp"):
        _parse_occurred_at("not-a-timestamp")
    with pytest.raises(ValueError, match="status must be active or paused"):
        _parse_subscription_status("disabled")


@pytest.mark.asyncio
async def test_get_notification_service_builds_and_caches_service() -> None:
    tenant_manager = AsyncMock()
    app = web.Application()
    app["tenant_manager"] = tenant_manager
    app["tenant_admin_manager"] = AsyncMock()
    app["announcement_repository"] = AnnouncementRepository()
    app["announcement_policy_engine"] = AnnouncementPolicyEngine(AnnouncementRepository())
    registry = AnnouncementChannelRegistry()
    registry.register(
        "webhook",
        _NoopAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="webhook",
            display_name="Webhook",
            description="POST notifications to a webhook",
            public_enabled=True,
            config_fields=("webhook_url",),
        ),
    )
    app["announcement_channel_registry"] = registry
    request = make_mocked_request("GET", "/api/v1/notifications/channels", app=app)

    service = _get_notification_service(request)

    assert isinstance(service, TenantNotificationService)
    assert app["tenant_notification_service"] is service


@pytest.mark.asyncio
async def test_list_notification_channels_includes_webhook_and_email(
    notifications_routes_client,
) -> None:
    client, _, tenant_admin_manager, _, _ = notifications_routes_client
    tenant_admin_manager.list_email_accounts.return_value = [
        {
            "account_id": "acct-1",
            "email_address": "ops@example.com",
            "status": "connected",
        }
    ]

    response = await client.get("/api/v1/notifications/channels")

    assert response.status == 200
    body = await response.json()
    assert body["count"] == 2
    channels = {item["channel_id"]: item for item in body["channels"]}
    assert channels["webhook"]["status"] == "available"
    assert channels["email"]["status"] == "available"
    assert channels["email"]["metadata"]["account_ids"] == ["acct-1"]


@pytest.mark.asyncio
async def test_create_notification_subscription_normalizes_payload(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.create_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": str(uuid4()),
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.post(
        "/api/v1/notifications/subscriptions",
        json={
            "source_app": "checkout",
            "event_types": ["order.failed", "order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
        },
    )

    assert response.status == 201
    body = await response.json()
    assert body["channel_id"] == "webhook"
    tenant_manager.create_notification_subscription.assert_awaited_once_with(
        "tenant-1",
        source_app="checkout",
        event_types=["order.failed"],
        channel_id="webhook",
        channel_config={"webhook_url": "https://example.com/hook"},
        template={},
        status="active",
    )


@pytest.mark.asyncio
async def test_create_notification_subscription_rejects_invalid_json(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client

    response = await client.post(
        "/api/v1/notifications/subscriptions",
        data="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "Invalid JSON body"}
    tenant_manager.create_notification_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_notification_subscription_validates_and_updates(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.update_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed", "order.refunded"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com", "account_id": "acct-1"},
            "template": {"title": "Alert: {title}"},
            "status": "paused",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        json={
            "event_types": ["order.failed", "order.refunded"],
            "channel_config": {"email": "alerts@example.com", "account_id": "acct-1"},
            "template": {"title": "Alert: {title}"},
            "status": "paused",
        },
    )

    assert response.status == 200
    body = await response.json()
    assert body["status"] == "paused"
    tenant_manager.update_notification_subscription.assert_awaited_once_with(
        "tenant-1",
        "sub-1",
        source_app=None,
        event_types=["order.failed", "order.refunded"],
        channel_config={"email": "alerts@example.com", "account_id": "acct-1"},
        template={"title": "Alert: {title}"},
        status="paused",
    )


@pytest.mark.asyncio
async def test_patch_notification_subscription_rejects_invalid_json(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        data="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "Invalid JSON body"}


@pytest.mark.asyncio
async def test_patch_notification_subscription_rejects_invalid_event_types(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        json={"event_types": "order.failed"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "event_types must be a list"}


@pytest.mark.asyncio
async def test_patch_notification_subscription_rejects_invalid_channel_config(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        json={"channel_config": "not-a-dict"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "channel_config must be an object"}


@pytest.mark.asyncio
async def test_create_notification_subscription_rejects_invalid_status(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client

    response = await client.post(
        "/api/v1/notifications/subscriptions",
        json={
            "event_types": ["order.failed"],
            "channel_id": "webhook",
            "channel_config": {"webhook_url": "https://example.com/hook"},
            "status": "disabled",
        },
    )

    assert response.status == 400
    assert await response.json() == {"error": "status must be active or paused"}
    tenant_manager.create_notification_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_notification_subscription_rejects_invalid_status(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        json={"status": "disabled"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "status must be active or paused"}
    tenant_manager.update_notification_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_notification_subscription_returns_404_when_missing(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    tenant_manager.get_notification_subscription = AsyncMock(return_value=None)

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-missing",
        json={"status": "paused"},
    )

    assert response.status == 404
    assert await response.json() == {"error": "Subscription not found"}


@pytest.mark.asyncio
async def test_patch_notification_subscription_returns_404_when_update_missing(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_notification_subscription = AsyncMock(
        return_value={
            "subscription_id": "sub-1",
            "tenant_id": "tenant-1",
            "source_app": "checkout",
            "event_types": ["order.failed"],
            "channel_id": "email",
            "channel_config": {"email": "alerts@example.com"},
            "template": {},
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.update_notification_subscription = AsyncMock(return_value=None)

    response = await client.patch(
        "/api/v1/notifications/subscriptions/sub-1",
        json={"status": "paused"},
    )

    assert response.status == 404
    assert await response.json() == {"error": "Subscription not found"}


@pytest.mark.asyncio
async def test_delete_notification_subscription_returns_404_when_missing(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    tenant_manager.delete_notification_subscription = AsyncMock(return_value=False)

    response = await client.delete("/api/v1/notifications/subscriptions/sub-missing")

    assert response.status == 404
    assert await response.json() == {"error": "Subscription not found"}


@pytest.mark.asyncio
async def test_delete_notification_subscription_returns_ok_when_deleted(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, _, _ = notifications_routes_client
    tenant_manager.delete_notification_subscription = AsyncMock(return_value=True)

    response = await client.delete("/api/v1/notifications/subscriptions/sub-1")

    assert response.status == 200
    assert await response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_publish_notification_event_schedules_deliveries(
    notifications_routes_client,
) -> None:
    client, tenant_manager, _, announcement_repository, announcement_policy_engine = (
        notifications_routes_client
    )
    tenant_manager.match_notification_subscriptions = AsyncMock(
        return_value=[
            {
                "subscription_id": "sub-1",
                "tenant_id": "tenant-1",
                "source_app": "checkout",
                "event_types": ["order.failed"],
                "channel_id": "webhook",
                "channel_config": {"webhook_url": "https://example.com/hook"},
                "template": {"title": "Alert: {title}"},
                "status": "active",
            }
        ]
    )
    announcement_policy_engine.evaluate_event = AsyncMock(
        return_value=type(
            "Decision",
            (),
            {
                "status": "scheduled",
                "delivery_mode": "immediate",
                "severity": None,
                "scheduled_for": datetime(2026, 3, 10, 0, 0, tzinfo=UTC),
                "reason_code": "recipient_channel_immediate_default",
            },
        )()
    )
    announcement_repository.create_event = AsyncMock(
        return_value=type(
            "Receipt",
            (),
            {
                "status": "accepted",
                "event_id": "evt-1",
                "reason_code": "accepted_new",
            },
        )()
    )
    announcement_repository.create_delivery = AsyncMock()

    response = await client.post(
        "/api/v1/notifications/events",
        json={
            "source_app": "checkout",
            "event_type": "order.failed",
            "severity": "high",
            "title": "Payment failed",
            "body": "Card charge was declined.",
            "payload": {"order_id": "ord-1"},
            "dedupe_key": "order:ord-1",
        },
    )

    assert response.status == 202
    body = await response.json()
    assert body["matched_subscriptions"] == 1
    assert body["deliveries"][0]["receipt"]["event_id"] == "evt-1"
    announcement_repository.create_delivery.assert_awaited_once_with(
        event_id="evt-1",
        channel="webhook",
        scheduled_for=datetime(2026, 3, 10, 0, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_publish_notification_event_rejects_invalid_json(
    notifications_routes_client,
) -> None:
    client, _, _, _, _ = notifications_routes_client

    response = await client.post(
        "/api/v1/notifications/events",
        data="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert await response.json() == {"error": "Invalid JSON body"}


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (
            {"event_type": "order.failed", "title": "Alert", "body": "Body"},
            "source_app is required",
        ),
        ({"source_app": "checkout", "title": "Alert", "body": "Body"}, "event_type is required"),
        (
            {"source_app": "checkout", "event_type": "order.failed", "body": "Body"},
            "title is required",
        ),
        (
            {"source_app": "checkout", "event_type": "order.failed", "title": "Alert"},
            "body is required",
        ),
    ],
)
@pytest.mark.asyncio
async def test_publish_notification_event_requires_fields(
    notifications_routes_client,
    payload: dict[str, str],
    error: str,
) -> None:
    client, _, _, _, _ = notifications_routes_client

    response = await client.post("/api/v1/notifications/events", json=payload)

    assert response.status == 400
    assert await response.json() == {"error": error}


@pytest.mark.asyncio
async def test_publish_notification_event_rejects_invalid_occurred_at(
    notifications_routes_client,
) -> None:
    client, _, _, _, _ = notifications_routes_client

    response = await client.post(
        "/api/v1/notifications/events",
        json={
            "source_app": "checkout",
            "event_type": "order.failed",
            "title": "Alert",
            "body": "Body",
            "occurred_at": "not-a-timestamp",
        },
    )

    assert response.status == 400
    assert await response.json() == {"error": "Invalid occurred_at timestamp"}
