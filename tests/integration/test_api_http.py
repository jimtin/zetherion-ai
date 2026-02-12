"""HTTP integration tests for the Public API server.

Exercises real HTTP communication with the PublicAPIServer using
``aiohttp.test_utils.TestClient`` pointed at an in-process TestServer.
The TenantManager is replaced with an AsyncMock so no Postgres is needed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.auth import create_session_token, generate_api_key
from zetherion_ai.api.middleware import (
    RateLimiter,
    create_auth_middleware,
    create_cors_middleware,
    create_rate_limit_middleware,
)
from zetherion_ai.api.routes.chat import handle_chat, handle_chat_history, handle_chat_stream
from zetherion_ai.api.routes.health import handle_health
from zetherion_ai.api.routes.sessions import (
    handle_create_session,
    handle_delete_session,
    handle_get_session,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_SECRET = "integration-test-jwt-secret"
TENANT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


def _make_tenant(tenant_id: str = TENANT_ID, *, active: bool = True, rpm: int = 60) -> dict:
    """Build a fake tenant dict."""
    return {
        "tenant_id": tenant_id,
        "name": "Test Tenant",
        "domain": "example.com",
        "is_active": active,
        "rate_limit_rpm": rpm,
        "config": {},
    }


def _make_session(
    session_id: str = SESSION_ID,
    tenant_id: str = TENANT_ID,
) -> dict:
    """Build a fake session dict."""
    now = datetime.now(UTC)
    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "external_user_id": None,
        "created_at": now,
        "last_active": now,
        "expires_at": now,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_message(
    role: str = "user",
    content: str = "Hello",
    session_id: str = SESSION_ID,
    tenant_id: str = TENANT_ID,
) -> dict:
    """Build a fake chat message dict."""
    now = datetime.now(UTC)
    return {
        "message_id": str(uuid.uuid4()),
        "session_id": session_id,
        "tenant_id": tenant_id,
        "role": role,
        "content": content,
        "created_at": now,
    }


def _build_app(
    tenant_manager: AsyncMock, *, inference_broker: AsyncMock | None = None
) -> web.Application:
    """Build the full public API app with mocked TenantManager."""
    rate_limiter = RateLimiter()

    app = web.Application(
        middlewares=[
            create_cors_middleware(["*"]),
            create_auth_middleware(JWT_SECRET),
            create_rate_limit_middleware(rate_limiter),
        ]
    )

    app["tenant_manager"] = tenant_manager
    app["jwt_secret"] = JWT_SECRET
    if inference_broker is not None:
        app["inference_broker"] = inference_broker

    # Routes
    app.router.add_get("/api/v1/health", handle_health)
    app.router.add_post("/api/v1/sessions", handle_create_session)
    app.router.add_get("/api/v1/sessions/{session_id}", handle_get_session)
    app.router.add_delete("/api/v1/sessions/{session_id}", handle_delete_session)
    app.router.add_post("/api/v1/chat", handle_chat)
    app.router.add_post("/api/v1/chat/stream", handle_chat_stream)
    app.router.add_get("/api/v1/chat/history", handle_chat_history)

    return app


@pytest_asyncio.fixture()
async def api_client():
    """Provide an aiohttp TestClient backed by a mocked TenantManager."""
    tm = AsyncMock()

    # Default mock behaviours — individual tests can override via tm.*
    full_key, key_prefix, key_hash = generate_api_key()
    tenant = _make_tenant()

    tm.authenticate_api_key = AsyncMock(return_value=tenant)
    tm.get_tenant = AsyncMock(return_value=tenant)
    tm.create_session = AsyncMock(return_value=_make_session())
    tm.get_session = AsyncMock(return_value=_make_session())
    tm.touch_session = AsyncMock()
    tm.delete_session = AsyncMock(return_value=True)

    app = _build_app(tm)
    async with TestClient(TestServer(app)) as client:
        yield client, tm, full_key


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_no_auth(api_client):
    """GET /api/v1/health returns 200 without auth."""
    client, _, _ = api_client
    resp = await client.get("/api/v1/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_session(api_client):
    """POST /api/v1/sessions creates a session and returns a token."""
    client, tm, api_key = api_client
    resp = await client.post(
        "/api/v1/sessions",
        json={"external_user_id": "visitor-42"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    data = await resp.json()
    assert "session_token" in data
    assert data["session_token"].startswith("zt_sess_")


@pytest.mark.integration
async def test_get_session(api_client):
    """GET /api/v1/sessions/{id} returns session info."""
    client, tm, api_key = api_client
    resp = await client.get(
        f"/api/v1/sessions/{SESSION_ID}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["session_id"] == SESSION_ID


@pytest.mark.integration
async def test_get_session_wrong_tenant(api_client):
    """GET session belonging to another tenant returns 404."""
    client, tm, api_key = api_client
    tm.get_session = AsyncMock(return_value=_make_session(tenant_id=str(uuid.uuid4())))
    resp = await client.get(
        f"/api/v1/sessions/{SESSION_ID}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_get_session_not_found(api_client):
    """GET non-existent session returns 404."""
    client, tm, api_key = api_client
    tm.get_session = AsyncMock(return_value=None)
    resp = await client.get(
        f"/api/v1/sessions/{uuid.uuid4()}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_delete_session(api_client):
    """DELETE /api/v1/sessions/{id} returns ok."""
    client, tm, api_key = api_client
    resp = await client.delete(
        f"/api/v1/sessions/{SESSION_ID}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True


@pytest.mark.integration
async def test_delete_session_not_found(api_client):
    """DELETE non-existent session returns 404."""
    client, tm, api_key = api_client
    tm.delete_session = AsyncMock(return_value=False)
    resp = await client.delete(
        f"/api/v1/sessions/{uuid.uuid4()}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_missing_api_key_401(api_client):
    """Request without API key returns 401."""
    client, _, _ = api_client
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status == 401


@pytest.mark.integration
async def test_invalid_api_key_401(api_client):
    """Invalid API key returns 401."""
    client, tm, _ = api_client
    tm.authenticate_api_key = AsyncMock(return_value=None)
    resp = await client.post(
        "/api/v1/sessions",
        json={},
        headers={"X-API-Key": "sk_live_bad_key"},
    )
    assert resp.status == 401


# ---------------------------------------------------------------------------
# Chat (POST /api/v1/chat)
# ---------------------------------------------------------------------------


@dataclass
class _FakeInferenceResult:
    content: str = "Hello! How can I help you?"
    model: str = "test-model"
    provider: str = "test"
    task_type: str = "conversation"
    input_tokens: int = 10
    output_tokens: int = 15
    latency_ms: float = 100.0
    estimated_cost_usd: float = 0.0


@dataclass
class _FakeStreamChunk:
    content: str = ""
    done: bool = False
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


async def _fake_infer_stream(**kwargs):
    """Async generator that simulates streaming tokens."""
    words = ["Hello!", " How", " can", " I", " help", " you?"]
    for word in words:
        yield _FakeStreamChunk(content=word)
    yield _FakeStreamChunk(
        content="",
        done=True,
        model="test-model",
        provider="test",
        input_tokens=10,
        output_tokens=15,
    )


@pytest_asyncio.fixture()
async def chat_client():
    """Provide an aiohttp TestClient with mocked TenantManager + InferenceBroker."""
    tm = AsyncMock()
    broker = AsyncMock()

    tenant = _make_tenant()
    session = _make_session()

    tm.authenticate_api_key = AsyncMock(return_value=tenant)
    tm.get_tenant = AsyncMock(return_value=tenant)
    tm.get_session = AsyncMock(return_value=session)
    tm.touch_session = AsyncMock()
    tm.add_message = AsyncMock(
        side_effect=lambda **kw: _make_message(
            role=kw["role"],
            content=kw["content"],
        )
    )
    tm.get_messages = AsyncMock(
        return_value=[
            _make_message(role="user", content="Hello"),
        ]
    )

    broker.infer = AsyncMock(return_value=_FakeInferenceResult())
    broker.infer_stream = _fake_infer_stream

    # Create a session token for Bearer auth
    token = create_session_token(TENANT_ID, SESSION_ID, JWT_SECRET)

    app = _build_app(tm, inference_broker=broker)
    async with TestClient(TestServer(app)) as client:
        yield client, tm, broker, token


@pytest.mark.integration
async def test_chat_send_message(chat_client):
    """POST /api/v1/chat sends a message and returns AI response."""
    client, tm, broker, token = chat_client
    resp = await client.post(
        "/api/v1/chat",
        json={"message": "What services do you offer?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["role"] == "assistant"
    assert data["content"] == "Hello! How can I help you?"
    assert data["model"] == "test-model"

    # Verify user message was stored
    assert tm.add_message.call_count == 2
    user_call = tm.add_message.call_args_list[0]
    assert user_call.kwargs["role"] == "user"
    assert user_call.kwargs["content"] == "What services do you offer?"

    # Verify assistant message was stored
    assistant_call = tm.add_message.call_args_list[1]
    assert assistant_call.kwargs["role"] == "assistant"


@pytest.mark.integration
async def test_chat_empty_message_400(chat_client):
    """POST /api/v1/chat with empty message returns 400."""
    client, _, _, token = chat_client
    resp = await client.post(
        "/api/v1/chat",
        json={"message": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_chat_no_broker_fallback(chat_client):
    """POST /api/v1/chat without inference_broker returns a placeholder."""
    client, tm, _, token = chat_client
    # Remove the broker from the app
    client.app.pop("inference_broker", None)

    resp = await client.post(
        "/api/v1/chat",
        json={"message": "Hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "not configured" in data["content"]


@pytest.mark.integration
async def test_chat_missing_bearer_401(chat_client):
    """POST /api/v1/chat without Bearer token returns 401."""
    client, _, _, _ = chat_client
    resp = await client.post(
        "/api/v1/chat",
        json={"message": "Hello"},
    )
    assert resp.status == 401


# ---------------------------------------------------------------------------
# Chat History (GET /api/v1/chat/history)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_chat_history(chat_client):
    """GET /api/v1/chat/history returns session messages."""
    client, tm, _, token = chat_client
    tm.get_messages = AsyncMock(
        return_value=[
            _make_message(role="user", content="Hi"),
            _make_message(role="assistant", content="Hello!"),
        ]
    )
    resp = await client.get(
        "/api/v1/chat/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["session_id"] == SESSION_ID
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"


@pytest.mark.integration
async def test_chat_history_with_limit(chat_client):
    """GET /api/v1/chat/history?limit=1 respects limit param."""
    client, tm, _, token = chat_client
    resp = await client.get(
        "/api/v1/chat/history?limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    # Verify limit was passed to get_messages
    tm.get_messages.assert_called_once()
    call_kwargs = tm.get_messages.call_args
    assert call_kwargs.kwargs["limit"] == 10


# ---------------------------------------------------------------------------
# Chat Stream (POST /api/v1/chat/stream)
# ---------------------------------------------------------------------------


def _parse_sse(raw: bytes) -> list[dict]:
    """Parse raw SSE bytes into a list of JSON event dicts."""
    events = []
    for line in raw.decode().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@pytest.mark.integration
async def test_chat_stream_tokens(chat_client):
    """POST /api/v1/chat/stream streams token events then done."""
    client, tm, broker, token = chat_client
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": "What services do you offer?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "text/event-stream"

    body = await resp.read()
    events = _parse_sse(body)

    # Should have token events + 1 done event
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]

    assert len(token_events) == 6  # "Hello!", " How", " can", " I", " help", " you?"
    assert len(done_events) == 1
    assert done_events[0]["model"] == "test-model"
    assert "message_id" in done_events[0]

    # Verify concatenated content matches expected response
    full_content = "".join(e["content"] for e in token_events)
    assert full_content == "Hello! How can I help you?"

    # Verify messages were stored (user + assistant)
    assert tm.add_message.call_count == 2
    assistant_call = tm.add_message.call_args_list[1]
    assert assistant_call.kwargs["role"] == "assistant"
    assert assistant_call.kwargs["content"] == "Hello! How can I help you?"


@pytest.mark.integration
async def test_chat_stream_empty_message_400(chat_client):
    """POST /api/v1/chat/stream with empty message returns 400."""
    client, _, _, token = chat_client
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_chat_stream_no_broker_fallback(chat_client):
    """POST /api/v1/chat/stream without inference_broker sends placeholder via SSE."""
    client, tm, _, token = chat_client
    client.app.pop("inference_broker", None)

    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": "Hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200

    body = await resp.read()
    events = _parse_sse(body)

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]

    assert len(token_events) == 1
    assert "not configured" in token_events[0]["content"]
    assert len(done_events) == 1


@pytest.mark.integration
async def test_chat_stream_missing_bearer_401(chat_client):
    """POST /api/v1/chat/stream without Bearer token returns 401."""
    client, _, _, _ = chat_client
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"message": "Hello"},
    )
    assert resp.status == 401
