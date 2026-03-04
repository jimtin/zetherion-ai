"""Shared helpers for CGS gateway routes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aiohttp import web

from zetherion_ai.cgs_gateway.errors import GatewayError
from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.rate_limit import TenantMutationRateLimiter
from zetherion_ai.cgs_gateway.storage import CGSGatewayStorage


def request_id(request: web.Request) -> str:
    """Return current request id from middleware context."""
    return str(request.get("request_id", ""))


def principal(request: web.Request) -> AuthPrincipal:
    """Return authenticated principal from middleware context."""
    p = request.get("principal")
    if not isinstance(p, AuthPrincipal):
        raise GatewayError(code="AI_AUTH_MISSING", message="Authentication required", status=401)
    return p


async def json_object(request: web.Request, *, required: bool = True) -> dict[str, Any]:
    """Parse request JSON and guarantee object payload."""
    try:
        data = await request.json()
    except Exception as exc:
        if required:
            raise GatewayError(
                code="AI_BAD_REQUEST",
                message="Invalid JSON body",
                status=400,
            ) from exc
        return {}

    if not isinstance(data, dict):
        raise GatewayError(code="AI_BAD_REQUEST", message="JSON body must be an object", status=400)
    return data


def enforce_tenant_access(principal_obj: AuthPrincipal, cgs_tenant_id: str) -> None:
    """Enforce principal tenant claim when present."""
    claim_tenant = (principal_obj.tenant_id or "").strip()
    if claim_tenant and claim_tenant != cgs_tenant_id:
        raise GatewayError(
            code="AI_AUTH_FORBIDDEN",
            message="Token tenant does not match requested tenant",
            status=403,
        )


def canonical_upstream_headers(
    *,
    request_id_value: str,
    bearer_token: str | None = None,
    api_key: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build standard upstream headers with tracing."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Request-Id": request_id_value,
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if api_key:
        headers["X-API-Key"] = api_key
    if extra:
        headers.update(extra)
    return headers


def fingerprint_payload(payload: dict[str, Any]) -> str:
    """Create deterministic SHA256 fingerprint for idempotency compare."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def resolve_active_mapping(storage: CGSGatewayStorage, cgs_tenant_id: str) -> dict[str, Any]:
    """Load tenant mapping and enforce active status."""
    mapping = await storage.get_tenant_mapping(cgs_tenant_id)
    if mapping is None:
        raise GatewayError(
            code="AI_TENANT_NOT_FOUND",
            message="Tenant mapping not found",
            status=404,
        )
    if not bool(mapping.get("is_active", True)):
        raise GatewayError(code="AI_TENANT_INACTIVE", message="Tenant is inactive", status=403)
    return mapping


def enforce_mutation_rate_limit(
    request: web.Request,
    *,
    cgs_tenant_id: str,
    family: str,
) -> None:
    """Apply tenant-aware mutation rate limits when configured."""
    limiter = request.app.get("cgs_mutation_rate_limiter")
    if not isinstance(limiter, TenantMutationRateLimiter):
        return

    allowed, retry_after = limiter.check(tenant_id=cgs_tenant_id, family=family)
    if allowed:
        return

    raise GatewayError(
        code="AI_UPSTREAM_429",
        message="Gateway rate limit exceeded",
        status=429,
        retryable=True,
        details={"retry_after_seconds": retry_after, "family": family},
    )
