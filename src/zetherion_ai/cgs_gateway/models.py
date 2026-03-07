"""Request/response models for CGS gateway."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    """Update tenant profile/configuration and staged migration state."""

    name: str | None = None
    domain: str | None = None
    config: dict[str, Any] | None = None
    desired_isolation_stage: str | None = None
    expected_key_version: int | None = Field(default=None, ge=1)
    owner_portfolio_ready: bool | None = None
    run_tenant_vector_backfill: bool = False
    derive_owner_portfolio: bool = False
    cutover_verified: bool = False
    document_backfill_limit: int = Field(default=200, ge=1, le=500)
    release_marker: ReleaseMarkerRequest | None = None


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


class DocumentUploadRequest(BaseModel):
    """Create document upload intent."""

    tenant_id: str
    file_name: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    size_bytes: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentCompleteUploadRequest(BaseModel):
    """Finalize document upload payload."""

    tenant_id: str
    file_base64: str = Field(min_length=4)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentReindexRequest(BaseModel):
    """Document re-index trigger payload."""

    tenant_id: str


class DocumentQueryRequest(BaseModel):
    """Tenant RAG query request."""

    tenant_id: str
    query: str = Field(min_length=1, max_length=20000)
    top_k: int = Field(default=6, ge=1, le=20)
    provider: str | None = None
    model: str | None = None


class TenantAdminDiscordUserCreateRequest(BaseModel):
    """Create/update tenant Discord allowlist user."""

    discord_user_id: int = Field(ge=1)
    role: str = Field(default="user", min_length=1, max_length=20)
    change_ticket_id: str | None = None


class TenantAdminDiscordRolePatchRequest(BaseModel):
    """Update tenant Discord user role."""

    role: str = Field(min_length=1, max_length=20)
    change_ticket_id: str | None = None


class TenantAdminGuildBindingRequest(BaseModel):
    """Create/update guild default tenant binding."""

    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True
    change_ticket_id: str | None = None


class TenantAdminChannelBindingRequest(BaseModel):
    """Create/update channel override tenant binding."""

    guild_id: int = Field(ge=1)
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True
    change_ticket_id: str | None = None


class TenantAdminSettingPutRequest(BaseModel):
    """Set tenant runtime setting override."""

    value: Any
    data_type: str = Field(default="string", min_length=1, max_length=20)
    change_ticket_id: str | None = None


class TenantAdminSecretPutRequest(BaseModel):
    """Set or rotate tenant secret."""

    value: str = Field(min_length=1)
    description: str | None = None
    change_ticket_id: str | None = None


class TenantAdminEmailOAuthAppPutRequest(BaseModel):
    """Configure per-tenant email provider OAuth app credentials."""

    redirect_uri: str = Field(min_length=1, max_length=2048)
    client_id: str | None = Field(default=None, min_length=1, max_length=1024)
    client_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    client_id_ref: str | None = Field(default=None, min_length=1, max_length=140)
    client_secret_ref: str | None = Field(default=None, min_length=1, max_length=140)
    change_ticket_id: str | None = None


class TenantAdminMailboxConnectStartRequest(BaseModel):
    """Start mailbox OAuth linking for a tenant."""

    provider: str = Field(default="google", min_length=1, max_length=40)
    account_hint: str | None = Field(default=None, max_length=255)
    change_ticket_id: str | None = None


class TenantAdminMailboxPatchRequest(BaseModel):
    """Patch mailbox status/metadata."""

    status: str | None = Field(default=None, min_length=1, max_length=20)
    sync_cursor: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None
    change_ticket_id: str | None = None


class TenantAdminMailboxSyncRequest(BaseModel):
    """Run mailbox sync for email/calendar ingestion."""

    direction: str = Field(default="bi_directional", min_length=1, max_length=30)
    idempotency_key: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default="cgs-admin", max_length=120)
    max_results: int = Field(default=20, ge=1, le=100)
    calendar_operations: list[dict[str, Any]] | None = None
    change_ticket_id: str | None = None


class TenantAdminMailboxSetPrimaryCalendarRequest(BaseModel):
    """Set tenant mailbox primary calendar."""

    calendar_id: str = Field(min_length=1, max_length=255)
    change_ticket_id: str | None = None


class TenantAdminInsightsReindexRequest(BaseModel):
    """Reindex tenant email insights into vector memory."""

    insight_type: str | None = Field(default=None, max_length=120)
    change_ticket_id: str | None = None


class TenantAdminMessagingProviderPutRequest(BaseModel):
    """Configure tenant messaging provider connectivity/options."""

    enabled: bool = True
    bridge_mode: str = Field(default="local_sidecar", min_length=1, max_length=40)
    account_ref: str | None = Field(default=None, max_length=255)
    session_ref: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    change_ticket_id: str | None = None


class TenantAdminMessagingChatPolicyPutRequest(BaseModel):
    """Configure per-chat messaging access policy."""

    provider: str = Field(default="whatsapp", min_length=1, max_length=40)
    read_enabled: bool = False
    send_enabled: bool = False
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    metadata: dict[str, Any] = Field(default_factory=dict)
    change_ticket_id: str | None = None


class TenantAdminMessagingSendRequest(BaseModel):
    """Queue a tenant messaging outbound send."""

    provider: str = Field(default="whatsapp", min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminMessagingDeleteRequest(BaseModel):
    """Delete tenant messaging records under policy-gated filters."""

    provider: str = Field(default="whatsapp", min_length=1, max_length=40)
    chat_id: str | None = Field(default=None, max_length=255)
    sender_id: str | None = Field(default=None, max_length=255)
    before_created_at: str | None = Field(default=None, max_length=80)
    message_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=5000, ge=1, le=20000)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminWorkerCapabilitiesPutRequest(BaseModel):
    """Replace the allowlisted capability set for one worker node."""

    capabilities: list[str] = Field(default_factory=list)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminWorkerNodeStatusPostRequest(BaseModel):
    """Apply worker node status mutation (quarantine/unquarantine)."""

    health_status: str | None = Field(default=None, min_length=1, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)
    change_ticket_id: str | None = None


class TenantAdminWorkerJobActionPostRequest(BaseModel):
    """Apply worker job control actions (retry/cancel)."""

    reason: str | None = Field(default=None, max_length=500)
    change_ticket_id: str | None = None


class TenantAdminWorkerMessagingGrantPutRequest(BaseModel):
    """Create/update scoped worker messaging grant for one node+chat."""

    allow_read: bool = False
    allow_draft: bool = False
    allow_send: bool = False
    ttl_seconds: int = Field(default=3600, ge=60, le=1_209_600)
    redacted_payload: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminWorkerMessagingGrantDeleteRequest(BaseModel):
    """Revoke one worker messaging grant."""

    reason: str | None = Field(default=None, max_length=500)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminWorkerDelegationGrantPutRequest(BaseModel):
    """Create/update scoped worker delegation grant for one node/resource."""

    resource_scope: str = Field(min_length=1, max_length=1024)
    permissions: list[str] = Field(default_factory=list)
    ttl_seconds: int = Field(default=3600, ge=60, le=1_209_600)
    metadata: dict[str, Any] = Field(default_factory=dict)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminWorkerDelegationGrantDeleteRequest(BaseModel):
    """Revoke one worker delegation grant."""

    reason: str | None = Field(default=None, max_length=500)
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None


class TenantAdminAutomergeExecuteRequest(BaseModel):
    """Execute guarded autonomous PR orchestration for one tenant."""

    repository: str = Field(min_length=3, max_length=255)
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    source_ref: str | None = Field(default=None, min_length=1, max_length=255)
    head_branch: str | None = Field(default=None, min_length=1, max_length=255)
    pr_title: str | None = Field(default=None, max_length=255)
    pr_body: str = ""
    merge_method: str = Field(default="squash", min_length=1, max_length=20)
    commit_title: str | None = Field(default=None, max_length=255)
    commit_message: str | None = None
    required_checks: list[str] = Field(default_factory=lambda: ["CI/CD Pipeline"])
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    max_changed_files: int = Field(default=120, ge=1, le=10000)
    max_additions: int = Field(default=6000, ge=1, le=200000)
    max_deletions: int = Field(default=3000, ge=1, le=200000)
    post_merge_validation_passed: bool = True
    branch_guard_passed: bool = False
    risk_guard_passed: bool = False
    explicitly_elevated: bool = False
    change_ticket_id: str | None = None

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        normalized = value.strip()
        parts = normalized.split("/", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError("repository must be in owner/repo format")
        return normalized

    @field_validator("merge_method")
    @classmethod
    def validate_merge_method(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"merge", "squash", "rebase"}
        if normalized not in allowed:
            raise ValueError(f"merge_method must be one of {sorted(allowed)}")
        return normalized


class TenantAdminChangeCreateRequest(BaseModel):
    """Submit pending high-risk admin change for review."""

    action: str = Field(min_length=1, max_length=100)
    target: str | None = Field(default=None, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class TenantAdminChangeDecisionRequest(BaseModel):
    """Approve or reject a pending admin change."""

    reason: str | None = None


class BlogPublishModels(BaseModel):
    """Model metadata included by the Windows promotions worker."""

    draft: str = Field(min_length=1, max_length=120)
    refine: str = Field(min_length=1, max_length=120)


class BlogPublishRequest(BaseModel):
    """Blog publish adapter payload from Windows promotions worker."""

    idempotency_key: str = Field(min_length=12, max_length=80)
    source: str = Field(min_length=1, max_length=120)
    sha: str = Field(min_length=7, max_length=64, pattern=r"^[A-Fa-f0-9]{7,64}$")
    repo: str = Field(min_length=1, max_length=255)
    release_tag: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    meta_description: str = Field(min_length=1, max_length=320)
    excerpt: str = Field(default="", max_length=500)
    primary_keyword: str = Field(min_length=1, max_length=120)
    content_markdown: str = Field(min_length=1)
    json_ld: dict[str, Any] = Field(default_factory=dict)
    models: BlogPublishModels
    published_at: str = Field(min_length=10, max_length=64)

    @field_validator("idempotency_key")
    @classmethod
    def validate_blog_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("blog-"):
            raise ValueError("idempotency_key must start with blog-")
        return normalized
