"""Tests for public API Pydantic models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zetherion_ai.api.models import (
    ChatHistoryResponse,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionCreate,
    SessionInfo,
    SessionResponse,
    TenantCreate,
    TenantCreatedResponse,
    TenantResponse,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


class TestTenantModels:
    def test_tenant_create_minimal(self):
        t = TenantCreate(name="Acme")
        assert t.name == "Acme"
        assert t.domain is None
        assert t.config == {}

    def test_tenant_create_full(self):
        t = TenantCreate(name="Acme", domain="acme.com", config={"k": "v"})
        assert t.domain == "acme.com"
        assert t.config == {"k": "v"}

    def test_tenant_create_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            TenantCreate(name="")

    def test_tenant_response(self):
        t = TenantResponse(
            tenant_id="abc",
            name="Acme",
            domain=None,
            is_active=True,
            rate_limit_rpm=60,
            config={},
            created_at=_NOW,
            updated_at=_NOW,
        )
        assert t.tenant_id == "abc"
        assert t.is_active is True

    def test_tenant_created_response(self):
        inner = TenantResponse(
            tenant_id="abc",
            name="Acme",
            domain=None,
            is_active=True,
            rate_limit_rpm=60,
            config={},
            created_at=_NOW,
            updated_at=_NOW,
        )
        resp = TenantCreatedResponse(tenant=inner, api_key="secret-key")
        assert resp.api_key == "secret-key"
        assert resp.tenant.name == "Acme"


class TestSessionModels:
    def test_session_create_defaults(self):
        s = SessionCreate()
        assert s.external_user_id is None
        assert s.metadata == {}

    def test_session_create_full(self):
        s = SessionCreate(external_user_id="u1", metadata={"k": 1})
        assert s.external_user_id == "u1"

    def test_session_response(self):
        s = SessionResponse(
            session_id="s1",
            tenant_id="t1",
            external_user_id=None,
            session_token="tok",
            created_at=_NOW,
            expires_at=_NOW,
        )
        assert s.session_token == "tok"

    def test_session_info(self):
        s = SessionInfo(
            session_id="s1",
            tenant_id="t1",
            external_user_id="u1",
            created_at=_NOW,
            last_active=_NOW,
            expires_at=_NOW,
        )
        assert s.external_user_id == "u1"


class TestChatModels:
    def test_chat_request_minimal(self):
        c = ChatRequest(message="hello")
        assert c.message == "hello"
        assert c.metadata == {}

    def test_chat_request_empty_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="")

    def test_chat_message(self):
        m = ChatMessage(message_id="m1", role="user", content="hi", created_at=_NOW)
        assert m.role == "user"

    def test_chat_response(self):
        r = ChatResponse(message_id="m1", content="reply", created_at=_NOW)
        assert r.role == "assistant"
        assert r.model is None

    def test_chat_history_response(self):
        msg = ChatMessage(message_id="m1", role="user", content="hi", created_at=_NOW)
        h = ChatHistoryResponse(session_id="s1", messages=[msg])
        assert len(h.messages) == 1


class TestHealthModel:
    def test_health_response(self):
        h = HealthResponse(status="ok")
        assert h.status == "ok"
        assert h.version == "0.1.0"

    def test_health_response_custom_version(self):
        h = HealthResponse(status="ok", version="2.0.0")
        assert h.version == "2.0.0"
