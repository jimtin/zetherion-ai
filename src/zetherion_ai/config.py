"""Configuration management for Zetherion AI."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from zetherion_ai.settings_manager import SettingsManager

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
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

    # PostgreSQL (for RBAC and dynamic settings)
    postgres_dsn: str = Field(
        default="postgresql://zetherion:password@postgres:5432/zetherion",
        description="PostgreSQL connection string",
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
    dev_agent_webhook_name: str = Field(
        default="zetherion-dev-agent",
        description="Webhook username for the local dev agent",
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
        default="gemini", description="Router backend: 'gemini' or 'ollama'"
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
        default="ollama",
        description="Embeddings backend: ollama (default), gemini, or openai",
    )
    openai_embedding_model: str = Field(
        default="text-embedding-3-large", description="OpenAI embedding model"
    )
    openai_embedding_dimensions: int = Field(
        default=3072, description="Embedding dimensions for OpenAI model"
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
        default="http://zetherion_ai-skills:8080",
        description="URL of the skills service (internal Docker network)",
    )
    skills_api_secret: SecretStr | None = Field(
        default=None, description="Shared secret for skills service authentication"
    )
    skills_request_timeout: int = Field(
        default=30, description="Timeout in seconds for skills service requests"
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
    update_require_approval: bool = Field(
        default=True, description="Require owner approval before applying updates"
    )
    updater_service_url: str = Field(default="", description="URL of the updater sidecar service")
    updater_secret: str = Field(
        default="", description="Shared secret for updater sidecar authentication"
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

    @field_validator(
        "profile_confidence_threshold",
        "default_formality",
        "default_verbosity",
        "default_proactivity",
        "trust_evolution_rate",
    )
    @classmethod
    def validate_float_0_1(cls, v: float) -> float:
        """Validate float values are between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError(f"Value must be between 0 and 1, got: {v}")
        return v

    @field_validator("router_backend")
    @classmethod
    def validate_router_backend(cls, v: str) -> str:
        """Validate router backend choice."""
        valid_backends = ["gemini", "ollama"]
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

    @field_validator("daily_summary_hour")
    @classmethod
    def validate_daily_summary_hour(cls, v: int) -> int:
        """Validate daily summary hour is between 0 and 23."""
        if not 0 <= v <= 23:
            raise ValueError(f"daily_summary_hour must be between 0 and 23, got: {v}")
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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]  # Pydantic loads from env vars


# ---------------------------------------------------------------------------
# Dynamic settings support (Workstream 4)
# ---------------------------------------------------------------------------

_settings_manager: SettingsManager | None = None


def set_settings_manager(mgr: SettingsManager) -> None:
    """Register the runtime settings manager for dynamic config."""
    global _settings_manager
    _settings_manager = mgr


def get_settings_manager() -> SettingsManager | None:
    """Get the registered settings manager (or None if not yet registered)."""
    return _settings_manager


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
    return default
