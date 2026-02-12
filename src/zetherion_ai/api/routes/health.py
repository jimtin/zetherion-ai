"""Health check endpoint for the public API."""

from aiohttp import web


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/v1/health — no auth required."""
    return web.json_response({"status": "healthy", "version": "0.1.0"})
