"""CRM read routes for tenant-scoped public API access."""

from __future__ import annotations

from typing import Any

from aiohttp import web


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


async def handle_get_contacts(request: web.Request) -> web.Response:
    """GET /api/v1/crm/contacts (API-key auth)."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    tenant_id = str(tenant["tenant_id"])

    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    email = request.query.get("email")
    rows = await tenant_manager.list_contacts(tenant_id, limit=limit, email=email)
    return web.json_response({"contacts": [_serialise(r) for r in rows], "count": len(rows)})


async def handle_get_interactions(request: web.Request) -> web.Response:
    """GET /api/v1/crm/interactions (API-key auth)."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    tenant_id = str(tenant["tenant_id"])

    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    rows = await tenant_manager.get_interactions(
        tenant_id,
        contact_id=request.query.get("contact_id"),
        session_id=request.query.get("session_id"),
        interaction_type=request.query.get("interaction_type"),
        limit=limit,
    )
    return web.json_response({"interactions": [_serialise(r) for r in rows], "count": len(rows)})
