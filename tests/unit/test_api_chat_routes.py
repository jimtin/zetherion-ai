"""Unit tests for public API chat route handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.chat import (
    _format_messages_for_llm,
    _get_chat_skill,
    _serialise,
    handle_chat,
    handle_chat_history,
    handle_chat_stream,
)


@dataclass
class _FakeStreamChunk:
    content: str
    done: bool = False
    model: str = ""


def _parse_sse_events(raw: bytes) -> list[dict[str, object]]:
    """Parse a text/event-stream body into JSON payloads."""
    events: list[dict[str, object]] = []
    for line in raw.decode().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@pytest_asyncio.fixture()
async def chat_routes_client():
    """aiohttp TestClient with tenant/session context injected."""
    tenant = {"tenant_id": "tenant-1", "name": "Test Tenant", "config": {}}
    session = {"session_id": "session-1", "tenant_id": "tenant-1"}

    tenant_manager = AsyncMock()

    async def _add_message(**kwargs: object) -> dict[str, object]:
        return {
            "message_id": str(uuid4()),
            "session_id": kwargs["session_id"],
            "tenant_id": kwargs["tenant_id"],
            "role": kwargs["role"],
            "content": kwargs["content"],
            "created_at": datetime.now(UTC),
        }

    tenant_manager.add_message = AsyncMock(side_effect=_add_message)
    tenant_manager.get_messages = AsyncMock(return_value=[])

    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["tenant"] = tenant
        request["session"] = session
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["tenant_manager"] = tenant_manager
    app["inference_broker"] = object()
    app.router.add_post("/api/v1/chat", handle_chat)
    app.router.add_get("/api/v1/chat/history", handle_chat_history)
    app.router.add_post("/api/v1/chat/stream", handle_chat_stream)

    async with TestClient(TestServer(app)) as client:
        yield client, tenant_manager, tenant, session


def test_format_messages_for_llm() -> None:
    """Stored message dicts are transformed into role/content pairs."""
    messages = [
        {"role": "user", "content": "Hello", "other": "ignored"},
        {"role": "assistant", "content": "Hi there"},
    ]
    assert _format_messages_for_llm(messages) == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]


def test_serialise_converts_datetime_and_uuid() -> None:
    """Datetime and UUID values are serialised to strings."""
    now = datetime.now(UTC)
    identifier = uuid4()
    out = _serialise({"ts": now, "id": identifier, "count": 2})
    assert out["ts"] == now.isoformat()
    assert out["id"] == str(identifier)
    assert out["count"] == 2


def test_get_chat_skill_uses_cached_instance() -> None:
    """When app has client_chat_skill cached, it is reused."""
    request = MagicMock()
    request.app = {"client_chat_skill": "cached-skill"}
    assert _get_chat_skill(request) == "cached-skill"


def test_get_chat_skill_creates_new_from_broker() -> None:
    """When no cached skill exists, one is built from inference_broker."""
    request = MagicMock()
    request.app = {"inference_broker": "broker"}
    with patch("zetherion_ai.api.routes.chat.ClientChatSkill") as mock_skill_cls:
        skill = MagicMock()
        mock_skill_cls.return_value = skill
        assert _get_chat_skill(request) is skill
        mock_skill_cls.assert_called_once_with(inference_broker="broker")


@pytest.mark.asyncio
async def test_handle_chat_rejects_invalid_json(chat_routes_client) -> None:
    """Invalid JSON body returns 400."""
    client, _, _, _ = chat_routes_client
    response = await client.post(
        "/api/v1/chat",
        data="{invalid",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400
    assert (await response.json())["error"] == "Invalid JSON body"


@pytest.mark.asyncio
async def test_handle_chat_rejects_empty_message(chat_routes_client) -> None:
    """Blank message is rejected."""
    client, _, _, _ = chat_routes_client
    response = await client.post("/api/v1/chat", json={"message": "   "})
    assert response.status == 400
    assert (await response.json())["error"] == "Message is required"


@pytest.mark.asyncio
async def test_handle_chat_rejects_too_long_message(chat_routes_client) -> None:
    """Messages over 10k chars are rejected."""
    client, _, _, _ = chat_routes_client
    response = await client.post("/api/v1/chat", json={"message": "x" * 10001})
    assert response.status == 400
    assert "Message too long" in (await response.json())["error"]


@pytest.mark.asyncio
async def test_handle_chat_success_with_context_and_model(chat_routes_client) -> None:
    """Chat stores messages, passes history, and returns model metadata."""
    client, tenant_manager, tenant, _ = chat_routes_client
    tenant_manager.get_messages = AsyncMock(
        return_value=[
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Current message"},
        ]
    )

    skill = MagicMock()
    skill.generate_response = AsyncMock(
        return_value=SimpleNamespace(content="Assistant response", model="test-model")
    )
    client.app["client_chat_skill"] = skill

    response = await client.post(
        "/api/v1/chat",
        json={"message": "Current message", "metadata": {"source": "widget"}},
    )

    assert response.status == 200
    body = await response.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Assistant response"
    assert body["model"] == "test-model"

    user_call = tenant_manager.add_message.call_args_list[0].kwargs
    assert user_call["role"] == "user"
    assert user_call["metadata"] == {"source": "widget"}

    skill.generate_response.assert_awaited_once()
    args = skill.generate_response.call_args.kwargs
    assert args["tenant"] == tenant
    assert args["message"] == "Current message"
    assert args["history"] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]


@pytest.mark.asyncio
async def test_handle_chat_inference_error_returns_safe_fallback(chat_routes_client) -> None:
    """Inference exceptions produce a safe fallback response."""
    client, tenant_manager, _, _ = chat_routes_client
    tenant_manager.get_messages = AsyncMock(return_value=[])

    skill = MagicMock()
    skill.generate_response = AsyncMock(side_effect=RuntimeError("boom"))
    client.app["client_chat_skill"] = skill

    response = await client.post("/api/v1/chat", json={"message": "Hello"})
    assert response.status == 200
    body = await response.json()
    assert "encountered an error" in body["content"]
    assert "model" not in body


@pytest.mark.asyncio
async def test_handle_chat_history_enforces_limit_cap_and_before(chat_routes_client) -> None:
    """History endpoint caps limit at 100 and passes before cursor."""
    client, tenant_manager, _, _ = chat_routes_client
    tenant_manager.get_messages = AsyncMock(return_value=[])

    response = await client.get("/api/v1/chat/history?limit=500&before=msg-1")
    assert response.status == 200
    tenant_manager.get_messages.assert_awaited_once()
    kwargs = tenant_manager.get_messages.call_args.kwargs
    assert kwargs["limit"] == 100
    assert kwargs["before_id"] == "msg-1"


@pytest.mark.asyncio
async def test_handle_chat_history_invalid_limit_defaults_to_50(chat_routes_client) -> None:
    """Invalid limit query falls back to default value."""
    client, tenant_manager, _, _ = chat_routes_client
    tenant_manager.get_messages = AsyncMock(return_value=[])

    response = await client.get("/api/v1/chat/history?limit=not-a-number")
    assert response.status == 200
    kwargs = tenant_manager.get_messages.call_args.kwargs
    assert kwargs["limit"] == 50


@pytest.mark.asyncio
async def test_handle_chat_stream_rejects_invalid_json(chat_routes_client) -> None:
    """Streaming endpoint rejects invalid JSON."""
    client, _, _, _ = chat_routes_client
    response = await client.post(
        "/api/v1/chat/stream",
        data="{invalid",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400
    assert (await response.json())["error"] == "Invalid JSON body"


@pytest.mark.asyncio
async def test_handle_chat_stream_rejects_too_long_message(chat_routes_client) -> None:
    """Streaming endpoint enforces max message length."""
    client, _, _, _ = chat_routes_client
    response = await client.post("/api/v1/chat/stream", json={"message": "x" * 10001})
    assert response.status == 400
    assert "Message too long" in (await response.json())["error"]


@pytest.mark.asyncio
async def test_handle_chat_stream_no_broker_uses_placeholder(chat_routes_client) -> None:
    """Without inference broker, stream sends a placeholder token + done."""
    client, tenant_manager, _, _ = chat_routes_client
    client.app.pop("inference_broker", None)

    response = await client.post("/api/v1/chat/stream", json={"message": "Hello"})
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")

    events = _parse_sse_events(await response.read())
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]

    assert len(token_events) == 1
    assert "not configured" in str(token_events[0]["content"])
    assert len(done_events) == 1
    assert "model" not in done_events[0]

    assistant_call = tenant_manager.add_message.call_args_list[1].kwargs
    assert assistant_call["role"] == "assistant"
    assert "not configured" in assistant_call["content"]


@pytest.mark.asyncio
async def test_handle_chat_stream_success(chat_routes_client) -> None:
    """Stream emits tokens and done metadata, then stores full assistant response."""
    client, tenant_manager, _, _ = chat_routes_client

    async def _stream():
        yield _FakeStreamChunk(content="Hello")
        yield _FakeStreamChunk(content=" there")
        yield _FakeStreamChunk(content="", done=True, model="stream-model")

    skill = MagicMock()
    skill.generate_stream = AsyncMock(return_value=(None, _stream()))
    client.app["client_chat_skill"] = skill

    response = await client.post("/api/v1/chat/stream", json={"message": "Hi"})
    assert response.status == 200

    events = _parse_sse_events(await response.read())
    token_events = [e for e in events if e["type"] == "token"]
    done_event = [e for e in events if e["type"] == "done"][0]

    assert "".join(str(e["content"]) for e in token_events) == "Hello there"
    assert done_event["model"] == "stream-model"

    assistant_call = tenant_manager.add_message.call_args_list[1].kwargs
    assert assistant_call["role"] == "assistant"
    assert assistant_call["content"] == "Hello there"


@pytest.mark.asyncio
async def test_handle_chat_stream_error_before_tokens_sends_fallback(chat_routes_client) -> None:
    """If streaming fails before output, endpoint emits fallback token."""
    client, _, _, _ = chat_routes_client
    skill = MagicMock()
    skill.generate_stream = AsyncMock(side_effect=RuntimeError("stream failed"))
    client.app["client_chat_skill"] = skill

    response = await client.post("/api/v1/chat/stream", json={"message": "Hi"})
    assert response.status == 200

    events = _parse_sse_events(await response.read())
    tokens = [e for e in events if e["type"] == "token"]
    assert len(tokens) == 1
    assert "encountered an error" in str(tokens[0]["content"])


@pytest.mark.asyncio
async def test_handle_chat_stream_error_after_partial_keeps_partial(chat_routes_client) -> None:
    """If stream fails after some output, partial content is retained."""
    client, tenant_manager, _, _ = chat_routes_client

    async def _broken_stream():
        yield _FakeStreamChunk(content="partial")
        raise RuntimeError("mid-stream failure")

    skill = MagicMock()
    skill.generate_stream = AsyncMock(return_value=(None, _broken_stream()))
    client.app["client_chat_skill"] = skill

    response = await client.post("/api/v1/chat/stream", json={"message": "Hi"})
    assert response.status == 200

    events = _parse_sse_events(await response.read())
    token_events = [e for e in events if e["type"] == "token"]
    assert [e["content"] for e in token_events] == ["partial"]

    assistant_call = tenant_manager.add_message.call_args_list[1].kwargs
    assert assistant_call["content"] == "partial"
