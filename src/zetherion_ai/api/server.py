"""Public API server for multi-tenant client websites.

Runs as a separate aiohttp process from the internal skills server.
Provides tenant/session runtime APIs, analytics endpoints, and tenant-scoped
CRM reporting reads.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from aiohttp import web

from zetherion_ai.analytics import (
    AnalyticsJobRunner,
    ReplayStore,
    create_replay_store_from_settings,
)
from zetherion_ai.api.middleware import (
    RateLimiter,
    create_auth_middleware,
    create_cors_middleware,
    create_rate_limit_middleware,
)
from zetherion_ai.api.routes.analytics import (
    handle_analytics_events,
    handle_get_funnel,
    handle_get_recommendations,
    handle_get_replay_chunk,
    handle_recommendation_feedback,
    handle_release_marker,
    handle_replay_chunks,
    handle_session_end,
)
from zetherion_ai.api.routes.chat import handle_chat, handle_chat_history, handle_chat_stream
from zetherion_ai.api.routes.crm import handle_get_contacts, handle_get_interactions
from zetherion_ai.api.routes.documents import (
    handle_archive_document,
    handle_complete_upload,
    handle_create_upload,
    handle_download_document,
    handle_get_document,
    handle_list_documents,
    handle_model_catalog,
    handle_preview_document,
    handle_rag_query,
    handle_reindex_document,
    handle_restore_document,
)
from zetherion_ai.api.routes.health import handle_health
from zetherion_ai.api.routes.messaging import (
    handle_list_messaging_chats,
    handle_list_messaging_messages,
    handle_send_messaging_message,
)
from zetherion_ai.api.routes.sessions import (
    handle_create_session,
    handle_delete_session,
    handle_get_session,
)
from zetherion_ai.api.routes.youtube import register_youtube_routes
from zetherion_ai.api.tenant import TenantManager
from zetherion_ai.documents.service import DocumentService
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.server")


class PublicAPIServer:
    """Public-facing REST API server for client websites."""

    def __init__(
        self,
        tenant_manager: TenantManager,
        jwt_secret: str,
        *,
        host: str = "0.0.0.0",  # nosec B104 - Intentional for Docker container
        port: int = 8443,
        allowed_origins: list[str] | None = None,
        inference_broker: Any = None,
        youtube_storage: Any = None,
        youtube_skills: dict[str, Any] | None = None,
        replay_store: ReplayStore | None = None,
        analytics_jobs_enabled: bool = False,
        analytics_hourly_interval_seconds: int = 3600,
        analytics_daily_interval_seconds: int = 86400,
        document_service: DocumentService | None = None,
        tenant_admin_manager: Any = None,
        trust_policy_evaluator: Any = None,
    ) -> None:
        self._tenant_manager = tenant_manager
        self._jwt_secret = jwt_secret
        self._host = host
        self._port = port
        self._allowed_origins = allowed_origins
        self._inference_broker = inference_broker
        self._youtube_storage = youtube_storage
        self._youtube_skills = youtube_skills or {}
        self._replay_store = replay_store
        self._analytics_jobs_enabled = analytics_jobs_enabled
        self._analytics_hourly_interval_seconds = analytics_hourly_interval_seconds
        self._analytics_daily_interval_seconds = analytics_daily_interval_seconds
        self._document_service = document_service
        self._tenant_admin_manager = tenant_admin_manager
        self._trust_policy_evaluator = trust_policy_evaluator
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._rate_limiter = RateLimiter()
        self._analytics_stop_event: asyncio.Event | None = None
        self._analytics_task: asyncio.Task[None] | None = None
        self._document_maintenance_stop_event: asyncio.Event | None = None
        self._document_maintenance_task: asyncio.Task[None] | None = None

        log.info("public_api_initialized", host=host, port=port)

    async def _run_document_maintenance_loop(self, stop_event: asyncio.Event) -> None:
        """Run periodic archive/purge maintenance for tenant documents."""
        if self._document_service is None:
            return

        while not stop_event.is_set():
            try:
                await self._document_service.run_archive_maintenance_once()
            except Exception:
                log.exception("document_archive_maintenance_loop_failed")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._document_service.maintenance_interval_seconds,
                )
            except TimeoutError:
                continue

    def create_app(self) -> web.Application:
        """Create and configure the aiohttp application."""
        middlewares: list[Any] = []

        # CORS (outermost)
        if self._allowed_origins:
            middlewares.append(create_cors_middleware(self._allowed_origins))

        # Auth
        middlewares.append(create_auth_middleware(self._jwt_secret))

        # Rate limiting (innermost, runs after auth)
        middlewares.append(create_rate_limit_middleware(self._rate_limiter))

        app = web.Application(middlewares=middlewares)

        # Store shared state on app for handlers to access
        app["tenant_manager"] = self._tenant_manager
        app["jwt_secret"] = self._jwt_secret
        if self._inference_broker is not None:
            app["inference_broker"] = self._inference_broker
        if self._replay_store is not None:
            app["replay_store"] = self._replay_store
        if self._document_service is not None:
            app["document_service"] = self._document_service
        if self._tenant_admin_manager is not None:
            app["tenant_admin_manager"] = self._tenant_admin_manager
        if self._trust_policy_evaluator is not None:
            app["trust_policy_evaluator"] = self._trust_policy_evaluator

        # YouTube skill state (accessed by route handlers)
        if self._youtube_storage is not None:
            app["youtube_storage"] = self._youtube_storage
        for skill_key, skill_obj in self._youtube_skills.items():
            app[f"youtube_{skill_key}"] = skill_obj

        # Health
        app.router.add_get("/api/v1/health", handle_health)

        # Sessions (API key auth)
        app.router.add_post("/api/v1/sessions", handle_create_session)
        app.router.add_get("/api/v1/sessions/{session_id}", handle_get_session)
        app.router.add_delete("/api/v1/sessions/{session_id}", handle_delete_session)

        # Chat (session token auth)
        app.router.add_post("/api/v1/chat", handle_chat)
        app.router.add_post("/api/v1/chat/stream", handle_chat_stream)
        app.router.add_get("/api/v1/chat/history", handle_chat_history)

        # Analytics / app watcher (session token auth)
        app.router.add_post("/api/v1/analytics/events", handle_analytics_events)
        app.router.add_post("/api/v1/analytics/replay/chunks", handle_replay_chunks)
        app.router.add_get(
            "/api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}",
            handle_get_replay_chunk,
        )
        app.router.add_post("/api/v1/analytics/sessions/end", handle_session_end)
        app.router.add_get("/api/v1/analytics/recommendations", handle_get_recommendations)
        app.router.add_get("/api/v1/analytics/recommendations/tenant", handle_get_recommendations)
        app.router.add_get("/api/v1/analytics/funnel", handle_get_funnel)
        app.router.add_post(
            "/api/v1/analytics/recommendations/{recommendation_id}/feedback",
            handle_recommendation_feedback,
        )

        # CRM read routes (API-key auth)
        app.router.add_get("/api/v1/crm/contacts", handle_get_contacts)
        app.router.add_get("/api/v1/crm/interactions", handle_get_interactions)
        app.router.add_get("/api/v1/messaging/chats", handle_list_messaging_chats)
        app.router.add_get("/api/v1/messaging/messages", handle_list_messaging_messages)
        app.router.add_post(
            "/api/v1/messaging/messages/{chat_id}/send",
            handle_send_messaging_message,
        )

        # CI/deploy marker ingest (API-key auth)
        app.router.add_post("/api/v1/releases/markers", handle_release_marker)

        # Tenant documents + retrieval
        app.router.add_post("/api/v1/documents/uploads", handle_create_upload)
        app.router.add_post(
            "/api/v1/documents/uploads/{upload_id}/complete",
            handle_complete_upload,
        )
        app.router.add_get("/api/v1/documents", handle_list_documents)
        app.router.add_get("/api/v1/documents/{document_id}", handle_get_document)
        app.router.add_delete("/api/v1/documents/{document_id}", handle_archive_document)
        app.router.add_get("/api/v1/documents/{document_id}/preview", handle_preview_document)
        app.router.add_get("/api/v1/documents/{document_id}/download", handle_download_document)
        app.router.add_post("/api/v1/documents/{document_id}/index", handle_reindex_document)
        app.router.add_post("/api/v1/documents/{document_id}/restore", handle_restore_document)
        app.router.add_post("/api/v1/rag/query", handle_rag_query)
        app.router.add_get("/api/v1/models/providers", handle_model_catalog)

        # YouTube (API key auth)
        if self._youtube_storage is not None:
            register_youtube_routes(app)

        self._app = app
        return app

    async def start(self) -> None:
        """Start the server."""
        if self._app is None:
            self.create_app()

        if self._app is None:  # pragma: no cover
            raise RuntimeError("create_app() must be called first")

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        if self._analytics_jobs_enabled:
            self._analytics_stop_event = asyncio.Event()
            analytics_runner = AnalyticsJobRunner(
                self._tenant_manager,
                replay_store=self._replay_store,
                hourly_interval_seconds=self._analytics_hourly_interval_seconds,
                daily_interval_seconds=self._analytics_daily_interval_seconds,
            )
            self._analytics_task = asyncio.create_task(
                analytics_runner.run_loop(self._analytics_stop_event)
            )

        if self._document_service is not None:
            self._document_maintenance_stop_event = asyncio.Event()
            self._document_maintenance_task = asyncio.create_task(
                self._run_document_maintenance_loop(self._document_maintenance_stop_event)
            )

        log.info("public_api_started", host=self._host, port=self._port)

    async def stop(self) -> None:
        """Stop the server."""
        if self._analytics_stop_event is not None:
            self._analytics_stop_event.set()
        if self._analytics_task is not None:
            self._analytics_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._analytics_task
            self._analytics_task = None
        self._analytics_stop_event = None

        if self._document_maintenance_stop_event is not None:
            self._document_maintenance_stop_event.set()
        if self._document_maintenance_task is not None:
            self._document_maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._document_maintenance_task
            self._document_maintenance_task = None
        self._document_maintenance_stop_event = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            log.info("public_api_stopped")


async def run_server(
    tenant_manager: TenantManager,
    jwt_secret: str,
    host: str = "0.0.0.0",  # nosec B104 - Intentional for Docker container
    port: int = 8443,
    inference_broker: Any = None,
    youtube_storage: Any = None,
    youtube_skills: dict[str, Any] | None = None,
    replay_store: ReplayStore | None = None,
    analytics_jobs_enabled: bool = False,
    analytics_hourly_interval_seconds: int = 3600,
    analytics_daily_interval_seconds: int = 86400,
    document_service: DocumentService | None = None,
    tenant_admin_manager: Any = None,
    trust_policy_evaluator: Any = None,
) -> None:
    """Run the public API server (main entry point for Docker container)."""
    server = PublicAPIServer(
        tenant_manager=tenant_manager,
        jwt_secret=jwt_secret,
        host=host,
        port=port,
        inference_broker=inference_broker,
        youtube_storage=youtube_storage,
        youtube_skills=youtube_skills,
        replay_store=replay_store,
        analytics_jobs_enabled=analytics_jobs_enabled,
        analytics_hourly_interval_seconds=analytics_hourly_interval_seconds,
        analytics_daily_interval_seconds=analytics_daily_interval_seconds,
        document_service=document_service,
        tenant_admin_manager=tenant_admin_manager,
        trust_policy_evaluator=trust_policy_evaluator,
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
    """Main entry point for the public API service."""
    import os

    from zetherion_ai.admin import TenantAdminManager
    from zetherion_ai.config import get_settings, set_tenant_admin_manager
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.security.domain_keys import build_runtime_encryptors
    from zetherion_ai.security.trust_policy import TrustPolicyEvaluator
    from zetherion_ai.trust.data_plane import ensure_postgres_isolation_schemas
    from zetherion_ai.trust.scope import TrustDomain
    from zetherion_ai.trust.storage import ensure_trust_storage_schema

    settings = get_settings()

    host = os.environ.get("API_HOST", "0.0.0.0")  # nosec B104
    port = int(os.environ.get("API_PORT", "8443"))
    jwt_secret = os.environ.get("API_JWT_SECRET", "")

    if not jwt_secret:
        log.error("API_JWT_SECRET is required")
        raise SystemExit(1)

    tenant_manager = TenantManager(dsn=settings.postgres_dsn)

    async def init_and_run() -> None:
        await tenant_manager.initialize()
        pool = getattr(tenant_manager, "_pool", None)
        if pool is not None:
            await ensure_postgres_isolation_schemas(pool, settings)
            await ensure_trust_storage_schema(
                pool,
                schema=settings.postgres_control_plane_schema,
            )
        tenant_admin_manager: TenantAdminManager | None = None
        trust_policy_evaluator = TrustPolicyEvaluator()
        tenant_encryptor = None

        try:
            passphrase_secret = getattr(settings, "encryption_passphrase", None)
            passphrase_getter = getattr(passphrase_secret, "get_secret_value", None)
            if callable(passphrase_getter):
                passphrase = str(passphrase_getter()).strip()
            else:
                passphrase = str(passphrase_secret or "").strip()
            if passphrase and pool is not None:
                encryptors = build_runtime_encryptors(settings)
                tenant_encryptor = encryptors.tenant_data
                tenant_admin_manager = TenantAdminManager(
                    pool=pool,
                    encryptor=tenant_encryptor,
                )
                await tenant_admin_manager.initialize()
                set_tenant_admin_manager(tenant_admin_manager)
                log.info("public_api_tenant_admin_manager_initialized")
        except Exception as e:
            log.warning("public_api_tenant_admin_manager_init_failed", error=str(e))

        api_broker: Any = None
        try:
            from zetherion_ai.agent.inference import InferenceBroker

            api_broker = InferenceBroker()
        except Exception as e:
            log.warning("api_inference_broker_init_failed", error=str(e))

        replay_store = None
        document_service = None
        try:
            replay_store = create_replay_store_from_settings(
                settings, trust_domain=TrustDomain.TENANT_RAW
            )
        except Exception as e:
            log.warning("replay_store_init_failed", error=str(e))

        try:
            doc_memory = QdrantMemory(
                encryptor=tenant_encryptor,
                trust_domain=TrustDomain.TENANT_RAW,
            )
            await doc_memory.initialize()
            document_service = DocumentService(
                tenant_manager=tenant_manager,
                memory=doc_memory,
                inference_broker=api_broker,
                blob_store=replay_store,
            )
            await document_service.initialize()
        except Exception as e:
            log.warning("document_service_init_failed", error=str(e))
            document_service = None

        # Initialize YouTube storage and skills if Postgres is available
        yt_storage = None
        yt_skills: dict[str, Any] = {}
        if settings.postgres_dsn:
            try:
                from zetherion_ai.skills.youtube.intelligence import YouTubeIntelligenceSkill
                from zetherion_ai.skills.youtube.management import YouTubeManagementSkill
                from zetherion_ai.skills.youtube.storage import YouTubeStorage
                from zetherion_ai.skills.youtube.strategy import YouTubeStrategySkill

                yt_storage = YouTubeStorage(dsn=settings.postgres_dsn)
                await yt_storage.initialize()

                intel_skill = YouTubeIntelligenceSkill(storage=yt_storage, broker=api_broker)
                mgmt_skill = YouTubeManagementSkill(storage=yt_storage, broker=api_broker)
                strat_skill = YouTubeStrategySkill(storage=yt_storage, broker=api_broker)

                await intel_skill.safe_initialize()
                await mgmt_skill.safe_initialize()
                await strat_skill.safe_initialize()

                yt_skills = {
                    "intelligence": intel_skill,
                    "management": mgmt_skill,
                    "strategy": strat_skill,
                }
                log.info("youtube_api_skills_initialized")
            except Exception as e:
                log.warning("youtube_api_init_failed", error=str(e))

        try:
            await run_server(
                tenant_manager,
                jwt_secret,
                host,
                port,
                inference_broker=api_broker,
                youtube_storage=yt_storage,
                youtube_skills=yt_skills,
                replay_store=replay_store,
                analytics_jobs_enabled=bool(getattr(settings, "analytics_jobs_enabled", True)),
                analytics_hourly_interval_seconds=int(
                    getattr(settings, "analytics_hourly_job_interval_seconds", 3600)
                ),
                analytics_daily_interval_seconds=int(
                    getattr(settings, "analytics_daily_job_interval_seconds", 86400)
                ),
                document_service=document_service,
                tenant_admin_manager=tenant_admin_manager,
                trust_policy_evaluator=trust_policy_evaluator,
            )
        finally:
            if api_broker is not None:
                await api_broker.close()
            if replay_store is not None:
                await replay_store.close()
            set_tenant_admin_manager(None)

    try:
        asyncio.run(init_and_run())
    except KeyboardInterrupt:
        log.info("public_api_shutdown")


if __name__ == "__main__":
    main()
