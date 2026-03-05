"""Configuration management for Zetherion AI."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from zetherion_ai.admin.tenant_admin_manager import TenantAdminManager
    from zetherion_ai.security.secret_resolver import SecretResolver
    from zetherion_ai.settings_manager import SettingsManager

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DISABLE_ENV_FILE = str(os.getenv("ZETHERION_DISABLE_ENV_FILE", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_SETTINGS_ENV_FILE: str | None = None if _DISABLE_ENV_FILE else ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=_SETTINGS_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Don't try to parse env vars as JSON for complex types
        env_parse_none_str="",
        # Disable JSON parsing for environment variables
        env_parse_enums=True,
    )

    # Discord
    discord_token: SecretStr = Field(description="Discord bot token")
    allowed_user_ids_str: str | None = Field(
        default=None,
        alias="ALLOWED_USER_IDS",
        description="Discord user IDs allowed to interact (comma-separated)",
    )
    allow_all_users: bool = Field(
        default=False, description="Explicitly allow all users when no allowlist is configured"
    )
    owner_user_id: int | None = Field(default=None, description="Bootstrap owner Discord user ID")
    allowlist_strict_startup: bool = Field(
        default=False,
        description="Fail startup when no effective allowlist users are configured",
    )
    allowlist_bootstrap_enabled: bool = Field(
        default=True,
        description="Synchronize OWNER_USER_ID and ALLOWED_USER_IDS into RBAC on startup",
    )

    # PostgreSQL (for RBAC and dynamic settings)
    postgres_dsn: str = Field(
        default="postgresql://zetherion:password@postgres:5432/zetherion",
        description="PostgreSQL connection string",
    )
    postgres_pool_min_size: int = Field(
        default=1,
        ge=1,
        description="Minimum asyncpg pool size per service pool",
    )
    postgres_pool_max_size: int = Field(
        default=5,
        ge=1,
        description="Maximum asyncpg pool size per service pool",
    )

    @property
    def allowed_user_ids(self) -> list[int]:
        """Parse and return allowed user IDs as a list."""
        if self.allowed_user_ids_str is None:
            return []
        value = self.allowed_user_ids_str.strip()
        if not value:
            return []
        return [int(uid.strip()) for uid in value.split(",") if uid.strip()]

    # Gemini (for embeddings)
    gemini_api_key: SecretStr = Field(description="Gemini API key for embeddings")

    # Anthropic (optional, for Claude LLM)
    anthropic_api_key: SecretStr | None = Field(
        default=None, description="Anthropic API key for Claude"
    )

    # OpenAI (optional, alternative LLM)
    openai_api_key: SecretStr | None = Field(default=None, description="OpenAI API key")

    # Groq (optional, for fast cloud inference via OpenAI-compatible API)
    groq_api_key: SecretStr | None = Field(default=None, description="Groq API key")
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model for email classification",
    )
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        description="Groq OpenAI-compatible API base URL",
    )

    # Qdrant
    qdrant_host: str = Field(default="qdrant", description="Qdrant server host")
    qdrant_port: int = Field(default=6333, description="Qdrant server port")
    qdrant_use_tls: bool = Field(default=False, description="Use TLS for Qdrant connection")
    qdrant_cert_path: str | None = Field(
        default=None, description="Path to Qdrant TLS certificate for verification"
    )

    # Application
    environment: str = Field(default="production", description="Environment name")
    log_level: str = Field(default="INFO", description="Logging level")

    # Logging Configuration
    log_to_file: bool = Field(default=True, description="Enable file-based logging")
    log_directory: str = Field(default="logs", description="Directory for log files")
    log_file_max_bytes: int = Field(
        default=52428800,  # 50MB
        description="Max size per log file before rotation",
    )
    log_file_backup_count: int = Field(
        default=10, description="Number of rotated log files to keep"
    )
    log_error_file_enabled: bool = Field(
        default=True, description="Enable separate error log file (WARNING+)"
    )
    log_file_prefix: str = Field(
        default="zetherion_ai", description="Prefix for log file names (set per container)"
    )

    # Testing Configuration
    allow_bot_messages: bool = Field(
        default=False, description="Allow messages from other bots (for E2E testing only)"
    )

    # Dev Agent Configuration
    dev_agent_enabled: bool = Field(
        default=False,
        description="Enable Docker dev-agent monitoring and cleanup automation",
    )
    dev_agent_service_url: str = Field(
        default="http://zetherion-ai-dev-agent:8787",
        description="Base URL for the dev-agent sidecar API",
    )
    dev_agent_bootstrap_secret: str = Field(
        default="",
        description="One-time bootstrap secret for dev-agent provisioning",
    )
    dev_agent_cleanup_hour: int = Field(
        default=2,
        description="Local cleanup schedule hour (0-23) for dev-agent",
    )
    dev_agent_cleanup_minute: int = Field(
        default=30,
        description="Local cleanup schedule minute (0-59) for dev-agent",
    )
    dev_agent_approval_reprompt_hours: int = Field(
        default=24,
        description="Hours before re-prompting for pending project cleanup approvals",
    )
    dev_agent_discord_channel_id: str = Field(
        default="",
        description="Discord channel ID used by dev-agent for events/prompts",
    )
    dev_agent_discord_guild_id: str = Field(
        default="",
        description="Discord guild ID used by dev-agent for events/prompts",
    )
    dev_agent_webhook_name: str = Field(
        default="zetherion-dev-agent",
        description="Webhook username for the local dev agent",
    )
    dev_agent_webhook_id: str = Field(
        default="",
        description="Optional Discord webhook ID to validate dev-agent ingestion source",
    )
    dev_journal_retention_days: int = Field(
        default=120,
        description="Days to retain dev journal entries before pruning",
    )

    @property
    def log_file_path(self) -> str:
        """Get the full log file path."""
        return f"{self.log_directory}/{self.log_file_prefix}.log"

    @property
    def error_log_file_path(self) -> str:
        """Get the error log file path."""
        return f"{self.log_directory}/{self.log_file_prefix}_error.log"

    # Model Configuration
    # Updated: 2026-02-05
    # Check for latest versions:
    # - Claude: https://docs.anthropic.com/en/docs/about-claude/models
    # - OpenAI: https://platform.openai.com/docs/models
    # - Gemini: https://ai.google.dev/gemini-api/docs/models
    claude_model: str = Field(
        default="claude-sonnet-4-5-20250929", description="Claude model to use for complex tasks"
    )
    openai_model: str = Field(
        default="gpt-5.2",  # or gpt-5.2-2026-01-15 for specific version
        description="OpenAI model to use for complex tasks",
    )
    router_model: str = Field(
        default="gemini-2.5-flash",  # Stable, fast model for routing
        description="Gemini model to use for routing and simple queries",
    )
    embedding_model: str = Field(default="text-embedding-004", description="Gemini embedding model")

    # Router Backend Configuration
    router_backend: str = Field(
        default="gemini", description="Router backend: 'gemini', 'ollama', or 'groq'"
    )

    # Ollama Configuration (Generation Container)
    # This container handles generation and embeddings
    ollama_host: str = Field(default="ollama", description="Ollama generation container host")
    ollama_port: int = Field(default=11434, description="Ollama generation API port")
    ollama_generation_model: str = Field(
        default="llama3.1:8b", description="Ollama model for generation (larger, capable)"
    )
    ollama_embedding_model: str = Field(
        default="nomic-embed-text", description="Ollama model for embeddings (768 dimensions)"
    )
    ollama_timeout: int = Field(default=30, description="Ollama API timeout in seconds")

    # Ollama Router Configuration (Dedicated Router Container)
    # Separate container for fast message classification
    ollama_router_host: str = Field(
        default="ollama-router", description="Ollama router container host (dedicated)"
    )
    ollama_router_port: int = Field(default=11434, description="Ollama router API port")
    ollama_router_model: str = Field(
        default="llama3.2:3b", description="Ollama model for routing (small, fast)"
    )

    # Embeddings Backend Configuration
    embeddings_backend: str = Field(
        default="openai",
        description="Embeddings backend: openai (default), gemini, or ollama",
    )
    openai_embedding_model: str = Field(
        default="text-embedding-3-large", description="OpenAI embedding model"
    )
    openai_embedding_dimensions: int = Field(
        default=3072, description="Embedding dimensions for OpenAI model"
    )
    rag_allowed_providers: str = Field(
        default="groq,openai,anthropic",
        description="Comma-separated providers allowed for /api/v1/rag/query overrides",
    )
    rag_allowed_models: str = Field(
        default="",
        description="Comma-separated model allowlist for /api/v1/rag/query overrides",
    )

    # Encryption Configuration (Phase 5A)
    encryption_passphrase: SecretStr = Field(
        description="Master passphrase for encryption key derivation (min 16 chars, required)"
    )
    encryption_salt_path: str = Field(
        default="data/salt.bin", description="Path to store the encryption salt file"
    )
    encryption_strict: bool = Field(
        default=False, description="Raise errors on decryption failure instead of passing through"
    )

    # InferenceBroker Configuration (Phase 5B)
    inference_broker_enabled: bool = Field(
        default=True, description="Enable smart multi-provider routing via InferenceBroker"
    )
    cost_tracking_enabled: bool = Field(
        default=True, description="Track costs per provider and task type"
    )

    # Model Registry Configuration (Phase 5B.1)
    model_discovery_enabled: bool = Field(
        default=True, description="Enable automatic model discovery from provider APIs"
    )
    model_refresh_hours: int = Field(
        default=24, description="Hours between model discovery refreshes"
    )
    anthropic_tier: str = Field(
        default="balanced", description="Default tier for Anthropic models: quality, balanced, fast"
    )
    openai_tier: str = Field(
        default="balanced", description="Default tier for OpenAI models: quality, balanced, fast"
    )
    google_tier: str = Field(
        default="fast", description="Default tier for Google models: quality, balanced, fast"
    )

    # Cost Tracking Configuration (Phase 5B.1)
    cost_db_path: str = Field(
        default="data/costs.db", description="Path to SQLite database for cost tracking"
    )
    daily_budget_usd: float | None = Field(
        default=None, description="Optional daily budget threshold in USD for alerts"
    )
    monthly_budget_usd: float | None = Field(
        default=None, description="Optional monthly budget threshold in USD for alerts"
    )
    budget_warning_pct: float = Field(
        default=80.0, description="Percentage of budget at which to send warning (0-100)"
    )

    # Notification Configuration (Phase 5B.1)
    notifications_enabled: bool = Field(
        default=True, description="Enable cost and model notifications"
    )
    notify_on_new_models: bool = Field(
        default=True, description="Send notification when new models are discovered"
    )
    notify_on_deprecation: bool = Field(
        default=True, description="Send notification when models are deprecated"
    )
    notify_on_missing_pricing: bool = Field(
        default=False, description="Send notification for models without pricing data"
    )
    daily_summary_enabled: bool = Field(
        default=False, description="Send daily cost summary notification"
    )
    daily_summary_hour: int = Field(default=9, description="Hour (0-23) to send daily cost summary")
    provider_issue_alerts_enabled: bool = Field(
        default=True,
        description=(
            "Send proactive alerts when paid providers fail due to " "auth/billing/rate-limits"
        ),
    )
    provider_issue_alert_cooldown_seconds: int = Field(
        default=3600,
        description="Minimum interval between repeated alerts for the same provider issue",
    )
    provider_probe_enabled: bool = Field(
        default=True,
        description="Enable periodic low-cost paid-provider readiness probes",
    )
    provider_probe_interval_seconds: int = Field(
        default=1800,
        description="Seconds between periodic paid-provider readiness probes",
    )

    # Profile System Configuration (Phase 5C)
    profile_inference_enabled: bool = Field(
        default=True, description="Enable profile extraction from conversations"
    )
    profile_tier1_only: bool = Field(
        default=False, description="Only use Tier 1 (free regex) inference for profiles"
    )
    profile_confidence_threshold: float = Field(
        default=0.6, description="Minimum confidence to auto-apply profile updates"
    )
    profile_cache_ttl: int = Field(
        default=300, description="Profile cache TTL in seconds (default 5 min)"
    )
    profile_db_path: str = Field(
        default="data/profiles.db",
        description="Path to SQLite database for profile operational data",
    )
    profile_max_pending_confirmations: int = Field(
        default=5, description="Maximum pending confirmations per user"
    )
    profile_confirmation_expiry_hours: int = Field(
        default=72, description="Hours before pending confirmations expire"
    )

    # Employment Profile Defaults (Phase 5C.1)
    default_formality: float = Field(
        default=0.5, description="Initial formality level (0=casual, 1=formal)"
    )
    default_verbosity: float = Field(
        default=0.5, description="Initial verbosity level (0=terse, 1=detailed)"
    )
    default_proactivity: float = Field(
        default=0.3, description="Initial proactivity level (0=reactive, 1=proactive)"
    )
    trust_evolution_rate: float = Field(
        default=0.05, description="How fast trust builds per positive interaction"
    )

    # Skills Service Configuration (Phase 5D)
    skills_service_url: str = Field(
        default="http://zetherion-ai-skills:8080",
        description="URL of the skills service (internal Docker network)",
    )
    skills_api_secret: SecretStr | None = Field(
        default=None, description="Shared secret for skills service authentication"
    )
    skills_request_timeout: int = Field(
        default=30, description="Timeout in seconds for skills service requests"
    )

    # Docs Knowledge Configuration (Phase 14)
    docs_knowledge_enabled: bool = Field(
        default=True, description="Enable docs-backed setup/help responses"
    )
    docs_knowledge_root: str = Field(
        default="docs",
        description="Path to markdown docs to index for setup/help answers",
    )
    docs_knowledge_state_path: str = Field(
        default="data/docs_knowledge_state.json",
        description="Path to local docs index state file",
    )
    docs_knowledge_gap_log_path: str = Field(
        default="data/docs_unknown_questions.jsonl",
        description="Path to unresolved docs-question log",
    )
    docs_knowledge_sync_interval_seconds: int = Field(
        default=300,
        description="How frequently docs sync is allowed to run (seconds)",
    )
    docs_knowledge_max_hits: int = Field(
        default=6,
        description="Maximum retrieved docs chunks used per question",
    )
    docs_knowledge_min_score: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum vector similarity score for docs context",
    )

    # Gmail Integration Configuration (Phase 8)
    google_client_id: str | None = Field(
        default=None, description="Google OAuth2 client ID for Gmail integration"
    )
    google_client_secret: SecretStr | None = Field(
        default=None, description="Google OAuth2 client secret"
    )
    google_redirect_uri: str = Field(
        default="http://localhost:8080/gmail/callback",
        description="OAuth2 callback URL for Gmail",
    )
    work_router_enabled: bool = Field(
        default=False,
        description="Enable provider-agnostic email/task/calendar router",
    )
    provider_outlook_enabled: bool = Field(
        default=False,
        description="Enable Outlook provider adapter (scaffold/feature-flagged)",
    )
    email_security_gate_enabled: bool = Field(
        default=True,
        description="Run the mandatory security gate for inbound email ingestion",
    )
    local_extraction_required: bool = Field(
        default=False,
        description=(
            "Require larger local extraction path; when disabled, cloud-first extraction "
            "runs with local as final fallback"
        ),
    )

    # GitHub Skill Configuration (Phase 7)
    github_token: SecretStr | None = Field(
        default=None, description="GitHub personal access token for API access"
    )
    github_default_repo: str | None = Field(
        default=None, description="Default repository in owner/repo format"
    )
    github_api_timeout: int = Field(
        default=30, description="Timeout in seconds for GitHub API requests"
    )

    # Public API Configuration (Phase 11)
    api_host: str = Field(
        default="0.0.0.0",  # nosec B104 - Intentional for Docker container
        description="Public API server bind host",
    )
    api_port: int = Field(default=8443, description="Public API server port")
    api_jwt_secret: SecretStr | None = Field(
        default=None, description="JWT signing secret for session tokens"
    )
    cgs_gateway_host: str = Field(
        default="0.0.0.0",  # nosec B104 - Intentional for Docker container
        description="CGS gateway bind host",
    )
    cgs_gateway_port: int = Field(default=8743, description="CGS gateway bind port")
    cgs_gateway_allowed_origins: str | None = Field(
        default=None,
        description="Comma-separated CORS origins for CGS gateway",
    )
    cgs_document_mutation_rpm: int = Field(
        default=30,
        description="Per-tenant document mutation limit per minute in CGS gateway",
    )
    cgs_admin_mutation_rpm: int = Field(
        default=20,
        description="Per-tenant admin mutation limit per minute in CGS gateway",
    )
    cgs_auth_jwks_url: str | None = Field(
        default=None,
        description="JWKS URL used to validate CGS JWT bearer tokens",
    )
    cgs_auth_issuer: str | None = Field(
        default=None,
        description="Expected JWT issuer for CGS auth tokens",
    )
    cgs_auth_audience: str | None = Field(
        default=None,
        description="Expected JWT audience for CGS auth tokens",
    )
    zetherion_public_api_base_url: str = Field(
        default="http://zetherion-ai-traefik:8443",
        description="Base URL for upstream Zetherion public API",
    )
    zetherion_skills_api_base_url: str = Field(
        default="http://zetherion-ai-traefik:8080",
        description="Base URL for upstream Zetherion skills API",
    )
    zetherion_skills_api_secret: SecretStr | None = Field(
        default=None,
        description="Optional override secret for Zetherion Skills API from CGS gateway",
    )

    # Health Monitoring Configuration (Phase 10B)
    health_analysis_enabled: bool = Field(
        default=True, description="Enable health analysis and metrics collection"
    )
    self_healing_enabled: bool = Field(
        default=True, description="Enable automatic self-healing actions"
    )

    # Auto-Update Configuration (Phase 10A)
    auto_update_enabled: bool = Field(default=False, description="Enable automatic update checking")
    auto_update_repo: str = Field(
        default="", description="GitHub repo for update checks (owner/repo)"
    )
    auto_update_check_interval_minutes: int = Field(
        default=15,
        description="Minutes between automatic update checks",
    )
    update_require_approval: bool = Field(
        default=False, description="Require owner approval before applying updates"
    )
    auto_update_pause_on_failure: bool = Field(
        default=True, description="Pause future auto-rollouts after a failed update"
    )
    updater_service_url: str = Field(default="", description="URL of the updater sidecar service")
    updater_secret: str = Field(
        default="", description="Shared secret for updater sidecar authentication"
    )
    updater_secret_path: str = Field(
        default="/app/data/.updater-secret",
        description="Shared updater secret file path used when env secret is unset",
    )
    updater_state_path: str = Field(
        default="/app/data/updater-state.json",
        description="Updater sidecar state file path for color/pause metadata",
    )
    updater_verify_signatures: bool = Field(
        default=True,
        description="Require release signature verification before applying updates",
    )
    updater_verify_identity: str = Field(
        default="",
        description="Expected Cosign certificate identity for release signatures",
    )
    updater_verify_oidc_issuer: str = Field(
        default="https://token.actions.githubusercontent.com",
        description="Expected OIDC issuer for Cosign keyless verification",
    )
    updater_verify_rekor_url: str = Field(
        default="https://rekor.sigstore.dev",
        description="Rekor transparency log URL for signature verification",
    )
    updater_release_manifest_asset: str = Field(
        default="release-manifest.json",
        description="Release asset name for signed update manifest",
    )
    updater_release_signature_asset: str = Field(
        default="release-manifest.sig",
        description="Release asset name for manifest signature",
    )
    updater_release_certificate_asset: str = Field(
        default="release-manifest.pem",
        description="Release asset name for signing certificate",
    )

    # Telemetry Configuration (Phase 10C)
    telemetry_sharing_enabled: bool = Field(
        default=False, description="Enable telemetry sharing with central instance"
    )
    telemetry_consent_categories: str = Field(
        default="", description="Comma-separated telemetry categories to share"
    )
    telemetry_central_url: str = Field(
        default="", description="URL of the central telemetry receiver"
    )
    telemetry_api_key: str = Field(default="", description="API key issued by central instance")
    telemetry_central_mode: bool = Field(
        default=False, description="Enable central telemetry receiver mode"
    )
    telemetry_instance_id: str = Field(
        default="", description="Unique instance ID for telemetry (auto-generated)"
    )
    telemetry_report_interval: int = Field(
        default=86400, description="Seconds between telemetry reports (default 24h)"
    )

    # Analytics / App Watcher Configuration
    analytics_event_retention_days: int = Field(
        default=90, description="Days to retain raw web analytics events"
    )
    analytics_replay_retention_days: int = Field(
        default=14, description="Days to retain session replay chunks"
    )
    analytics_replay_enabled_default: bool = Field(
        default=False, description="Default replay capture setting for tenants"
    )
    analytics_replay_sample_rate_default: float = Field(
        default=0.1, description="Default replay sampling rate for tenants (0.0-1.0)"
    )
    analytics_jobs_enabled: bool = Field(
        default=True, description="Enable periodic analytics aggregation and retention jobs"
    )
    analytics_hourly_job_interval_seconds: int = Field(
        default=3600, description="Interval for hourly analytics job loop"
    )
    analytics_daily_job_interval_seconds: int = Field(
        default=86400, description="Interval for daily analytics job loop"
    )

    object_storage_backend: str = Field(
        default="local",
        validation_alias=AliasChoices("OBJECT_STORAGE_BACKEND", "REPLAY_STORAGE_BACKEND"),
        description="Replay chunk storage backend: none, local, s3",
    )
    object_storage_local_path: str = Field(
        default="data/replay_chunks",
        validation_alias=AliasChoices("OBJECT_STORAGE_LOCAL_PATH", "REPLAY_STORAGE_LOCAL_PATH"),
        description="Local object storage path for replay chunks",
    )
    object_storage_bucket: str = Field(
        default="",
        validation_alias=AliasChoices("OBJECT_STORAGE_BUCKET", "REPLAY_STORAGE_BUCKET"),
        description="Object storage bucket for replay chunks when object_storage_backend=s3",
    )
    object_storage_region: str = Field(
        default="",
        validation_alias=AliasChoices("OBJECT_STORAGE_REGION", "REPLAY_STORAGE_REGION"),
        description="Object storage region for replay chunks",
    )
    object_storage_endpoint: str = Field(
        default="",
        validation_alias=AliasChoices("OBJECT_STORAGE_ENDPOINT", "REPLAY_STORAGE_ENDPOINT"),
        description="Optional custom S3-compatible object storage endpoint",
    )
    object_storage_access_key_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "OBJECT_STORAGE_ACCESS_KEY_ID", "REPLAY_STORAGE_ACCESS_KEY_ID"
        ),
        description="Optional object storage access key ID for replay chunks",
    )
    object_storage_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OBJECT_STORAGE_SECRET_ACCESS_KEY", "REPLAY_STORAGE_SECRET_ACCESS_KEY"
        ),
        description="Optional object storage secret access key for replay chunks",
    )
    object_storage_force_path_style: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "OBJECT_STORAGE_FORCE_PATH_STYLE", "REPLAY_STORAGE_FORCE_PATH_STYLE"
        ),
        description="Force path-style addressing for S3-compatible object storage",
    )

    release_marker_signing_secret: SecretStr | None = Field(
        default=None,
        description="Optional shared secret for signed release marker ingestion",
    )
    release_marker_signature_ttl_seconds: int = Field(
        default=300,
        description="Allowed clock skew/age window for signed release marker requests",
    )
    cgs_blog_publish_url: str = Field(
        default="",
        description="CGS publish API endpoint used for post-deploy blog publication",
    )
    cgs_blog_publish_token: SecretStr | None = Field(
        default=None,
        description="Auth token for CGS blog publish API",
    )
    blog_model_primary: str = Field(
        default="gpt-5.2",
        description="Primary high-tier model for blog drafting",
    )
    blog_model_secondary: str = Field(
        default="claude-sonnet-4-6",
        description="Secondary high-tier model for blog refinement",
    )
    blog_publish_enabled: bool = Field(
        default=True,
        description="Enable post-deploy blog generation/publishing workflow",
    )
    release_auto_increment_enabled: bool = Field(
        default=True,
        description="Enable automatic SemVer release increment after successful main deploy",
    )

    app_watcher_trust_mode: str = Field(
        default="recommend_only",
        description="Autonomy mode: recommend_only, guarded_autopilot, full_autonomous",
    )
    app_watcher_autopilot_enabled: bool = Field(
        default=False, description="Enable guarded autopilot rollout path"
    )
    app_watcher_global_kill_switch: bool = Field(
        default=False, description="Disable all autonomous app-watcher actions"
    )
    messaging_ingestion_kill_switch: bool = Field(
        default=False, description="Global kill switch for tenant messaging ingestion actions"
    )
    messaging_send_kill_switch: bool = Field(
        default=False, description="Global kill switch for tenant messaging send actions"
    )
    auto_merge_execution_kill_switch: bool = Field(
        default=False, description="Global kill switch for autonomous PR merge execution"
    )
    auto_merge_policy_enabled: bool = Field(
        default=False, description="Enable trust-policy auto-merge execution path"
    )
    security_default_trust_tier: str = Field(
        default="tier3", description="Default trust tier for trust-policy action gating"
    )

    # Security Pipeline Configuration (Phase 13)
    security_tier2_enabled: bool = Field(
        default=True, description="Enable AI-based Tier 2 security analysis on all messages"
    )
    security_block_threshold: float = Field(
        default=0.6, description="Threat score threshold for blocking messages (0.0-1.0)"
    )
    security_flag_threshold: float = Field(
        default=0.3, description="Threat score threshold for flagging messages (0.0-1.0)"
    )
    security_bypass_enabled: bool = Field(
        default=False,
        description="Disable all security checks (testing only, logged as security event)",
    )
    security_notify_owner: bool = Field(
        default=True, description="DM the bot owner when messages are flagged"
    )

    # Queue Configuration (Phase 13)
    queue_enabled: bool = Field(default=True, description="Enable priority message queue")
    queue_interactive_workers: int = Field(
        default=3, description="Interactive queue workers (P0/P1)"
    )
    queue_background_workers: int = Field(default=2, description="Background queue workers (P2/P3)")
    queue_poll_interval_ms: int = Field(
        default=100, description="Interactive worker poll interval (ms)"
    )
    queue_background_poll_ms: int = Field(
        default=1000, description="Background worker poll interval (ms)"
    )
    queue_stale_timeout_seconds: int = Field(
        default=300, description="Stale processing timeout (seconds)"
    )
    queue_max_retry_attempts: int = Field(
        default=3, description="Max retry attempts before dead letter"
    )

    @field_validator(
        "profile_confidence_threshold",
        "default_formality",
        "default_verbosity",
        "default_proactivity",
        "trust_evolution_rate",
        "analytics_replay_sample_rate_default",
    )
    @classmethod
    def validate_float_0_1(cls, v: float) -> float:
        """Validate float values are between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError(f"Value must be between 0 and 1, got: {v}")
        return v

    @field_validator("app_watcher_trust_mode")
    @classmethod
    def validate_app_watcher_trust_mode(cls, v: str) -> str:
        """Validate app watcher trust mode."""
        valid_modes = ["recommend_only", "guarded_autopilot", "full_autonomous"]
        if v not in valid_modes:
            raise ValueError(f"app_watcher_trust_mode must be one of {valid_modes}, got: {v}")
        return v

    @field_validator("security_default_trust_tier")
    @classmethod
    def validate_security_default_trust_tier(cls, v: str) -> str:
        """Validate trust tier default used by policy evaluator."""
        normalized = v.strip().lower()
        allowed = {"tier0", "tier1", "tier2", "tier3", "tier4", "0", "1", "2", "3", "4"}
        if normalized not in allowed:
            raise ValueError("security_default_trust_tier must be one of tier0..tier4")
        return normalized

    @field_validator("object_storage_backend")
    @classmethod
    def validate_object_storage_backend(cls, v: str) -> str:
        """Validate object storage backend choice."""
        valid_backends = ["none", "local", "s3"]
        if v not in valid_backends:
            raise ValueError(f"object_storage_backend must be one of {valid_backends}, got: {v}")
        return v

    @field_validator("router_backend")
    @classmethod
    def validate_router_backend(cls, v: str) -> str:
        """Validate router backend choice."""
        valid_backends = ["gemini", "ollama", "groq"]
        if v not in valid_backends:
            raise ValueError(f"router_backend must be one of {valid_backends}, got: {v}")
        return v

    @field_validator("embeddings_backend")
    @classmethod
    def validate_embeddings_backend(cls, v: str) -> str:
        """Validate embeddings backend choice."""
        valid_backends = ["ollama", "gemini", "openai"]
        if v not in valid_backends:
            raise ValueError(f"embeddings_backend must be one of {valid_backends}, got: {v}")
        return v

    @field_validator("anthropic_tier", "openai_tier", "google_tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        """Validate tier setting."""
        valid_tiers = ["quality", "balanced", "fast"]
        if v not in valid_tiers:
            raise ValueError(f"tier must be one of {valid_tiers}, got: {v}")
        return v

    @field_validator("budget_warning_pct")
    @classmethod
    def validate_budget_warning_pct(cls, v: float) -> float:
        """Validate budget warning percentage is between 0 and 100."""
        if not 0 <= v <= 100:
            raise ValueError(f"budget_warning_pct must be between 0 and 100, got: {v}")
        return v

    @field_validator("daily_summary_hour", "dev_agent_cleanup_hour")
    @classmethod
    def validate_daily_summary_hour(cls, v: int) -> int:
        """Validate hour values are between 0 and 23."""
        if not 0 <= v <= 23:
            raise ValueError(f"hour value must be between 0 and 23, got: {v}")
        return v

    @field_validator("dev_agent_cleanup_minute")
    @classmethod
    def validate_minute_0_59(cls, v: int) -> int:
        """Validate minute values are between 0 and 59."""
        if not 0 <= v <= 59:
            raise ValueError(f"minute value must be between 0 and 59, got: {v}")
        return v

    @field_validator(
        "analytics_hourly_job_interval_seconds",
        "analytics_daily_job_interval_seconds",
        "release_marker_signature_ttl_seconds",
        "provider_issue_alert_cooldown_seconds",
        "provider_probe_interval_seconds",
    )
    @classmethod
    def validate_positive_seconds(cls, v: int) -> int:
        """Validate second-based durations are positive."""
        if v <= 0:
            raise ValueError(f"Duration must be > 0, got: {v}")
        return v

    @model_validator(mode="after")
    def validate_encryption_config(self) -> Self:
        """Validate encryption configuration consistency."""
        passphrase = self.encryption_passphrase.get_secret_value()
        if not passphrase:
            raise ValueError(
                "ENCRYPTION_PASSPHRASE is required — the bot cannot start without encryption"
            )
        if len(passphrase) < 16:
            raise ValueError("encryption_passphrase must be at least 16 characters")
        return self

    @property
    def ollama_url(self) -> str:
        """Get the full Ollama URL (generation container)."""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def ollama_router_url(self) -> str:
        """Get the Ollama router URL (dedicated routing container)."""
        return f"http://{self.ollama_router_host}:{self.ollama_router_port}"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment.lower() == "development"

    @property
    def qdrant_url(self) -> str:
        """Get the full Qdrant URL."""
        scheme = "https" if self.qdrant_use_tls else "http"
        return f"{scheme}://{self.qdrant_host}:{self.qdrant_port}"

    # Backward-compatible accessors (deprecated): prefer object_storage_*.
    @property
    def replay_storage_backend(self) -> str:
        return self.object_storage_backend

    @property
    def replay_storage_local_path(self) -> str:
        return self.object_storage_local_path

    @property
    def replay_storage_bucket(self) -> str:
        return self.object_storage_bucket

    @property
    def replay_storage_region(self) -> str:
        return self.object_storage_region

    @property
    def replay_storage_endpoint(self) -> str:
        return self.object_storage_endpoint

    @property
    def replay_storage_access_key_id(self) -> str:
        return self.object_storage_access_key_id

    @property
    def replay_storage_secret_access_key(self) -> SecretStr | None:
        return self.object_storage_secret_access_key

    @property
    def replay_storage_force_path_style(self) -> bool:
        return self.object_storage_force_path_style


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]  # Pydantic loads from env vars


# ---------------------------------------------------------------------------
# Dynamic settings support (Workstream 4)
# ---------------------------------------------------------------------------

_settings_manager: SettingsManager | None = None
_tenant_admin_manager: TenantAdminManager | None = None


def set_settings_manager(mgr: SettingsManager) -> None:
    """Register the runtime settings manager for dynamic config."""
    global _settings_manager
    _settings_manager = mgr


def get_settings_manager() -> SettingsManager | None:
    """Get the registered settings manager (or None if not yet registered)."""
    return _settings_manager


def set_tenant_admin_manager(mgr: TenantAdminManager | None) -> None:
    """Register tenant-admin manager for tenant-scoped dynamic reads."""
    global _tenant_admin_manager
    _tenant_admin_manager = mgr


def get_tenant_admin_manager() -> TenantAdminManager | None:
    """Get the registered tenant-admin manager."""
    return _tenant_admin_manager


def get_dynamic(namespace: str, key: str, default: Any = None) -> Any:
    """Get a setting with cascade: DB override -> .env -> default.

    Synchronous. Never blocks on DB (reads from in-memory cache).

    Args:
        namespace: Setting namespace (e.g. "models", "tuning").
        key: Setting key (e.g. "claude_model").
        default: Fallback if not found in DB or .env.

    Returns:
        The setting value.
    """
    if _settings_manager is not None:
        val = _settings_manager.get(namespace, key)
        if val is not None:
            return val

    settings = get_settings()
    for attr in (f"{namespace}_{key}", key):
        if hasattr(settings, attr):
            env_val = getattr(settings, attr)
            if env_val is not None:
                return env_val

    return default


def get_dynamic_for_tenant(tenant_id: str, namespace: str, key: str, default: Any = None) -> Any:
    """Get a tenant-scoped setting with fallback to global dynamic settings."""
    if _tenant_admin_manager is not None:
        value = _tenant_admin_manager.get_setting_cached(tenant_id, namespace, key)
        if value is not None:
            return value
    return get_dynamic(namespace, key, default)


# ---------------------------------------------------------------------------
# Secret resolver support (Phase 13)
# ---------------------------------------------------------------------------

_secret_resolver: SecretResolver | None = None


def set_secret_resolver(resolver: SecretResolver) -> None:
    """Register the secret resolver for encrypted secret retrieval."""
    global _secret_resolver
    _secret_resolver = resolver


def get_secret_resolver() -> SecretResolver | None:
    """Get the registered secret resolver (or None if not yet registered)."""
    return _secret_resolver


def get_secret(name: str, default: str | None = None) -> str | None:
    """Get a secret with cascade: DB (encrypted) -> .env -> default.

    Synchronous. Never blocks on DB (reads from in-memory cache).

    Args:
        name: Secret name (e.g. ``"anthropic_api_key"``).
        default: Fallback if not found in DB or .env.

    Returns:
        The secret value.
    """
    if _secret_resolver is not None:
        return _secret_resolver.get_secret(name, default)
    return default


def get_secret_for_tenant(tenant_id: str, name: str, default: str | None = None) -> str | None:
    """Get a tenant-scoped secret with fallback to global resolver."""
    if _tenant_admin_manager is not None:
        value = _tenant_admin_manager.get_secret_cached(tenant_id, name)
        if value is not None:
            return value
    return get_secret(name, default)
