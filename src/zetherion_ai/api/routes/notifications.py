"""Tenant notification API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from zetherion_ai.announcements import AnnouncementPolicyEngine, AnnouncementRepository
from zetherion_ai.api.notification_service import TenantNotificationService


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _get_notification_service(request: web.Request) -> TenantNotificationService:
    service = request.app.get("tenant_notification_service")
    if isinstance(service, TenantNotificationService):
        return service

    repository = request.app.get("announcement_repository")
    policy_engine = request.app.get("announcement_policy_engine")
    channel_registry = request.app.get("announcement_channel_registry")
    if not isinstance(repository, AnnouncementRepository):
        raise RuntimeError("Announcement repository is not configured")
    if not isinstance(policy_engine, AnnouncementPolicyEngine):
        raise RuntimeError("Announcement policy engine is not configured")
    if channel_registry is None:
        raise RuntimeError("Announcement channel registry is not configured")

    service = TenantNotificationService(
        tenant_manager=request.app["tenant_manager"],
        announcement_repository=repository,
        announcement_policy_engine=policy_engine,
        channel_registry=channel_registry,
        tenant_admin_manager=request.app.get("tenant_admin_manager"),
    )
    request.app["tenant_notification_service"] = service
    return service


def _parse_occurred_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("Invalid occurred_at timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_subscription_status(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in {"active", "paused"}:
        raise ValueError("status must be active or paused")
    return normalized


async def handle_list_notification_channels(request: web.Request) -> web.Response:
    """GET /api/v1/notifications/channels."""
    tenant_id = str(request["tenant"]["tenant_id"])
    channels = await _get_notification_service(request).list_channels(tenant_id)
    return web.json_response({"channels": channels, "count": len(channels)})


async def handle_list_notification_subscriptions(request: web.Request) -> web.Response:
    """GET /api/v1/notifications/subscriptions."""
    tenant_id = str(request["tenant"]["tenant_id"])
    subscriptions = await request.app["tenant_manager"].list_notification_subscriptions(tenant_id)
    return web.json_response(
        {
            "subscriptions": [_serialise(item) for item in subscriptions],
            "count": len(subscriptions),
        }
    )


async def handle_create_notification_subscription(request: web.Request) -> web.Response:
    """POST /api/v1/notifications/subscriptions."""
    tenant_id = str(request["tenant"]["tenant_id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    service = _get_notification_service(request)
    event_types = data.get("event_types") if isinstance(data.get("event_types"), list) else []
    channel_config = (
        data.get("channel_config") if isinstance(data.get("channel_config"), dict) else {}
    )
    try:
        status = _parse_subscription_status(data.get("status")) or "active"
        validated = service.validate_subscription(
            channel_id=data.get("channel_id"),
            event_types=event_types,
            channel_config=channel_config,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    subscription = await request.app["tenant_manager"].create_notification_subscription(
        tenant_id,
        source_app=(
            (str(data.get("source_app")).strip() or None) if data.get("source_app") else None
        ),
        event_types=validated["event_types"],
        channel_id=validated["channel_id"],
        channel_config=validated["channel_config"],
        template=data.get("template") if isinstance(data.get("template"), dict) else {},
        status=status,
    )
    return web.json_response(_serialise(subscription), status=201)


async def handle_patch_notification_subscription(request: web.Request) -> web.Response:
    """PATCH /api/v1/notifications/subscriptions/{subscription_id}."""
    tenant_id = str(request["tenant"]["tenant_id"])
    subscription_id = request.match_info["subscription_id"]
    tenant_manager = request.app["tenant_manager"]
    existing = await tenant_manager.get_notification_subscription(tenant_id, subscription_id)
    if existing is None:
        return web.json_response({"error": "Subscription not found"}, status=404)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    event_types = existing["event_types"]
    if "event_types" in data:
        if not isinstance(data.get("event_types"), list):
            return web.json_response({"error": "event_types must be a list"}, status=400)
        event_types = data["event_types"]
    channel_config = existing["channel_config"]
    if "channel_config" in data:
        if not isinstance(data.get("channel_config"), dict):
            return web.json_response({"error": "channel_config must be an object"}, status=400)
        channel_config = data["channel_config"]

    service = _get_notification_service(request)
    try:
        status = _parse_subscription_status(data.get("status")) if "status" in data else None
        validated = service.validate_subscription(
            channel_id=existing["channel_id"],
            event_types=event_types,
            channel_config=channel_config,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    updated = await tenant_manager.update_notification_subscription(
        tenant_id,
        subscription_id,
        source_app=(str(data.get("source_app")).strip() or None if "source_app" in data else None),
        event_types=validated["event_types"] if "event_types" in data else None,
        channel_config=validated["channel_config"] if "channel_config" in data else None,
        template=data.get("template") if isinstance(data.get("template"), dict) else None,
        status=status,
    )
    if updated is None:
        return web.json_response({"error": "Subscription not found"}, status=404)
    return web.json_response(_serialise(updated))


async def handle_delete_notification_subscription(request: web.Request) -> web.Response:
    """DELETE /api/v1/notifications/subscriptions/{subscription_id}."""
    tenant_id = str(request["tenant"]["tenant_id"])
    deleted = await request.app["tenant_manager"].delete_notification_subscription(
        tenant_id,
        request.match_info["subscription_id"],
    )
    if not deleted:
        return web.json_response({"error": "Subscription not found"}, status=404)
    return web.json_response({"ok": True})


async def handle_publish_notification_event(request: web.Request) -> web.Response:
    """POST /api/v1/notifications/events."""
    tenant_id = str(request["tenant"]["tenant_id"])
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    source_app = str(data.get("source_app") or "").strip()
    event_type = str(data.get("event_type") or "").strip()
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not source_app:
        return web.json_response({"error": "source_app is required"}, status=400)
    if not event_type:
        return web.json_response({"error": "event_type is required"}, status=400)
    if not title:
        return web.json_response({"error": "title is required"}, status=400)
    if not body:
        return web.json_response({"error": "body is required"}, status=400)
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    try:
        occurred_at = _parse_occurred_at(data.get("occurred_at"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    result = await _get_notification_service(request).publish_event(
        tenant_id=tenant_id,
        source_app=source_app,
        event_type=event_type,
        severity=str(data.get("severity") or "normal"),
        title=title,
        body=body,
        payload=payload,
        occurred_at=occurred_at,
        dedupe_key=(
            (str(data.get("dedupe_key")).strip() or None) if data.get("dedupe_key") else None
        ),
    )
    return web.json_response(result, status=202)
