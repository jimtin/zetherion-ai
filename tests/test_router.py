"""Tests for message router."""

from unittest.mock import Mock, patch

import pytest


class TestMessageRouter:
    """Tests for MessageRouter intent classification."""

    @pytest.mark.asyncio
    async def test_router_imports(self) -> None:
        """Test that router imports correctly."""
        from secureclaw.agent.router import MessageIntent

        assert MessageIntent.SIMPLE_QUERY.value == "simple_query"
        assert MessageIntent.COMPLEX_TASK.value == "complex_task"
        assert MessageIntent.MEMORY_STORE.value == "memory_store"
        assert MessageIntent.MEMORY_RECALL.value == "memory_recall"
        assert MessageIntent.SYSTEM_COMMAND.value == "system_command"

    def test_routing_decision_dataclass(self) -> None:
        """Test RoutingDecision dataclass."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        decision = RoutingDecision(
            intent=MessageIntent.SIMPLE_QUERY,
            confidence=0.95,
            reasoning="greeting detected",
            use_claude=False,
        )

        assert decision.intent == MessageIntent.SIMPLE_QUERY
        assert decision.confidence == 0.95
        assert decision.use_claude is False

    def test_complex_task_uses_claude(self) -> None:
        """Test that complex tasks with high confidence use Claude."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        decision = RoutingDecision(
            intent=MessageIntent.COMPLEX_TASK,
            confidence=0.9,
            reasoning="code generation request",
            use_claude=True,
        )

        assert decision.use_claude is True

    @pytest.fixture
    def router(self, monkeypatch):
        """Create MessageRouter with mocked Gemini client."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_client = Mock()
        with patch("secureclaw.agent.router.genai.Client", return_value=mock_client):
            from secureclaw.agent.router import MessageRouter

            return MessageRouter()

    @pytest.mark.asyncio
    async def test_classify_simple_query(self, router, monkeypatch):
        """Test classification of simple query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        # Mock Gemini response
        mock_response = Mock()
        mock_response.text = (
            '{"intent": "simple_query", "confidence": 0.95, "reasoning": "greeting detected"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("Hello!")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.95
        assert decision.use_claude is False
        assert "greeting" in decision.reasoning

    @pytest.mark.asyncio
    async def test_classify_complex_task_high_confidence(self, router, monkeypatch):
        """Test complex task with high confidence uses Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "complex_task", "confidence": 0.85, "reasoning": "code generation request"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("Write a Python script to scrape websites")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.85
        assert decision.use_claude is True

    @pytest.mark.asyncio
    async def test_classify_complex_task_low_confidence(self, router, monkeypatch):
        """Test complex task with low confidence doesn't use Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "complex_task", "confidence": 0.6, "reasoning": "unclear request"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("Can you help?")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.6
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_memory_store(self, router, monkeypatch):
        """Test classification of memory storage request."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "memory_store", "confidence": 0.92, '
            '"reasoning": "explicit remember request"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("Remember that I prefer dark mode")

        assert decision.intent.value == "memory_store"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_memory_recall(self, router, monkeypatch):
        """Test classification of memory recall request."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "memory_recall", "confidence": 0.88, "reasoning": "asking about past"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("What did we discuss yesterday?")

        assert decision.intent.value == "memory_recall"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_system_command(self, router, monkeypatch):
        """Test classification of system command."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "system_command", "confidence": 0.99, "reasoning": "help request"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("/help")

        assert decision.intent.value == "system_command"
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_json_in_markdown_code_block(self, router, monkeypatch):
        """Test parsing JSON wrapped in markdown code blocks."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '```json\n{"intent": "simple_query", "confidence": 0.9, "reasoning": "test"}\n```'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test message")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.9

    @pytest.mark.asyncio
    async def test_classify_json_without_language_tag(self, router, monkeypatch):
        """Test parsing JSON in code blocks without language tag."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '```\n{"intent": "simple_query", "confidence": 0.85, "reasoning": "test"}\n```'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.85

    @pytest.mark.asyncio
    async def test_classify_raw_json(self, router, monkeypatch):
        """Test parsing raw JSON without code blocks."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "simple_query", "confidence": 0.8, "reasoning": "plain json"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_invalid_json_fallback(self, router, monkeypatch):
        """Test that invalid JSON falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = "Not valid JSON at all"
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert "fallback" in decision.reasoning.lower()
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_missing_intent_field(self, router, monkeypatch):
        """Test that missing intent field falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = '{"confidence": 0.9, "reasoning": "missing intent"}'
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5
        assert decision.use_claude is False

    @pytest.mark.asyncio
    async def test_classify_invalid_intent_value(self, router, monkeypatch):
        """Test that invalid intent value falls back to simple_query."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "invalid_intent", "confidence": 0.9, "reasoning": "bad value"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
        assert decision.confidence == 0.5

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping_high(self, router, monkeypatch):
        """Test that confidence > 1.0 is clamped to 1.0."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "simple_query", "confidence": 1.5, "reasoning": "too high"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.confidence == 1.0

    @pytest.mark.asyncio
    async def test_classify_confidence_clamping_low(self, router, monkeypatch):
        """Test that confidence < 0.0 is clamped to 0.0."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "simple_query", "confidence": -0.5, "reasoning": "negative"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_missing_confidence_uses_default(self, router, monkeypatch):
        """Test that missing confidence uses default 0.8."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = '{"intent": "simple_query", "reasoning": "no confidence"}'
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_unexpected_exception_fallback(self, router, monkeypatch):
        """Test that unexpected exceptions fall back to complex_task with Claude."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        router._client.models.generate_content = Mock(side_effect=RuntimeError("API error"))

        decision = await router.classify("test")

        assert decision.intent.value == "complex_task"
        assert decision.confidence == 0.5
        assert decision.use_claude is True
        assert "failed" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_generate_simple_response_success(self, router, monkeypatch):
        """Test successful simple response generation."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = "Hello! How can I help you today?"
        router._client.models.generate_content = Mock(return_value=mock_response)

        response = await router.generate_simple_response("Hi")

        assert response == "Hello! How can I help you today?"
        router._client.models.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_simple_response_error_fallback(self, router, monkeypatch):
        """Test that generation errors return fallback message."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        router._client.models.generate_content = Mock(side_effect=Exception("API error"))

        response = await router.generate_simple_response("Hi")

        assert "trouble" in response.lower()
        assert "try again" in response.lower()

    @pytest.mark.asyncio
    async def test_classify_case_insensitive_intent(self, router, monkeypatch):
        """Test that intent parsing is case-insensitive."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        mock_response = Mock()
        mock_response.text = (
            '{"intent": "SIMPLE_QUERY", "confidence": 0.9, "reasoning": "uppercase"}'
        )
        router._client.models.generate_content = Mock(return_value=mock_response)

        decision = await router.classify("test")

        assert decision.intent.value == "simple_query"
