"""Pydantic request/response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Tenant models
# ---------------------------------------------------------------------------


class TenantCreate(BaseModel):
    """Request body for creating a new tenant."""

    name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    config: dict[str, Any] = Field(default_factory=dict)


class TenantResponse(BaseModel):
    """Tenant info returned by the API."""

    tenant_id: str
    name: str
    domain: str | None
    is_active: bool
    rate_limit_rpm: int
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TenantCreatedResponse(BaseModel):
    """Response after creating a tenant, includes the one-time API key."""

    tenant: TenantResponse
    api_key: str  # Shown once, never stored in plaintext


# ---------------------------------------------------------------------------
# Session models
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Request body for creating a chat session."""

    external_user_id: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Session info returned by the API."""

    session_id: str
    tenant_id: str
    external_user_id: str | None
    session_token: str
    created_at: datetime
    expires_at: datetime


class SessionInfo(BaseModel):
    """Session info without token (for GET requests)."""

    session_id: str
    tenant_id: str
    external_user_id: str | None
    created_at: datetime
    last_active: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Chat models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Request body for sending a chat message."""

    message: str = Field(min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """A single chat message."""

    message_id: str
    role: str
    content: str
    created_at: datetime


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    message_id: str
    role: str = "assistant"
    content: str
    created_at: datetime
    model: str | None = None


class ChatHistoryResponse(BaseModel):
    """Response for chat history endpoint."""

    session_id: str
    messages: list[ChatMessage]


# ---------------------------------------------------------------------------
# Health models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str = "0.1.0"
