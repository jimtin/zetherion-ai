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
    memory_subject_id: str | None = Field(default=None, max_length=500)
    test_profile_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Session info returned by the API."""

    session_id: str
    tenant_id: str
    external_user_id: str | None
    memory_subject_id: str | None = None
    execution_mode: str = "live"
    test_profile_id: str | None = None
    conversation_summary: str = ""
    session_token: str
    created_at: datetime
    expires_at: datetime


class SessionInfo(BaseModel):
    """Session info without token (for GET requests)."""

    session_id: str
    tenant_id: str
    external_user_id: str | None
    memory_subject_id: str | None = None
    execution_mode: str = "live"
    test_profile_id: str | None = None
    conversation_summary: str = ""
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
# Analytics / App watcher models
# ---------------------------------------------------------------------------


class AnalyticsEvent(BaseModel):
    """Single behavior event from the client observer SDK."""

    event_type: str = Field(min_length=1, max_length=64)
    event_name: str = Field(default="", max_length=255)
    page_url: str | None = None
    element_selector: str | None = Field(default=None, max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    web_session_id: str | None = None


class AnalyticsEventBatchRequest(BaseModel):
    """Batch ingest payload for analytics events."""

    events: list[AnalyticsEvent] = Field(min_length=1, max_length=500)
    web_session_id: str | None = None
    external_user_id: str | None = Field(default=None, max_length=500)
    consent_replay: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayChunkRequest(BaseModel):
    """Replay chunk metadata payload."""

    web_session_id: str
    sequence_no: int = Field(ge=0)
    object_key: str = Field(min_length=1, max_length=2048)
    checksum_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    chunk_size_bytes: int = Field(default=0, ge=0)
    chunk_base64: str | None = Field(default=None, min_length=4)
    consent: bool = False
    sampled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionEndRequest(BaseModel):
    """Payload to end a web behavior session and trigger enrichment."""

    web_session_id: str | None = None
    contact_id: str | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecommendationResponse(BaseModel):
    """Tenant-scoped app watcher recommendation."""

    recommendation_id: str
    recommendation_type: str
    title: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    risk_class: str
    confidence: float
    expected_impact: float | None = None
    status: str
    source: str
    generated_at: datetime


class RecommendationFeedbackRequest(BaseModel):
    """Operator feedback for a recommendation."""

    feedback_type: str = Field(min_length=1, max_length=30)
    note: str | None = Field(default=None, max_length=5000)
    actor: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# Health models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str = "0.1.0"
