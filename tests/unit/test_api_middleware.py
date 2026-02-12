"""Tests for API middleware (CORS, auth routing, rate limiting)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.middleware import (
    RateLimiter,
    create_auth_middleware,
    create_cors_middleware,
)

# ---------------------------------------------------------------------------
# RateLimiter unit tests (no HTTP needed)
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """Tests for the token-bucket rate limiter."""

    def test_allows_requests_within_limit(self):
        """Requests within the RPM limit are allowed."""
        rl = RateLimiter()
        assert rl.check("t1", rpm_limit=60) is True
        assert rl.check("t1", rpm_limit=60) is True

    def test_blocks_after_bucket_empty(self):
        """Once tokens are exhausted, requests are blocked."""
        rl = RateLimiter()
        # Drain the bucket (default starts at 60 tokens)
        for _ in range(60):
            assert rl.check("t1", rpm_limit=60) is True
        assert rl.check("t1", rpm_limit=60) is False

    def test_separate_buckets_per_tenant(self):
        """Each tenant has its own independent bucket."""
        rl = RateLimiter()
        for _ in range(60):
            rl.check("t1", rpm_limit=60)
        assert rl.check("t1", rpm_limit=60) is False
        # t2 is still full
        assert rl.check("t2", rpm_limit=60) is True

    def test_low_rpm_limit(self):
        """Very low RPM limit (1 RPM) still works correctly."""
        rl = RateLimiter()
        # First request uses the starting tokens (which equal the rpm_limit)
        assert rl.check("t1", rpm_limit=1) is True
        assert rl.check("t1", rpm_limit=1) is False


# ---------------------------------------------------------------------------
# CORS middleware tests (HTTP)
# ---------------------------------------------------------------------------


class TestCORSMiddleware:
    """Tests for CORS middleware using aiohttp TestClient."""

    @pytest.fixture
    def make_app(self):
        """Factory for a minimal app with the CORS middleware."""

        def _make(allowed_origins: list[str] | None = None):
            middlewares = []
            if allowed_origins is not None:
                middlewares.append(create_cors_middleware(allowed_origins))

            app = web.Application(middlewares=middlewares)

            async def hello(request: web.Request) -> web.Response:
                return web.json_response({"ok": True})

            app.router.add_get("/test", hello)
            return app

        return _make

    @pytest.mark.asyncio
    async def test_preflight_returns_204(self, make_app):
        """OPTIONS request returns 204 with CORS headers."""
        app = make_app(["https://example.com"])
        async with TestClient(TestServer(app)) as client:
            resp = await client.options("/test", headers={"Origin": "https://example.com"})
            assert resp.status == 204
            assert resp.headers["Access-Control-Allow-Origin"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_cors_headers_on_normal_request(self, make_app):
        """GET request includes CORS headers when origin matches."""
        app = make_app(["https://example.com"])
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/test", headers={"Origin": "https://example.com"})
            assert resp.status == 200
            assert resp.headers["Access-Control-Allow-Origin"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_cors_no_headers_for_unknown_origin(self, make_app):
        """GET request from unknown origin gets no CORS headers."""
        app = make_app(["https://example.com"])
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/test", headers={"Origin": "https://evil.com"})
            assert resp.status == 200
            assert "Access-Control-Allow-Origin" not in resp.headers

    @pytest.mark.asyncio
    async def test_cors_wildcard(self, make_app):
        """Wildcard origin allows any origin."""
        app = make_app(["*"])
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/test", headers={"Origin": "https://anything.com"})
            assert resp.headers["Access-Control-Allow-Origin"] == "https://anything.com"


# ---------------------------------------------------------------------------
# Auth middleware tests (HTTP)
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Tests for auth middleware routing logic."""

    JWT_SECRET = "test-jwt-secret"

    def _make_app(self, tenant_manager=None):
        """Create a minimal app with auth middleware."""
        app = web.Application(middlewares=[create_auth_middleware(self.JWT_SECRET)])

        if tenant_manager is not None:
            app["tenant_manager"] = tenant_manager

        async def health(request: web.Request) -> web.Response:
            return web.json_response({"status": "healthy"})

        async def protected(request: web.Request) -> web.Response:
            tenant = request.get("tenant", {})
            return web.json_response({"tenant_id": str(tenant.get("tenant_id", ""))})

        async def chat(request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        app.router.add_get("/api/v1/health", health)
        app.router.add_get("/api/v1/tenants", protected)
        app.router.add_post("/api/v1/chat", chat)

        return app

    @pytest.mark.asyncio
    async def test_public_path_no_auth(self):
        """Health endpoint requires no auth."""
        app = self._make_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_no_tenant_manager_503(self):
        """Missing tenant_manager returns 503."""
        app = self._make_app(tenant_manager=None)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/tenants")
            assert resp.status == 503

    @pytest.mark.asyncio
    async def test_api_key_missing_401(self):
        """Missing X-API-Key returns 401."""
        tm = AsyncMock()
        app = self._make_app(tenant_manager=tm)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/tenants")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_api_key_invalid_401(self):
        """Invalid X-API-Key returns 401."""
        tm = AsyncMock()
        tm.authenticate_api_key = AsyncMock(return_value=None)
        app = self._make_app(tenant_manager=tm)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/tenants", headers={"X-API-Key": "sk_live_invalid"})
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_api_key_valid(self):
        """Valid X-API-Key authenticates and attaches tenant."""
        tm = AsyncMock()
        tm.authenticate_api_key = AsyncMock(
            return_value={"tenant_id": "abc-123", "is_active": True}
        )
        app = self._make_app(tenant_manager=tm)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/tenants", headers={"X-API-Key": "sk_live_valid"})
            assert resp.status == 200
            data = await resp.json()
            assert data["tenant_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_session_auth_missing_bearer(self):
        """Chat endpoint without Bearer token returns 401."""
        tm = AsyncMock()
        app = self._make_app(tenant_manager=tm)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/chat")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_session_auth_invalid_token(self):
        """Chat endpoint with invalid JWT returns 401."""
        tm = AsyncMock()
        app = self._make_app(tenant_manager=tm)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/v1/chat",
                headers={"Authorization": "Bearer zt_sess_invalid.jwt.token"},
            )
            assert resp.status == 401
