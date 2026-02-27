"""Unit tests for CRM read route handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.crm import handle_get_contacts, handle_get_interactions


@pytest_asyncio.fixture()
async def crm_routes_client():
    """aiohttp TestClient with tenant context injected."""
    tenant = {"tenant_id": "tenant-1", "name": "Test Tenant", "config": {}}
    tenant_manager = AsyncMock()

    tenant_manager.list_contacts = AsyncMock(
        return_value=[
            {
                "contact_id": "contact-1",
                "tenant_id": "tenant-1",
                "name": "Alex",
                "email": "alex@example.com",
                "updated_at": datetime.now(UTC),
            }
        ]
    )
    tenant_manager.get_interactions = AsyncMock(
        return_value=[
            {
                "interaction_id": "interaction-1",
                "tenant_id": "tenant-1",
                "interaction_type": "chat",
                "created_at": datetime.now(UTC),
            }
        ]
    )

    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["tenant"] = tenant
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["tenant_manager"] = tenant_manager
    app.router.add_get("/api/v1/crm/contacts", handle_get_contacts)
    app.router.add_get("/api/v1/crm/interactions", handle_get_interactions)

    async with TestClient(TestServer(app)) as client:
        yield client, tenant_manager


@pytest.mark.asyncio
async def test_get_contacts_returns_rows(crm_routes_client) -> None:
    client, tenant_manager = crm_routes_client
    response = await client.get("/api/v1/crm/contacts")
    assert response.status == 200
    payload = await response.json()
    assert payload["count"] == 1
    assert payload["contacts"][0]["contact_id"] == "contact-1"
    tenant_manager.list_contacts.assert_awaited_once_with("tenant-1", limit=50, email=None)


@pytest.mark.asyncio
async def test_get_contacts_applies_email_and_limit_cap(crm_routes_client) -> None:
    client, tenant_manager = crm_routes_client
    response = await client.get("/api/v1/crm/contacts?email=alex@example.com&limit=999")
    assert response.status == 200
    tenant_manager.list_contacts.assert_awaited_once_with(
        "tenant-1",
        limit=200,
        email="alex@example.com",
    )


@pytest.mark.asyncio
async def test_get_interactions_returns_rows(crm_routes_client) -> None:
    client, tenant_manager = crm_routes_client
    response = await client.get("/api/v1/crm/interactions")
    assert response.status == 200
    payload = await response.json()
    assert payload["count"] == 1
    assert payload["interactions"][0]["interaction_id"] == "interaction-1"
    tenant_manager.get_interactions.assert_awaited_once_with(
        "tenant-1",
        contact_id=None,
        session_id=None,
        interaction_type=None,
        limit=50,
    )


@pytest.mark.asyncio
async def test_get_interactions_applies_filters(crm_routes_client) -> None:
    client, tenant_manager = crm_routes_client
    response = await client.get(
        "/api/v1/crm/interactions?contact_id=contact-1&session_id=session-1"
        "&interaction_type=message_extraction&limit=500"
    )
    assert response.status == 200
    tenant_manager.get_interactions.assert_awaited_once_with(
        "tenant-1",
        contact_id="contact-1",
        session_id="session-1",
        interaction_type="message_extraction",
        limit=200,
    )
