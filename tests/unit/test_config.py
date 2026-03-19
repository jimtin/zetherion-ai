"""Unit tests for the configuration module."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from zetherion_ai.config import Settings, get_dynamic


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with minimal required fields plus overrides.

    All required fields (no default) are given safe test values unless
    explicitly overridden by the caller.  We disable .env file loading
    (_env_file=None) so that tests are isolated from the real environment.
    """
    defaults = {
        "discord_token": "test-discord-token",
        "gemini_api_key": "test-gemini-key",
        "encryption_passphrase": "test-passphrase-minimum-16-chars",
        "_env_file": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestEncryptionPassphraseRequired:
    """Tests for encryption_passphrase being required."""

    def test_missing_encryption_passphrase_raises_validation_error(self):
        """Test that omitting ENCRYPTION_PASSPHRASE raises ValidationError."""
        # Clear the env var that conftest sets, and disable .env file loading,
        # so that Settings truly has no passphrase value available.
        env_overrides = {
            k: v for k, v in os.environ.items() if k.upper() != "ENCRYPTION_PASSPHRASE"
        }
        with patch.dict(os.environ, env_overrides, clear=True):
            with pytest.raises(ValidationError):
                Settings(
                    _env_file=None,
                    discord_token="test-token",
                    gemini_api_key="test-key",
                    # encryption_passphrase deliberately omitted
                )


class TestOpenAIModelDefault:
    """Tests for the openai_model field default."""

    def test_openai_model_default_is_gpt_5_2(self, monkeypatch):
        """Test that openai_model defaults to 'gpt-5.2'."""
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        settings = _make_settings()
        assert settings.openai_model == "gpt-5.2"

    def test_openai_model_can_be_overridden(self):
        """Test that openai_model can be set to a custom value."""
        settings = _make_settings(openai_model="gpt-4o")
        assert settings.openai_model == "gpt-4o"


class TestEmbeddingsBackend:
    """Tests for the embeddings_backend field."""

    def test_embeddings_backend_default_is_openai(self):
        """Test that embeddings_backend defaults to 'openai'."""
        settings = _make_settings()
        assert settings.embeddings_backend == "openai"

    def test_embeddings_backend_accepts_openai(self):
        """Test that embeddings_backend accepts 'openai' as valid."""
        settings = _make_settings(embeddings_backend="openai")
        assert settings.embeddings_backend == "openai"

    def test_embeddings_backend_accepts_gemini(self):
        """Test that embeddings_backend accepts 'gemini' as valid."""
        settings = _make_settings(embeddings_backend="gemini")
        assert settings.embeddings_backend == "gemini"

    def test_embeddings_backend_accepts_ollama(self):
        """Test that embeddings_backend accepts 'ollama' as valid."""
        settings = _make_settings(embeddings_backend="ollama")
        assert settings.embeddings_backend == "ollama"

    def test_embeddings_backend_rejects_invalid_value(self):
        """Test that embeddings_backend rejects invalid values."""
        with pytest.raises(ValidationError, match="embeddings_backend"):
            _make_settings(embeddings_backend="invalid_backend")

    def test_embeddings_backend_rejects_empty_string(self):
        """Test that embeddings_backend rejects empty string."""
        with pytest.raises(ValidationError, match="embeddings_backend"):
            _make_settings(embeddings_backend="")


class TestOpenAIEmbeddingModel:
    """Tests for the openai_embedding_model field."""

    def test_openai_embedding_model_default(self):
        """Test that openai_embedding_model defaults to 'text-embedding-3-large'."""
        settings = _make_settings()
        assert settings.openai_embedding_model == "text-embedding-3-large"

    def test_openai_embedding_model_can_be_overridden(self):
        """Test that openai_embedding_model can be set to a custom value."""
        settings = _make_settings(openai_embedding_model="text-embedding-3-small")
        assert settings.openai_embedding_model == "text-embedding-3-small"


class TestOpenAIEmbeddingDimensions:
    """Tests for the openai_embedding_dimensions field."""

    def test_openai_embedding_dimensions_default(self):
        """Test that openai_embedding_dimensions defaults to 3072."""
        settings = _make_settings()
        assert settings.openai_embedding_dimensions == 3072

    def test_openai_embedding_dimensions_can_be_overridden(self):
        """Test that openai_embedding_dimensions can be set to a custom value."""
        settings = _make_settings(openai_embedding_dimensions=1536)
        assert settings.openai_embedding_dimensions == 1536


class TestAllowAllUsers:
    """Tests for the allow_all_users field."""

    def test_allow_all_users_default_is_false(self):
        """Test that allow_all_users defaults to False."""
        settings = _make_settings()
        assert settings.allow_all_users is False

    def test_allow_all_users_can_be_set_true(self):
        """Test that allow_all_users can be set to True."""
        settings = _make_settings(allow_all_users=True)
        assert settings.allow_all_users is True


class TestAllowlistStartupControls:
    """Tests for allowlist startup hardening fields."""

    def test_allowlist_strict_startup_default_is_false(self):
        """Strict startup mode is opt-in by default."""
        settings = _make_settings()
        assert settings.allowlist_strict_startup is False

    def test_allowlist_bootstrap_enabled_default_is_true(self):
        """Allowlist bootstrap sync is enabled by default."""
        settings = _make_settings()
        assert settings.allowlist_bootstrap_enabled is True


class TestRouterBackendValidation:
    """Tests for router backend validation."""

    def test_router_backend_accepts_groq(self):
        """Router backend accepts groq option."""
        settings = _make_settings(router_backend="groq")
        assert settings.router_backend == "groq"

    def test_router_backend_rejects_invalid_value(self):
        """Router backend rejects unknown values."""
        with pytest.raises(ValidationError, match="router_backend"):
            _make_settings(router_backend="invalid-router")


class TestEncryptionStrict:
    """Tests for the encryption_strict field."""

    def test_encryption_strict_default_is_false(self):
        """Test that encryption_strict defaults to False."""
        settings = _make_settings()
        assert settings.encryption_strict is False

    def test_encryption_strict_can_be_set_true(self):
        """Test that encryption_strict can be set to True."""
        settings = _make_settings(encryption_strict=True)
        assert settings.encryption_strict is True


class TestStrictTransportSecurity:
    """Tests for strict production transport hardening controls."""

    def test_strict_transport_rejects_http_urls(self):
        with pytest.raises(ValidationError, match="skills_service_url must use https://"):
            _make_settings(
                strict_transport_security=True,
                postgres_use_tls=True,
                qdrant_use_tls=True,
                router_backend="gemini",
                embeddings_backend="openai",
                internal_tls_ca_path="/certs/ca.pem",
                internal_tls_client_cert_path="/certs/client.pem",
                internal_tls_client_key_path="/certs/client-key.pem",
                api_tls_cert_path="/certs/api.pem",
                api_tls_key_path="/certs/api-key.pem",
                skills_tls_cert_path="/certs/skills.pem",
                skills_tls_key_path="/certs/skills-key.pem",
                cgs_gateway_tls_cert_path="/certs/gateway.pem",
                cgs_gateway_tls_key_path="/certs/gateway-key.pem",
                postgres_tls_ca_path="/certs/pg-ca.pem",
                postgres_tls_cert_path="/certs/pg-client.pem",
                postgres_tls_key_path="/certs/pg-client-key.pem",
                qdrant_cert_path="/certs/qdrant-ca.pem",
                backup_age_recipient="age1example",
                skills_service_url="http://zetherion-ai-skills:8080",
            )

    def test_strict_transport_rejects_ollama_backends(self):
        with pytest.raises(ValidationError, match="router_backend cannot be 'ollama'"):
            _make_settings(
                strict_transport_security=True,
                postgres_use_tls=True,
                qdrant_use_tls=True,
                router_backend="ollama",
                embeddings_backend="openai",
                internal_tls_ca_path="/certs/ca.pem",
                internal_tls_client_cert_path="/certs/client.pem",
                internal_tls_client_key_path="/certs/client-key.pem",
                api_tls_cert_path="/certs/api.pem",
                api_tls_key_path="/certs/api-key.pem",
                skills_tls_cert_path="/certs/skills.pem",
                skills_tls_key_path="/certs/skills-key.pem",
                cgs_gateway_tls_cert_path="/certs/gateway.pem",
                cgs_gateway_tls_key_path="/certs/gateway-key.pem",
                postgres_tls_ca_path="/certs/pg-ca.pem",
                postgres_tls_cert_path="/certs/pg-client.pem",
                postgres_tls_key_path="/certs/pg-client-key.pem",
                qdrant_cert_path="/certs/qdrant-ca.pem",
                backup_age_recipient="age1example",
            )

    def test_strict_transport_accepts_https_and_tls_configuration(self):
        settings = _make_settings(
            strict_transport_security=True,
            postgres_use_tls=True,
            qdrant_use_tls=True,
            router_backend="gemini",
            embeddings_backend="openai",
            skills_service_url="https://zetherion-ai-skills:8080",
            zetherion_public_api_base_url="https://zetherion-ai-api-green:8443",
            zetherion_skills_api_base_url="https://zetherion-ai-skills-green:8080",
            announcement_api_url="https://127.0.0.1:8080/announcements/events",
            internal_tls_ca_path="/certs/ca.pem",
            internal_tls_client_cert_path="/certs/client.pem",
            internal_tls_client_key_path="/certs/client-key.pem",
            api_tls_cert_path="/certs/api.pem",
            api_tls_key_path="/certs/api-key.pem",
            skills_tls_cert_path="/certs/skills.pem",
            skills_tls_key_path="/certs/skills-key.pem",
            cgs_gateway_tls_cert_path="/certs/gateway.pem",
            cgs_gateway_tls_key_path="/certs/gateway-key.pem",
            postgres_tls_ca_path="/certs/pg-ca.pem",
            postgres_tls_cert_path="/certs/pg-client.pem",
            postgres_tls_key_path="/certs/pg-client-key.pem",
            qdrant_cert_path="/certs/qdrant-ca.pem",
            runtime_secret_bundle_path="C:/ZetherionAI/data/secrets/runtime.bin",
            backup_age_recipient="age1example",
        )
        assert settings.strict_transport_security is True


class TestDynamicSettingsFallback:
    """Tests for get_dynamic() DB -> env -> default cascade."""

    def test_get_dynamic_prefers_db_override(self):
        mgr = type("Mgr", (), {"get": lambda self, ns, key: 0.9})()
        fake_settings = type("SettingsObj", (), {"security_block_threshold": 0.6})()

        with patch("zetherion_ai.config._settings_manager", mgr):
            with patch("zetherion_ai.config.get_settings", return_value=fake_settings):
                value = get_dynamic("security", "block_threshold", 0.5)

        assert value == 0.9

    def test_get_dynamic_uses_namespaced_env_fallback(self):
        mgr = type("Mgr", (), {"get": lambda self, ns, key: None})()
        fake_settings = type("SettingsObj", (), {"security_block_threshold": 0.7})()

        with patch("zetherion_ai.config._settings_manager", mgr):
            with patch("zetherion_ai.config.get_settings", return_value=fake_settings):
                value = get_dynamic("security", "block_threshold", 0.5)

        assert value == 0.7

    def test_get_dynamic_uses_key_env_fallback(self):
        mgr = type("Mgr", (), {"get": lambda self, ns, key: None})()
        fake_settings = type("SettingsObj", (), {"api_port": 9000})()

        with patch("zetherion_ai.config._settings_manager", mgr):
            with patch("zetherion_ai.config.get_settings", return_value=fake_settings):
                value = get_dynamic("api", "port", 8443)

        assert value == 9000


class TestCgsGatewayOptionalEnvParsing:
    """Tests for optional CGS env parsing with empty-string env values."""

    def test_empty_cgs_env_values_do_not_raise_validation_error(self, monkeypatch):
        monkeypatch.setenv("CGS_GATEWAY_ALLOWED_ORIGINS", "")
        monkeypatch.setenv("CGS_AUTH_JWKS_URL", "")
        monkeypatch.setenv("CGS_AUTH_ISSUER", "")
        monkeypatch.setenv("CGS_AUTH_AUDIENCE", "")

        settings = _make_settings()

        assert settings.cgs_gateway_allowed_origins is None
        assert settings.cgs_auth_jwks_url is None
        assert settings.cgs_auth_issuer is None
        assert settings.cgs_auth_audience is None
