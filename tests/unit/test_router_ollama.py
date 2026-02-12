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
    settings.ollama_router_model = "llama3.2:3b"
    settings.ollama_timeout = 30.0
    # Fallback to generation container
    settings.ollama_url = "http://ollama:11434"
    settings.ollama_generation_model = "llama3.1:8b"
    return settings


class TestOllamaRouterBackendInit:
    """Tests for OllamaRouterBackend initialization."""

    def test_init(self, mock_settings):
        """Test initialization with dedicated router container."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            assert backend._url == "http://ollama-router:11434"
            assert backend._model == "llama3.2:3b"
            assert backend._timeout == 30.0
            assert backend._fallback_url == "http://ollama:11434"
            assert backend._fallback_model == "llama3.1:8b"


class TestOllamaRouterBackendWarmup:
    """Tests for warmup method."""

    @pytest.mark.asyncio
    async def test_warmup_success(self, mock_settings):
        """Test successful warmup loads model and sets _is_warm."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            assert backend._is_warm is False

        with patch("zetherion_ai.agent.router_ollama.httpx.AsyncClient") as mock_client_cls:
            mock_ctx_client = AsyncMock()
            mock_ctx_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await backend.warmup()

        assert result is True
        assert backend._is_warm is True

    @pytest.mark.asyncio
    async def test_warmup_already_warm(self, mock_settings):
        """Test early return when already warm."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._is_warm = True

            result = await backend.warmup()

        assert result is True

    @pytest.mark.asyncio
    async def test_warmup_exception(self, mock_settings):
        """Test warmup returns False on exception."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()

        with patch("zetherion_ai.agent.router_ollama.httpx.AsyncClient") as mock_client_cls:
            mock_ctx_client = AsyncMock()
            mock_ctx_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await backend.warmup()

        assert result is False
        assert backend._is_warm is False


class TestOllamaRouterBackendKeepWarm:
    """Tests for keep_warm method."""

    @pytest.mark.asyncio
    async def test_keep_warm_success(self, mock_settings):
        """Test successful keep-warm ping."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            result = await backend.keep_warm()

        assert result is True

    @pytest.mark.asyncio
    async def test_keep_warm_exception(self, mock_settings):
        """Test keep_warm returns False and resets _is_warm on exception."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._is_warm = True
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=Exception("Connection lost"))

            result = await backend.keep_warm()

        assert result is False
        assert backend._is_warm is False


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
        assert "timed out" in decision.reasoning.lower()

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
        assert "unreachable" in decision.reasoning.lower()

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
    async def test_classify_json_decode_error_from_response(self, mock_settings):
        """Test handling JSONDecodeError raised by response.json() itself."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5
        assert "failed parsing" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_classify_value_error_fallback(self, mock_settings):
        """Test that a ValueError from inner parsing falls back to simple_query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        # Valid JSON but missing the intent field => triggers ValueError
        mock_response.json.return_value = {
            "response": json.dumps({"no_intent_key": "oops", "confidence": 0.5})
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(return_value=mock_response)

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5
        assert "failed parsing" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_classify_generic_exception_fallback(self, mock_settings):
        """Test that an unexpected Exception falls back to complex_task with Claude."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(side_effect=OSError("disk error"))

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True
        assert "failed" in decision.reasoning.lower()

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


class TestOllamaRouterBackendCascade:
    """Tests for the primary -> fallback cascade in classify."""

    @pytest.mark.asyncio
    async def test_cascade_primary_fails_fallback_succeeds(self, mock_settings):
        """Test that when primary fails, fallback model succeeds."""
        fallback_response = MagicMock()
        fallback_response.status_code = 200
        fallback_response.raise_for_status = MagicMock()
        fallback_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "task_management",
                    "confidence": 0.9,
                    "reasoning": "Fallback classified correctly",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            # First call (primary) fails, second call (fallback) succeeds
            backend._client.post = AsyncMock(
                side_effect=[
                    httpx.TimeoutException("Primary timed out"),
                    fallback_response,
                ]
            )

            decision = await backend.classify("Add a task to review docs")

        assert decision.intent == MessageIntent.TASK_MANAGEMENT
        assert decision.confidence == 0.9
        assert decision.reasoning == "Fallback classified correctly"
        assert backend._client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_cascade_primary_fails_fallback_fails_timeout(self, mock_settings):
        """Test that when both models timeout, returns simple query fallback."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5
        assert "timed out" in decision.reasoning.lower()
        assert backend._client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_cascade_primary_fails_fallback_fails_generic(self, mock_settings):
        """Test that when both models fail with generic error, returns complex task."""
        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(
                side_effect=RuntimeError("Unexpected error")
            )

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True
        assert backend._client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_cascade_primary_succeeds_no_fallback(self, mock_settings):
        """Test that when primary succeeds, fallback is never called."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "simple_query",
                    "confidence": 0.95,
                    "reasoning": "Primary succeeded",
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
        # Only one call — primary succeeded, no fallback needed
        assert backend._client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_cascade_uses_correct_urls_and_models(self, mock_settings):
        """Test that cascade passes correct URLs and models to each attempt."""
        fallback_response = MagicMock()
        fallback_response.status_code = 200
        fallback_response.raise_for_status = MagicMock()
        fallback_response.json.return_value = {
            "response": json.dumps(
                {
                    "intent": "simple_query",
                    "confidence": 0.85,
                    "reasoning": "Fallback OK",
                }
            )
        }

        with patch("zetherion_ai.agent.router_ollama.get_settings", return_value=mock_settings):
            backend = OllamaRouterBackend()
            backend._client = MagicMock()
            backend._client.post = AsyncMock(
                side_effect=[
                    ValueError("Primary parse failed"),
                    fallback_response,
                ]
            )

            decision = await backend.classify("test")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        calls = backend._client.post.call_args_list
        assert len(calls) == 2
        # Primary call uses router URL
        assert "http://ollama-router:11434/api/generate" in str(calls[0])
        # Fallback call uses generation URL
        assert "http://ollama:11434/api/generate" in str(calls[1])


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
                {"name": "llama3.2:3b"},  # Default router model
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
