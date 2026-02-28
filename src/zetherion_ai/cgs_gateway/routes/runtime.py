"""Runtime CGS routes proxied to Zetherion public API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from aiohttp import web
from pydantic import ValidationError

from zetherion_ai.cgs_gateway.errors import GatewayError, success_response
from zetherion_ai.cgs_gateway.models import CreateConversationRequest, MessageRequest
from zetherion_ai.cgs_gateway.routes._utils import (
    canonical_upstream_headers,
    enforce_tenant_access,
    fingerprint_payload,
    json_object,
    principal,
    request_id,
    resolve_active_mapping,
)
from zetherion_ai.cgs_gateway.storage import CGSGatewayStorage


def _map_upstream_error(status: int, payload: Any) -> GatewayError:
    details = payload if isinstance(payload, dict) else {"upstream": str(payload)}
    if status == 401:
        return GatewayError(
            code="AI_UPSTREAM_401",
            message="Upstream authentication failed",
            status=401,
            details=details,
        )
    if status == 403:
        return GatewayError(
            code="AI_UPSTREAM_403",
            message="Upstream request forbidden",
            status=403,
            details=details,
        )
    if status == 404:
        return GatewayError(
            code="AI_UPSTREAM_404",
            message="Upstream resource not found",
            status=404,
            details=details,
        )
    if status == 409:
        return GatewayError(
            code="AI_UPSTREAM_409",
            message="Upstream conflict",
            status=409,
            details=details,
        )
    if status == 429:
        return GatewayError(
            code="AI_UPSTREAM_429",
            message="Upstream rate limited",
            status=429,
            details=details,
        )
    if status >= 500:
        return GatewayError(
            code="AI_UPSTREAM_5XX",
            message="Upstream service unavailable",
            status=503,
            details=details,
        )
    return GatewayError(
        code="AI_UPSTREAM_ERROR",
        message="Upstream request failed",
        status=502,
        details=details,
    )


async def _idempotency_check(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    payload: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Check for cached idempotency record and return existing response when present."""
    storage = request.app["cgs_storage"]
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return None, None, None

    endpoint = request.path
    method = request.method.upper()
    fp = fingerprint_payload(payload)

    record = await storage.get_idempotency_record(
        cgs_tenant_id=cgs_tenant_id,
        endpoint=endpoint,
        method=method,
        idempotency_key=key,
    )
    if record is None:
        return key, fp, None

    if str(record.get("request_fingerprint", "")) != fp:
        raise GatewayError(
            code="AI_IDEMPOTENCY_CONFLICT",
            message="Idempotency key already used with different payload",
            status=409,
        )

    cached_body = record.get("response_body")
    if not isinstance(cached_body, dict):
        cached_body = {"request_id": request_id(request), "data": None, "error": None}

    return (
        key,
        fp,
        {
            "status": int(record.get("response_status", 200)),
            "body": cached_body,
        },
    )


async def _save_idempotency(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    idempotency_key: str | None,
    request_fingerprint: str | None,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    if not idempotency_key or not request_fingerprint:
        return
    storage = request.app["cgs_storage"]
    await storage.save_idempotency_record(
        cgs_tenant_id=cgs_tenant_id,
        endpoint=request.path,
        method=request.method.upper(),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        response_status=response_status,
        response_body=response_body,
    )


async def handle_create_conversation(request: web.Request) -> web.Response:
    """POST /service/ai/v1/conversations."""
    rid = request_id(request)
    principal_obj = principal(request)
    raw = await json_object(request)
    try:
        payload = CreateConversationRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    enforce_tenant_access(principal_obj, payload.tenant_id)

    storage = request.app["cgs_storage"]
    public_client = request.app["cgs_public_client"]

    mapping = await resolve_active_mapping(storage, payload.tenant_id)

    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=payload.tenant_id,
        payload=payload.model_dump(mode="json"),
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await public_client.request_json(
        "POST",
        "/api/v1/sessions",
        headers=upstream_headers,
        json_body={
            "external_user_id": payload.external_user_id,
            "metadata": {
                **(payload.metadata or {}),
                "app_user_id": payload.app_user_id,
                "source": "cgs-gateway",
            },
        },
    )
    if status >= 400:
        raise _map_upstream_error(status, upstream)
    if not isinstance(upstream, dict):
        raise GatewayError(
            code="AI_UPSTREAM_ERROR",
            message="Invalid upstream response",
            status=502,
        )

    session_id = str(upstream.get("session_id", ""))
    session_token = str(upstream.get("session_token", ""))
    if not session_id or not session_token:
        raise GatewayError(
            code="AI_UPSTREAM_ERROR",
            message="Upstream response missing session fields",
            status=502,
        )

    conversation = await storage.create_conversation(
        cgs_tenant_id=payload.tenant_id,
        zetherion_session_id=session_id,
        zetherion_session_token=session_token,
        app_user_id=payload.app_user_id,
        external_user_id=payload.external_user_id,
        metadata=payload.metadata,
    )

    response_data = {
        "conversation_id": conversation["conversation_id"],
        "tenant_id": payload.tenant_id,
        "session_id": session_id,
        "created_at": upstream.get("created_at") or conversation.get("created_at"),
        "expires_at": upstream.get("expires_at"),
    }
    envelope = {
        "request_id": rid,
        "data": response_data,
        "error": None,
    }
    await _save_idempotency(
        request,
        cgs_tenant_id=payload.tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=201,
        response_body=envelope,
    )

    return web.json_response(envelope, status=201)


async def _load_conversation_for_access(
    request: web.Request, conversation_id: str
) -> dict[str, Any]:
    storage = cast(CGSGatewayStorage, request.app["cgs_storage"])
    principal_obj = principal(request)

    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise GatewayError(
            code="AI_CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status=404,
        )

    enforce_tenant_access(principal_obj, str(conversation["cgs_tenant_id"]))
    if not bool(conversation.get("is_active", True)):
        raise GatewayError(code="AI_TENANT_INACTIVE", message="Tenant is inactive", status=403)

    return conversation


async def handle_get_conversation(request: web.Request) -> web.Response:
    """GET /service/ai/v1/conversations/{conversation_id}."""
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]

    conversation = await _load_conversation_for_access(request, conversation_id)
    public_client = request.app["cgs_public_client"]

    status, upstream, _ = await public_client.request_json(
        "GET",
        f"/api/v1/sessions/{conversation['zetherion_session_id']}",
        headers=canonical_upstream_headers(
            request_id_value=rid,
            api_key=str(conversation["zetherion_api_key"]),
        ),
    )
    if status >= 400:
        raise _map_upstream_error(status, upstream)

    if isinstance(upstream, dict):
        data = {
            "conversation_id": conversation_id,
            "tenant_id": conversation["cgs_tenant_id"],
            "session_id": str(conversation["zetherion_session_id"]),
            "created_at": upstream.get("created_at") or conversation.get("created_at"),
            "expires_at": upstream.get("expires_at"),
            "is_closed": bool(conversation.get("is_closed", False)),
        }
    else:
        data = {
            "conversation_id": conversation_id,
            "tenant_id": conversation["cgs_tenant_id"],
            "session_id": str(conversation["zetherion_session_id"]),
            "is_closed": bool(conversation.get("is_closed", False)),
        }
    return success_response(rid, data)


async def handle_delete_conversation(request: web.Request) -> web.Response:
    """DELETE /service/ai/v1/conversations/{conversation_id}."""
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]

    conversation = await _load_conversation_for_access(request, conversation_id)
    public_client = request.app["cgs_public_client"]
    storage = request.app["cgs_storage"]

    payload = {"conversation_id": conversation_id}
    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=str(conversation["cgs_tenant_id"]),
        payload=payload,
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    status, upstream, _ = await public_client.request_json(
        "DELETE",
        f"/api/v1/sessions/{conversation['zetherion_session_id']}",
        headers=canonical_upstream_headers(
            request_id_value=rid,
            api_key=str(conversation["zetherion_api_key"]),
        ),
    )
    if status >= 400 and status != 404:
        raise _map_upstream_error(status, upstream)

    await storage.close_conversation(conversation_id)
    envelope = {
        "request_id": rid,
        "data": {"conversation_id": conversation_id, "closed": True},
        "error": None,
    }
    await _save_idempotency(
        request,
        cgs_tenant_id=str(conversation["cgs_tenant_id"]),
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=200,
        response_body=envelope,
    )
    return web.json_response(envelope)


async def _conversation_message_payload(request: web.Request) -> MessageRequest:
    raw = await json_object(request)
    try:
        return MessageRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc


async def handle_post_message(request: web.Request) -> web.Response:
    """POST /service/ai/v1/conversations/{conversation_id}/messages."""
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]

    conversation = await _load_conversation_for_access(request, conversation_id)
    message = await _conversation_message_payload(request)
    public_client = request.app["cgs_public_client"]

    payload = message.model_dump(mode="json")
    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=str(conversation["cgs_tenant_id"]),
        payload=payload,
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    status, upstream, _ = await public_client.request_json(
        "POST",
        "/api/v1/chat",
        headers=canonical_upstream_headers(
            request_id_value=rid,
            bearer_token=str(conversation["zetherion_session_token"]),
        ),
        json_body=payload,
    )
    if status >= 400:
        raise _map_upstream_error(status, upstream)

    response_data = upstream if isinstance(upstream, dict) else {"content": str(upstream)}
    envelope = {
        "request_id": rid,
        "data": response_data,
        "error": None,
    }
    await _save_idempotency(
        request,
        cgs_tenant_id=str(conversation["cgs_tenant_id"]),
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=200,
        response_body=envelope,
    )
    return web.json_response(envelope)


async def handle_post_message_stream(request: web.Request) -> web.StreamResponse:
    """POST /service/ai/v1/conversations/{conversation_id}/messages/stream."""
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]

    conversation = await _load_conversation_for_access(request, conversation_id)
    message = await _conversation_message_payload(request)

    public_client = request.app["cgs_public_client"]
    upstream_response = await public_client.open_stream(
        "POST",
        "/api/v1/chat/stream",
        headers=canonical_upstream_headers(
            request_id_value=rid,
            bearer_token=str(conversation["zetherion_session_token"]),
            extra={"Accept": "text/event-stream"},
        ),
        json_body=message.model_dump(mode="json"),
    )

    if upstream_response.status >= 400:
        try:
            payload = await upstream_response.json()
        except Exception:
            payload = await upstream_response.text()
        await upstream_response.release()
        raise _map_upstream_error(upstream_response.status, payload)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    try:
        async for chunk in upstream_response.content.iter_chunked(1024):
            await response.write(chunk)
    finally:
        await upstream_response.release()

    await response.write_eof()
    return response


async def handle_get_messages(request: web.Request) -> web.Response:
    """GET /service/ai/v1/conversations/{conversation_id}/messages."""
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]
    conversation = await _load_conversation_for_access(request, conversation_id)
    public_client = request.app["cgs_public_client"]

    params: dict[str, Any] = {}
    if "limit" in request.query:
        params["limit"] = request.query["limit"]
    if "before" in request.query:
        params["before"] = request.query["before"]

    status, upstream, _ = await public_client.request_json(
        "GET",
        "/api/v1/chat/history",
        headers=canonical_upstream_headers(
            request_id_value=rid,
            bearer_token=str(conversation["zetherion_session_token"]),
        ),
        params=params,
    )
    if status >= 400:
        raise _map_upstream_error(status, upstream)

    data = upstream if isinstance(upstream, dict) else {"messages": []}
    return success_response(rid, data)


async def _forward_conversation_json(
    request: web.Request,
    *,
    upstream_method: str,
    upstream_path: str,
    include_query: bool = False,
    require_body: bool = False,
) -> web.Response:
    rid = request_id(request)
    conversation_id = request.match_info["conversation_id"]
    conversation = await _load_conversation_for_access(request, conversation_id)

    payload = await json_object(request, required=require_body)
    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=str(conversation["cgs_tenant_id"]),
        payload=payload,
    )
    if cached is not None and upstream_method.upper() != "GET":
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    params = dict(request.query) if include_query else None
    public_client = request.app["cgs_public_client"]
    status, upstream, _ = await public_client.request_json(
        upstream_method,
        upstream_path,
        headers=canonical_upstream_headers(
            request_id_value=rid,
            bearer_token=str(conversation["zetherion_session_token"]),
        ),
        json_body=payload if payload else None,
        params=params,
    )
    if status >= 400:
        raise _map_upstream_error(status, upstream)

    data = upstream if isinstance(upstream, dict) else {"result": upstream}
    envelope = {
        "request_id": rid,
        "data": data,
        "error": None,
    }
    if upstream_method.upper() != "GET":
        await _save_idempotency(
            request,
            cgs_tenant_id=str(conversation["cgs_tenant_id"]),
            idempotency_key=idem_key,
            request_fingerprint=idem_fp,
            response_status=200,
            response_body=envelope,
        )
    return web.json_response(envelope)


async def handle_analytics_events(request: web.Request) -> web.Response:
    return await _forward_conversation_json(
        request,
        upstream_method="POST",
        upstream_path="/api/v1/analytics/events",
        require_body=True,
    )


async def handle_replay_chunks(request: web.Request) -> web.Response:
    return await _forward_conversation_json(
        request,
        upstream_method="POST",
        upstream_path="/api/v1/analytics/replay/chunks",
        require_body=True,
    )


async def handle_get_replay_chunk(request: web.Request) -> web.Response:
    web_session_id = request.match_info["web_session_id"]
    sequence_no = request.match_info["sequence_no"]
    return await _forward_conversation_json(
        request,
        upstream_method="GET",
        upstream_path=f"/api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}",
        include_query=True,
        require_body=False,
    )


async def handle_analytics_end(request: web.Request) -> web.Response:
    return await _forward_conversation_json(
        request,
        upstream_method="POST",
        upstream_path="/api/v1/analytics/sessions/end",
        require_body=False,
    )


async def handle_get_recommendations(request: web.Request) -> web.Response:
    return await _forward_conversation_json(
        request,
        upstream_method="GET",
        upstream_path="/api/v1/analytics/recommendations",
        include_query=True,
    )


async def handle_recommendation_feedback(request: web.Request) -> web.Response:
    recommendation_id = request.match_info["recommendation_id"]
    return await _forward_conversation_json(
        request,
        upstream_method="POST",
        upstream_path=f"/api/v1/analytics/recommendations/{recommendation_id}/feedback",
        require_body=True,
    )


def register_runtime_routes(app: web.Application) -> None:
    """Register all runtime conversation routes."""
    prefix = "/service/ai/v1"

    app.router.add_post(prefix + "/conversations", handle_create_conversation)
    app.router.add_get(prefix + "/conversations/{conversation_id}", handle_get_conversation)
    app.router.add_delete(prefix + "/conversations/{conversation_id}", handle_delete_conversation)

    app.router.add_post(
        prefix + "/conversations/{conversation_id}/messages",
        handle_post_message,
    )
    app.router.add_post(
        prefix + "/conversations/{conversation_id}/messages/stream",
        handle_post_message_stream,
    )
    app.router.add_get(
        prefix + "/conversations/{conversation_id}/messages",
        handle_get_messages,
    )

    app.router.add_post(
        prefix + "/conversations/{conversation_id}/analytics/events",
        handle_analytics_events,
    )
    app.router.add_post(
        prefix + "/conversations/{conversation_id}/analytics/replay/chunks",
        handle_replay_chunks,
    )
    app.router.add_get(
        prefix
        + "/conversations/{conversation_id}/analytics/replay/chunks/{web_session_id}/{sequence_no}",
        handle_get_replay_chunk,
    )
    app.router.add_post(
        prefix + "/conversations/{conversation_id}/analytics/end",
        handle_analytics_end,
    )

    app.router.add_get(
        prefix + "/conversations/{conversation_id}/recommendations",
        handle_get_recommendations,
    )
    app.router.add_post(
        prefix + "/conversations/{conversation_id}/recommendations/{recommendation_id}/feedback",
        handle_recommendation_feedback,
    )


def now_iso() -> str:
    """Return current timestamp in UTC ISO format."""
    return datetime.now(tz=UTC).isoformat()
