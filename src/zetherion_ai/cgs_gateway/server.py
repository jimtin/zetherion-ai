"""CGS gateway service exposing /service/ai/v1 contract."""

from __future__ import annotations

import asyncio
import os
import ssl
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiohttp import web

from zetherion_ai.cgs_gateway.errors import from_exception
from zetherion_ai.cgs_gateway.middleware import (
    JWTVerifier,
    create_auth_middleware,
    create_cors_middleware,
    create_request_context_middleware,
)
from zetherion_ai.cgs_gateway.rate_limit import TenantMutationRateLimiter
from zetherion_ai.cgs_gateway.routes.internal import register_internal_routes
from zetherion_ai.cgs_gateway.routes.internal_admin import register_internal_admin_routes
from zetherion_ai.cgs_gateway.routes.reporting import register_reporting_routes
from zetherion_ai.cgs_gateway.routes.runtime import register_runtime_routes
from zetherion_ai.cgs_gateway.storage import CGSGatewayStorage
from zetherion_ai.cgs_gateway.upstream.public_api_client import PublicAPIClient
from zetherion_ai.cgs_gateway.upstream.skills_client import SkillsClient
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.portfolio.storage import PortfolioStorage
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.security.keys import KeyManager

log = get_logger("zetherion_ai.cgs_gateway.server")
RequestHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def create_error_middleware() -> Any:
    """Catch route exceptions and emit standard envelope errors."""

    @web.middleware
    async def error_middleware(request: web.Request, handler: RequestHandler) -> web.StreamResponse:
        try:
            return await handler(request)
        except Exception as exc:
            return from_exception(str(request.get("request_id", "")), exc)

    return error_middleware


def create_request_logging_middleware() -> Any:
    """Persist lightweight request logs for attribution."""

    @web.middleware
    async def request_logging_middleware(
        request: web.Request, handler: RequestHandler
    ) -> web.StreamResponse:
        started = time.monotonic()
        response = await handler(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        storage: CGSGatewayStorage | None = request.app.get("cgs_storage")
        if storage is not None:
            details: dict[str, Any] = {}
            for key in ("tenant_id", "conversation_id"):
                val = request.match_info.get(key)
                if val:
                    details[key] = val
            for context_key in (
                "change_ticket_id",
                "upstream_request_id",
                "blog_publish_receipt_id",
            ):
                value = request.get(context_key)
                if isinstance(value, str) and value:
                    details[context_key] = value
            with suppress(Exception):
                await storage.log_request(
                    request_id=str(request.get("request_id", "")),
                    cgs_tenant_id=request.match_info.get("tenant_id")
                    or str(request.get("cgs_tenant_id", "") or "")
                    or None,
                    conversation_id=request.match_info.get("conversation_id"),
                    endpoint=request.path,
                    method=request.method.upper(),
                    upstream_status=None,
                    duration_ms=elapsed_ms,
                    error_code=None if response.status < 400 else "HTTP_ERROR",
                    details=details,
                )

        return response

    return request_logging_middleware


async def handle_health(_: web.Request) -> web.Response:
    """GET /service/ai/v1/health."""
    return web.json_response(
        {
            "status": "healthy",
            "service": "cgs-gateway",
        }
    )


class CGSGatewayServer:
    """Aiohttp server for CGS API provider layer."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        allowed_origins: list[str] | None,
        jwt_verifier: JWTVerifier,
        storage: CGSGatewayStorage,
        portfolio_storage: PortfolioStorage,
        public_client: PublicAPIClient,
        skills_client: SkillsClient,
        blog_publish_token: str | None = None,
        rag_allowed_providers: set[str] | None = None,
        rag_allowed_models: set[str] | None = None,
        mutation_rate_limiter: TenantMutationRateLimiter | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._allowed_origins = allowed_origins
        self._jwt_verifier = jwt_verifier
        self._storage = storage
        self._portfolio_storage = portfolio_storage
        self._public_client = public_client
        self._skills_client = skills_client
        self._blog_publish_token = (blog_publish_token or "").strip()
        self._rag_allowed_providers = rag_allowed_providers or {"groq", "openai", "anthropic"}
        self._rag_allowed_models = rag_allowed_models or set()
        self._mutation_rate_limiter = mutation_rate_limiter
        self._ssl_context = ssl_context
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    def create_app(self) -> web.Application:
        middlewares: list[Any] = [
            create_request_context_middleware(),
            create_error_middleware(),
        ]
        if self._allowed_origins:
            middlewares.append(create_cors_middleware(self._allowed_origins))
        middlewares.append(create_auth_middleware(self._jwt_verifier))
        middlewares.append(create_request_logging_middleware())

        app = web.Application(middlewares=middlewares)
        app["cgs_storage"] = self._storage
        app["owner_portfolio_storage"] = self._portfolio_storage
        app["cgs_public_client"] = self._public_client
        app["cgs_skills_client"] = self._skills_client
        app["cgs_blog_publish_token"] = self._blog_publish_token
        app["cgs_rag_allowed_providers"] = self._rag_allowed_providers
        app["cgs_rag_allowed_models"] = self._rag_allowed_models
        app["cgs_mutation_rate_limiter"] = self._mutation_rate_limiter

        app.router.add_get("/service/ai/v1/health", handle_health)
        register_runtime_routes(app)
        register_internal_routes(app)
        register_internal_admin_routes(app)
        register_reporting_routes(app)

        self._app = app
        return app

    async def start(self) -> None:
        await self._storage.initialize()
        await self._portfolio_storage.initialize()
        await self._public_client.start()
        await self._skills_client.start()

        if self._app is None:
            self.create_app()
        if self._app is None:  # pragma: no cover
            raise RuntimeError("create_app() must be called first")

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port, ssl_context=self._ssl_context)
        await site.start()
        log.info("cgs_gateway_started", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        await self._skills_client.close()
        await self._public_client.close()
        await self._portfolio_storage.close()
        await self._storage.close()
        log.info("cgs_gateway_stopped")


async def run_server(
    *,
    host: str,
    port: int,
    allowed_origins: list[str] | None,
    jwks_url: str,
    issuer: str | None,
    audience: str | None,
    postgres_dsn: str,
    encryption_passphrase: str,
    encryption_salt_path: str,
    zetherion_public_api_base_url: str,
    zetherion_skills_api_base_url: str,
    zetherion_skills_api_secret: str,
    postgres_owner_portfolio_schema: str = "owner_portfolio",
    blog_publish_token: str | None = None,
    rag_allowed_providers: set[str] | None = None,
    rag_allowed_models: set[str] | None = None,
    mutation_rate_limiter: TenantMutationRateLimiter | None = None,
    ssl_context: ssl.SSLContext | None = None,
    upstream_ssl_context: ssl.SSLContext | None = None,
    postgres_ssl_context: ssl.SSLContext | None = None,
) -> None:
    """Create and run CGS gateway server until cancelled."""
    key_manager = KeyManager(encryption_passphrase, encryption_salt_path)
    encryptor = FieldEncryptor(key=key_manager.key)

    storage = CGSGatewayStorage(
        dsn=postgres_dsn,
        encryptor=encryptor,
        owner_portfolio_schema=postgres_owner_portfolio_schema,
        ssl_context=postgres_ssl_context,
    )
    portfolio_storage = PortfolioStorage(
        dsn=postgres_dsn,
        owner_portfolio_schema=postgres_owner_portfolio_schema,
        ssl_context=postgres_ssl_context,
    )
    public_client = PublicAPIClient(
        base_url=zetherion_public_api_base_url,
        ssl_context=upstream_ssl_context,
    )
    skills_client = SkillsClient(
        base_url=zetherion_skills_api_base_url,
        api_secret=zetherion_skills_api_secret,  # gitleaks:allow
        ssl_context=upstream_ssl_context,
    )
    verifier = JWTVerifier(jwks_url=jwks_url, issuer=issuer, audience=audience)

    server = CGSGatewayServer(
        host=host,
        port=port,
        allowed_origins=allowed_origins,
        jwt_verifier=verifier,
        storage=storage,
        portfolio_storage=portfolio_storage,
        public_client=public_client,
        skills_client=skills_client,
        blog_publish_token=blog_publish_token,
        rag_allowed_providers=rag_allowed_providers,
        rag_allowed_models=rag_allowed_models,
        mutation_rate_limiter=mutation_rate_limiter,
        ssl_context=ssl_context,
    )

    await server.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


def main() -> None:
    """CLI entry point for the CGS gateway service."""
    settings = get_settings()

    default_gateway_host = "0.0.0.0"  # nosec B104 - service binds all interfaces in container runtime
    host = os.environ.get(
        "CGS_GATEWAY_HOST",
        getattr(settings, "cgs_gateway_host", default_gateway_host),
    )
    port = int(os.environ.get("CGS_GATEWAY_PORT", str(getattr(settings, "cgs_gateway_port", 8743))))

    allowed_origins_raw = os.environ.get(
        "CGS_GATEWAY_ALLOWED_ORIGINS",
        getattr(settings, "cgs_gateway_allowed_origins", ""),
    )
    allowed_origins = _split_csv(allowed_origins_raw or "")

    jwks_url = os.environ.get("CGS_AUTH_JWKS_URL", getattr(settings, "cgs_auth_jwks_url", "")) or ""
    issuer = os.environ.get("CGS_AUTH_ISSUER", getattr(settings, "cgs_auth_issuer", "")) or ""
    audience = os.environ.get("CGS_AUTH_AUDIENCE", getattr(settings, "cgs_auth_audience", "")) or ""

    z_public = os.environ.get(
        "ZETHERION_PUBLIC_API_BASE_URL",
        getattr(
            settings,
            "zetherion_public_api_base_url",
            "https://zetherion-ai-api-green:8443,https://zetherion-ai-api-blue:8443",
        ),
    )
    z_skills = os.environ.get(
        "ZETHERION_SKILLS_API_BASE_URL",
        getattr(
            settings,
            "zetherion_skills_api_base_url",
            "https://zetherion-ai-skills-green:8080,https://zetherion-ai-skills-blue:8080",
        ),
    )

    z_skills_secret = os.environ.get("ZETHERION_SKILLS_API_SECRET", "")
    if not z_skills_secret and settings.skills_api_secret is not None:
        z_skills_secret = settings.skills_api_secret.get_secret_value()

    rag_allowed_providers_raw = os.environ.get(
        "RAG_ALLOWED_PROVIDERS",
        getattr(settings, "rag_allowed_providers", "groq,openai,anthropic"),
    )
    rag_allowed_providers = {
        provider.strip().lower()
        for provider in _split_csv(rag_allowed_providers_raw or "")
        if provider.strip()
    } or {"groq", "openai", "anthropic"}
    rag_allowed_models_raw = os.environ.get(
        "RAG_ALLOWED_MODELS",
        getattr(settings, "rag_allowed_models", ""),
    )
    rag_allowed_models = {model.strip() for model in _split_csv(rag_allowed_models_raw or "")}
    blog_publish_token = os.environ.get("CGS_BLOG_PUBLISH_TOKEN", "")
    if not blog_publish_token and getattr(settings, "cgs_blog_publish_token", None) is not None:
        token = settings.cgs_blog_publish_token
        blog_publish_token = token.get_secret_value() if token is not None else ""
    doc_mutation_rpm = int(
        os.environ.get(
            "CGS_DOCUMENT_MUTATION_RPM",
            str(getattr(settings, "cgs_document_mutation_rpm", 30)),
        )
    )
    admin_mutation_rpm = int(
        os.environ.get(
            "CGS_ADMIN_MUTATION_RPM",
            str(getattr(settings, "cgs_admin_mutation_rpm", 20)),
        )
    )
    mutation_limiter = TenantMutationRateLimiter(
        default_limit_per_minute=max(1, min(doc_mutation_rpm, admin_mutation_rpm)),
        family_limits_per_minute={
            "documents": doc_mutation_rpm,
            "admin": admin_mutation_rpm,
        },
    )

    if not settings.postgres_dsn:
        log.error("POSTGRES_DSN is required for CGS gateway")
        raise SystemExit(1)
    if not jwks_url.strip():
        log.error("CGS_AUTH_JWKS_URL is required for CGS gateway")
        raise SystemExit(1)
    if not z_skills_secret.strip():
        log.error("ZETHERION_SKILLS_API_SECRET (or SKILLS_API_SECRET) is required")
        raise SystemExit(1)

    try:
        asyncio.run(
            run_server(
                host=host,
                port=port,
                allowed_origins=allowed_origins or None,
                jwks_url=jwks_url,
                issuer=issuer or None,
                audience=audience or None,
                postgres_dsn=settings.postgres_dsn,
                encryption_passphrase=settings.encryption_passphrase.get_secret_value(),
                encryption_salt_path=settings.encryption_salt_path,
                zetherion_public_api_base_url=z_public,
                zetherion_skills_api_base_url=z_skills,
                zetherion_skills_api_secret=z_skills_secret,
                postgres_owner_portfolio_schema=getattr(
                    settings,
                    "postgres_owner_portfolio_schema",
                    "owner_portfolio",
                ),
                blog_publish_token=blog_publish_token,
                rag_allowed_providers=rag_allowed_providers,
                rag_allowed_models=rag_allowed_models,
                mutation_rate_limiter=mutation_limiter,
                ssl_context=settings.cgs_gateway_server_ssl_context,
                upstream_ssl_context=settings.internal_client_ssl_context,
                postgres_ssl_context=settings.postgres_ssl_context,
            )
        )
    except KeyboardInterrupt:
        log.info("cgs_gateway_shutdown")


if __name__ == "__main__":
    main()
