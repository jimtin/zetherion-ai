"""REST API server for the skills service.

This server runs in its own Docker container and provides:
- /health - Health check endpoint
- /handle - Handle skill requests
- /heartbeat - Trigger heartbeat cycle
- /skills - List available skills
- /status - Get service status
"""

import asyncio
import hmac
import os
from json import JSONDecodeError
from typing import Any

from aiohttp import web

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.registry import SkillRegistry

log = get_logger("zetherion_ai.skills.server")


def _serialise_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB record dict so it is JSON-serialisable (datetime → str)."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


class SkillsServer:
    """REST API server for skills service.

    Provides endpoints for the bot to interact with skills,
    plus user management and runtime settings CRUD.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        api_secret: str | None = None,
        host: str = "0.0.0.0",  # nosec B104 - Intentional for Docker container
        port: int = 8080,
        user_manager: Any | None = None,
        settings_manager: Any | None = None,
    ):
        """Initialize the skills server.

        Args:
            registry: The skill registry.
            api_secret: Optional shared secret for authentication.
            host: Host to bind to.
            port: Port to listen on.
            user_manager: Optional UserManager for RBAC API.
            settings_manager: Optional SettingsManager for settings API.
        """
        self._registry = registry
        self._api_secret = api_secret
        self._host = host
        self._port = port
        self._user_manager = user_manager
        self._settings_manager = settings_manager
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

        log.info("skills_server_initialized", host=host, port=port)

    def _check_auth(self, request: web.Request) -> bool:
        """Check if request is authenticated.

        Args:
            request: The incoming request.

        Returns:
            True if authenticated or no secret required.
        """
        if not self._api_secret:
            return True

        provided = request.headers.get("X-API-Secret")
        return provided is not None and hmac.compare_digest(provided, self._api_secret)

    @staticmethod
    async def _read_json_object(request: web.Request) -> dict[str, Any]:
        """Read request JSON and ensure the payload is a JSON object."""
        try:
            data = await request.json()
        except (ValueError, JSONDecodeError) as err:
            raise ValueError("Invalid JSON body") from err
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    @staticmethod
    def _parse_int(raw: Any, field_name: str) -> int:
        """Parse an integer field and raise ValueError on invalid input."""
        try:
            return int(raw)
        except (TypeError, ValueError) as err:
            raise ValueError(f"Invalid integer for '{field_name}'") from err

    @web.middleware
    async def auth_middleware(
        self,
        request: web.Request,
        handler: Any,
    ) -> web.Response:
        """Middleware to check authentication.

        Args:
            request: The incoming request.
            handler: The route handler.

        Returns:
            Response from handler or 401 if unauthorized.
        """
        # Skip auth for health check
        if request.path == "/health":
            response: web.Response = await handler(request)
            return response

        if not self._check_auth(request):
            return web.json_response(
                {"error": "Unauthorized"},
                status=401,
            )

        response = await handler(request)
        return response

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint.

        Args:
            request: The incoming request.

        Returns:
            200 OK with status.
        """
        return web.json_response(
            {
                "status": "healthy",
                "skills_ready": len(self._registry.list_ready_skills()),
                "skills_total": self._registry.skill_count,
            }
        )

    async def handle_request(self, request: web.Request) -> web.Response:
        """Handle skill request endpoint.

        Args:
            request: The incoming request with skill request body.

        Returns:
            Response from the skill.
        """
        try:
            data = await self._read_json_object(request)
            skill_request = SkillRequest.from_dict(data)

            response = await self._registry.handle_request(skill_request)

            return web.json_response(response.to_dict())

        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            log.exception("handle_request_error")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
            )

    async def handle_heartbeat(self, request: web.Request) -> web.Response:
        """Heartbeat endpoint.

        Args:
            request: The incoming request with user_ids.

        Returns:
            List of heartbeat actions.
        """
        try:
            data = await self._read_json_object(request)
            user_ids = data.get("user_ids", [])

            actions = await self._registry.run_heartbeat(user_ids)

            return web.json_response(
                {
                    "actions": [a.to_dict() for a in actions],
                }
            )

        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            log.exception("heartbeat_error")
            return web.json_response(
                {"error": "Internal server error"},
                status=500,
            )

    async def handle_list_skills(self, request: web.Request) -> web.Response:
        """List skills endpoint.

        Args:
            request: The incoming request.

        Returns:
            List of skill metadata.
        """
        skills = self._registry.list_skills()
        return web.json_response(
            {
                "skills": [s.to_dict() for s in skills],
            }
        )

    async def handle_get_skill(self, request: web.Request) -> web.Response:
        """Get specific skill endpoint.

        Args:
            request: The incoming request.

        Returns:
            Skill metadata or 404.
        """
        name = request.match_info.get("name", "")
        skill = self._registry.get_skill(name)

        if skill is None:
            return web.json_response(
                {"error": "Skill not found"},
                status=404,
            )

        return web.json_response(skill.metadata.to_dict())

    async def handle_status(self, request: web.Request) -> web.Response:
        """Status endpoint.

        Args:
            request: The incoming request.

        Returns:
            Service status.
        """
        return web.json_response(self._registry.get_status_summary())

    async def handle_prompt_fragments(self, request: web.Request) -> web.Response:
        """Get prompt fragments endpoint.

        Args:
            request: The incoming request with user_id param.

        Returns:
            List of prompt fragments.
        """
        user_id = request.query.get("user_id", "")

        fragments = self._registry.get_system_prompt_fragments(user_id)

        return web.json_response(
            {
                "fragments": fragments,
            }
        )

    async def handle_intents(self, request: web.Request) -> web.Response:
        """List intents endpoint.

        Args:
            request: The incoming request.

        Returns:
            Mapping of intents to skills.
        """
        return web.json_response(
            {
                "intents": self._registry.list_intents(),
            }
        )

    # ------------------------------------------------------------------
    # User management API
    # ------------------------------------------------------------------

    async def handle_list_users(self, request: web.Request) -> web.Response:
        """GET /users — list users, optionally filtered by role."""
        if self._user_manager is None:
            return web.json_response({"error": "User management not configured"}, status=501)
        role = request.query.get("role")
        users = await self._user_manager.list_users(role_filter=role)
        return web.json_response({"users": [_serialise_record(u) for u in users]})

    async def handle_add_user(self, request: web.Request) -> web.Response:
        """POST /users — add a user."""
        if self._user_manager is None:
            return web.json_response({"error": "User management not configured"}, status=501)
        try:
            data = await self._read_json_object(request)
            user_id = self._parse_int(data.get("user_id"), "user_id")
            added_by = self._parse_int(data.get("added_by"), "added_by")
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        success = await self._user_manager.add_user(
            user_id=user_id,
            role=data.get("role", "user"),
            added_by=added_by,
        )
        if success:
            return web.json_response({"ok": True}, status=201)
        return web.json_response(
            {"ok": False, "error": "Permission denied or invalid role"}, status=403
        )

    async def handle_delete_user(self, request: web.Request) -> web.Response:
        """DELETE /users/{user_id} — remove a user."""
        if self._user_manager is None:
            return web.json_response({"error": "User management not configured"}, status=501)
        try:
            user_id = self._parse_int(request.match_info.get("user_id"), "user_id")
            removed_by = self._parse_int(request.query.get("removed_by", "0"), "removed_by")
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        success = await self._user_manager.remove_user(user_id=user_id, removed_by=removed_by)
        if success:
            return web.json_response({"ok": True})
        return web.json_response(
            {"ok": False, "error": "Permission denied or user not found"}, status=403
        )

    async def handle_patch_user_role(self, request: web.Request) -> web.Response:
        """PATCH /users/{user_id}/role — change a user's role."""
        if self._user_manager is None:
            return web.json_response({"error": "User management not configured"}, status=501)
        try:
            user_id = self._parse_int(request.match_info.get("user_id"), "user_id")
            data = await self._read_json_object(request)
            changed_by = self._parse_int(data.get("changed_by"), "changed_by")
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        success = await self._user_manager.set_role(
            user_id=user_id,
            new_role=data["role"],
            changed_by=changed_by,
        )
        if success:
            return web.json_response({"ok": True})
        return web.json_response(
            {"ok": False, "error": "Permission denied or invalid role"}, status=403
        )

    async def handle_audit_log(self, request: web.Request) -> web.Response:
        """GET /users/audit — recent audit log entries."""
        if self._user_manager is None:
            return web.json_response({"error": "User management not configured"}, status=501)
        try:
            limit = self._parse_int(request.query.get("limit", "50"), "limit")
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        entries = await self._user_manager.get_audit_log(limit=limit)
        return web.json_response({"entries": [_serialise_record(e) for e in entries]})

    # ------------------------------------------------------------------
    # Settings API
    # ------------------------------------------------------------------

    async def handle_list_settings(self, request: web.Request) -> web.Response:
        """GET /settings — list all settings."""
        if self._settings_manager is None:
            return web.json_response({"error": "Settings not configured"}, status=501)
        namespace = request.query.get("namespace")
        settings = await self._settings_manager.get_all(namespace=namespace)
        return web.json_response({"settings": settings})

    async def handle_get_setting(self, request: web.Request) -> web.Response:
        """GET /settings/{namespace}/{key} — get a specific setting."""
        if self._settings_manager is None:
            return web.json_response({"error": "Settings not configured"}, status=501)
        namespace = request.match_info["namespace"]
        key = request.match_info["key"]
        value = self._settings_manager.get(namespace, key)
        return web.json_response({"namespace": namespace, "key": key, "value": value})

    async def handle_put_setting(self, request: web.Request) -> web.Response:
        """PUT /settings/{namespace}/{key} — update a setting."""
        if self._settings_manager is None:
            return web.json_response({"error": "Settings not configured"}, status=501)
        namespace = request.match_info["namespace"]
        key = request.match_info["key"]
        try:
            data = await self._read_json_object(request)
            await self._settings_manager.set(
                namespace=namespace,
                key=key,
                value=data["value"],
                changed_by=self._parse_int(data.get("changed_by", 0), "changed_by"),
                data_type=data.get("data_type", "string"),
            )
            return web.json_response({"ok": True})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_delete_setting(self, request: web.Request) -> web.Response:
        """DELETE /settings/{namespace}/{key} — remove a setting override."""
        if self._settings_manager is None:
            return web.json_response({"error": "Settings not configured"}, status=501)
        namespace = request.match_info["namespace"]
        key = request.match_info["key"]
        try:
            deleted_by = self._parse_int(request.query.get("deleted_by", "0"), "deleted_by")
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        deleted = await self._settings_manager.delete(
            namespace=namespace, key=key, deleted_by=deleted_by
        )
        return web.json_response({"ok": True, "existed": deleted})

    # ------------------------------------------------------------------
    # Application factory
    # ------------------------------------------------------------------

    def create_app(self) -> web.Application:
        """Create the aiohttp application.

        Returns:
            The configured application.
        """
        app = web.Application(middlewares=[self.auth_middleware])

        # Core routes
        app.router.add_get("/health", self.handle_health)
        app.router.add_post("/handle", self.handle_request)
        app.router.add_post("/heartbeat", self.handle_heartbeat)
        app.router.add_get("/skills", self.handle_list_skills)
        app.router.add_get("/skills/{name}", self.handle_get_skill)
        app.router.add_get("/status", self.handle_status)
        app.router.add_get("/prompt-fragments", self.handle_prompt_fragments)
        app.router.add_get("/intents", self.handle_intents)

        # User management routes
        app.router.add_get("/users", self.handle_list_users)
        app.router.add_post("/users", self.handle_add_user)
        app.router.add_delete("/users/{user_id}", self.handle_delete_user)
        app.router.add_patch("/users/{user_id}/role", self.handle_patch_user_role)
        app.router.add_get("/users/audit", self.handle_audit_log)

        # Settings routes
        app.router.add_get("/settings", self.handle_list_settings)
        app.router.add_get("/settings/{namespace}/{key}", self.handle_get_setting)
        app.router.add_put("/settings/{namespace}/{key}", self.handle_put_setting)
        app.router.add_delete("/settings/{namespace}/{key}", self.handle_delete_setting)

        self._app = app
        return app

    async def start(self) -> None:
        """Start the server."""
        if self._app is None:
            self.create_app()

        if self._app is None:  # pragma: no cover - create_app() sets self._app
            raise RuntimeError("create_app() must be called first")
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        log.info("skills_server_started", host=self._host, port=self._port)

    async def stop(self) -> None:
        """Stop the server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            log.info("skills_server_stopped")


async def run_server(  # pragma: no cover — starts infinite loop
    registry: SkillRegistry,
    api_secret: str | None = None,
    host: str = "0.0.0.0",  # nosec B104 - Intentional for Docker container
    port: int = 8080,
) -> None:
    """Run the skills server.

    This is the main entry point for the skills service container.

    Args:
        registry: The skill registry.
        api_secret: Optional API secret.
        host: Host to bind to.
        port: Port to listen on.
    """
    server = SkillsServer(
        registry=registry,
        api_secret=api_secret,
        host=host,
        port=port,
    )

    await server.start()

    # Wait indefinitely
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


def main() -> None:  # pragma: no cover — CLI entry-point
    """Main entry point for the skills service."""
    from zetherion_ai.config import get_settings

    # Get configuration from environment
    api_secret = os.environ.get("SKILLS_API_SECRET")
    host = os.environ.get("SKILLS_HOST", "0.0.0.0")  # nosec B104 - Docker container
    port = int(os.environ.get("SKILLS_PORT", "8080"))

    # Create registry and register built-in skills
    registry = SkillRegistry()

    from zetherion_ai.skills.calendar import CalendarSkill
    from zetherion_ai.skills.dev_watcher import DevWatcherSkill
    from zetherion_ai.skills.health_analyzer import HealthAnalyzerSkill
    from zetherion_ai.skills.milestone import MilestoneSkill
    from zetherion_ai.skills.profile_skill import ProfileSkill
    from zetherion_ai.skills.task_manager import TaskManagerSkill
    from zetherion_ai.skills.update_checker import UpdateCheckerSkill

    settings = get_settings()
    registry.register(TaskManagerSkill())
    registry.register(CalendarSkill())
    registry.register(ProfileSkill())
    registry.register(HealthAnalyzerSkill())
    registry.register(DevWatcherSkill())
    registry.register(MilestoneSkill())
    registry.register(
        UpdateCheckerSkill(
            github_repo=settings.auto_update_repo,
            auto_apply=not settings.update_require_approval,
            enabled=settings.auto_update_enabled,
            updater_url=settings.updater_service_url,
            updater_secret=settings.updater_secret,
        )
    )

    # Conditional: YouTube Skills (require Postgres + InferenceBroker)
    _yt_storage = None
    if settings.postgres_dsn:
        try:
            from zetherion_ai.agent.inference import InferenceBroker
            from zetherion_ai.skills.youtube.intelligence import YouTubeIntelligenceSkill
            from zetherion_ai.skills.youtube.management import YouTubeManagementSkill
            from zetherion_ai.skills.youtube.storage import YouTubeStorage
            from zetherion_ai.skills.youtube.strategy import YouTubeStrategySkill

            _yt_broker = InferenceBroker()
            _yt_storage = YouTubeStorage(dsn=settings.postgres_dsn)
            # Pool is created async in init_and_run; skills call storage after initialize_all
            registry.register(YouTubeIntelligenceSkill(storage=_yt_storage, broker=_yt_broker))
            registry.register(YouTubeManagementSkill(storage=_yt_storage, broker=_yt_broker))
            registry.register(YouTubeStrategySkill(storage=_yt_storage, broker=_yt_broker))
            log.info("youtube_skills_registered")
        except Exception as e:
            log.warning("youtube_skills_registration_failed", error=str(e))

    # Conditional: Client Provisioning (requires Postgres for TenantManager)
    _tenant_manager = None
    if settings.postgres_dsn:
        from zetherion_ai.api.tenant import TenantManager
        from zetherion_ai.skills.client_provisioning import ClientProvisioningSkill

        _tenant_manager = TenantManager(dsn=settings.postgres_dsn)
        registry.register(ClientProvisioningSkill(tenant_manager=_tenant_manager))

    # Conditional: Fleet Insights (central instance only)
    if settings.telemetry_central_mode:
        from zetherion_ai.skills.fleet_insights import FleetInsightsSkill

        registry.register(FleetInsightsSkill())

    # Initialize all skills
    async def init_and_run() -> None:
        if _yt_storage is not None:
            await _yt_storage.initialize()
        if _tenant_manager is not None:
            await _tenant_manager.initialize()
        await registry.initialize_all()
        await run_server(registry, api_secret, host, port)

    try:
        asyncio.run(init_and_run())
    except KeyboardInterrupt:
        log.info("skills_server_shutdown")


if __name__ == "__main__":
    main()
