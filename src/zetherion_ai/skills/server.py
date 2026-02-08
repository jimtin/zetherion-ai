"""REST API server for the skills service.

This server runs in its own Docker container and provides:
- /health - Health check endpoint
- /handle - Handle skill requests
- /heartbeat - Trigger heartbeat cycle
- /skills - List available skills
- /status - Get service status
"""

import asyncio
import os
from typing import Any

from aiohttp import web

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.registry import SkillRegistry

log = get_logger("zetherion_ai.skills.server")


class SkillsServer:
    """REST API server for skills service.

    Provides endpoints for the bot to interact with skills.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        api_secret: str | None = None,
        host: str = "0.0.0.0",  # nosec B104 - Intentional for Docker container
        port: int = 8080,
    ):
        """Initialize the skills server.

        Args:
            registry: The skill registry.
            api_secret: Optional shared secret for authentication.
            host: Host to bind to.
            port: Port to listen on.
        """
        self._registry = registry
        self._api_secret = api_secret
        self._host = host
        self._port = port
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
        return provided == self._api_secret

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
            data = await request.json()
            skill_request = SkillRequest.from_dict(data)

            response = await self._registry.handle_request(skill_request)

            return web.json_response(response.to_dict())

        except Exception as e:
            log.error("handle_request_error", error=str(e))
            return web.json_response(
                {"error": str(e)},
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
            data = await request.json()
            user_ids = data.get("user_ids", [])

            actions = await self._registry.run_heartbeat(user_ids)

            return web.json_response(
                {
                    "actions": [a.to_dict() for a in actions],
                }
            )

        except Exception as e:
            log.error("heartbeat_error", error=str(e))
            return web.json_response(
                {"error": str(e)},
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

    def create_app(self) -> web.Application:
        """Create the aiohttp application.

        Returns:
            The configured application.
        """
        app = web.Application(middlewares=[self.auth_middleware])

        # Add routes
        app.router.add_get("/health", self.handle_health)
        app.router.add_post("/handle", self.handle_request)
        app.router.add_post("/heartbeat", self.handle_heartbeat)
        app.router.add_get("/skills", self.handle_list_skills)
        app.router.add_get("/skills/{name}", self.handle_get_skill)
        app.router.add_get("/status", self.handle_status)
        app.router.add_get("/prompt-fragments", self.handle_prompt_fragments)
        app.router.add_get("/intents", self.handle_intents)

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


async def run_server(
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


def main() -> None:
    """Main entry point for the skills service."""
    from zetherion_ai.config import get_settings

    get_settings()

    # Get configuration from environment
    api_secret = os.environ.get("SKILLS_API_SECRET")
    host = os.environ.get("SKILLS_HOST", "0.0.0.0")  # nosec B104 - Docker container
    port = int(os.environ.get("SKILLS_PORT", "8080"))

    # Create registry and register built-in skills
    registry = SkillRegistry()

    from zetherion_ai.skills.calendar import CalendarSkill
    from zetherion_ai.skills.profile_skill import ProfileSkill
    from zetherion_ai.skills.task_manager import TaskManagerSkill

    registry.register(TaskManagerSkill())
    registry.register(CalendarSkill())
    registry.register(ProfileSkill())

    # Initialize all skills
    async def init_and_run() -> None:
        await registry.initialize_all()
        await run_server(registry, api_secret, host, port)

    try:
        asyncio.run(init_and_run())
    except KeyboardInterrupt:
        log.info("skills_server_shutdown")


if __name__ == "__main__":
    main()
