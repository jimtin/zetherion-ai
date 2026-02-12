"""Middleware for the public API server.

Provides CORS, API key authentication, session token authentication,
and tenant context injection.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from aiohttp import web

from zetherion_ai.api.auth import validate_session_token
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.middleware")

# Paths that don't require any authentication
PUBLIC_PATHS = frozenset({"/api/v1/health"})

# Paths that require session token (Bearer) auth instead of API key
SESSION_AUTH_PATHS = frozenset({"/api/v1/chat", "/api/v1/chat/stream", "/api/v1/chat/history"})


def create_cors_middleware(allowed_origins: list[str] | None = None) -> Any:
    """Create CORS middleware.

    Args:
        allowed_origins: List of allowed origins, or None for no CORS headers.
    """

    @web.middleware
    async def cors_middleware(request: web.Request, handler: Any) -> web.Response:
        # Handle preflight
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        else:
            response = await handler(request)

        if allowed_origins:
            origin = request.headers.get("Origin", "")
            if origin in allowed_origins or "*" in allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = (
                    "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                )
                response.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, X-API-Key, Content-Type"
                )
                response.headers["Access-Control-Max-Age"] = "86400"

        return response

    return cors_middleware


def create_auth_middleware(jwt_secret: str) -> Any:
    """Create authentication middleware.

    Routes are authenticated based on their path:
    - PUBLIC_PATHS: No auth required
    - SESSION_AUTH_PATHS: Bearer session token (JWT)
    - Everything else: X-API-Key header (tenant API key)

    The middleware attaches ``request["tenant"]`` and optionally
    ``request["session"]`` for downstream handlers.
    """

    @web.middleware
    async def auth_middleware(request: web.Request, handler: Any) -> web.Response:
        path = request.path

        # Public endpoints — no auth
        if path in PUBLIC_PATHS:
            return await handler(request)  # type: ignore[no-any-return]

        tenant_manager = request.app.get("tenant_manager")
        if tenant_manager is None:
            return web.json_response({"error": "Service unavailable"}, status=503)

        # Session token auth (for chat endpoints)
        if path in SESSION_AUTH_PATHS:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return web.json_response(
                    {"error": "Missing or invalid Authorization header"},
                    status=401,
                )

            token = auth_header[7:]
            try:
                payload = validate_session_token(token, jwt_secret)
            except Exception:
                return web.json_response({"error": "Invalid or expired session token"}, status=401)

            # Load tenant
            tenant = await tenant_manager.get_tenant(payload["tenant_id"])
            if tenant is None or not tenant.get("is_active", False):
                return web.json_response({"error": "Tenant not found or inactive"}, status=403)

            # Load session
            session = await tenant_manager.get_session(payload["session_id"])
            if session is None:
                return web.json_response({"error": "Session expired or not found"}, status=401)

            # Verify session belongs to tenant
            if str(session["tenant_id"]) != str(tenant["tenant_id"]):
                return web.json_response({"error": "Session does not belong to tenant"}, status=403)

            request["tenant"] = tenant
            request["session"] = session

            # Update session activity
            await tenant_manager.touch_session(payload["session_id"])

            return await handler(request)  # type: ignore[no-any-return]

        # API key auth (for everything else)
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return web.json_response({"error": "Missing X-API-Key header"}, status=401)

        tenant = await tenant_manager.authenticate_api_key(api_key)
        if tenant is None:
            return web.json_response({"error": "Invalid API key"}, status=401)

        request["tenant"] = tenant
        return await handler(request)  # type: ignore[no-any-return]

    return auth_middleware


class RateLimiter:
    """Simple in-memory token bucket rate limiter per tenant."""

    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, float]] = defaultdict(
            lambda: {"tokens": 60.0, "last_refill": time.monotonic()}
        )

    def check(self, tenant_id: str, rpm_limit: int) -> bool:
        """Return True if the request is allowed, False if rate limited."""
        now = time.monotonic()
        bucket = self._buckets[tenant_id]

        # Refill tokens
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(float(rpm_limit), bucket["tokens"] + elapsed * (rpm_limit / 60.0))
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False


def create_rate_limit_middleware(rate_limiter: RateLimiter) -> Any:
    """Create rate limiting middleware (runs after auth)."""

    @web.middleware
    async def rate_limit_middleware(request: web.Request, handler: Any) -> web.Response:
        if request.path in PUBLIC_PATHS:
            return await handler(request)  # type: ignore[no-any-return]

        tenant = request.get("tenant")
        if tenant is None:
            return await handler(request)  # type: ignore[no-any-return]

        tenant_id = str(tenant["tenant_id"])
        rpm_limit = tenant.get("rate_limit_rpm", 60)

        if not rate_limiter.check(tenant_id, rpm_limit):
            log.warning("rate_limited", tenant_id=tenant_id)
            return web.json_response(
                {"error": "Rate limit exceeded", "retry_after": 60},
                status=429,
            )

        return await handler(request)  # type: ignore[no-any-return]

    return rate_limit_middleware
