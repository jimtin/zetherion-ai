"""Configuration management for SecureClaw."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Don't try to parse list fields as JSON
        env_parse_none_str="",
    )

    # Discord
    discord_token: SecretStr = Field(description="Discord bot token")
    allowed_user_ids: list[int] = Field(
        default_factory=list, description="Discord user IDs allowed to interact"
    )

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, v: str | list[int] | None) -> list[int]:
        """Parse comma-separated user IDs from environment variable."""
        if v is None or v == "":
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        return []

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

    # Application
    environment: str = Field(default="production", description="Environment name")
    log_level: str = Field(default="INFO", description="Logging level")

    # Logging Configuration
    log_to_file: bool = Field(default=True, description="Enable file-based logging")
    log_directory: str = Field(default="logs", description="Directory for log files")
    log_file_max_bytes: int = Field(
        default=10485760,  # 10MB
        description="Max size per log file before rotation",
    )
    log_file_backup_count: int = Field(default=5, description="Number of rotated log files to keep")

    @property
    def log_file_path(self) -> str:
        """Get the full log file path."""
        return f"{self.log_directory}/secureclaw.log"

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
        default="gpt-4o",  # or gpt-4o-2024-11-20 for specific version
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

    # Ollama Configuration
    ollama_host: str = Field(default="ollama", description="Ollama container host")
    ollama_port: int = Field(default=11434, description="Ollama API port")
    ollama_router_model: str = Field(default="llama3.1:8b", description="Ollama model for routing")
    ollama_timeout: int = Field(default=30, description="Ollama API timeout in seconds")

    @field_validator("router_backend")
    @classmethod
    def validate_router_backend(cls, v: str) -> str:
        """Validate router backend choice."""
        valid_backends = ["gemini", "ollama"]
        if v not in valid_backends:
            raise ValueError(f"router_backend must be one of {valid_backends}, got: {v}")
        return v

    @property
    def ollama_url(self) -> str:
        """Get the full Ollama URL."""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment.lower() == "development"

    @property
    def qdrant_url(self) -> str:
        """Get the full Qdrant URL."""
        return f"http://{self.qdrant_host}:{self.qdrant_port}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]  # Pydantic loads from env vars
