"""Tests for Ollama router backend."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest


class TestOllamaRouterBackend:
    """Tests for OllamaRouterBackend intent classification."""

    @pytest.fixture
    def mock_httpx_client(self):
        """Create mock httpx.AsyncClient."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def router_backend(self, monkeypatch, mock_httpx_client):
        """Create OllamaRouterBackend with mocked HTTP client."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        # Router uses dedicated container
        monkeypatch.setenv("OLLAMA_ROUTER_HOST", "ollama-router")
        monkeypatch.setenv("OLLAMA_ROUTER_PORT", "11434")
        monkeypatch.setenv("OLLAMA_ROUTER_MODEL", "llama3.2:3b")

        with patch(
            "zetherion_ai.agent.router_ollama.httpx.AsyncClient", return_value=mock_httpx_client
        ):
            from zetherion_ai.agent.router_ollama import OllamaRouterBackend

            return OllamaRouterBackend()

    @pytest.mark.asyncio
    async def test_router_imports(self) -> None:
        """Test that Ollama router imports correctly."""
        from zetherion_ai.agent.router_ollama import OllamaRouterBackend

        assert OllamaRouterBackend is not None

    @pytest.mark.asyncio
    async def test_classify_simple_query(self, router_backend, mock_httpx_client, monkeypatch):
        """Test classification of simple query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        # Mock Ollama HTTP response
        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "simple_query", "confidence": 0.95, "reasoning": "greeting detected"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("Hello!")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.95
        assert decision.use_claude is False
        assert "greeting" in decision.reasoning

    @pytest.mark.asyncio
    async def test_classify_complex_task_high_confidence(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test complex task with high confidence uses Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "complex_task", "confidence": 0.85, "reasoning": "code generation request"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("Write a Python script to scrape websites")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.85
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_complex_task_low_confidence(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test complex task with low confidence doesn't use Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "complex_task", "confidence": 0.6, "reasoning": "unclear request"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("Can you help?")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.6
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_memory_store(self, router_backend, mock_httpx_client, monkeypatch):
        """Test classification of memory storage request."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "memory_store", "confidence": 0.92, "reasoning": "explicit remember request"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("Remember that I prefer dark mode")

        assert decision.intent.value == "memory_store"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_memory_recall(self, router_backend, mock_httpx_client, monkeypatch):
        """Test classification of memory recall request."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "memory_recall", "confidence": 0.88, "reasoning": "asking about past"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("What did we discuss yesterday?")

        assert decision.intent.value == "memory_recall"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_system_command(self, router_backend, mock_httpx_client, monkeypatch):
        """Test classification of system command."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "system_command", "confidence": 0.99, "reasoning": "help request"}'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("/help")

        assert decision.intent.value == "system_command"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_json_in_markdown_code_block(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test parsing JSON wrapped in markdown code blocks."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '```json\n{"intent": "simple_query", "confidence": 0.9, "reasoning": "test"}\n```'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test message")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.9

    @pytest.mark.asyncio
    async def test_classify_json_without_language_tag(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test parsing JSON in code blocks without language tag."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '```\n{"intent": "simple_query", "confidence": 0.85, "reasoning": "test"}\n```'  # noqa: E501
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.85

    @pytest.mark.asyncio
    async def test_classify_raw_json(self, router_backend, mock_httpx_client, monkeypatch):
        """Test parsing raw JSON without code blocks."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "simple_query", "confidence": 0.8, "reasoning": "plain json"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_timeout_fallback(self, router_backend, mock_httpx_client, monkeypatch):
        """Test that timeout falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_httpx_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert "timeout" in decision.reasoning.lower()
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_connection_error_fallback(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that connection errors fall back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_httpx_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert "connection" in decision.reasoning.lower()
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_invalid_json_fallback(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that invalid JSON falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {"response": "Not valid JSON at all"}
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert "fallback" in decision.reasoning.lower()
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_missing_intent_field(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that missing intent field falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"confidence": 0.9, "reasoning": "missing intent"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_invalid_intent_value(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that invalid intent value falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "invalid_intent", "confidence": 0.9, "reasoning": "bad value"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping_high(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that confidence > 1.0 is clamped to 1.0."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "simple_query", "confidence": 1.5, "reasoning": "too high"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.confidence == 1.0

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping_low(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that confidence < 0.0 is clamped to 0.0."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "simple_query", "confidence": -0.5, "reasoning": "negative"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_missing_confidence_uses_default(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that missing confidence uses default 0.8."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "simple_query", "reasoning": "no confidence"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_unexpected_exception_fallback(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that unexpected exceptions fall back to complex_task with Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_httpx_client.post = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        decision = await router_backend.classify("test")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.5
        assert decision.use_claude is True
        assert "failed" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_generate_simple_response_success(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test successful simple response generation."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {"response": "Hello! How can I help you today?"}
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        response = await router_backend.generate_simple_response("Hi")

        assert response == "Hello! How can I help you today?"
        mock_httpx_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_simple_response_error_fallback(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that generation errors return fallback message."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_httpx_client.post = AsyncMock(side_effect=Exception("API error"))

        response = await router_backend.generate_simple_response("Hi")

        assert "trouble" in response.lower()
        assert "try again" in response.lower()

    @pytest.mark.asyncio
    async def test_classify_case_insensitive_intent(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test that intent parsing is case-insensitive."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "response": '{"intent": "SIMPLE_QUERY", "confidence": 0.9, "reasoning": "uppercase"}'
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.post = AsyncMock(return_value=mock_response)

        decision = await router_backend.classify("test")

        assert decision.intent.value == "simple_query"

    @pytest.mark.asyncio
    async def test_health_check_model_available(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test health check when model is available."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.2:3b"},  # Default router model
                {"name": "llama3.1:8b"},
                {"name": "mistral:7b"},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get = AsyncMock(return_value=mock_response)

        is_healthy = await router_backend.health_check()

        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_health_check_model_not_available(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test health check when model is not available."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.json.return_value = {
            "models": [
                {"name": "mistral:7b"},  # Different model
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get = AsyncMock(return_value=mock_response)

        is_healthy = await router_backend.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_connection_error(
        self, router_backend, mock_httpx_client, monkeypatch
    ):
        """Test health check returns False on connection error."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_httpx_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        is_healthy = await router_backend.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_close_client(self, router_backend, mock_httpx_client):
        """Test that close() properly closes the HTTP client."""
        await router_backend.close()

        mock_httpx_client.aclose.assert_called_once()
