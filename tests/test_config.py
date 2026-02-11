"""Tests for configuration management."""

import pytest
from pydantic import SecretStr, ValidationError


def create_test_settings(**kwargs):
    """Helper to create Settings instance without loading .env file."""
    from zetherion_ai.config import Settings

    # Disable .env file loading for tests
    return Settings(_env_file=None, **kwargs)


class TestSettingsInitialization:
    """Tests for Settings initialization from environment variables."""

    def test_settings_from_env_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Settings initialization with minimal required environment variables."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.discord_token.get_secret_value() == "test-discord-token"
        assert settings.gemini_api_key.get_secret_value() == "test-gemini-key"

    def test_settings_from_env_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Settings initialization with all environment variables."""
        monkeypatch.setenv("DISCORD_TOKEN", "discord-token-123")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key-456")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key-789")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key-abc")
        monkeypatch.setenv("ALLOWED_USER_IDS", "111,222,333")
        monkeypatch.setenv("QDRANT_HOST", "custom-host")
        monkeypatch.setenv("QDRANT_PORT", "7000")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-5-20251101")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("ROUTER_MODEL", "gemini-2.0-flash-lite")
        monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-005")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.discord_token.get_secret_value() == "discord-token-123"
        assert settings.gemini_api_key.get_secret_value() == "gemini-key-456"
        assert settings.anthropic_api_key.get_secret_value() == "anthropic-key-789"
        assert settings.openai_api_key.get_secret_value() == "openai-key-abc"
        assert settings.allowed_user_ids == [111, 222, 333]
        assert settings.qdrant_host == "custom-host"
        assert settings.qdrant_port == 7000
        assert settings.environment == "development"
        assert settings.log_level == "DEBUG"
        assert settings.claude_model == "claude-opus-4-5-20251101"
        assert settings.openai_model == "gpt-4o-mini"
        assert settings.router_model == "gemini-2.0-flash-lite"
        assert settings.embedding_model == "text-embedding-005"

    def test_settings_missing_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Settings raises ValidationError when required fields are missing."""
        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        # Clear all environment variables
        for key in ["DISCORD_TOKEN", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]:
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(ValidationError) as exc_info:
            create_test_settings()

        errors = exc_info.value.errors()
        error_fields = {error["loc"][0] for error in errors}
        assert "discord_token" in error_fields
        assert "gemini_api_key" in error_fields


class TestSecretStrFields:
    """Tests for SecretStr fields in Settings."""

    def test_discord_token_is_secret_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that discord_token is a SecretStr field."""
        monkeypatch.setenv("DISCORD_TOKEN", "secret-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert isinstance(settings.discord_token, SecretStr)
        assert settings.discord_token.get_secret_value() == "secret-discord-token"
        # Verify it's not exposed in string representation
        assert "secret-discord-token" not in str(settings.discord_token)

    def test_gemini_api_key_is_secret_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that gemini_api_key is a SecretStr field."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert isinstance(settings.gemini_api_key, SecretStr)
        assert settings.gemini_api_key.get_secret_value() == "secret-gemini-key"
        assert "secret-gemini-key" not in str(settings.gemini_api_key)

    def test_anthropic_api_key_is_optional_secret_str(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that anthropic_api_key is an optional SecretStr field."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert isinstance(settings.anthropic_api_key, SecretStr)
        assert settings.anthropic_api_key.get_secret_value() == "secret-anthropic-key"
        assert "secret-anthropic-key" not in str(settings.anthropic_api_key)

    def test_openai_api_key_is_optional_secret_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that openai_api_key is an optional SecretStr field."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "secret-openai-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert isinstance(settings.openai_api_key, SecretStr)
        assert settings.openai_api_key.get_secret_value() == "secret-openai-key"
        assert "secret-openai-key" not in str(settings.openai_api_key)

    def test_optional_api_keys_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that optional API keys default to None when not provided."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.anthropic_api_key is None
        assert settings.openai_api_key is None


class TestDefaultValues:
    """Tests for default values in Settings."""

    def test_allowed_user_ids_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that allowed_user_ids defaults to empty list."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.allowed_user_ids == []

    def test_qdrant_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Qdrant settings have correct defaults."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("QDRANT_HOST", raising=False)
        monkeypatch.delenv("QDRANT_PORT", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.qdrant_host == "qdrant"
        assert settings.qdrant_port == 6333

    def test_environment_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment defaults to 'production'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.environment == "production"

    def test_log_level_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that log_level defaults to 'INFO'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_level == "INFO"


class TestModelConfigurationDefaults:
    """Tests for model configuration default values."""

    def test_claude_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that claude_model has correct default."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.claude_model == "claude-sonnet-4-5-20250929"

    def test_openai_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that openai_model has correct default."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.openai_model == "gpt-5.2"

    def test_router_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that router_model has correct default."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.router_model == "gemini-2.5-flash"

    def test_embedding_model_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that embedding_model has correct default."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.embedding_model == "text-embedding-004"

    def test_custom_model_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that model configuration can be customized."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("CLAUDE_MODEL", "custom-claude-model")
        monkeypatch.setenv("OPENAI_MODEL", "custom-openai-model")
        monkeypatch.setenv("ROUTER_MODEL", "custom-router-model")
        monkeypatch.setenv("EMBEDDING_MODEL", "custom-embedding-model")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.claude_model == "custom-claude-model"
        assert settings.openai_model == "custom-openai-model"
        assert settings.router_model == "custom-router-model"
        assert settings.embedding_model == "custom-embedding-model"


class TestIsDevelopmentProperty:
    """Tests for the is_development property."""

    def test_is_development_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that is_development returns True when environment is 'development'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ENVIRONMENT", "development")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.is_development is True

    def test_is_development_true_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that is_development is case-insensitive."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        # Test uppercase
        monkeypatch.setenv("ENVIRONMENT", "DEVELOPMENT")
        get_settings.cache_clear()
        settings = create_test_settings()
        assert settings.is_development is True

        # Test mixed case
        monkeypatch.setenv("ENVIRONMENT", "Development")
        get_settings.cache_clear()
        settings = create_test_settings()
        assert settings.is_development is True

    def test_is_development_false_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that is_development returns False when environment is 'production'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ENVIRONMENT", "production")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.is_development is False

    def test_is_development_false_test(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that is_development returns False when environment is 'test'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ENVIRONMENT", "test")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.is_development is False

    def test_is_development_false_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that is_development returns False when environment is default (production)."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.is_development is False


class TestQdrantUrlProperty:
    """Tests for the qdrant_url property."""

    def test_qdrant_url_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that qdrant_url is constructed correctly with default values."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("QDRANT_HOST", raising=False)
        monkeypatch.delenv("QDRANT_PORT", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.qdrant_url == "http://qdrant:6333"

    def test_qdrant_url_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that qdrant_url uses custom host."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("QDRANT_HOST", "custom-qdrant-host")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.qdrant_url == "http://custom-qdrant-host:6333"

    def test_qdrant_url_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that qdrant_url uses custom port."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly set host to default (avoid .env file interference)
        monkeypatch.setenv("QDRANT_HOST", "qdrant")
        monkeypatch.setenv("QDRANT_PORT", "9999")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.qdrant_url == "http://qdrant:9999"

    def test_qdrant_url_custom_host_and_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that qdrant_url uses custom host and port."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("QDRANT_HOST", "localhost")
        monkeypatch.setenv("QDRANT_PORT", "8000")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.qdrant_url == "http://localhost:8000"


class TestGetSettings:
    """Tests for the get_settings cached function."""

    def test_get_settings_returns_settings_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that get_settings returns a Settings instance."""
        from unittest.mock import patch

        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import Settings, get_settings

        get_settings.cache_clear()

        # Patch Settings.__init__ to prevent .env file loading
        original_init = Settings.__init__

        def patched_init(self, **kwargs):
            kwargs["_env_file"] = None
            original_init(self, **kwargs)

        with patch.object(Settings, "__init__", patched_init):
            settings = get_settings()
            assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that get_settings returns the same instance (cached)."""
        from unittest.mock import patch

        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        # Patch Settings to prevent .env file loading
        with patch("zetherion_ai.config.Settings") as mock_settings:
            mock_settings.return_value = create_test_settings()
            settings1 = get_settings()
            settings2 = get_settings()
            assert settings1 is settings2

    def test_get_settings_cache_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that cache_clear creates a new instance."""
        from unittest.mock import patch

        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import Settings, get_settings

        get_settings.cache_clear()

        # Patch Settings.__init__ to prevent .env file loading
        original_init = Settings.__init__

        def patched_init(self, **kwargs):
            kwargs["_env_file"] = None
            original_init(self, **kwargs)

        with patch.object(Settings, "__init__", patched_init):
            settings1 = get_settings()
            get_settings.cache_clear()
            settings2 = get_settings()
            assert settings1 is not settings2


class TestSettingsValidation:
    """Tests for Settings validation and error handling."""

    def test_allowed_user_ids_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that allowed_user_ids is parsed correctly from comma-separated string."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ALLOWED_USER_IDS", "123,456,789")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.allowed_user_ids == [123, 456, 789]

    def test_allowed_user_ids_empty_array(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that empty string for allowed_user_ids results in empty list."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ALLOWED_USER_IDS", "")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.allowed_user_ids == []

    def test_qdrant_port_must_be_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that qdrant_port must be a valid integer."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("QDRANT_PORT", "not-a-number")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError) as exc_info:
            create_test_settings()

        errors = exc_info.value.errors()
        assert any(error["loc"][0] == "qdrant_port" for error in errors)

    def test_case_insensitive_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment variables are case-insensitive."""
        # Pydantic Settings with case_sensitive=False should accept lowercase env vars
        monkeypatch.setenv("discord_token", "test-discord-token")
        monkeypatch.setenv("gemini_api_key", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.discord_token.get_secret_value() == "test-discord-token"
        assert settings.gemini_api_key.get_secret_value() == "test-gemini-key"

    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that extra environment variables are ignored."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("EXTRA_UNKNOWN_VAR", "should-be-ignored")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        # Should not raise an error
        settings = create_test_settings()
        assert not hasattr(settings, "extra_unknown_var")


class TestOllamaConfiguration:
    """Tests for Ollama generation container configuration settings."""

    def test_ollama_generation_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Ollama generation container settings have correct defaults."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_PORT", raising=False)
        monkeypatch.delenv("OLLAMA_GENERATION_MODEL", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_host == "ollama"
        assert settings.ollama_port == 11434
        assert settings.ollama_generation_model == "llama3.1:8b"
        assert settings.ollama_timeout == 30

    def test_ollama_url_property_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_url is constructed correctly with default values."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_PORT", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_url == "http://ollama:11434"

    def test_ollama_url_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_url uses custom host."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OLLAMA_HOST", "localhost")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_url == "http://localhost:11434"

    def test_ollama_url_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_url uses custom port."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly set host to default (avoid .env file interference)
        monkeypatch.setenv("OLLAMA_HOST", "ollama")
        monkeypatch.setenv("OLLAMA_PORT", "8080")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_url == "http://ollama:8080"

    def test_ollama_custom_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Ollama generation container configuration can be customized."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OLLAMA_HOST", "custom-host")
        monkeypatch.setenv("OLLAMA_PORT", "9999")
        monkeypatch.setenv("OLLAMA_GENERATION_MODEL", "llama3.1:8b")
        monkeypatch.setenv("OLLAMA_TIMEOUT", "60")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_host == "custom-host"
        assert settings.ollama_port == 9999
        assert settings.ollama_generation_model == "llama3.1:8b"
        assert settings.ollama_timeout == 60
        assert settings.ollama_url == "http://custom-host:9999"

    def test_ollama_port_must_be_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_port must be a valid integer."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OLLAMA_PORT", "not-a-number")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError) as exc_info:
            create_test_settings()

        errors = exc_info.value.errors()
        assert any(error["loc"][0] == "ollama_port" for error in errors)


class TestOllamaRouterContainerConfiguration:
    """Tests for dedicated Ollama router container configuration settings."""

    def test_ollama_router_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Ollama router container settings have correct defaults."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("OLLAMA_ROUTER_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_ROUTER_PORT", raising=False)
        monkeypatch.delenv("OLLAMA_ROUTER_MODEL", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_router_host == "ollama-router"
        assert settings.ollama_router_port == 11434
        assert settings.ollama_router_model == "llama3.2:3b"

    def test_ollama_router_url_property_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_router_url is constructed correctly with default values."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("OLLAMA_ROUTER_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_ROUTER_PORT", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_router_url == "http://ollama-router:11434"

    def test_ollama_router_url_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_router_url uses custom host."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OLLAMA_ROUTER_HOST", "localhost")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_router_url == "http://localhost:11434"

    def test_ollama_router_url_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ollama_router_url uses custom port."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly set host to default (avoid .env file interference)
        monkeypatch.setenv("OLLAMA_ROUTER_HOST", "ollama-router")
        monkeypatch.setenv("OLLAMA_ROUTER_PORT", "11435")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_router_url == "http://ollama-router:11435"

    def test_ollama_router_custom_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that Ollama router container configuration can be customized."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("OLLAMA_ROUTER_HOST", "custom-router-host")
        monkeypatch.setenv("OLLAMA_ROUTER_PORT", "8888")
        monkeypatch.setenv("OLLAMA_ROUTER_MODEL", "llama3.2:3b")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.ollama_router_host == "custom-router-host"
        assert settings.ollama_router_port == 8888
        assert settings.ollama_router_model == "llama3.2:3b"
        assert settings.ollama_router_url == "http://custom-router-host:8888"

    def test_dual_container_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that both router and generation containers can be configured separately."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Router container
        monkeypatch.setenv("OLLAMA_ROUTER_HOST", "router-container")
        monkeypatch.setenv("OLLAMA_ROUTER_PORT", "11435")
        monkeypatch.setenv("OLLAMA_ROUTER_MODEL", "llama3.2:3b")
        # Generation container
        monkeypatch.setenv("OLLAMA_HOST", "generation-container")
        monkeypatch.setenv("OLLAMA_PORT", "11434")
        monkeypatch.setenv("OLLAMA_GENERATION_MODEL", "llama3.1:8b")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        # Verify router container settings
        assert settings.ollama_router_host == "router-container"
        assert settings.ollama_router_port == 11435
        assert settings.ollama_router_model == "llama3.2:3b"
        assert settings.ollama_router_url == "http://router-container:11435"
        # Verify generation container settings
        assert settings.ollama_host == "generation-container"
        assert settings.ollama_port == 11434
        assert settings.ollama_generation_model == "llama3.1:8b"
        assert settings.ollama_url == "http://generation-container:11434"


class TestRouterBackendConfiguration:
    """Tests for router backend configuration and validation."""

    def test_router_backend_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that router_backend defaults to 'gemini'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        # Explicitly unset to test defaults (avoid .env file interference)
        monkeypatch.delenv("ROUTER_BACKEND", raising=False)

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.router_backend == "gemini"

    def test_router_backend_gemini(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that router_backend can be set to 'gemini'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ROUTER_BACKEND", "gemini")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.router_backend == "gemini"

    def test_router_backend_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that router_backend can be set to 'ollama'."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ROUTER_BACKEND", "ollama")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.router_backend == "ollama"

    def test_router_backend_invalid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid router_backend value raises ValidationError."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("ROUTER_BACKEND", "invalid-backend")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        with pytest.raises(ValidationError) as exc_info:
            create_test_settings()

        errors = exc_info.value.errors()
        assert any(error["loc"][0] == "router_backend" for error in errors)
        # Check error message mentions valid backends
        error_msg = str(exc_info.value)
        assert "gemini" in error_msg or "ollama" in error_msg


class TestLoggingConfiguration:
    """Tests for logging configuration settings."""

    def test_logging_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that logging settings have correct defaults."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_to_file is True
        assert settings.log_directory == "logs"
        assert settings.log_file_max_bytes == 52428800  # 50MB
        assert settings.log_file_backup_count == 10

    def test_log_file_path_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that log_file_path property is constructed correctly."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_file_path == "logs/zetherion_ai.log"

    def test_log_file_path_custom_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test log_file_path with custom directory."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("LOG_DIRECTORY", "/var/log/zetherion_ai")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_file_path == "/var/log/zetherion_ai/zetherion_ai.log"

    def test_log_to_file_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that log_to_file can be disabled."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("LOG_TO_FILE", "false")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_to_file is False

    def test_logging_custom_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that logging configuration can be customized."""
        monkeypatch.setenv("DISCORD_TOKEN", "test-discord-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("LOG_TO_FILE", "true")
        monkeypatch.setenv("LOG_DIRECTORY", "custom_logs")
        monkeypatch.setenv("LOG_FILE_MAX_BYTES", "20971520")  # 20MB
        monkeypatch.setenv("LOG_FILE_BACKUP_COUNT", "10")

        from zetherion_ai.config import get_settings

        get_settings.cache_clear()

        settings = create_test_settings()
        assert settings.log_to_file is True
        assert settings.log_directory == "custom_logs"
        assert settings.log_file_max_bytes == 20971520
        assert settings.log_file_backup_count == 10
        assert settings.log_file_path == "custom_logs/zetherion_ai.log"
