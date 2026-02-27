"""Unit tests for CGS runtime routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.cgs_gateway.models import AuthPrincipal
from zetherion_ai.cgs_gateway.routes._utils import fingerprint_payload
from zetherion_ai.cgs_gateway.routes.runtime import register_runtime_routes


def _app_with_runtime_routes(storage: MagicMock, public_client: MagicMock) -> web.Application:
    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["principal"] = AuthPrincipal(
            sub="user-1",
            tenant_id="tenant-a",
            roles=["operator"],
            scopes=["cgs:internal"],
            claims={},
        )
        request["request_id"] = "req_test_1"
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["cgs_storage"] = storage
    app["cgs_public_client"] = public_client
    register_runtime_routes(app)
    return app


@pytest.mark.asyncio
async def test_create_conversation_success() -> None:
    storage = MagicMock()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.create_conversation = AsyncMock(
        return_value={"conversation_id": "cgs_conv_123", "created_at": "2026-02-27T00:00:00Z"}
    )
    storage.save_idempotency_record = AsyncMock()

    public_client = MagicMock()
    public_client.request_json = AsyncMock(
        return_value=(
            201,
            {
                "session_id": "11111111-1111-1111-1111-111111111111",
                "session_token": "zt_sess_abc",
                "created_at": "2026-02-27T00:00:00Z",
                "expires_at": "2026-02-28T00:00:00Z",
            },
            {},
        )
    )

    app = _app_with_runtime_routes(storage, public_client)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations",
            json={
                "tenant_id": "tenant-a",
                "app_user_id": "app-user-1",
                "external_user_id": "ext-1",
                "metadata": {"app": "portal"},
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["error"] is None
        assert body["data"]["conversation_id"] == "cgs_conv_123"
        assert body["data"]["session_id"] == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_create_conversation_idempotent_replay() -> None:
    payload = {
        "tenant_id": "tenant-a",
        "app_user_id": "app-user-1",
        "external_user_id": "ext-1",
        "metadata": {"app": "portal"},
    }
    storage = MagicMock()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "is_active": True,
            "zetherion_api_key": "sk_live_abc",
        }
    )
    storage.get_idempotency_record = AsyncMock(
        return_value={
            "request_fingerprint": fingerprint_payload(payload),
            "response_status": 200,
            "response_body": {"request_id": "req_old", "data": {"ok": True}, "error": None},
        }
    )

    public_client = MagicMock()
    public_client.request_json = AsyncMock()

    app = _app_with_runtime_routes(storage, public_client)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations",
            headers={"Idempotency-Key": "idem-1"},
            json=payload,
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["request_id"] == "req_old"
        assert body["data"]["ok"] is True
        assert resp.headers["X-Idempotent-Replay"] == "true"
        public_client.request_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_message_success() -> None:
    storage = MagicMock()
    storage.get_conversation = AsyncMock(
        return_value={
            "conversation_id": "cgs_conv_123",
            "cgs_tenant_id": "tenant-a",
            "zetherion_session_id": "11111111-1111-1111-1111-111111111111",
            "zetherion_session_token": "zt_sess_token",
            "zetherion_api_key": "sk_live_abc",
            "is_active": True,
            "is_closed": False,
        }
    )
    storage.get_idempotency_record = AsyncMock(return_value=None)
    storage.save_idempotency_record = AsyncMock()

    public_client = MagicMock()
    public_client.request_json = AsyncMock(
        return_value=(200, {"message_id": "m1", "content": "hello"}, {})
    )

    app = _app_with_runtime_routes(storage, public_client)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/service/ai/v1/conversations/cgs_conv_123/messages",
            json={"message": "hi", "metadata": {}},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["error"] is None
        assert body["data"]["message_id"] == "m1"
