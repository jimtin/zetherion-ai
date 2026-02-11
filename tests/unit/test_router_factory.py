"""Unit tests for the router factory module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.agent.router import MessageRouter
from zetherion_ai.agent.router_factory import create_router, create_router_sync


@pytest.fixture
def mock_settings_ollama():
    """Create mock settings for Ollama backend (dual-container architecture)."""
    settings = MagicMock()
    settings.router_backend = "ollama"
    # Router container settings (dedicated for routing)
    settings.ollama_router_url = "http://ollama-router:11434"
    settings.ollama_router_model = "llama3.2:3b"
    settings.ollama_timeout = 30.0
    # Generation container settings (for generation + embeddings)
    settings.ollama_url = "http://ollama:11434"
    settings.ollama_generation_model = "llama3.1:8b"
    # Gemini settings (fallback)
    settings.gemini_api_key = MagicMock()
    settings.gemini_api_key.get_secret_value.return_value = "test-key"
    settings.router_model = "gemini-2.0-flash"
    return settings


@pytest.fixture
def mock_settings_gemini():
    """Create mock settings for Gemini backend."""
    settings = MagicMock()
    settings.router_backend = "gemini"
    settings.gemini_api_key = MagicMock()
    settings.gemini_api_key.get_secret_value.return_value = "test-key"
    settings.router_model = "gemini-2.0-flash"
    return settings


class TestCreateRouter:
    """Tests for create_router async function."""

    @pytest.mark.asyncio
    async def test_create_router_ollama_healthy(self, mock_settings_ollama):
        """Test creating router with healthy Ollama backend."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_ollama
            ),
            patch("zetherion_ai.agent.router_factory.OllamaRouterBackend") as mock_ollama,
        ):
            mock_backend = MagicMock()
            mock_backend.health_check = AsyncMock(return_value=True)
            mock_backend.warmup = AsyncMock()  # Mock warmup method
            mock_ollama.return_value = mock_backend

            router = await create_router()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_unhealthy_fallback_gemini(self, mock_settings_ollama):
        """Test falling back to Gemini when Ollama is unhealthy."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_ollama
            ),
            patch("zetherion_ai.agent.router_factory.OllamaRouterBackend") as mock_ollama,
        ):
            with patch("zetherion_ai.agent.router_factory.GeminiRouterBackend") as mock_gemini:
                mock_ollama_backend = MagicMock()
                mock_ollama_backend.health_check = AsyncMock(return_value=False)
                mock_ollama_backend.close = AsyncMock()
                mock_ollama.return_value = mock_ollama_backend

                mock_gemini_backend = MagicMock()
                mock_gemini.return_value = mock_gemini_backend

                router = await create_router()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_gemini_backend
        mock_ollama_backend.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_router_ollama_error_fallback_gemini(self, mock_settings_ollama):
        """Test falling back to Gemini when Ollama initialization fails."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_ollama
            ),
            patch("zetherion_ai.agent.router_factory.OllamaRouterBackend") as mock_ollama,
        ):
            with patch("zetherion_ai.agent.router_factory.GeminiRouterBackend") as mock_gemini:
                mock_ollama.side_effect = Exception("Ollama init failed")

                mock_gemini_backend = MagicMock()
                mock_gemini.return_value = mock_gemini_backend

                router = await create_router()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_gemini_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_no_gemini_fallback(self, mock_settings_ollama):
        """Test error when Ollama fails and no Gemini API key."""
        mock_settings_ollama.gemini_api_key = None

        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_ollama
            ),
            patch("zetherion_ai.agent.router_factory.OllamaRouterBackend") as mock_ollama,
        ):
            mock_ollama_backend = MagicMock()
            mock_ollama_backend.health_check = AsyncMock(return_value=False)
            mock_ollama_backend.close = AsyncMock()
            mock_ollama.return_value = mock_ollama_backend

            with pytest.raises(RuntimeError) as exc_info:
                await create_router()

        assert "Ollama backend requested but unavailable" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_router_gemini(self, mock_settings_gemini):
        """Test creating router with Gemini backend."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_gemini
            ),
            patch("zetherion_ai.agent.router_factory.GeminiRouterBackend") as mock_gemini,
        ):
            mock_backend = MagicMock()
            mock_gemini.return_value = mock_backend

            router = await create_router()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_backend

    @pytest.mark.asyncio
    async def test_create_router_invalid_backend(self):
        """Test error with invalid backend configuration."""
        mock_settings = MagicMock()
        mock_settings.router_backend = "invalid"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError) as exc_info:
                await create_router()

        assert "Invalid router backend" in str(exc_info.value)


class TestCreateRouterSync:
    """Tests for create_router_sync function."""

    def test_create_router_sync_ollama(self, mock_settings_ollama):
        """Test creating router synchronously with Ollama."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_ollama
            ),
            patch("zetherion_ai.agent.router_factory.OllamaRouterBackend") as mock_ollama,
        ):
            mock_backend = MagicMock()
            mock_ollama.return_value = mock_backend

            router = create_router_sync()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_backend

    def test_create_router_sync_gemini(self, mock_settings_gemini):
        """Test creating router synchronously with Gemini."""
        with (
            patch(
                "zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings_gemini
            ),
            patch("zetherion_ai.agent.router_factory.GeminiRouterBackend") as mock_gemini,
        ):
            mock_backend = MagicMock()
            mock_gemini.return_value = mock_backend

            router = create_router_sync()

        assert isinstance(router, MessageRouter)
        assert router._backend == mock_backend

    def test_create_router_sync_invalid_backend(self):
        """Test error with invalid backend configuration."""
        mock_settings = MagicMock()
        mock_settings.router_backend = "invalid"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError) as exc_info:
                create_router_sync()

        assert "Invalid router backend" in str(exc_info.value)
