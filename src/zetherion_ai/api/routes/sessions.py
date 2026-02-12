"""Session management endpoints for the public API.

All endpoints require X-API-Key authentication (handled by middleware).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from zetherion_ai.api.auth import create_session_token
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.routes.sessions")


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

    try:
        data = await request.json()
    except Exception:
        data = {}

    external_user_id = data.get("external_user_id")
    metadata = data.get("metadata", {})

    session = await tenant_manager.create_session(
        tenant_id=str(tenant["tenant_id"]),
        external_user_id=external_user_id,
        metadata=metadata,
    )

    token = create_session_token(
        tenant_id=str(session["tenant_id"]),
        session_id=str(session["session_id"]),
        secret=jwt_secret,
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

    return web.json_response(_serialise(session))


async def handle_delete_session(request: web.Request) -> web.Response:
    """DELETE /api/v1/sessions/{session_id} — delete a session.

    Requires X-API-Key.
    """
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    session_id = request.match_info["session_id"]

    deleted = await tenant_manager.delete_session(
        session_id=session_id,
        tenant_id=str(tenant["tenant_id"]),
    )

    if not deleted:
        return web.json_response({"error": "Session not found"}, status=404)

    return web.json_response({"ok": True})
