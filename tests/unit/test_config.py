"""Unit tests for the configuration module."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from zetherion_ai.config import Settings


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

    def test_embeddings_backend_default_is_ollama(self):
        """Test that embeddings_backend defaults to 'ollama'."""
        settings = _make_settings()
        assert settings.embeddings_backend == "ollama"

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
