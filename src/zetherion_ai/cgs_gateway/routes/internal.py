"""Internal CGS operator routes for tenant lifecycle and release markers."""

from __future__ import annotations

import hmac
import inspect
import re
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from zetherion_ai.cgs_gateway.errors import GatewayError, map_upstream_error, success_response
from zetherion_ai.cgs_gateway.middleware import principal_is_operator
from zetherion_ai.cgs_gateway.models import (
    BlogPublishRequest,
    ConfigureTenantRequest,
    CreateTenantRequest,
    ReleaseMarkerRequest,
)
from zetherion_ai.cgs_gateway.provisioning import CGSTenantProvisioningOrchestrator
from zetherion_ai.cgs_gateway.routes._utils import (
    canonical_upstream_headers,
    enforce_tenant_access,
    fingerprint_payload,
    json_object,
    principal,
    request_id,
    resolve_active_mapping,
)

_BLOG_IDEMPOTENCY_PATTERN = re.compile(r"^blog-[A-Fa-f0-9]{7,64}$")


def _extract_skill_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _ensure_internal_access(request: web.Request) -> None:
    p = principal(request)
    if not principal_is_operator(p):
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Operator scope is required for internal endpoints",
            status=403,
        )


def _ensure_internal_tenant_access(request: web.Request, cgs_tenant_id: str) -> None:
    p = principal(request)
    enforce_tenant_access(p, cgs_tenant_id)
    allowed = p.claims.get("allowed_tenants")
    if isinstance(allowed, list) and allowed:
        normalized = {str(item).strip() for item in allowed if str(item).strip()}
        if cgs_tenant_id not in normalized:
            raise GatewayError(
                code="AI_AUTH_FORBIDDEN",
                message="Operator is not authorized for this tenant",
                status=403,
            )


def _verify_blog_publish_token(request: web.Request) -> None:
    expected_token = str(request.app.get("cgs_blog_publish_token", "")).strip()
    if not expected_token:
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Blog publish adapter is not configured",
            status=403,
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise GatewayError(
            code="AI_AUTH_MISSING",
            message="Missing or invalid Authorization header",
            status=401,
        )
    provided = auth_header[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected_token):
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Invalid blog publish token",
            status=403,
        )


async def handle_internal_list_tenants(request: web.Request) -> web.Response:
    """GET /service/ai/v1/internal/tenants."""
    _ensure_internal_access(request)
    rid = request_id(request)
    storage = request.app["cgs_storage"]
    include_inactive = request.query.get("include_inactive", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    rows = await storage.list_tenant_mappings(active_only=not include_inactive)
    return success_response(rid, {"tenants": rows, "count": len(rows)})


async def handle_internal_create_tenant(request: web.Request) -> web.Response:
    """POST /service/ai/v1/internal/tenants."""
    _ensure_internal_access(request)
    rid = request_id(request)
    raw = await json_object(request)
    try:
        payload = CreateTenantRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    skills_client = request.app["cgs_skills_client"]
    storage = request.app["cgs_storage"]
    orchestrator = CGSTenantProvisioningOrchestrator(
        storage=storage,
        skills_client=skills_client,
        portfolio_storage=request.app.get("owner_portfolio_storage"),
    )

    mapping, api_key, created = await orchestrator.provision_tenant(
        cgs_tenant_id=payload.cgs_tenant_id,
        name=payload.name,
        domain=payload.domain,
        config=payload.config,
        user_id=str(principal(request).sub),
        request_id=rid,
    )

    response_data = {
        "cgs_tenant_id": mapping["cgs_tenant_id"],
        "zetherion_tenant_id": str(mapping["zetherion_tenant_id"]),
        "name": mapping["name"],
        "domain": mapping.get("domain"),
        "api_key": api_key,
        "key_version": mapping["key_version"],
        "isolation_stage": mapping.get("isolation_stage"),
        "provisioning_status": "created" if created else "existing",
    }
    return success_response(rid, response_data, status=201 if created else 200)


async def handle_internal_update_tenant(request: web.Request) -> web.Response:
    """PATCH /service/ai/v1/internal/tenants/{tenant_id}."""
    _ensure_internal_access(request)
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_internal_tenant_access(request, cgs_tenant_id)

    raw = await json_object(request)
    try:
        payload = ConfigureTenantRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)
    orchestrator = CGSTenantProvisioningOrchestrator(
        storage=request.app["cgs_storage"],
        skills_client=request.app["cgs_skills_client"],
        public_client=request.app.get("cgs_public_client"),
        portfolio_storage=request.app.get("owner_portfolio_storage"),
    )
    updated = await orchestrator.reconcile_tenant(
        cgs_tenant_id=cgs_tenant_id,
        user_id=str(principal(request).sub),
        request_id=rid,
        desired_isolation_stage=payload.desired_isolation_stage,
        expected_key_version=payload.expected_key_version,
        owner_portfolio_ready=payload.owner_portfolio_ready,
        name=payload.name,
        domain=payload.domain,
        config=payload.config,
        run_tenant_vector_backfill=payload.run_tenant_vector_backfill,
        derive_owner_portfolio=payload.derive_owner_portfolio,
        cutover_verified=payload.cutover_verified,
        release_marker=(
            payload.release_marker.model_dump(mode="json")
            if payload.release_marker is not None
            else None
        ),
        document_backfill_limit=payload.document_backfill_limit,
    )
    if updated is None:
        raise GatewayError(
            code="AI_TENANT_NOT_FOUND",
            message="Tenant mapping not found",
            status=404,
        )

    owner_portfolio_snapshot = updated.get("owner_portfolio_snapshot")
    snapshot_id = None
    if isinstance(owner_portfolio_snapshot, dict):
        snapshot_id = owner_portfolio_snapshot.get("snapshot_id")

    return success_response(
        rid,
        {
            "cgs_tenant_id": cgs_tenant_id,
            "zetherion_tenant_id": str(updated["zetherion_tenant_id"]),
            "updated": True,
            "isolation_stage": updated.get("isolation_stage"),
            "reconciliation_issues": updated.get("reconciliation_issues", []),
            "migration_receipt_id": updated.get("migration_receipt_id"),
            "migration_status": updated.get("migration_status"),
            "migration_runtime_policy": updated.get("migration_runtime_policy"),
            "tenant_vector_backfill": updated.get("tenant_vector_backfill"),
            "owner_portfolio_snapshot_id": snapshot_id,
            "release_marker": updated.get("release_marker"),
        },
    )


async def handle_internal_deactivate_tenant(request: web.Request) -> web.Response:
    """POST /service/ai/v1/internal/tenants/{tenant_id}/deactivate."""
    _ensure_internal_access(request)
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_internal_tenant_access(request, cgs_tenant_id)

    mapping = await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)

    status, skill_response = await request.app["cgs_skills_client"].handle_intent(
        intent="client_deactivate",
        user_id=str(principal(request).sub),
        message="",
        request_id=rid,
        context={"tenant_id": str(mapping["zetherion_tenant_id"])},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=skill_response, source="skills")

    await request.app["cgs_storage"].deactivate_tenant_mapping(cgs_tenant_id)
    return success_response(rid, {"cgs_tenant_id": cgs_tenant_id, "deactivated": True})


async def handle_internal_rotate_key(request: web.Request) -> web.Response:
    """POST /service/ai/v1/internal/tenants/{tenant_id}/keys/rotate."""
    _ensure_internal_access(request)
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_internal_tenant_access(request, cgs_tenant_id)

    mapping = await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)

    status, skill_response = await request.app["cgs_skills_client"].handle_intent(
        intent="client_rotate_key",
        user_id=str(principal(request).sub),
        message="",
        request_id=rid,
        context={"tenant_id": str(mapping["zetherion_tenant_id"])},
    )
    if status >= 400:
        raise map_upstream_error(status=status, payload=skill_response, source="skills")

    skill_data = _extract_skill_data(skill_response)
    new_api_key = str(skill_data.get("api_key", ""))
    if not new_api_key:
        raise GatewayError(
            code="AI_SKILLS_UPSTREAM_ERROR",
            message="Skills API response missing api_key",
            status=502,
            details={"upstream": skill_response},
        )

    updated = await request.app["cgs_storage"].rotate_tenant_api_key(
        cgs_tenant_id=cgs_tenant_id,
        new_api_key=new_api_key,
    )
    if updated is None:
        raise GatewayError(
            code="AI_TENANT_NOT_FOUND",
            message="Tenant mapping not found",
            status=404,
        )

    return success_response(
        rid,
        {
            "cgs_tenant_id": cgs_tenant_id,
            "api_key": new_api_key,
            "key_version": updated["key_version"],
        },
    )


async def handle_internal_release_marker(request: web.Request) -> web.Response:
    """POST /service/ai/v1/internal/tenants/{tenant_id}/release-markers."""
    _ensure_internal_access(request)
    rid = request_id(request)
    cgs_tenant_id = request.match_info["tenant_id"]
    _ensure_internal_tenant_access(request, cgs_tenant_id)

    raw = await json_object(request, required=False)
    try:
        payload = ReleaseMarkerRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    mapping = await resolve_active_mapping(request.app["cgs_storage"], cgs_tenant_id)

    public_client = request.app["cgs_public_client"]
    upstream_headers = canonical_upstream_headers(
        request_id_value=rid,
        api_key=str(mapping["zetherion_api_key"]),
    )
    payload_dict = payload.model_dump(mode="json")
    create_release_marker = getattr(public_client, "create_release_marker", None)
    if callable(create_release_marker) and inspect.iscoroutinefunction(create_release_marker):
        status, upstream, _ = await create_release_marker(
            headers=upstream_headers,
            payload=payload_dict,
        )
    else:
        status, upstream, _ = await public_client.request_json(
            "POST",
            "/api/v1/releases/markers",
            headers=upstream_headers,
            json_body=payload_dict,
        )
    if status >= 400:
        raise map_upstream_error(status=status, payload=upstream)

    return success_response(
        rid,
        {
            "cgs_tenant_id": cgs_tenant_id,
            "marker": upstream,
        },
        status=201,
    )


async def handle_internal_blog_publish(request: web.Request) -> web.Response:
    """POST /service/ai/v1/internal/blog/publish."""
    rid = request_id(request)
    _verify_blog_publish_token(request)

    raw = await json_object(request)
    try:
        payload = BlogPublishRequest.model_validate(raw)
    except ValidationError as exc:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Validation failed",
            status=400,
            details={"errors": exc.errors()},
        ) from exc

    header_key = request.headers.get("Idempotency-Key", "").strip()
    if not header_key:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Idempotency-Key header is required",
            status=400,
        )
    if not _BLOG_IDEMPOTENCY_PATTERN.fullmatch(header_key):
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Idempotency-Key must match blog-<sha>",
            status=400,
        )
    if payload.idempotency_key != header_key:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="idempotency_key body field must match Idempotency-Key header",
            status=400,
        )
    expected_key = f"blog-{payload.sha}"
    if expected_key != header_key:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="idempotency_key must align with payload sha",
            status=400,
        )

    storage = request.app["cgs_storage"]
    payload_dict = payload.model_dump(mode="json")
    payload_fingerprint = fingerprint_payload(payload_dict)
    existing = await storage.find_blog_publish_receipt(
        idempotency_key=header_key,
        sha=payload.sha,
    )
    if existing is not None:
        if str(existing.get("payload_fingerprint", "")) != payload_fingerprint:
            raise GatewayError(
                code="AI_IDEMPOTENCY_CONFLICT",
                message="Idempotency key already used with different payload",
                status=409,
            )
        request["blog_publish_receipt_id"] = str(existing.get("receipt_id", ""))
        envelope = {
            "request_id": rid,
            "data": {
                "status": "duplicate",
                "receipt_id": existing.get("receipt_id"),
                "idempotency_key": existing.get("idempotency_key"),
                "sha": existing.get("sha"),
            },
            "error": None,
        }
        return web.json_response(envelope, status=409)

    created = await storage.create_blog_publish_receipt(
        idempotency_key=header_key,
        payload_fingerprint=payload_fingerprint,
        source=payload.source,
        sha=payload.sha,
        repo=payload.repo,
        release_tag=payload.release_tag,
        title=payload.title,
        slug=payload.slug,
        meta_description=payload.meta_description,
        excerpt=payload.excerpt,
        primary_keyword=payload.primary_keyword,
        content_markdown=payload.content_markdown,
        json_ld=payload.json_ld,
        models=payload.models.model_dump(mode="json"),
        published_at=payload.published_at,
        request_id=rid,
    )
    request["blog_publish_receipt_id"] = str(created.get("receipt_id", ""))
    return success_response(
        rid,
        {
            "status": "published",
            "receipt_id": created.get("receipt_id"),
            "idempotency_key": created.get("idempotency_key"),
            "sha": created.get("sha"),
            "published_at": created.get("published_at"),
        },
        status=201,
    )


def register_internal_routes(app: web.Application) -> None:
    """Register internal tenant lifecycle routes."""
    prefix = "/service/ai/v1/internal"

    app.router.add_post(prefix + "/blog/publish", handle_internal_blog_publish)
    app.router.add_get(prefix + "/tenants", handle_internal_list_tenants)
    app.router.add_post(prefix + "/tenants", handle_internal_create_tenant)
    app.router.add_patch(prefix + "/tenants/{tenant_id}", handle_internal_update_tenant)
    app.router.add_post(
        prefix + "/tenants/{tenant_id}/deactivate",
        handle_internal_deactivate_tenant,
    )
    app.router.add_post(
        prefix + "/tenants/{tenant_id}/keys/rotate",
        handle_internal_rotate_key,
    )
    app.router.add_post(
        prefix + "/tenants/{tenant_id}/release-markers",
        handle_internal_release_marker,
    )
