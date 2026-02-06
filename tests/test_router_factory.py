"""Tests for router_factory module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from zetherion_ai.agent.router import GeminiRouterBackend, MessageRouter
from zetherion_ai.agent.router_factory import create_router, create_router_sync
from zetherion_ai.agent.router_ollama import OllamaRouterBackend


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.router_backend = "gemini"
    settings.router_model = "gemini-2.5-flash"
    settings.ollama_router_model = "llama3.1:8b"
    settings.ollama_url = "http://ollama:11434"
    settings.ollama_timeout = 30
    settings.gemini_api_key = SecretStr("test-gemini-key")
    return settings


class TestCreateRouter:
    """Tests for async create_router function."""

    @pytest.mark.asyncio
    async def test_create_router_gemini_backend(self, mock_settings):
        """Test creating router with Gemini backend."""
        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.GeminiRouterBackend"
            ) as mock_gemini_class:
                mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                mock_gemini_class.return_value = mock_gemini_backend

                router = await create_router()

                assert isinstance(router, MessageRouter)
                mock_gemini_class.assert_called_once()
                assert router._backend == mock_gemini_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_backend_healthy(self, mock_settings):
        """Test creating router with healthy Ollama backend."""
        mock_settings.router_backend = "ollama"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                # Create mock backend that reports healthy
                mock_ollama_backend = MagicMock(spec=OllamaRouterBackend)
                mock_ollama_backend.health_check = AsyncMock(return_value=True)
                mock_ollama_class.return_value = mock_ollama_backend

                router = await create_router()

                assert isinstance(router, MessageRouter)
                mock_ollama_class.assert_called_once()
                mock_ollama_backend.health_check.assert_called_once()
                assert router._backend == mock_ollama_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_unhealthy_fallback_to_gemini(self, mock_settings):
        """Test Ollama unhealthy falls back to Gemini."""
        mock_settings.router_backend = "ollama"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                with patch(
                    "zetherion_ai.agent.router_factory.GeminiRouterBackend"
                ) as mock_gemini_class:
                    # Ollama reports unhealthy
                    mock_ollama_backend = MagicMock(spec=OllamaRouterBackend)
                    mock_ollama_backend.health_check = AsyncMock(return_value=False)
                    mock_ollama_backend.close = AsyncMock()
                    mock_ollama_class.return_value = mock_ollama_backend

                    # Gemini as fallback
                    mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                    mock_gemini_class.return_value = mock_gemini_backend

                    router = await create_router()

                    assert isinstance(router, MessageRouter)
                    mock_ollama_backend.health_check.assert_called_once()
                    mock_ollama_backend.close.assert_called_once()
                    mock_gemini_class.assert_called_once()
                    assert router._backend == mock_gemini_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_exception_fallback_to_gemini(self, mock_settings):
        """Test Ollama initialization exception falls back to Gemini."""
        mock_settings.router_backend = "ollama"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                with patch(
                    "zetherion_ai.agent.router_factory.GeminiRouterBackend"
                ) as mock_gemini_class:
                    # Ollama raises exception during initialization
                    mock_ollama_class.side_effect = ConnectionError("Ollama not available")

                    # Gemini as fallback
                    mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                    mock_gemini_class.return_value = mock_gemini_backend

                    router = await create_router()

                    assert isinstance(router, MessageRouter)
                    mock_gemini_class.assert_called_once()
                    assert router._backend == mock_gemini_backend

    @pytest.mark.asyncio
    async def test_create_router_ollama_exception_no_gemini_fallback(self, mock_settings):
        """Test Ollama exception with no Gemini fallback raises RuntimeError."""
        mock_settings.router_backend = "ollama"
        mock_settings.gemini_api_key = None  # No Gemini API key

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                # Ollama raises exception
                mock_ollama_class.side_effect = ConnectionError("Ollama not available")

                with pytest.raises(RuntimeError) as exc_info:
                    await create_router()

                assert "Ollama backend requested but unavailable" in str(exc_info.value)
                assert "no Gemini API key configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_router_ollama_unhealthy_no_gemini_fallback(self, mock_settings):
        """Test Ollama unhealthy with no Gemini fallback raises RuntimeError."""
        mock_settings.router_backend = "ollama"
        mock_settings.gemini_api_key = None  # No Gemini API key

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                # Ollama reports unhealthy
                mock_ollama_backend = MagicMock(spec=OllamaRouterBackend)
                mock_ollama_backend.health_check = AsyncMock(return_value=False)
                mock_ollama_backend.close = AsyncMock()
                mock_ollama_class.return_value = mock_ollama_backend

                with pytest.raises(RuntimeError) as exc_info:
                    await create_router()

                assert "Ollama backend requested but unavailable" in str(exc_info.value)
                mock_ollama_backend.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_router_invalid_backend(self, mock_settings):
        """Test invalid backend raises ValueError."""
        mock_settings.router_backend = "invalid_backend"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with pytest.raises(ValueError) as exc_info:
                await create_router()

            assert "Invalid router backend: invalid_backend" in str(exc_info.value)
            assert "Must be 'gemini' or 'ollama'" in str(exc_info.value)


class TestCreateRouterSync:
    """Tests for synchronous create_router_sync function."""

    def test_create_router_sync_gemini_backend(self, mock_settings):
        """Test creating router synchronously with Gemini backend."""
        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.GeminiRouterBackend"
            ) as mock_gemini_class:
                mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                mock_gemini_class.return_value = mock_gemini_backend

                router = create_router_sync()

                assert isinstance(router, MessageRouter)
                mock_gemini_class.assert_called_once()
                assert router._backend == mock_gemini_backend

    def test_create_router_sync_ollama_backend(self, mock_settings):
        """Test creating router synchronously with Ollama backend (no health check)."""
        mock_settings.router_backend = "ollama"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                mock_ollama_backend = MagicMock(spec=OllamaRouterBackend)
                mock_ollama_class.return_value = mock_ollama_backend

                router = create_router_sync()

                assert isinstance(router, MessageRouter)
                mock_ollama_class.assert_called_once()
                assert router._backend == mock_ollama_backend
                # Health check should NOT be called in sync mode
                assert not hasattr(mock_ollama_backend, "health_check") or not getattr(
                    mock_ollama_backend.health_check, "called", False
                )

    def test_create_router_sync_invalid_backend(self, mock_settings):
        """Test invalid backend raises ValueError in sync mode."""
        mock_settings.router_backend = "invalid_backend"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with pytest.raises(ValueError) as exc_info:
                create_router_sync()

            assert "Invalid router backend: invalid_backend" in str(exc_info.value)
            assert "Must be 'gemini' or 'ollama'" in str(exc_info.value)


class TestRouterFactoryEdgeCases:
    """Tests for edge cases and integration scenarios."""

    @pytest.mark.asyncio
    async def test_ollama_health_check_timeout(self, mock_settings):
        """Test Ollama health check timeout falls back to Gemini."""
        mock_settings.router_backend = "ollama"

        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.OllamaRouterBackend"
            ) as mock_ollama_class:
                with patch(
                    "zetherion_ai.agent.router_factory.GeminiRouterBackend"
                ) as mock_gemini_class:
                    # Ollama health check times out
                    mock_ollama_backend = MagicMock(spec=OllamaRouterBackend)
                    mock_ollama_backend.health_check = AsyncMock(
                        side_effect=TimeoutError("Health check timed out")
                    )
                    mock_ollama_class.return_value = mock_ollama_backend

                    # Gemini as fallback
                    mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                    mock_gemini_class.return_value = mock_gemini_backend

                    router = await create_router()

                    assert isinstance(router, MessageRouter)
                    mock_gemini_class.assert_called_once()
                    assert router._backend == mock_gemini_backend

    @pytest.mark.asyncio
    async def test_create_router_preserves_backend_reference(self, mock_settings):
        """Test that MessageRouter correctly wraps the backend."""
        with patch("zetherion_ai.agent.router_factory.get_settings", return_value=mock_settings):  # noqa: SIM117
            with patch(
                "zetherion_ai.agent.router_factory.GeminiRouterBackend"
            ) as mock_gemini_class:
                mock_gemini_backend = MagicMock(spec=GeminiRouterBackend)
                mock_gemini_class.return_value = mock_gemini_backend

                router = await create_router()

                # Verify backend is properly wrapped
                assert router._backend is mock_gemini_backend
                assert isinstance(router, MessageRouter)
