"""Session management endpoints for the public API.

All endpoints require X-API-Key authentication (handled by middleware).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from zetherion_ai.api.auth import create_session_token
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.routes.sessions")


def _derive_memory_subject_id(
    *,
    memory_subject_id: str | None,
    external_user_id: str | None,
) -> str | None:
    """Resolve the stable tenant-local subject ID for a session."""
    explicit = (memory_subject_id or "").strip()
    if explicit:
        return explicit
    derived = (external_user_id or "").strip()
    if derived:
        return derived
    return None


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    """Convert datetime fields to ISO strings for JSON."""
    out = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


async def handle_create_session(request: web.Request) -> web.Response:
    """POST /api/v1/sessions — create a new chat session.

    Requires X-API-Key. Returns a session token for the client frontend.
    """
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    jwt_secret = request.app["jwt_secret"]
    execution_mode = str(request.get("execution_mode") or tenant.get("execution_mode") or "live")

    try:
        data = await request.json()
    except Exception:
        data = {}

    external_user_id = data.get("external_user_id")
    memory_subject_id = _derive_memory_subject_id(
        memory_subject_id=data.get("memory_subject_id"),
        external_user_id=external_user_id,
    )
    requested_test_profile_id = data.get("test_profile_id")
    if execution_mode != "test" and requested_test_profile_id:
        return web.json_response(
            {"error": "test_profile_id requires a test-mode API key"},
            status=400,
        )
    resolved_test_profile = None
    if execution_mode == "test":
        resolved_test_profile = await tenant_manager.resolve_test_profile(
            str(tenant["tenant_id"]),
            requested_test_profile_id,
        )
        if requested_test_profile_id and resolved_test_profile is None:
            return web.json_response({"error": "Sandbox profile not found"}, status=404)
    metadata = data.get("metadata", {})

    session = await tenant_manager.create_session(
        tenant_id=str(tenant["tenant_id"]),
        external_user_id=external_user_id,
        memory_subject_id=memory_subject_id,
        execution_mode=execution_mode,
        test_profile_id=(
            str(resolved_test_profile["profile_id"])
            if isinstance(resolved_test_profile, dict)
            else None
        ),
        metadata=metadata,
    )

    token = create_session_token(
        tenant_id=str(session["tenant_id"]),
        session_id=str(session["session_id"]),
        secret=jwt_secret,
        execution_mode=str(session.get("execution_mode") or execution_mode),
    )

    response = _serialise(session)
    response["session_token"] = token

    return web.json_response(response, status=201)


async def handle_get_session(request: web.Request) -> web.Response:
    """GET /api/v1/sessions/{session_id} — get session info.

    Requires X-API-Key. Only returns sessions belonging to the authenticated tenant.
    """
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    session_id = request.match_info["session_id"]

    session = await tenant_manager.get_session(session_id)
    if session is None:
        return web.json_response({"error": "Session not found"}, status=404)

    if str(session["tenant_id"]) != str(tenant["tenant_id"]):
        return web.json_response({"error": "Session not found"}, status=404)

    if (
        str(request.get("execution_mode") or "live") == "test"
        and str(session.get("execution_mode") or "live") != "test"
    ):
        return web.json_response({"error": "Session not found"}, status=404)

    return web.json_response(_serialise(session))


async def handle_delete_session(request: web.Request) -> web.Response:
    """DELETE /api/v1/sessions/{session_id} — delete a session.

    Requires X-API-Key.
    """
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    session_id = request.match_info["session_id"]
    execution_mode = str(request.get("execution_mode") or "live")

    if execution_mode == "test":
        session = await tenant_manager.get_session(session_id)
        if session is None or str(session["tenant_id"]) != str(tenant["tenant_id"]):
            return web.json_response({"error": "Session not found"}, status=404)
        if str(session.get("execution_mode") or "live") != "test":
            return web.json_response({"error": "Session not found"}, status=404)

    deleted = await tenant_manager.delete_session(
        session_id=session_id,
        tenant_id=str(tenant["tenant_id"]),
    )

    if not deleted:
        return web.json_response({"error": "Session not found"}, status=404)

    return web.json_response({"ok": True})
