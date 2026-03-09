"""Unit tests for sandbox profile and rule route handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.test_mode import (
    handle_create_test_profile,
    handle_create_test_rule,
    handle_delete_test_profile,
    handle_delete_test_rule,
    handle_get_test_profile,
    handle_list_test_profiles,
    handle_list_test_rules,
    handle_patch_test_profile,
    handle_patch_test_rule,
    handle_preview_test_profile,
)


@pytest_asyncio.fixture()
async def test_mode_client():
    tenant = {"tenant_id": "tenant-1", "execution_mode": "live"}
    now = datetime.now(tz=UTC)
    tenant_manager = AsyncMock()
    tenant_manager.list_test_profiles = AsyncMock(return_value=[])
    tenant_manager.create_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Default sandbox",
            "description": "Primary profile",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.get_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Default sandbox",
            "description": "Primary profile",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.update_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Updated sandbox",
            "description": "Primary profile",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.delete_test_profile = AsyncMock(return_value=True)
    tenant_manager.list_test_rules = AsyncMock(return_value=[])
    tenant_manager.create_test_rule = AsyncMock(
        return_value={
            "rule_id": "rule-1",
            "tenant_id": "tenant-1",
            "profile_id": "profile-1",
            "priority": 10,
            "method": "POST",
            "route_pattern": "/api/v1/chat",
            "enabled": True,
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "Simulated pricing reply", "model": "sandbox-simulated"}},
            "latency_ms": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.update_test_rule = AsyncMock(
        return_value={
            "rule_id": "rule-1",
            "tenant_id": "tenant-1",
            "profile_id": "profile-1",
            "priority": 5,
            "method": "POST",
            "route_pattern": "/api/v1/chat",
            "enabled": True,
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "Updated pricing reply", "model": "sandbox-simulated"}},
            "latency_ms": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.delete_test_rule = AsyncMock(return_value=True)
    tenant_manager.resolve_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Default sandbox",
            "is_default": True,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    tenant_manager.list_subject_memories = AsyncMock(return_value=[])

    @web.middleware
    async def inject_tenant(request: web.Request, handler):
        request["tenant"] = tenant
        request["execution_mode"] = "live"
        return await handler(request)

    app = web.Application(middlewares=[inject_tenant])
    app["tenant_manager"] = tenant_manager
    app.router.add_get("/api/v1/test/profiles", handle_list_test_profiles)
    app.router.add_post("/api/v1/test/profiles", handle_create_test_profile)
    app.router.add_get("/api/v1/test/profiles/{profile_id}", handle_get_test_profile)
    app.router.add_patch("/api/v1/test/profiles/{profile_id}", handle_patch_test_profile)
    app.router.add_delete("/api/v1/test/profiles/{profile_id}", handle_delete_test_profile)
    app.router.add_get("/api/v1/test/profiles/{profile_id}/rules", handle_list_test_rules)
    app.router.add_post("/api/v1/test/profiles/{profile_id}/rules", handle_create_test_rule)
    app.router.add_patch("/api/v1/test/profiles/{profile_id}/rules/{rule_id}", handle_patch_test_rule)
    app.router.add_delete("/api/v1/test/profiles/{profile_id}/rules/{rule_id}", handle_delete_test_rule)
    app.router.add_post("/api/v1/test/profiles/{profile_id}/preview", handle_preview_test_profile)

    async with TestClient(TestServer(app)) as client:
        yield client, tenant_manager


@pytest.mark.asyncio
async def test_create_profile_and_patch(test_mode_client) -> None:
    client, tenant_manager = test_mode_client

    create_resp = await client.post(
        "/api/v1/test/profiles",
        json={"name": "Default sandbox", "description": "Primary profile", "is_default": True},
    )
    assert create_resp.status == 201
    create_body = await create_resp.json()
    assert create_body["profile_id"] == "profile-1"

    patch_resp = await client.patch(
        "/api/v1/test/profiles/profile-1",
        json={"name": "Updated sandbox"},
    )
    assert patch_resp.status == 200
    patch_body = await patch_resp.json()
    assert patch_body["name"] == "Updated sandbox"

    tenant_manager.create_test_profile.assert_awaited_once()
    tenant_manager.update_test_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rule_and_preview(test_mode_client) -> None:
    client, tenant_manager = test_mode_client
    tenant_manager.list_test_rules = AsyncMock(
        return_value=[
            {
                "rule_id": "rule-1",
                "tenant_id": "tenant-1",
                "profile_id": "profile-1",
                "priority": 10,
                "method": "POST",
                "route_pattern": "/api/v1/chat",
                "enabled": True,
                "match": {"body_contains": ["price"]},
                "response": {
                    "json_body": {
                        "content": "Simulated pricing reply",
                        "model": "sandbox-simulated",
                    }
                },
                "latency_ms": 0,
            }
        ]
    )

    create_resp = await client.post(
        "/api/v1/test/profiles/profile-1/rules",
        json={
            "priority": 10,
            "method": "POST",
            "route_pattern": "/api/v1/chat",
            "match": {"body_contains": ["price"]},
            "response": {"json_body": {"content": "Simulated pricing reply"}},
        },
    )
    assert create_resp.status == 201

    preview_resp = await client.post(
        "/api/v1/test/profiles/profile-1/preview",
        json={
            "route": "/api/v1/chat",
            "method": "POST",
            "body": {"message": "Can I get a price?"},
            "history": [],
            "session": {"memory_subject_id": "visitor-1", "conversation_summary": ""},
        },
    )
    assert preview_resp.status == 200
    preview_body = await preview_resp.json()
    assert preview_body["matched_rule_id"] == "rule-1"
    assert preview_body["chat_result"]["content"] == "Simulated pricing reply"


@pytest.mark.asyncio
async def test_profile_list_get_and_delete_routes(test_mode_client) -> None:
    client, tenant_manager = test_mode_client

    tenant_manager.list_test_profiles = AsyncMock(
        return_value=[
            {
                "profile_id": "profile-1",
                "tenant_id": "tenant-1",
                "name": "Default sandbox",
                "description": "Primary profile",
                "is_default": True,
                "is_active": True,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        ]
    )

    list_resp = await client.get("/api/v1/test/profiles")
    assert list_resp.status == 200
    list_body = await list_resp.json()
    assert list_body["count"] == 1

    get_resp = await client.get("/api/v1/test/profiles/profile-1")
    assert get_resp.status == 200

    tenant_manager.list_test_rules = AsyncMock(
        return_value=[
            {
                "rule_id": "rule-1",
                "tenant_id": "tenant-1",
                "profile_id": "profile-1",
                "priority": 10,
                "method": "POST",
                "route_pattern": "/api/v1/chat",
                "enabled": True,
                "match": {},
                "response": {"preset_id": "default"},
                "latency_ms": 0,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        ]
    )
    list_rules_resp = await client.get("/api/v1/test/profiles/profile-1/rules")
    assert list_rules_resp.status == 200
    list_rules_body = await list_rules_resp.json()
    assert list_rules_body["count"] == 1

    delete_rule_resp = await client.delete("/api/v1/test/profiles/profile-1/rules/rule-1")
    assert delete_rule_resp.status == 200
    assert await delete_rule_resp.json() == {"ok": True}

    delete_resp = await client.delete("/api/v1/test/profiles/profile-1")
    assert delete_resp.status == 200
    assert await delete_resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_profile_route_validation_and_not_found_paths(test_mode_client) -> None:
    client, tenant_manager = test_mode_client

    create_resp = await client.post("/api/v1/test/profiles", json={"description": "missing name"})
    assert create_resp.status == 400

    tenant_manager.get_test_profile = AsyncMock(return_value=None)
    tenant_manager.update_test_profile = AsyncMock(return_value=None)
    tenant_manager.delete_test_profile = AsyncMock(return_value=False)

    get_resp = await client.get("/api/v1/test/profiles/profile-missing")
    assert get_resp.status == 404

    patch_resp = await client.patch("/api/v1/test/profiles/profile-missing", json={"name": "x"})
    assert patch_resp.status == 404

    delete_resp = await client.delete("/api/v1/test/profiles/profile-missing")
    assert delete_resp.status == 404


@pytest.mark.asyncio
async def test_rule_route_validation_and_not_found_paths(test_mode_client) -> None:
    client, tenant_manager = test_mode_client

    tenant_manager.get_test_profile = AsyncMock(return_value=None)
    list_resp = await client.get("/api/v1/test/profiles/profile-missing/rules")
    assert list_resp.status == 404

    create_missing_pattern = await client.post(
        "/api/v1/test/profiles/profile-missing/rules",
        json={"method": "POST"},
    )
    assert create_missing_pattern.status == 404

    tenant_manager.get_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Default sandbox",
            "is_default": True,
            "is_active": True,
            "created_at": datetime.now(tz=UTC),
            "updated_at": datetime.now(tz=UTC),
        }
    )
    tenant_manager.update_test_rule = AsyncMock(return_value=None)
    tenant_manager.delete_test_rule = AsyncMock(return_value=False)

    create_resp = await client.post(
        "/api/v1/test/profiles/profile-1/rules",
        json={"response": {"json_body": {"content": "x"}}},
    )
    assert create_resp.status == 400

    create_bad_match = await client.post(
        "/api/v1/test/profiles/profile-1/rules",
        json={"route_pattern": "/api/v1/chat", "match": "bad"},
    )
    assert create_bad_match.status == 400

    create_bad_response = await client.post(
        "/api/v1/test/profiles/profile-1/rules",
        json={"route_pattern": "/api/v1/chat", "response": "bad"},
    )
    assert create_bad_response.status == 400

    patch_bad_match = await client.patch(
        "/api/v1/test/profiles/profile-1/rules/rule-1",
        json={"match": "bad"},
    )
    assert patch_bad_match.status == 400

    patch_bad_response = await client.patch(
        "/api/v1/test/profiles/profile-1/rules/rule-1",
        json={"response": "bad"},
    )
    assert patch_bad_response.status == 400

    patch_not_found = await client.patch(
        "/api/v1/test/profiles/profile-1/rules/rule-missing",
        json={"priority": 9},
    )
    assert patch_not_found.status == 404

    delete_not_found = await client.delete("/api/v1/test/profiles/profile-1/rules/rule-missing")
    assert delete_not_found.status == 404


@pytest.mark.asyncio
async def test_preview_validation_and_profile_not_found(test_mode_client) -> None:
    client, tenant_manager = test_mode_client

    tenant_manager.get_test_profile = AsyncMock(return_value=None)
    missing_resp = await client.post("/api/v1/test/profiles/profile-missing/preview", json={})
    assert missing_resp.status == 404

    tenant_manager.get_test_profile = AsyncMock(
        return_value={
            "profile_id": "profile-1",
            "tenant_id": "tenant-1",
            "name": "Default sandbox",
            "is_default": True,
            "is_active": True,
            "created_at": datetime.now(tz=UTC),
            "updated_at": datetime.now(tz=UTC),
        }
    )

    bad_body = await client.post(
        "/api/v1/test/profiles/profile-1/preview",
        json={"body": "bad"},
    )
    assert bad_body.status == 400

    bad_session = await client.post(
        "/api/v1/test/profiles/profile-1/preview",
        json={"body": {}, "session": "bad"},
    )
    assert bad_session.status == 400

    bad_history = await client.post(
        "/api/v1/test/profiles/profile-1/preview",
        json={"body": {}, "session": {}, "history": "bad"},
    )
    assert bad_history.status == 400
