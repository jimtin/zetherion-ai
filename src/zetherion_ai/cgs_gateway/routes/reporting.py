"""CGS reporting routes proxied to Zetherion public API read endpoints."""

from __future__ import annotations

from aiohttp import web

from zetherion_ai.cgs_gateway.errors import map_upstream_error, success_response
from zetherion_ai.cgs_gateway.routes._utils import (
    canonical_upstream_headers,
    enforce_tenant_access,
    principal,
    request_id,
    resolve_active_mapping,
)


async def _forward_tenant_report(request: web.Request, upstream_path: str) -> web.Response:
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]

    enforce_tenant_access(principal(request), cgs_tenant_id)
    mapping = await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)

    status, upstream, _ = await request.app["cgs_public_client"].request_json(
        "GET",
        upstream_path,
        headers=canonical_upstream_headers(
            request_id_value=rid,
            api_key=str(mapping["zetherion_api_key"]),
        ),
        params=dict(request.query),
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)

    data = upstream if isinstance(upstream, dict) else {"result": upstream}
    return success_response(rid, data)


async def handle_crm_contacts(request: web.Request) -> web.Response:
    """GET /service/ai/v1/tenants/{tenant_id}/crm/contacts."""
    return await _forward_tenant_report(request, "/api/v1/crm/contacts")


async def handle_crm_interactions(request: web.Request) -> web.Response:
    """GET /service/ai/v1/tenants/{tenant_id}/crm/interactions."""
    return await _forward_tenant_report(request, "/api/v1/crm/interactions")


async def handle_analytics_funnel(request: web.Request) -> web.Response:
    """GET /service/ai/v1/tenants/{tenant_id}/analytics/funnel."""
    return await _forward_tenant_report(request, "/api/v1/analytics/funnel")


async def handle_analytics_recommendations(request: web.Request) -> web.Response:
    """GET /service/ai/v1/tenants/{tenant_id}/analytics/recommendations."""
    return await _forward_tenant_report(request, "/api/v1/analytics/recommendations/tenant")


def register_reporting_routes(app: web.Application) -> None:
    """Register tenant reporting routes."""
    prefix = "/service/ai/v1/tenants/{tenant_id}"

    app.router.add_get(prefix + "/crm/contacts", handle_crm_contacts)
    app.router.add_get(prefix + "/crm/interactions", handle_crm_interactions)
    app.router.add_get(prefix + "/analytics/funnel", handle_analytics_funnel)
    app.router.add_get(
        prefix + "/analytics/recommendations",
        handle_analytics_recommendations,
    )
