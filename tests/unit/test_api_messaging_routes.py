"""Unit tests for tenant public messaging routes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.messaging import (
    handle_list_messaging_chats,
    handle_list_messaging_messages,
    handle_send_messaging_message,
)
from zetherion_ai.security.trust_policy import (
    TrustActionClass,
    TrustDecisionOutcome,
    TrustPolicyDecision,
)


def _make_policy_decision(*, allowed: bool = True) -> TrustPolicyDecision:
    if allowed:
        return TrustPolicyDecision(
            action="messaging.read",
            action_class=TrustActionClass.SENSITIVE,
            outcome=TrustDecisionOutcome.ALLOW,
            status=200,
            code="AI_OK",
            message="Allowed",
            details={},
        )
    return TrustPolicyDecision(
        action="messaging.read",
        action_class=TrustActionClass.SENSITIVE,
        outcome=TrustDecisionOutcome.DENY,
        status=403,
        code="AI_MESSAGING_CHAT_NOT_ALLOWLISTED",
        message="Chat is not allowlisted for this action",
        details={"chat_id": "chat-1"},
    )


def _make_app(*, manager: object | None = None, evaluator: object | None = None) -> web.Application:
    @web.middleware
    async def inject_tenant(request: web.Request, handler):
        request["tenant"] = {"tenant_id": "11111111-1111-1111-1111-111111111111"}
        return await handler(request)

    app = web.Application(middlewares=[inject_tenant])
    if manager is not None:
        app["tenant_admin_manager"] = manager
    if evaluator is not None:
        app["trust_policy_evaluator"] = evaluator

    app.router.add_get("/api/v1/messaging/chats", handle_list_messaging_chats)
    app.router.add_get("/api/v1/messaging/messages", handle_list_messaging_messages)
    app.router.add_post(
        "/api/v1/messaging/messages/{chat_id}/send",
        handle_send_messaging_message,
    )
    return app


@pytest.mark.asyncio
async def test_list_messaging_chats_success() -> None:
    manager = MagicMock()
    manager.list_messaging_chats = AsyncMock(
        return_value=[
            {
                "chat_id": "chat-1",
                "provider": "whatsapp",
                "message_count": 2,
                "updated_at": datetime.now(UTC),
            }
        ]
    )
    app = _make_app(manager=manager)

    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/v1/messaging/chats",
            params={"provider": "whatsapp", "include_inactive": "false", "limit": "10"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["count"] == 1
        assert body["chats"][0]["chat_id"] == "chat-1"

    manager.list_messaging_chats.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_messaging_chats_without_manager_returns_503() -> None:
    app = _make_app()
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/v1/messaging/chats")
        assert response.status == 503


@pytest.mark.asyncio
async def test_list_messaging_messages_requires_chat_id() -> None:
    manager = MagicMock()
    manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
    manager.list_messaging_messages = AsyncMock(return_value=[])
    evaluator = SimpleNamespace(
        evaluate=MagicMock(return_value=_make_policy_decision(allowed=True))
    )
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/v1/messaging/messages")
        assert response.status == 400


@pytest.mark.asyncio
async def test_list_messaging_messages_policy_denied() -> None:
    manager = MagicMock()
    manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
    manager.list_messaging_messages = AsyncMock(return_value=[])
    evaluator = SimpleNamespace(
        evaluate=MagicMock(return_value=_make_policy_decision(allowed=False))
    )
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/v1/messaging/messages", params={"chat_id": "chat-1"})
        assert response.status == 403
        body = await response.json()
        assert body["code"] == "AI_MESSAGING_CHAT_NOT_ALLOWLISTED"

    manager.list_messaging_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_messaging_messages_success() -> None:
    manager = MagicMock()
    manager.purge_expired_messaging_messages = AsyncMock(return_value=1)
    manager.list_messaging_messages = AsyncMock(
        return_value=[
            {
                "message_id": "m-1",
                "chat_id": "chat-1",
                "body_text": "hello",
                "created_at": datetime.now(UTC),
            }
        ]
    )
    evaluator = SimpleNamespace(
        evaluate=MagicMock(return_value=_make_policy_decision(allowed=True))
    )
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/v1/messaging/messages",
            params={"chat_id": "chat-1", "provider": "whatsapp", "limit": "25"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["count"] == 1
        assert body["messages"][0]["message_id"] == "m-1"

    manager.purge_expired_messaging_messages.assert_awaited_once()
    manager.list_messaging_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_messaging_message_requires_approval_when_policy_requires() -> None:
    manager = MagicMock()
    manager.queue_messaging_send = AsyncMock()
    approval_required = TrustPolicyDecision(
        action="messaging.send",
        action_class=TrustActionClass.CRITICAL,
        outcome=TrustDecisionOutcome.APPROVAL_REQUIRED,
        status=409,
        code="AI_APPROVAL_REQUIRED",
        message="Approval required",
        details={"requires_two_person": True},
        requires_two_person=True,
    )
    evaluator = SimpleNamespace(evaluate=MagicMock(return_value=approval_required))
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/v1/messaging/messages/chat-1/send",
            json={"text": "hello"},
        )
        assert response.status == 409
        body = await response.json()
        assert body["code"] == "AI_APPROVAL_REQUIRED"
        assert body["requires_two_person"] is True

    manager.queue_messaging_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_messaging_message_success() -> None:
    manager = MagicMock()
    manager.queue_messaging_send = AsyncMock(
        return_value={
            "action": {"action_id": "action-1", "status": "queued"},
            "message": {
                "message_id": "message-1",
                "chat_id": "chat-1",
                "direction": "outbound",
                "created_at": datetime.now(UTC),
            },
        }
    )
    allow_send = TrustPolicyDecision(
        action="messaging.send",
        action_class=TrustActionClass.CRITICAL,
        outcome=TrustDecisionOutcome.ALLOW,
        status=200,
        code="AI_OK",
        message="Allowed",
        details={},
    )
    evaluator = SimpleNamespace(evaluate=MagicMock(return_value=allow_send))
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/v1/messaging/messages/chat-1/send",
            headers={"X-Request-ID": "req-123"},
            json={
                "provider": "whatsapp",
                "text": "hello there",
                "metadata": {"source": "ui"},
                "explicitly_elevated": True,
            },
        )
        assert response.status == 202
        body = await response.json()
        assert body["ok"] is True
        assert body["queued_action"]["action_id"] == "action-1"
        assert body["message"]["message_id"] == "message-1"

    manager.queue_messaging_send.assert_awaited_once()
    kwargs = manager.queue_messaging_send.await_args.kwargs
    assert kwargs["chat_id"] == "chat-1"
    assert kwargs["actor"].request_id == "req-123"


@pytest.mark.asyncio
async def test_send_messaging_message_rejects_invalid_metadata() -> None:
    manager = MagicMock()
    manager.queue_messaging_send = AsyncMock()
    allow_send = TrustPolicyDecision(
        action="messaging.send",
        action_class=TrustActionClass.CRITICAL,
        outcome=TrustDecisionOutcome.ALLOW,
        status=200,
        code="AI_OK",
        message="Allowed",
        details={},
    )
    evaluator = SimpleNamespace(evaluate=MagicMock(return_value=allow_send))
    app = _make_app(manager=manager, evaluator=evaluator)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/v1/messaging/messages/chat-1/send",
            json={"text": "hello", "metadata": "invalid"},
        )
        assert response.status == 400
        body = await response.json()
        assert "metadata" in body["error"]

    manager.queue_messaging_send.assert_not_awaited()
