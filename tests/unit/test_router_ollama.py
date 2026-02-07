"""Unit tests for the Ollama router backend."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.agent.router import MessageIntent
from zetherion_ai.agent.router_ollama import OllamaRouterBackend


@pytest.fixture
def mock_settings():
    """Create mock settings for router container."""
    settings = MagicMock()
    # Router uses dedicated container URL
    settings.ollama_router_url = "http://ollama-router:11434"
    settings.ollama_router_model = "qwen2.5:0.5b"
    settings.ollama_timeout = 30.0
    return settings


class TestOllamaRouterBackendInit:
    """Tests for OllamaRouterBackend initialization."""

    def test_init(self, mock_settings):
        """Test initialization with dedicated router container."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            assert backend._url == "http://ollama-router:11434"
            assert backend._model == "qwen2.5:0.5b"
            assert backend._timeout == 30.0


class TestOllamaRouterBackendClassify:
    """Tests for classify method."""

    @pytest.mark.asyncio
    async def test_classify_simple_query(self, mock_settings):
        """Test classifying a simple query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "simple_query",
                    "confidence": 0.95,
                    "reasoning": "Simple greeting",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("Hello!")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.95
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_complex_task(self, mock_settings):
        """Test classifying a complex task."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "complex_task",
                    "confidence": 0.9,
                    "reasoning": "Code generation request",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("Write a Python script")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_memory_store(self, mock_settings):
        """Test classifying a memory store request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "memory_store",
                    "confidence": 0.88,
                    "reasoning": "User wants to store preference",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("Remember that I prefer dark mode")

        assert decision.intent == MessageIntent.MEMORY_STORE

    @pytest.mark.asyncio
    async def test_classify_memory_recall(self, mock_settings):
        """Test classifying a memory recall request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "memory_recall",
                    "confidence": 0.92,
                    "reasoning": "User asking about stored info",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("What's my favorite color?")

        assert decision.intent == MessageIntent.MEMORY_RECALL

    @pytest.mark.asyncio
    async def test_classify_system_command(self, mock_settings):
        """Test classifying a system command."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "system_command",
                    "confidence": 0.99,
                    "reasoning": "Help request",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("help")

        assert decision.intent == MessageIntent.SYSTEM_COMMAND

    @pytest.mark.asyncio
    async def test_classify_task_management(self, mock_settings):
        """Test classifying a task management request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "task_management",
                    "confidence": 0.92,
                    "reasoning": "User wants to create a task",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("Add a task to buy groceries")

        assert decision.intent == MessageIntent.TASK_MANAGEMENT
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_calendar_query(self, mock_settings):
        """Test classifying a calendar query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "calendar_query",
                    "confidence": 0.88,
                    "reasoning": "User asking about schedule",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("What's on my calendar today?")

        assert decision.intent == MessageIntent.CALENDAR_QUERY
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_profile_query(self, mock_settings):
        """Test classifying a profile query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "profile_query",
                    "confidence": 0.95,
                    "reasoning": "User asking about stored profile data",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("What do you know about me?")

        assert decision.intent == MessageIntent.PROFILE_QUERY
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_json_in_code_block(self, mock_settings):
        """Test handling JSON wrapped in code block."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": """```json
{"intent": "simple_query", "confidence": 0.85, "reasoning": "Question"}
```"""
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("What is 2+2?")

        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_timeout(self, mock_settings):
        """Test handling timeout."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5
        assert "timeout" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_classify_connection_error(self, mock_settings):
        """Test handling connection error."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5
        assert "connection failed" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_classify_invalid_json(self, mock_settings):
        """Test handling invalid JSON response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "This is not valid JSON"}

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5

    @pytest.mark.asyncio
    async def test_classify_missing_intent(self, mock_settings):
        """Test handling response missing intent field."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "confidence": 0.9,
                    "reasoning": "No intent provided",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_invalid_intent(self, mock_settings):
        """Test handling invalid intent value."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "invalid_intent_type",
                    "confidence": 0.9,
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping(self, mock_settings):
        """Test that confidence is clamped to valid range."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "simple_query",
                    "confidence": 1.5,  # Invalid: > 1.0
                    "reasoning": "Test",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.confidence == 1.0  # Clamped to max

    @pytest.mark.asyncio
    async def test_classify_unexpected_error(self, mock_settings):
        """Test handling unexpected error."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=RuntimeError("Unexpected"))

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_complex_task_low_confidence(self, mock_settings):
        """Test that complex task with low confidence doesn't use Claude."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "complex_task",
                    "confidence": 0.5,  # Below 0.7 threshold
                    "reasoning": "Uncertain",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("maybe complex task")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is False


class TestOllamaRouterBackendGenerateSimpleResponse:
    """Tests for generate_simple_response method."""

    @pytest.mark.asyncio
    async def test_generate_simple_response_success(self, mock_settings):
        """Test successful simple response generation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "Hello! How can I help you?"}

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            response = await backend.generate_simple_response("Hi")

        assert response == "Hello! How can I help you?"

    @pytest.mark.asyncio
    async def test_generate_simple_response_error(self, mock_settings):
        """Test handling error in response generation."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=Exception("API Error"))

            response = await backend.generate_simple_response("Hi")

        assert "trouble processing" in response


class TestOllamaRouterBackendHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_model_found(self, mock_settings):
        """Test health check when model is available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "qwen2.5:0.5b"},  # Default router model
                {"name": "llama3.1:8b"},
                {"name": "phi-3"},
            ]
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.get = AsyncMock(return_value=mock_response)

            is_healthy = await backend.health_check()

        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_health_check_model_not_found(self, mock_settings):
        """Test health check when model is not available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "phi-3"},  # Our model is not in the list
            ]
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.get = AsyncMock(return_value=mock_response)

            is_healthy = await backend.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_error(self, mock_settings):
        """Test health check with error."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.get = AsyncMock(side_effect=Exception("Connection error"))

            is_healthy = await backend.health_check()

        assert is_healthy is False


class TestOllamaRouterBackendClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close(self, mock_settings):
        """Test closing the HTTP client."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.aclose = AsyncMock()

            await backend.close()

        backend._client.aclose.assert_called_once()
