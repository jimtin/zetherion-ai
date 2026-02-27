"""Request/response models for CGS gateway."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuthPrincipal(BaseModel):
    """Authenticated app/operator principal extracted from JWT."""

    model_config = ConfigDict(extra="allow")

    sub: str = ""
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)


class CreateConversationRequest(BaseModel):
    """Create a CGS conversation mapped to a Zetherion session."""

    tenant_id: str
    app_user_id: str | None = None
    external_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRequest(BaseModel):
    """Conversation message request payload."""

    message: str = Field(min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateTenantRequest(BaseModel):
    """Create or map a tenant for CGS -> Zetherion."""

    cgs_tenant_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = None
    config: dict[str, Any] | None = None


class ConfigureTenantRequest(BaseModel):
    """Update tenant profile/configuration."""

    name: str | None = None
    domain: str | None = None
    config: dict[str, Any] | None = None


class RecommendationFeedbackRequest(BaseModel):
    """Recommendation feedback payload pass-through."""

    feedback_type: str = Field(min_length=1, max_length=30)
    note: str | None = None
    actor: str | None = None


class ReleaseMarkerRequest(BaseModel):
    """Release marker payload pass-through."""

    source: str = "cgs-deploy"
    environment: str = "production"
    commit_sha: str | None = None
    branch: str | None = None
    tag_name: str | None = None
    deployed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
