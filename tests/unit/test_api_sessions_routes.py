"""Unit tests for public API session route handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.sessions import (
    _serialise,
    handle_create_session,
    handle_delete_session,
    handle_get_session,
)


@pytest_asyncio.fixture()
async def sessions_routes_client():
    tenant = {"tenant_id": "tenant-1"}
    tenant_manager = AsyncMock()
    tenant_manager.create_session = AsyncMock(
        return_value={
            "session_id": uuid4(),
            "tenant_id": uuid4(),
            "external_user_id": "user-1",
            "created_at": datetime.now(tz=UTC),
            "metadata": {"source": "tests"},
        }
    )
    tenant_manager.get_session = AsyncMock(return_value=None)
    tenant_manager.delete_session = AsyncMock(return_value=False)

    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["tenant"] = tenant
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["tenant_manager"] = tenant_manager
    app["jwt_secret"] = "unit-test-secret"
    app.router.add_post("/api/v1/sessions", handle_create_session)
    app.router.add_get("/api/v1/sessions/{session_id}", handle_get_session)
    app.router.add_delete("/api/v1/sessions/{session_id}", handle_delete_session)

    async with TestClient(TestServer(app)) as client:
        yield client, tenant_manager


def test_serialise_converts_datetime_and_uuid() -> None:
    now = datetime.now(tz=UTC)
    value = uuid4()
    out = _serialise({"ts": now, "id": value, "name": "ok"})
    assert out["ts"] == now.isoformat()
    assert out["id"] == str(value)
    assert out["name"] == "ok"


@pytest.mark.asyncio
async def test_handle_create_session_with_payload(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    response = await client.post(
        "/api/v1/sessions",
        json={"external_user_id": "external-1", "metadata": {"plan": "pro"}},
    )

    assert response.status == 201
    body = await response.json()
    assert body["external_user_id"] == "user-1"
    assert isinstance(body["session_token"], str)
    tenant_manager.create_session.assert_awaited_once_with(
        tenant_id="tenant-1",
        external_user_id="external-1",
        metadata={"plan": "pro"},
    )


@pytest.mark.asyncio
async def test_handle_create_session_with_invalid_json_defaults_empty_body(
    sessions_routes_client,
) -> None:
    client, tenant_manager = sessions_routes_client
    response = await client.post(
        "/api/v1/sessions",
        data="{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 201
    tenant_manager.create_session.assert_awaited_once_with(
        tenant_id="tenant-1",
        external_user_id=None,
        metadata={},
    )


@pytest.mark.asyncio
async def test_handle_get_session_not_found(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    tenant_manager.get_session.return_value = None

    response = await client.get("/api/v1/sessions/session-404")
    assert response.status == 404
    assert await response.json() == {"error": "Session not found"}


@pytest.mark.asyncio
async def test_handle_get_session_denies_cross_tenant(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    tenant_manager.get_session.return_value = {
        "session_id": "session-1",
        "tenant_id": "tenant-other",
    }

    response = await client.get("/api/v1/sessions/session-1")
    assert response.status == 404
    assert await response.json() == {"error": "Session not found"}


@pytest.mark.asyncio
async def test_handle_get_session_success(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    now = datetime.now(tz=UTC)
    tenant_manager.get_session.return_value = {
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "created_at": now,
    }

    response = await client.get("/api/v1/sessions/session-1")
    assert response.status == 200
    body = await response.json()
    assert body["session_id"] == "session-1"
    assert body["tenant_id"] == "tenant-1"
    assert body["created_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_handle_delete_session_not_found(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    tenant_manager.delete_session.return_value = False

    response = await client.delete("/api/v1/sessions/session-404")
    assert response.status == 404
    assert await response.json() == {"error": "Session not found"}
    tenant_manager.delete_session.assert_awaited_once_with(
        session_id="session-404",
        tenant_id="tenant-1",
    )


@pytest.mark.asyncio
async def test_handle_delete_session_success(sessions_routes_client) -> None:
    client, tenant_manager = sessions_routes_client
    tenant_manager.delete_session.return_value = True

    response = await client.delete("/api/v1/sessions/session-1")
    assert response.status == 200
    assert await response.json() == {"ok": True}
