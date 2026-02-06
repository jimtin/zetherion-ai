"""Unit tests for the message router module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.agent.router import (
    GeminiRouterBackend,
    MessageIntent,
    MessageRouter,
    RoutingDecision,
)


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.gemini_api_key.get_secret_value.return_value = "test-api-key"
    settings.router_model = "gemini-2.0-flash-exp"
    return settings


class TestMessageIntent:
    """Tests for MessageIntent enum."""

    def test_all_intents_defined(self):
        """Test that all expected intents are defined."""
        expected = [
            "SIMPLE_QUERY",
            "COMPLEX_TASK",
            "MEMORY_STORE",
            "MEMORY_RECALL",
            "SYSTEM_COMMAND",
            # Phase 5G skill intents
            "TASK_MANAGEMENT",
            "CALENDAR_QUERY",
            "PROFILE_QUERY",
        ]
        for intent in expected:
            assert hasattr(MessageIntent, intent)

    def test_intent_values(self):
        """Test intent values."""
        assert MessageIntent.SIMPLE_QUERY.value == "simple_query"
        assert MessageIntent.COMPLEX_TASK.value == "complex_task"
        assert MessageIntent.MEMORY_STORE.value == "memory_store"
        assert MessageIntent.MEMORY_RECALL.value == "memory_recall"
        assert MessageIntent.SYSTEM_COMMAND.value == "system_command"
        # Phase 5G skill intents
        assert MessageIntent.TASK_MANAGEMENT.value == "task_management"
        assert MessageIntent.CALENDAR_QUERY.value == "calendar_query"
        assert MessageIntent.PROFILE_QUERY.value == "profile_query"


class TestRoutingDecision:
    """Tests for RoutingDecision dataclass."""

    def test_basic_creation(self):
        """Test basic routing decision creation."""
        decision = RoutingDecision(
            intent=MessageIntent.SIMPLE_QUERY,
            confidence=0.9,
            reasoning="Simple greeting",
            use_claude=False,
        )
        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.9
        assert decision.reasoning == "Simple greeting"
        assert decision.use_claude is False

    def test_requires_complex_model_property(self):
        """Test requires_complex_model property."""
        simple_decision = RoutingDecision(
            intent=MessageIntent.SIMPLE_QUERY,
            confidence=0.8,
            reasoning="Simple",
            use_claude=False,
        )
        assert simple_decision.requires_complex_model is False

        complex_decision = RoutingDecision(
            intent=MessageIntent.COMPLEX_TASK,
            confidence=0.9,
            reasoning="Complex",
            use_claude=True,
        )
        assert complex_decision.requires_complex_model is True


class TestGeminiRouterBackendInit:
    """Tests for GeminiRouterBackend initialization."""

    def test_init(self, mock_settings):
        """Test initialization."""
        mock_client = MagicMock()
        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                assert backend._model == "gemini-2.0-flash-exp"


class TestGeminiRouterBackendClassify:
    """Tests for classify method."""

    @pytest.mark.asyncio
    async def test_classify_simple_query(self, mock_settings):
        """Test classifying a simple query."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "simple_query",
                "confidence": 0.95,
                "reasoning": "Simple greeting",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("Hello!")

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.95
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_complex_task(self, mock_settings):
        """Test classifying a complex task."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "complex_task",
                "confidence": 0.9,
                "reasoning": "Code generation request",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("Write a Python script to sort a list")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_memory_store(self, mock_settings):
        """Test classifying a memory store request."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "memory_store",
                "confidence": 0.88,
                "reasoning": "User wants to store preference",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("Remember that I prefer dark mode")

        assert decision.intent == MessageIntent.MEMORY_STORE

    @pytest.mark.asyncio
    async def test_classify_memory_recall(self, mock_settings):
        """Test classifying a memory recall request."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "memory_recall",
                "confidence": 0.92,
                "reasoning": "User asking about stored info",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("What's my favorite color?")

        assert decision.intent == MessageIntent.MEMORY_RECALL

    @pytest.mark.asyncio
    async def test_classify_system_command(self, mock_settings):
        """Test classifying a system command."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "system_command",
                "confidence": 0.99,
                "reasoning": "Help request",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("help")

        assert decision.intent == MessageIntent.SYSTEM_COMMAND

    @pytest.mark.asyncio
    async def test_classify_task_management(self, mock_settings):
        """Test classifying a task management request."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "task_management",
                "confidence": 0.92,
                "reasoning": "User wants to create a task",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("Add a task to buy groceries")

        assert decision.intent == MessageIntent.TASK_MANAGEMENT
        assert decision.use_claude is False  # Skills handle their own processing

    @pytest.mark.asyncio
    async def test_classify_calendar_query(self, mock_settings):
        """Test classifying a calendar query."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "calendar_query",
                "confidence": 0.88,
                "reasoning": "User asking about schedule",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("What's on my calendar today?")

        assert decision.intent == MessageIntent.CALENDAR_QUERY
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_profile_query(self, mock_settings):
        """Test classifying a profile query."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "profile_query",
                "confidence": 0.95,
                "reasoning": "User asking about stored profile data",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("What do you know about me?")

        assert decision.intent == MessageIntent.PROFILE_QUERY
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_json_in_code_block(self, mock_settings):
        """Test handling JSON wrapped in code block."""
        mock_response = MagicMock()
        mock_response.text = """```json
{
    "intent": "simple_query",
    "confidence": 0.85,
    "reasoning": "Question"
}
```"""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("What is 2+2?")

        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_invalid_json(self, mock_settings):
        """Test handling invalid JSON response."""
        mock_response = MagicMock()
        mock_response.text = "This is not valid JSON"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("test")

        # Should default to simple_query on JSON error
        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.5

    @pytest.mark.asyncio
    async def test_classify_missing_intent_field(self, mock_settings):
        """Test handling response missing intent field."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "confidence": 0.9,
                "reasoning": "No intent provided",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("test")

        # Should default to simple_query on validation error
        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_invalid_intent_value(self, mock_settings):
        """Test handling invalid intent value."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "invalid_intent_type",
                "confidence": 0.9,
                "reasoning": "Invalid",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("test")

        # Should default to simple_query on invalid intent
        assert decision.intent == MessageIntent.SIMPLE_QUERY

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping(self, mock_settings):
        """Test that confidence is clamped to valid range."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "simple_query",
                "confidence": 1.5,  # Invalid: > 1.0
                "reasoning": "Test",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("test")

        assert decision.confidence == 1.0  # Clamped to max

    @pytest.mark.asyncio
    async def test_classify_api_error(self, mock_settings):
        """Test handling API error."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API Error")

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("test")

        # Should default to complex_task with Claude on unexpected errors
        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_complex_task_low_confidence(self, mock_settings):
        """Test that complex task with low confidence doesn't use Claude."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "intent": "complex_task",
                "confidence": 0.5,  # Below 0.7 threshold
                "reasoning": "Uncertain",
            }
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                decision = await backend.classify("maybe complex task")

        assert decision.intent == MessageIntent.COMPLEX_TASK
        assert decision.use_claude is False  # Low confidence, don't use Claude


class TestGeminiRouterBackendGenerateSimpleResponse:
    """Tests for generate_simple_response method."""

    @pytest.mark.asyncio
    async def test_generate_simple_response_success(self, mock_settings):
        """Test successful simple response generation."""
        mock_response = MagicMock()
        mock_response.text = "Hello! How can I help you?"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                response = await backend.generate_simple_response("Hi")

        assert response == "Hello! How can I help you?"

    @pytest.mark.asyncio
    async def test_generate_simple_response_empty(self, mock_settings):
        """Test handling empty response."""
        mock_response = MagicMock()
        mock_response.text = None

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                response = await backend.generate_simple_response("Hi")

        assert response == ""

    @pytest.mark.asyncio
    async def test_generate_simple_response_error(self, mock_settings):
        """Test handling error in response generation."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API Error")

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                response = await backend.generate_simple_response("Hi")

        assert "trouble processing" in response


class TestGeminiRouterBackendHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_settings):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.text = "OK"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                is_healthy = await backend.health_check()

        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_health_check_empty_response(self, mock_settings):
        """Test health check with empty response."""
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                is_healthy = await backend.health_check()

        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_error(self, mock_settings):
        """Test health check with error."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("Connection error")

        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client", return_value=mock_client):
                backend = GeminiRouterBackend()
                is_healthy = await backend.health_check()

        assert is_healthy is False


class TestMessageRouter:
    """Tests for MessageRouter wrapper class."""

    def test_init_default_backend(self, mock_settings):
        """Test initialization with default backend."""
        with patch("zetherion_ai.agent.router.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.router.genai.Client"):
                router = MessageRouter()
                assert isinstance(router._backend, GeminiRouterBackend)

    def test_init_custom_backend(self):
        """Test initialization with custom backend."""
        mock_backend = MagicMock()
        router = MessageRouter(backend=mock_backend)
        assert router._backend == mock_backend

    @pytest.mark.asyncio
    async def test_classify_delegates_to_backend(self):
        """Test that classify delegates to backend."""
        mock_backend = AsyncMock()
        expected_decision = RoutingDecision(
            intent=MessageIntent.SIMPLE_QUERY,
            confidence=0.9,
            reasoning="Test",
            use_claude=False,
        )
        mock_backend.classify.return_value = expected_decision

        router = MessageRouter(backend=mock_backend)
        decision = await router.classify("test message")

        mock_backend.classify.assert_called_once_with("test message")
        assert decision == expected_decision

    @pytest.mark.asyncio
    async def test_generate_simple_response_delegates(self):
        """Test that generate_simple_response delegates to backend."""
        mock_backend = AsyncMock()
        mock_backend.generate_simple_response.return_value = "Hello!"

        router = MessageRouter(backend=mock_backend)
        response = await router.generate_simple_response("Hi")

        mock_backend.generate_simple_response.assert_called_once_with("Hi")
        assert response == "Hello!"

    @pytest.mark.asyncio
    async def test_health_check_delegates(self):
        """Test that health_check delegates to backend."""
        mock_backend = AsyncMock()
        mock_backend.health_check.return_value = True

        router = MessageRouter(backend=mock_backend)
        is_healthy = await router.health_check()

        mock_backend.health_check.assert_called_once()
        assert is_healthy is True
