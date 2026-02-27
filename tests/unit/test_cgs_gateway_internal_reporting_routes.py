"""Unit tests for CGS internal + reporting routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.routes.internal import register_internal_routes
from zetherion_ai.cgs_gateway.routes.reporting import register_reporting_routes


@pytest.fixture
def app_with_internal_and_reporting() -> web.Application:
    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["principal"] = AuthPrincipal(
            sub="operator-1",
            tenant_id=None,
            roles=["operator"],
            scopes=["cgs:internal"],
            claims={},
        )
        request["request_id"] = "req_test_internal"
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["cgs_storage"] = MagicMock()
    app["cgs_public_client"] = MagicMock()
    app["cgs_skills_client"] = MagicMock()
    register_internal_routes(app)
    register_reporting_routes(app)
    return app


@pytest.mark.asyncio
async def test_internal_create_tenant_success(
    app_with_internal_and_reporting: web.Application,
) -> None:
    app = app_with_internal_and_reporting
    app["cgs_skills_client"].handle_intent = AsyncMock(
        return_value=(
            200,
            {
                "success": True,
                "data": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "api_key": "sk_live_new",
                },
            },
        )
    )
    app["cgs_storage"].upsert_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant-a.example",
            "key_version": 1,
        }
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/internal/tenants",
            json={
                "cgs_tenant_id": "tenant-a",
                "name": "Tenant A",
                "domain": "tenant-a.example",
                "config": {"tone": "formal"},
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["error"] is None
        assert body["data"]["cgs_tenant_id"] == "tenant-a"
        assert body["data"]["api_key"] == "sk_live_new"


@pytest.mark.asyncio
async def test_reporting_contacts_success(app_with_internal_and_reporting: web.Application) -> None:
    app = app_with_internal_and_reporting
    app["cgs_storage"].get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_existing",
        }
    )
    app["cgs_public_client"].request_json = AsyncMock(
        return_value=(
            200,
            {"contacts": [{"contact_id": "c1"}], "count": 1},
            {},
        )
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/service/ai/v1/tenants/tenant-a/crm/contacts")
        assert resp.status == 200
        body = await resp.json()
        assert body["error"] is None
        assert body["data"]["count"] == 1
        assert body["data"]["contacts"][0]["contact_id"] == "c1"
