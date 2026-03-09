"""Sandbox profile and rule management routes for the tenant public API."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from zetherion_ai.api.conversation_runtime import TenantConversationRuntime
from zetherion_ai.api.test_runtime import TenantSandboxRuntime


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "hex"):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _tenant_id(request: web.Request) -> str:
    return str(request["tenant"]["tenant_id"])


def _sandbox_runtime(request: web.Request) -> TenantSandboxRuntime:
    runtime = request.app.get("tenant_sandbox_runtime")
    if isinstance(runtime, TenantSandboxRuntime):
        return runtime
    runtime = TenantSandboxRuntime(tenant_manager=request.app["tenant_manager"])
    request.app["tenant_sandbox_runtime"] = runtime
    return runtime


def _conversation_runtime(request: web.Request) -> TenantConversationRuntime:
    runtime = request.app.get("tenant_conversation_runtime")
    if isinstance(runtime, TenantConversationRuntime):
        return runtime
    runtime = TenantConversationRuntime(tenant_manager=request.app["tenant_manager"])
    request.app["tenant_conversation_runtime"] = runtime
    return runtime


async def handle_list_test_profiles(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    rows = await tenant_manager.list_test_profiles(_tenant_id(request))
    return web.json_response({"profiles": [_serialise(row) for row in rows], "count": len(rows)})


async def handle_create_test_profile(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    try:
        raw = await request.json()
    except Exception:
        raw = {}

    name = str(raw.get("name", "")).strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    profile = await tenant_manager.create_test_profile(
        _tenant_id(request),
        name=name,
        description=str(raw.get("description", "")).strip() or None,
        is_default=bool(raw.get("is_default", False)),
    )
    return web.json_response(_serialise(profile), status=201)


async def handle_get_test_profile(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    profile = await tenant_manager.get_test_profile(_tenant_id(request), request.match_info["profile_id"])
    if profile is None:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)
    return web.json_response(_serialise(profile))


async def handle_patch_test_profile(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    try:
        raw = await request.json()
    except Exception:
        raw = {}

    updated = await tenant_manager.update_test_profile(
        _tenant_id(request),
        request.match_info["profile_id"],
        name=str(raw["name"]).strip() if "name" in raw and raw["name"] is not None else None,
        description=(
            str(raw["description"]).strip()
            if "description" in raw and raw["description"] is not None
            else None
        ),
        is_default=bool(raw["is_default"]) if "is_default" in raw else None,
        is_active=bool(raw["is_active"]) if "is_active" in raw else None,
    )
    if updated is None:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)
    return web.json_response(_serialise(updated))


async def handle_delete_test_profile(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    deleted = await tenant_manager.delete_test_profile(
        _tenant_id(request),
        request.match_info["profile_id"],
    )
    if not deleted:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)
    return web.json_response({"ok": True})


async def handle_list_test_rules(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    profile_id = request.match_info["profile_id"]
    profile = await tenant_manager.get_test_profile(_tenant_id(request), profile_id)
    if profile is None:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)

    rows = await tenant_manager.list_test_rules(_tenant_id(request), profile_id)
    return web.json_response({"rules": [_serialise(row) for row in rows], "count": len(rows)})


async def handle_create_test_rule(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    profile_id = request.match_info["profile_id"]
    profile = await tenant_manager.get_test_profile(_tenant_id(request), profile_id)
    if profile is None:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)

    try:
        raw = await request.json()
    except Exception:
        raw = {}

    route_pattern = str(raw.get("route_pattern", "")).strip()
    if not route_pattern:
        return web.json_response({"error": "route_pattern is required"}, status=400)

    match = raw.get("match")
    response = raw.get("response")
    if match is not None and not isinstance(match, dict):
        return web.json_response({"error": "match must be an object"}, status=400)
    if response is not None and not isinstance(response, dict):
        return web.json_response({"error": "response must be an object"}, status=400)

    rule = await tenant_manager.create_test_rule(
        _tenant_id(request),
        profile_id,
        priority=int(raw.get("priority", 100)),
        method=str(raw.get("method", "POST")),
        route_pattern=route_pattern,
        enabled=bool(raw.get("enabled", True)),
        match=match,
        response=response,
        latency_ms=int(raw.get("latency_ms", 0)),
    )
    return web.json_response(_serialise(rule), status=201)


async def handle_patch_test_rule(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    profile_id = request.match_info["profile_id"]
    try:
        raw = await request.json()
    except Exception:
        raw = {}

    if "match" in raw and raw["match"] is not None and not isinstance(raw["match"], dict):
        return web.json_response({"error": "match must be an object"}, status=400)
    if "response" in raw and raw["response"] is not None and not isinstance(raw["response"], dict):
        return web.json_response({"error": "response must be an object"}, status=400)

    updated = await tenant_manager.update_test_rule(
        _tenant_id(request),
        profile_id,
        request.match_info["rule_id"],
        priority=int(raw["priority"]) if "priority" in raw and raw["priority"] is not None else None,
        method=str(raw["method"]) if "method" in raw and raw["method"] is not None else None,
        route_pattern=(
            str(raw["route_pattern"]).strip()
            if "route_pattern" in raw and raw["route_pattern"] is not None
            else None
        ),
        enabled=bool(raw["enabled"]) if "enabled" in raw else None,
        match=raw.get("match") if "match" in raw else None,
        response=raw.get("response") if "response" in raw else None,
        latency_ms=(
            int(raw["latency_ms"]) if "latency_ms" in raw and raw["latency_ms"] is not None else None
        ),
    )
    if updated is None:
        return web.json_response({"error": "Sandbox rule not found"}, status=404)
    return web.json_response(_serialise(updated))


async def handle_delete_test_rule(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    deleted = await tenant_manager.delete_test_rule(
        _tenant_id(request),
        request.match_info["profile_id"],
        request.match_info["rule_id"],
    )
    if not deleted:
        return web.json_response({"error": "Sandbox rule not found"}, status=404)
    return web.json_response({"ok": True})


async def handle_preview_test_profile(request: web.Request) -> web.Response:
    tenant_manager = request.app["tenant_manager"]
    tenant_id = _tenant_id(request)
    profile_id = request.match_info["profile_id"]
    profile = await tenant_manager.get_test_profile(tenant_id, profile_id)
    if profile is None:
        return web.json_response({"error": "Sandbox profile not found"}, status=404)

    try:
        raw = await request.json()
    except Exception:
        raw = {}

    route_path = str(raw.get("route", "/api/v1/chat")).strip() or "/api/v1/chat"
    method = str(raw.get("method", "POST")).strip() or "POST"
    body = raw.get("body")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    session = raw.get("session")
    if session is None:
        session = {}
    if not isinstance(session, dict):
        return web.json_response({"error": "session must be an object"}, status=400)
    history = raw.get("history")
    if history is None:
        history = []
    if not isinstance(history, list):
        return web.json_response({"error": "history must be an array"}, status=400)

    context = await _conversation_runtime(request).build_context(
        tenant_id=tenant_id,
        session=session,
        history=[
            {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in history
            if isinstance(item, dict)
        ],
    )
    preview = await _sandbox_runtime(request).preview(
        tenant_id=tenant_id,
        profile_id=profile_id,
        method=method,
        route_path=route_path,
        body=body,
        session=session,
        context=context,
        history=[
            {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in history
            if isinstance(item, dict)
        ],
    )
    return web.json_response(preview)
