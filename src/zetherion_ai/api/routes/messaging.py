"""Tenant messaging routes for the public upstream API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from aiohttp import web

from zetherion_ai.admin.tenant_admin_manager import AdminActorContext
from zetherion_ai.security.trust_policy import TrustPolicyDecision, TrustPolicyEvaluator

_TRUST_POLICY_EVALUATOR = TrustPolicyEvaluator()


def _tenant_id(request: web.Request) -> str:
    tenant = request.get("tenant")
    if not isinstance(tenant, dict) or "tenant_id" not in tenant:
        raise web.HTTPUnauthorized(reason="Missing tenant context")
    return str(tenant["tenant_id"])


def _manager(request: web.Request) -> Any | None:
    manager = request.app.get("tenant_admin_manager")
    if manager is None:
        return None
    required = (
        "list_messaging_chats",
        "list_messaging_messages",
        "queue_messaging_send",
        "purge_expired_messaging_messages",
    )
    if not all(hasattr(manager, name) for name in required):
        return None
    return manager


def _coerce_bool(value: Any, *, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _coerce_limit(value: Any, *, default: int = 200) -> int:
    if value is None or str(value).strip() == "":
        return default
    limit = int(value)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, 500)


def _policy_evaluator(request: web.Request) -> Any:
    evaluator = request.app.get("trust_policy_evaluator")
    if evaluator is not None and hasattr(evaluator, "evaluate"):
        return evaluator
    return _TRUST_POLICY_EVALUATOR


def _policy_response(decision: TrustPolicyDecision) -> web.Response:
    body: dict[str, Any] = {
        "error": decision.message,
        "code": decision.code,
        "details": decision.details,
    }
    if decision.requires_two_person:
        body["requires_two_person"] = True
    return web.json_response(body, status=decision.status)


def _serialise_record(record: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
        else:
            data[key] = value
    return data


def _request_id(request: web.Request) -> str:
    header_value = request.headers.get("X-Request-ID") or request.headers.get("X-Request-Id")
    if isinstance(header_value, str) and header_value.strip():
        return header_value.strip()
    return f"api-msg-{uuid4().hex}"


async def handle_list_messaging_chats(request: web.Request) -> web.Response:
    """GET /api/v1/messaging/chats."""
    manager = _manager(request)
    if manager is None:
        return web.json_response({"error": "Messaging service unavailable"}, status=503)

    tenant_id = _tenant_id(request)
    try:
        include_inactive = _coerce_bool(
            request.query.get("include_inactive"),
            default=True,
            field_name="include_inactive",
        )
        limit = _coerce_limit(request.query.get("limit"), default=200)
        provider = request.query.get("provider", "whatsapp")
        rows = await manager.list_messaging_chats(
            tenant_id=tenant_id,
            provider=provider,
            include_inactive=include_inactive,
            limit=limit,
        )
        chats = [_serialise_record(dict(row)) for row in rows]
        return web.json_response({"chats": chats, "count": len(chats)})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def handle_list_messaging_messages(request: web.Request) -> web.Response:
    """GET /api/v1/messaging/messages."""
    manager = _manager(request)
    if manager is None:
        return web.json_response({"error": "Messaging service unavailable"}, status=503)

    tenant_id = _tenant_id(request)
    chat_id = str(request.query.get("chat_id") or "").strip()
    if not chat_id:
        return web.json_response({"error": "Missing chat_id query parameter"}, status=400)

    try:
        decision: TrustPolicyDecision = _policy_evaluator(request).evaluate(
            tenant_id=tenant_id,
            action="messaging.read",
            context={
                "method": "GET",
                "subpath": "/api/v1/messaging/messages",
                "chat_id": chat_id,
            },
        )
        if not decision.allowed:
            return _policy_response(decision)

        await manager.purge_expired_messaging_messages(tenant_id=tenant_id, limit=1000)
        limit = _coerce_limit(request.query.get("limit"), default=200)
        rows = await manager.list_messaging_messages(
            tenant_id=tenant_id,
            provider=request.query.get("provider", "whatsapp"),
            chat_id=chat_id,
            direction=request.query.get("direction"),
            limit=limit,
            include_expired=False,
        )
        messages = [_serialise_record(dict(row)) for row in rows]
        return web.json_response({"messages": messages, "count": len(messages)})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def handle_send_messaging_message(request: web.Request) -> web.Response:
    """POST /api/v1/messaging/messages/{chat_id}/send."""
    manager = _manager(request)
    if manager is None:
        return web.json_response({"error": "Messaging service unavailable"}, status=503)

    tenant_id = _tenant_id(request)
    chat_id = request.match_info["chat_id"]

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    try:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object when provided")
        explicitly_elevated = _coerce_bool(
            payload.get("explicitly_elevated"),
            default=False,
            field_name="explicitly_elevated",
        )

        decision: TrustPolicyDecision = _policy_evaluator(request).evaluate(
            tenant_id=tenant_id,
            action="messaging.send",
            context={
                "method": "POST",
                "subpath": f"/api/v1/messaging/messages/{chat_id}/send",
                "chat_id": chat_id,
                "explicitly_elevated": explicitly_elevated,
            },
        )
        if decision.approval_required or not decision.allowed:
            return _policy_response(decision)

        change_ticket_raw = payload.get("change_ticket_id")
        change_ticket_id = None
        if isinstance(change_ticket_raw, str) and change_ticket_raw.strip():
            change_ticket_id = change_ticket_raw.strip()

        actor = AdminActorContext(
            actor_sub=f"tenant_api:{tenant_id}",
            actor_roles=("tenant-api",),
            request_id=_request_id(request),
            timestamp=datetime.now(UTC),
            nonce=uuid4().hex,
            actor_email=None,
            change_ticket_id=change_ticket_id,
        )

        queued = await manager.queue_messaging_send(
            tenant_id=tenant_id,
            provider=str(payload.get("provider") or "whatsapp"),
            chat_id=chat_id,
            body_text=text,
            metadata=metadata,
            actor=actor,
        )
        return web.json_response(
            {
                "ok": True,
                "queued_action": _serialise_record(dict(queued["action"])),
                "message": _serialise_record(dict(queued["message"])),
            },
            status=202,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
