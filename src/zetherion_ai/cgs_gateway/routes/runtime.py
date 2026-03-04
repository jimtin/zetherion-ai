"""Runtime CGS routes proxied to Zetherion public API."""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime
from typing import Any, cast

from aiohttp import web
from pydantic import ValidationError

from zetherion_ai.cgs_gateway.errors import GatewayError, map_upstream_error, success_response
from zetherion_ai.cgs_gateway.models import (
    CreateConversationRequest,
    DocumentCompleteUploadRequest,
    DocumentQueryRequest,
    DocumentReindexRequest,
    DocumentUploadRequest,
    MessageRequest,
)
from zetherion_ai.cgs_gateway.routes._utils import (
    canonical_upstream_headers,
    enforce_mutation_rate_limit,
    enforce_tenant_access,
    fingerprint_payload,
    json_object,
    principal,
    request_id,
    resolve_active_mapping,
)
from zetherion_ai.cgs_gateway.storage import CGSGatewayStorage

_ALLOWED_DOCUMENT_STATUSES = {"uploaded", "processing", "indexed", "failed"}
_DOCUMENT_STATUS_ALIASES = {
    "pending": "processing",
    "queued": "processing",
    "indexing": "processing",
    "complete": "indexed",
    "completed": "indexed",
    "ready": "indexed",
    "error": "failed",
}
_PROVIDER_ALIASES = {"claude": "anthropic"}


def _normalize_document_status(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _DOCUMENT_STATUS_ALIASES.get(normalized, normalized)
    if normalized in _ALLOWED_DOCUMENT_STATUSES:
        return normalized
    return "processing"


def _normalize_document_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "status" and isinstance(value, str):
                normalized[key] = _normalize_document_status(value)
            else:
                normalized[key] = _normalize_document_payload(value)
        return normalized
    if isinstance(payload, list):
        return [_normalize_document_payload(item) for item in payload]
    return payload


def _normalize_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    value = provider.strip().lower()
    if not value:
        return None
    return _PROVIDER_ALIASES.get(value, value)


def _allowed_providers(request: web.Request) -> set[str]:
    configured = request.app.get("cgs_rag_allowed_providers")
    if isinstance(configured, set) and configured:
        return {str(item).strip().lower() for item in configured if str(item).strip()}
    return {"groq", "openai", "anthropic"}


def _allowed_models(request: web.Request) -> set[str]:
    configured = request.app.get("cgs_rag_allowed_models")
    if isinstance(configured, set):
        return {str(item).strip() for item in configured if str(item).strip()}
    return set()


def _build_rag_upstream_body(
    request: web.Request,
    payload: DocumentQueryRequest,
) -> dict[str, Any]:
    provider = _normalize_provider(payload.provider)
    model = payload.model.strip() if isinstance(payload.model, str) else None
    allowed_providers = _allowed_providers(request)
    allowed_models = _allowed_models(request)

    if provider is not None and provider not in allowed_providers:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="provider is not allowed",
            status=400,
            details={"allowed_providers": sorted(allowed_providers)},
        )
    if model and allowed_models and model not in allowed_models:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="model is not allowed",
            status=400,
            details={"allowed_models": sorted(allowed_models)},
        )

    upstream_body = payload.model_dump(mode="json", exclude={"tenant_id"})
    if provider is not None:
        upstream_body["provider"] = provider
    if model:
        upstream_body["model"] = model
    return upstream_body


def _normalize_provider_catalog(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    providers = normalized.get("providers")
    if isinstance(providers, list):
        seen: set[str] = set()
        out: list[str] = []
        for provider in providers:
            mapped = _normalize_provider(str(provider))
            if not mapped or mapped in seen:
                continue
            seen.add(mapped)
            out.append(mapped)
        normalized["providers"] = out

    defaults = normalized.get("defaults")
    if isinstance(defaults, dict):
        normalized_defaults: dict[str, Any] = {}
        for key, value in defaults.items():
            mapped = _normalize_provider(str(key))
            if mapped is None:
                continue
            normalized_defaults[mapped] = value
        normalized["defaults"] = normalized_defaults

    return normalized


async def _public_request_json(
    request: web.Request,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    data: Any = None,
    typed_method: str | None = None,
    typed_kwargs: dict[str, Any] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    client = request.app["cgs_public_client"]
    if typed_method:
        candidate = getattr(client, typed_method, None)
        if callable(candidate) and inspect.iscoroutinefunction(candidate):
            kwargs = typed_kwargs or {}
            return await candidate(**kwargs)
    return await client.request_json(
        method,
        path,
        headers=headers,
        json_body=json_body,
        params=params,
        data=data,
    )


async def _public_request_raw(
    request: web.Request,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    typed_method: str | None = None,
    typed_kwargs: dict[str, Any] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    client = request.app["cgs_public_client"]
    if typed_method:
        candidate = getattr(client, typed_method, None)
        if callable(candidate) and inspect.iscoroutinefunction(candidate):
            kwargs = typed_kwargs or {}
            return await candidate(**kwargs)
    return await client.request_raw(
        method,
        path,
        headers=headers,
        params=params,
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
        raise map_upstream_error(status=status, payload=upstream)
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
        raise map_upstream_error(status=status, payload=upstream)

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
        raise map_upstream_error(status=status, payload=upstream)

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
        raise map_upstream_error(status=status, payload=upstream)

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
        raise map_upstream_error(status=upstream_response.status, payload=payload)

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
        raise map_upstream_error(status=status, payload=upstream)

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
        raise map_upstream_error(status=status, payload=upstream)

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


async def _load_mapping_for_tenant_payload(
    request: web.Request,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    principal_obj = principal(request)
    enforce_tenant_access(principal_obj, tenant_id)
    return await resolve_active_mapping(request.app["cgs_storage"], tenant_id)


async def handle_documents_create_upload(request: web.Request) -> web.Response:
    """POST /service/ai/v1/documents/uploads."""
    rid = request_id(request)
    raw = await json_object(request)
    try:
        payload = DocumentUploadRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=payload.tenant_id)
    enforce_mutation_rate_limit(
        request,
        cgs_tenant_id=payload.tenant_id,
        family="documents",
    )

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
    upstream_payload = payload.model_dump(mode="json", exclude={"tenant_id"})
    status, upstream, _ = await _public_request_json(
        request,
        method="POST",
        path="/api/v1/documents/uploads",
        headers=upstream_headers,
        json_body=upstream_payload,
        typed_method="create_document_upload",
        typed_kwargs={
            "headers": upstream_headers,
            "payload": upstream_payload,
        },
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    data = _normalize_document_payload(upstream)
    envelope = {
        "request_id": rid,
        "data": data,
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


async def handle_documents_complete_upload(request: web.Request) -> web.Response:
    """POST /service/ai/v1/documents/uploads/{upload_id}/complete."""
    rid = request_id(request)
    upload_id = request.match_info["upload_id"]
    raw_content_type = request.headers.get("Content-Type", "")
    content_type = raw_content_type.lower()
    if content_type.startswith("multipart/"):
        tenant_id = request.query.get("tenant_id", "").strip()
        if not tenant_id:
            raise GatewayError(
                code="AI_BAD_REQUEST",
                message="tenant_id query parameter is required for multipart upload completion",
                status=400,
            )

        mapping = await _load_mapping_for_tenant_payload(request, tenant_id=tenant_id)
        enforce_mutation_rate_limit(
            request,
            cgs_tenant_id=tenant_id,
            family="documents",
        )
        body = await request.read()
        if not body:
            raise GatewayError(
                code="AI_BAD_REQUEST",
                message="multipart body is required",
                status=400,
            )

        idem_key, idem_fp, cached = await _idempotency_check(
            request,
            cgs_tenant_id=tenant_id,
            payload={
                "upload_id": upload_id,
                "tenant_id": tenant_id,
                "multipart_sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        if cached is not None:
            response = web.json_response(cached["body"], status=cached["status"])
            response.headers["X-Idempotent-Replay"] = "true"
            return response

        upstream_headers = canonical_upstream_headers(
            request_id_value=rid,
            api_key=str(mapping["zetherion_api_key"]),
        )
        if raw_content_type:
            upstream_headers["Content-Type"] = raw_content_type

        status, upstream, _ = await _public_request_json(
            request,
            method="POST",
            path=f"/api/v1/documents/uploads/{upload_id}/complete",
            headers=upstream_headers,
            data=body,
            typed_method="complete_document_upload_multipart",
            typed_kwargs={
                "upload_id": upload_id,
                "headers": upstream_headers,
                "body": body,
            },
        )
        if status >= 400:
            raise map_upstream_error(status=status, payload=upstream)
        data = _normalize_document_payload(upstream)
        envelope = {
            "request_id": rid,
            "data": data,
            "error": None,
        }
        await _save_idempotency(
            request,
            cgs_tenant_id=tenant_id,
            idempotency_key=idem_key,
            request_fingerprint=idem_fp,
            response_status=201,
            response_body=envelope,
        )
        return web.json_response(envelope, status=201)

    raw = await json_object(request)
    try:
        payload = DocumentCompleteUploadRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=payload.tenant_id)
    enforce_mutation_rate_limit(
        request,
        cgs_tenant_id=payload.tenant_id,
        family="documents",
    )

    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=payload.tenant_id,
        payload={"upload_id": upload_id, **payload.model_dump(mode="json")},
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    upstream_body = payload.model_dump(mode="json", exclude={"tenant_id"})

    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="POST",
        path=f"/api/v1/documents/uploads/{upload_id}/complete",
        headers=upstream_headers,
        json_body=upstream_body,
        typed_method="complete_document_upload_json",
        typed_kwargs={
            "upload_id": upload_id,
            "headers": upstream_headers,
            "payload": upstream_body,
        },
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    data = _normalize_document_payload(upstream)
    envelope = {
        "request_id": rid,
        "data": data,
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


async def handle_documents_list(request: web.Request) -> web.Response:
    """GET /service/ai/v1/documents."""
    rid = request_id(request)
    tenant_id = request.query.get("tenant_id", "").strip()
    if not tenant_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="tenant_id query parameter is required",
            status=400,
        )

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=tenant_id)
    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="GET",
        path="/api/v1/documents",
        headers=upstream_headers,
        typed_method="list_documents",
        typed_kwargs={"headers": upstream_headers},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    return success_response(rid, _normalize_document_payload(upstream))


async def handle_documents_get(request: web.Request) -> web.Response:
    """GET /service/ai/v1/documents/{document_id}."""
    rid = request_id(request)
    tenant_id = request.query.get("tenant_id", "").strip()
    if not tenant_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="tenant_id query parameter is required",
            status=400,
        )

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=tenant_id)
    document_id = request.match_info["document_id"]
    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="GET",
        path=f"/api/v1/documents/{document_id}",
        headers=upstream_headers,
        typed_method="get_document",
        typed_kwargs={"document_id": document_id, "headers": upstream_headers},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    return success_response(rid, _normalize_document_payload(upstream))


async def _proxy_document_binary(
    request: web.Request,
    *,
    suffix: str,
) -> web.Response:
    rid = request_id(request)
    tenant_id = request.query.get("tenant_id", "").strip()
    if not tenant_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="tenant_id query parameter is required",
            status=400,
        )

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=tenant_id)
    document_id = request.match_info["document_id"]

    request_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, payload, upstream_headers = await _public_request_raw(
        request,
        method="GET",
        path=f"/api/v1/documents/{document_id}/{suffix}",
        headers=request_headers,
        typed_method="get_document_binary",
        typed_kwargs={
            "document_id": document_id,
            "suffix": suffix,
            "headers": request_headers,
        },
    )
    if status >= 400:
        detail: Any
        try:
            detail = json.loads(payload.decode("utf-8", errors="ignore"))
        except Exception:
            detail = payload.decode("utf-8", errors="ignore")
        raise map_upstream_error(status=status, payload=detail)

    headers = {}
    for key in ("Content-Type", "Content-Disposition", "Cache-Control"):
        if key in upstream_headers:
            headers[key] = upstream_headers[key]
    return web.Response(body=payload, status=200, headers=headers)


async def handle_documents_preview(request: web.Request) -> web.Response:
    """GET /service/ai/v1/documents/{document_id}/preview."""
    return await _proxy_document_binary(request, suffix="preview")


async def handle_documents_download(request: web.Request) -> web.Response:
    """GET /service/ai/v1/documents/{document_id}/download."""
    return await _proxy_document_binary(request, suffix="download")


async def handle_documents_reindex(request: web.Request) -> web.Response:
    """POST /service/ai/v1/documents/{document_id}/index."""
    rid = request_id(request)
    raw = await json_object(request)
    try:
        payload = DocumentReindexRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=payload.tenant_id)
    enforce_mutation_rate_limit(
        request,
        cgs_tenant_id=payload.tenant_id,
        family="documents",
    )
    document_id = request.match_info["document_id"]
    idem_key, idem_fp, cached = await _idempotency_check(
        request,
        cgs_tenant_id=payload.tenant_id,
        payload={"document_id": document_id, **payload.model_dump(mode="json")},
    )
    if cached is not None:
        response = web.json_response(cached["body"], status=cached["status"])
        response.headers["X-Idempotent-Replay"] = "true"
        return response

    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="POST",
        path=f"/api/v1/documents/{document_id}/index",
        headers=upstream_headers,
        typed_method="reindex_document",
        typed_kwargs={"document_id": document_id, "headers": upstream_headers},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    data = _normalize_document_payload(upstream)
    envelope = {
        "request_id": rid,
        "data": data,
        "error": None,
    }
    await _save_idempotency(
        request,
        cgs_tenant_id=payload.tenant_id,
        idempotency_key=idem_key,
        request_fingerprint=idem_fp,
        response_status=200,
        response_body=envelope,
    )
    return web.json_response(envelope)


async def handle_documents_rag_query(request: web.Request) -> web.Response:
    """POST /service/ai/v1/rag/query."""
    rid = request_id(request)
    raw = await json_object(request)
    try:
        payload = DocumentQueryRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=payload.tenant_id)
    upstream_body = _build_rag_upstream_body(request, payload)

    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="POST",
        path="/api/v1/rag/query",
        headers=upstream_headers,
        json_body=upstream_body,
        typed_method="rag_query",
        typed_kwargs={"headers": upstream_headers, "payload": upstream_body},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    return success_response(rid, upstream)


async def handle_model_providers(request: web.Request) -> web.Response:
    """GET /service/ai/v1/models/providers."""
    rid = request_id(request)
    tenant_id = request.query.get("tenant_id", "").strip()
    if not tenant_id:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="tenant_id query parameter is required",
            status=400,
        )

    mapping = await _load_mapping_for_tenant_payload(request, tenant_id=tenant_id)
    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    status, upstream, _ = await _public_request_json(
        request,
        method="GET",
        path="/api/v1/models/providers",
        headers=upstream_headers,
        typed_method="list_model_providers",
        typed_kwargs={"headers": upstream_headers},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)
    return success_response(rid, _normalize_provider_catalog(upstream))


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

    # Tenant-scoped document intelligence endpoints.
    app.router.add_post(prefix + "/documents/uploads", handle_documents_create_upload)
    app.router.add_post(
        prefix + "/documents/uploads/{upload_id}/complete",
        handle_documents_complete_upload,
    )
    app.router.add_get(prefix + "/documents", handle_documents_list)
    app.router.add_get(prefix + "/documents/{document_id}", handle_documents_get)
    app.router.add_get(prefix + "/documents/{document_id}/preview", handle_documents_preview)
    app.router.add_get(prefix + "/documents/{document_id}/download", handle_documents_download)
    app.router.add_post(prefix + "/documents/{document_id}/index", handle_documents_reindex)
    app.router.add_post(prefix + "/rag/query", handle_documents_rag_query)
    app.router.add_get(prefix + "/models/providers", handle_model_providers)


def now_iso() -> str:
    """Return current timestamp in UTC ISO format."""
    return datetime.now(tz=UTC).isoformat()
