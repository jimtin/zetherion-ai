"""Unit tests for the model discovery module."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.models.discovery import (
    DiscoveryError,
    ModelDiscovery,
    check_deprecation,
)


class TestDiscoveryError:
    """Tests for DiscoveryError."""

    def test_error_creation(self):
        """Test error creation with provider and message."""
        error = DiscoveryError("openai", "API error")
        assert error.provider == "openai"
        assert error.message == "API error"
        assert "openai: API error" in str(error)


class TestModelDiscoveryInit:
    """Tests for ModelDiscovery initialization."""

    def test_init_with_all_keys(self):
        """Test initialization with all API keys."""
        discovery = ModelDiscovery(
            openai_api_key="sk-test",
            anthropic_api_key="ant-test",
            google_api_key="AIza-test",
            ollama_host="http://localhost:11434",
        )
        assert discovery._openai_key == "sk-test"
        assert discovery._anthropic_key == "ant-test"
        assert discovery._google_key == "AIza-test"
        assert discovery._ollama_host == "http://localhost:11434"

    def test_init_with_trailing_slash(self):
        """Test that trailing slash is stripped from ollama host."""
        discovery = ModelDiscovery(ollama_host="http://localhost:11434/")
        assert discovery._ollama_host == "http://localhost:11434"

    def test_init_defaults(self):
        """Test default values."""
        discovery = ModelDiscovery()
        assert discovery._openai_key is None
        assert discovery._anthropic_key is None
        assert discovery._google_key is None
        assert "localhost" in discovery._ollama_host


class TestModelDiscoveryOpenAI:
    """Tests for OpenAI discovery."""

    @pytest.mark.asyncio
    async def test_discover_openai_success(self):
        """Test successful OpenAI discovery."""
        discovery = ModelDiscovery(openai_api_key="sk-test")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "gpt-4o", "owned_by": "openai"},
                {"id": "gpt-4o-mini", "owned_by": "openai"},
                {"id": "text-embedding-ada-002", "owned_by": "openai"},  # Should be filtered
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            models = await discovery._discover_openai()

        assert len(models) == 2
        assert models[0].id == "gpt-4o"
        assert models[0].provider == "openai"
        assert models[1].id == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_discover_openai_no_key(self):
        """Test OpenAI discovery without API key."""
        discovery = ModelDiscovery()
        models = await discovery._discover_openai()
        assert models == []

    @pytest.mark.asyncio
    async def test_discover_openai_http_error(self):
        """Test OpenAI discovery with HTTP error."""
        discovery = ModelDiscovery(openai_api_key="sk-test")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_response
        )

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            with pytest.raises(DiscoveryError) as exc_info:
                await discovery._discover_openai()

        assert "openai" in str(exc_info.value)
        assert "HTTP 401" in str(exc_info.value)


class TestModelDiscoveryAnthropic:
    """Tests for Anthropic discovery."""

    @pytest.mark.asyncio
    async def test_discover_anthropic_success(self):
        """Test successful Anthropic discovery."""
        discovery = ModelDiscovery(anthropic_api_key="ant-test")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "claude-opus-4-5", "display_name": "Claude Opus 4.5", "max_tokens": 200000},
                {
                    "id": "claude-sonnet-4-5",
                    "display_name": "Claude Sonnet 4.5",
                    "max_tokens": 200000,
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            models = await discovery._discover_anthropic()

        assert len(models) == 2
        assert models[0].id == "claude-opus-4-5"
        assert models[0].provider == "anthropic"
        assert models[0].display_name == "Claude Opus 4.5"

    @pytest.mark.asyncio
    async def test_discover_anthropic_no_key(self):
        """Test Anthropic discovery without API key."""
        discovery = ModelDiscovery()
        models = await discovery._discover_anthropic()
        assert models == []


class TestModelDiscoveryGoogle:
    """Tests for Google discovery."""

    @pytest.mark.asyncio
    async def test_discover_google_success(self):
        """Test successful Google discovery."""
        discovery = ModelDiscovery(google_api_key="AIza-test")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "models/gemini-2.0-flash",
                    "displayName": "Gemini 2.0 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1000000,
                },
                {
                    "name": "models/embedding-001",
                    "displayName": "Embedding",
                    "supportedGenerationMethods": ["embedText"],  # Should be filtered
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            models = await discovery._discover_google()

        assert len(models) == 1
        assert models[0].id == "gemini-2.0-flash"
        assert models[0].provider == "google"

    @pytest.mark.asyncio
    async def test_discover_google_no_key(self):
        """Test Google discovery without API key."""
        discovery = ModelDiscovery()
        models = await discovery._discover_google()
        assert models == []


class TestModelDiscoveryOllama:
    """Tests for Ollama discovery."""

    @pytest.mark.asyncio
    async def test_discover_ollama_success(self):
        """Test successful Ollama discovery."""
        discovery = ModelDiscovery(ollama_host="http://localhost:11434")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.1:8b", "details": {"parameter_size": "8B"}},
                {"name": "phi-3", "details": {"parameter_size": "3.8B"}},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            models = await discovery._discover_ollama()

        assert len(models) == 2
        assert models[0].id == "llama3.1:8b"
        assert models[0].provider == "ollama"

    @pytest.mark.asyncio
    async def test_discover_ollama_not_running(self):
        """Test Ollama discovery when not running."""
        discovery = ModelDiscovery(ollama_host="http://localhost:11434")

        with patch.object(discovery, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get_client.return_value = mock_client

            models = await discovery._discover_ollama()

        # Should return empty list, not raise
        assert models == []


class TestModelDiscoveryAll:
    """Tests for discover_all method."""

    @pytest.mark.asyncio
    async def test_discover_all_success(self):
        """Test successful discovery from all providers."""
        discovery = ModelDiscovery(
            openai_api_key="sk-test",
            anthropic_api_key="ant-test",
            google_api_key="AIza-test",
        )

        with (
            patch.object(discovery, "_discover_openai", return_value=[MagicMock()]),
            patch.object(discovery, "_discover_anthropic", return_value=[MagicMock(), MagicMock()]),
            patch.object(discovery, "_discover_google", return_value=[MagicMock()]),
            patch.object(discovery, "_discover_ollama", return_value=[]),
        ):
            results = await discovery.discover_all()

        assert len(results["openai"]) == 1
        assert len(results["anthropic"]) == 2
        assert len(results["google"]) == 1
        assert len(results["ollama"]) == 0

    @pytest.mark.asyncio
    async def test_discover_all_partial_failure(self):
        """Test discovery with some providers failing."""
        discovery = ModelDiscovery(
            openai_api_key="sk-test",
            google_api_key="AIza-test",
        )

        with (
            patch.object(
                discovery, "_discover_openai", side_effect=DiscoveryError("openai", "Failed")
            ),
            patch.object(discovery, "_discover_google", return_value=[MagicMock()]),
            patch.object(discovery, "_discover_ollama", return_value=[]),
        ):
            results = await discovery.discover_all()

        # Failed provider should have empty list
        assert results["openai"] == []
        assert len(results["google"]) == 1


class TestModelDiscoveryHelpers:
    """Tests for helper methods."""

    def test_is_chat_model_openai(self):
        """Test OpenAI chat model detection."""
        discovery = ModelDiscovery()

        # Chat models
        assert discovery._is_chat_model_openai("gpt-4o") is True
        assert discovery._is_chat_model_openai("gpt-4-turbo") is True
        assert discovery._is_chat_model_openai("o1-preview") is True

        # Non-chat models
        assert discovery._is_chat_model_openai("text-embedding-ada-002") is False
        assert discovery._is_chat_model_openai("dall-e-3") is False
        assert discovery._is_chat_model_openai("whisper-1") is False
        assert discovery._is_chat_model_openai("gpt-3.5-turbo-instruct") is False

    def test_is_generative_model_google(self):
        """Test Google generative model detection."""
        discovery = ModelDiscovery()

        assert (
            discovery._is_generative_model_google(
                {"supportedGenerationMethods": ["generateContent"]}
            )
            is True
        )
        assert (
            discovery._is_generative_model_google({"supportedGenerationMethods": ["embedText"]})
            is False
        )
        assert discovery._is_generative_model_google({"supportedGenerationMethods": []}) is False

    def test_extract_openai_metadata(self):
        """Test OpenAI metadata extraction."""
        discovery = ModelDiscovery()

        # GPT-4o
        metadata = discovery._extract_openai_metadata({"id": "gpt-4o"})
        assert metadata["context_window"] == 128_000

        # GPT-4
        metadata = discovery._extract_openai_metadata({"id": "gpt-4"})
        assert metadata["context_window"] == 8_192

        # GPT-3.5-turbo
        metadata = discovery._extract_openai_metadata({"id": "gpt-3.5-turbo"})
        assert metadata["context_window"] == 16_385

        # Unknown
        metadata = discovery._extract_openai_metadata({"id": "unknown-model"})
        assert metadata["context_window"] is None

    def test_extract_anthropic_metadata(self):
        """Test Anthropic metadata extraction."""
        discovery = ModelDiscovery()

        metadata = discovery._extract_anthropic_metadata({"max_tokens": 200000})
        assert metadata["context_window"] == 200000

    def test_extract_google_metadata(self):
        """Test Google metadata extraction."""
        discovery = ModelDiscovery()

        metadata = discovery._extract_google_metadata({"inputTokenLimit": 1000000})
        assert metadata["context_window"] == 1000000

    def test_extract_ollama_metadata(self):
        """Test Ollama metadata extraction."""
        discovery = ModelDiscovery()

        # Llama3
        metadata = discovery._extract_ollama_metadata(
            {"name": "llama3.1:8b", "details": {"parameter_size": "8B"}}
        )
        assert metadata["context_window"] == 128_000
        assert metadata["parameter_size"] == "8B"

        # Mistral
        metadata = discovery._extract_ollama_metadata({"name": "mistral:7b", "details": {}})
        assert metadata["context_window"] == 32_768


class TestCheckDeprecation:
    """Tests for check_deprecation function."""

    def test_model_disappeared_starts_grace(self):
        """Test that disappeared models start grace period."""
        current = {"model-a", "model-b"}
        previous = {"model-a", "model-b", "model-c"}  # model-c disappeared
        deprecated = {}

        newly_deprecated, updated = check_deprecation(current, previous, deprecated)

        assert newly_deprecated == set()
        assert "model-c" in updated

    def test_model_deprecated_after_grace(self):
        """Test that models are deprecated after grace period."""
        current = {"model-a"}
        previous = {"model-a", "model-b"}
        # model-b disappeared 8 days ago (past 7-day grace)
        deprecated = {"model-b": datetime.now() - timedelta(days=8)}

        newly_deprecated, updated = check_deprecation(current, previous, deprecated)

        assert "model-b" in newly_deprecated

    def test_model_reappears_within_grace(self):
        """Test that reappeared models are removed from tracking."""
        current = {"model-a", "model-b"}
        previous = {"model-a"}
        # model-b was being tracked but reappeared
        deprecated = {"model-b": datetime.now() - timedelta(days=3)}

        newly_deprecated, updated = check_deprecation(current, previous, deprecated)

        assert newly_deprecated == set()
        assert "model-b" not in updated

    def test_no_changes(self):
        """Test when no models changed."""
        current = {"model-a", "model-b"}
        previous = {"model-a", "model-b"}
        deprecated = {}

        newly_deprecated, updated = check_deprecation(current, previous, deprecated)

        assert newly_deprecated == set()
        assert updated == {}


class TestModelDiscoveryClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test closing the HTTP client."""
        discovery = ModelDiscovery()

        # Create a mock client
        mock_client = AsyncMock()
        mock_client.is_closed = False
        discovery._http_client = mock_client

        await discovery.close()

        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_client(self):
        """Test closing when no client exists."""
        discovery = ModelDiscovery()
        # Should not raise
        await discovery.close()
