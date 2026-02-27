"""Middleware for CGS gateway authentication, request context, and CORS."""

from __future__ import annotations

import uuid
from typing import Any

import jwt  # type: ignore[import-not-found]
from aiohttp import web

from zetherion_ai.cgs_gateway.errors import GatewayError, error_response
from zetherion_ai.cgs_gateway.models import AuthPrincipal

PUBLIC_PATHS = frozenset({"/service/ai/v1/health"})


class JWTVerifier:
    """Validates CGS JWTs against a JWKS endpoint."""

    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        if not jwks_url.strip():
            raise ValueError("CGS_AUTH_JWKS_URL is required")
        self._jwks_client = jwt.PyJWKClient(jwks_url)
        self._issuer = (issuer or "").strip() or None
        self._audience = (audience or "").strip() or None

    def verify(self, token: str) -> AuthPrincipal:
        """Verify JWT signature/claims and return normalized principal."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            options = {
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": self._audience is not None,
                "verify_iss": self._issuer is not None,
            }
            decode_kwargs: dict[str, Any] = {
                "algorithms": ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                "options": options,
            }
            if self._audience is not None:
                decode_kwargs["audience"] = self._audience
            if self._issuer is not None:
                decode_kwargs["issuer"] = self._issuer

            payload: dict[str, Any] = jwt.decode(token, signing_key.key, **decode_kwargs)
        except Exception as exc:  # pragma: no cover - branch-specific JWT errors
            raise GatewayError(
                code="AI_AUTH_INVALID_TOKEN",
                message="Invalid or expired auth token",
                status=401,
            ) from exc

        sub = str(payload.get("sub") or "")
        if not sub:
            raise GatewayError(
                code="AI_AUTH_INVALID_TOKEN",
                message="Token subject is missing",
                status=401,
            )

        roles_raw = payload.get("roles", [])
        if isinstance(roles_raw, str):
            roles = [r for r in roles_raw.replace(",", " ").split() if r]
        elif isinstance(roles_raw, list):
            roles = [str(r) for r in roles_raw if str(r)]
        else:
            roles = []

        scopes: list[str] = []
        scope_raw = payload.get("scope", "")
        if isinstance(scope_raw, str):
            scopes.extend([s for s in scope_raw.split() if s])
        scopes_raw = payload.get("scopes", [])
        if isinstance(scopes_raw, list):
            scopes.extend([str(s) for s in scopes_raw if str(s)])

        tenant_id = payload.get("tenant_id") or payload.get("cgs_tenant_id")

        return AuthPrincipal(
            sub=sub,
            tenant_id=str(tenant_id) if tenant_id is not None else None,
            roles=roles,
            scopes=sorted(set(scopes)),
            claims=payload,
        )


def create_request_context_middleware() -> Any:
    """Attach request id and mirror it in response headers."""

    @web.middleware
    async def request_context(request: web.Request, handler: Any) -> web.Response:
        request_id = request.headers.get("X-Request-Id", "").strip()
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:20]}"
        request["request_id"] = request_id

        response = await handler(request)
        response.headers["X-Request-Id"] = request_id
        return response

    return request_context


def create_cors_middleware(allowed_origins: list[str] | None = None) -> Any:
    """Create CORS middleware for browser-based CGS apps."""

    @web.middleware
    async def cors_middleware(request: web.Request, handler: Any) -> web.Response:
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
                    "Authorization, Content-Type, X-Request-Id, Idempotency-Key"
                )
                response.headers["Access-Control-Max-Age"] = "86400"

        return response

    return cors_middleware


def create_auth_middleware(verifier: JWTVerifier) -> Any:
    """Require JWT auth for all non-public gateway routes."""

    @web.middleware
    async def auth_middleware(request: web.Request, handler: Any) -> web.Response:
        path = request.path
        if path in PUBLIC_PATHS or not path.startswith("/service/ai/v1"):
            return await handler(request)  # type: ignore[no-any-return]

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return error_response(
                request.get("request_id", ""),
                code="AI_AUTH_MISSING",
                message="Missing or invalid Authorization header",
                status=401,
            )

        token = auth_header[7:]
        try:
            principal = verifier.verify(token)
        except GatewayError as exc:
            return error_response(
                request.get("request_id", ""),
                code=exc.code,
                message=exc.message,
                status=exc.status,
                details=exc.details,
            )

        request["principal"] = principal
        return await handler(request)  # type: ignore[no-any-return]

    return auth_middleware


def principal_is_operator(principal: AuthPrincipal) -> bool:
    """Return True when principal has internal/operator capability."""
    role_set = {r.lower() for r in principal.roles}
    if role_set.intersection({"operator", "admin", "owner"}):
        return True
    scope_set = {s.lower() for s in principal.scopes}
    return bool(scope_set.intersection({"cgs:internal", "cgs:operator", "cgs:admin"}))
