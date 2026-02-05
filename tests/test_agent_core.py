"""Tests for Agent core functionality."""

import sys
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from anthropic import APIConnectionError, APITimeoutError, RateLimitError

# Mock openai module if not installed
if "openai" not in sys.modules:
    # Create mock openai module
    mock_openai = MagicMock()

    class OpenAIConnectionError(Exception):
        """Mock OpenAI connection error."""

        pass

    class OpenAITimeoutError(Exception):
        """Mock OpenAI timeout error."""

        pass

    class OpenAIRateLimitError(Exception):
        """Mock OpenAI rate limit error."""

        pass

    # Add exceptions to mock module
    mock_openai.APIConnectionError = OpenAIConnectionError
    mock_openai.APITimeoutError = OpenAITimeoutError
    mock_openai.RateLimitError = OpenAIRateLimitError
    mock_openai.AsyncOpenAI = MagicMock

    sys.modules["openai"] = mock_openai
else:
    from openai import APIConnectionError as OpenAIConnectionError
    from openai import APITimeoutError as OpenAITimeoutError
    from openai import RateLimitError as OpenAIRateLimitError


class TestRetryWithExponentialBackoff:
    """Tests for retry_with_exponential_backoff function."""

    @pytest.mark.asyncio
    async def test_successful_first_attempt(self):
        """Test that function succeeds on first attempt without retry."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        async def success_func():
            return "success"

        result = await retry_with_exponential_backoff(success_func)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self):
        """Test retry logic on connection errors."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise APIConnectionError("Connection failed")
            return "success"

        result = await retry_with_exponential_backoff(flaky_func, max_retries=3)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_timeout_error(self):
        """Test retry logic on timeout errors."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        call_count = 0

        async def timeout_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise APITimeoutError("Timeout")
            return "success"

        result = await retry_with_exponential_backoff(timeout_func, max_retries=3)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_openai_connection_error(self):
        """Test retry logic on OpenAI connection errors."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        call_count = 0

        async def openai_error_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OpenAIConnectionError("Connection failed")
            return "success"

        result = await retry_with_exponential_backoff(openai_error_func, max_retries=3)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Test that exception is raised when max retries exceeded."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        async def always_fail():
            raise APIConnectionError("Always fails")

        with pytest.raises(APIConnectionError):
            await retry_with_exponential_backoff(always_fail, max_retries=3)

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        """Test retry logic handles rate limits with longer backoff."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        call_count = 0

        async def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError("Rate limited")
            return "success"

        result = await retry_with_exponential_backoff(rate_limited_func, max_retries=3)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_openai_rate_limit_retry(self):
        """Test retry logic handles OpenAI rate limits."""
        from secureclaw.agent.core import retry_with_exponential_backoff

        call_count = 0

        async def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OpenAIRateLimitError("Rate limited")
            return "success"

        result = await retry_with_exponential_backoff(rate_limited_func, max_retries=3)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exponential_backoff_delay(self):
        """Test that delay increases exponentially."""
        import asyncio

        from secureclaw.agent.core import retry_with_exponential_backoff

        delays = []

        async def track_delay_func():
            raise APIConnectionError("Fail")

        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            delays.append(delay)
            await original_sleep(0)  # Don't actually sleep in tests

        with (  # noqa: SIM117
            patch("asyncio.sleep", side_effect=mock_sleep),
            pytest.raises(APIConnectionError),
        ):
            await retry_with_exponential_backoff(
                track_delay_func,
                max_retries=3,
                initial_delay=1.0,
                exponential_base=2.0,
            )

        # Should have 2 delays (3 attempts = 2 retries)
        assert len(delays) == 2
        assert delays[0] == 1.0
        assert delays[1] == 2.0

    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        import asyncio

        from secureclaw.agent.core import retry_with_exponential_backoff

        delays = []

        async def always_fail():
            raise APIConnectionError("Fail")

        async def mock_sleep(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        with (  # noqa: SIM117
            patch("asyncio.sleep", side_effect=mock_sleep),
            pytest.raises(APIConnectionError),
        ):
            await retry_with_exponential_backoff(
                always_fail,
                max_retries=5,
                initial_delay=10.0,
                max_delay=15.0,
                exponential_base=2.0,
            )

        # All delays should be capped at max_delay
        assert all(delay <= 15.0 for delay in delays)


class TestAgentInitialization:
    """Tests for Agent initialization."""

    @pytest.mark.asyncio
    async def test_agent_init_with_all_keys(
        self, mock_qdrant_client, mock_embeddings_client, monkeypatch
    ):
        """Test agent initialization with all API keys."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)

            assert agent._has_claude is True
            assert agent._has_openai is True
            assert agent._claude_client is not None
            assert agent._openai_client is not None

    @pytest.mark.asyncio
    async def test_agent_init_without_anthropic_key(
        self, mock_qdrant_client, mock_embeddings_client, monkeypatch
    ):
        """Test agent initialization without Anthropic API key."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)

            assert agent._has_claude is False
            assert agent._has_openai is True
            assert agent._claude_client is None
            assert agent._openai_client is not None

    @pytest.mark.asyncio
    async def test_agent_init_without_openai_key(
        self, mock_qdrant_client, mock_embeddings_client, monkeypatch
    ):
        """Test agent initialization without OpenAI API key."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)

            assert agent._has_claude is True
            assert agent._has_openai is False
            assert agent._claude_client is not None
            assert agent._openai_client is None

    @pytest.mark.asyncio
    async def test_agent_init_without_any_llm_keys(
        self, mock_qdrant_client, mock_embeddings_client, monkeypatch
    ):
        """Test agent initialization without Claude or OpenAI keys."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)

            assert agent._has_claude is False
            assert agent._has_openai is False
            assert agent._claude_client is None
            assert agent._openai_client is None


class TestBuildContext:
    """Tests for _build_context method."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_build_context_parallel_fetch(self, agent):
        """Test that context is fetched in parallel."""
        # Mock the memory methods
        agent._memory.get_recent_context = AsyncMock(
            return_value=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
        )
        agent._memory.search_memories = AsyncMock(
            return_value=[
                {"content": "User prefers dark mode", "score": 0.9},
            ]
        )

        recent, memories = await agent._build_context(
            user_id=123,
            channel_id=456,
            message="What's my preference?",
        )

        assert len(recent) == 2
        assert len(memories) == 1
        agent._memory.get_recent_context.assert_called_once()
        agent._memory.search_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_context_with_custom_limits(self, agent):
        """Test context fetching with custom limits."""
        agent._memory.get_recent_context = AsyncMock(return_value=[])
        agent._memory.search_memories = AsyncMock(return_value=[])

        await agent._build_context(
            user_id=123,
            channel_id=456,
            message="test",
            memory_limit=10,
            history_limit=50,
        )

        agent._memory.get_recent_context.assert_called_once_with(
            user_id=123,
            channel_id=456,
            limit=50,
        )
        agent._memory.search_memories.assert_called_once_with(
            query="test",
            limit=10,
        )


class TestClaudeResponseGeneration:
    """Tests for Claude response generation."""

    @pytest.fixture
    def agent_with_claude(
        self, mock_qdrant_client, mock_embeddings_client, mock_claude_client, monkeypatch
    ):
        """Create agent with Claude client."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
            patch(
                "secureclaw.agent.core.anthropic.AsyncAnthropic", return_value=mock_claude_client
            ),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)
            agent._claude_client = mock_claude_client
            return agent

    @pytest.mark.asyncio
    async def test_generate_claude_response_success(self, agent_with_claude, mock_claude_client):
        """Test successful Claude response generation."""
        recent_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        relevant_memories = [
            {"content": "User prefers concise responses", "score": 0.85},
        ]

        response = await agent_with_claude._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="How are you?",
            recent_messages=recent_messages,
            relevant_memories=relevant_memories,
        )

        assert response == "Test response from Claude"
        mock_claude_client.messages.create.assert_called_once()

        # Verify the call arguments
        call_args = mock_claude_client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-3-5-sonnet-20241022"
        assert call_args.kwargs["max_tokens"] == 2048
        assert "SecureClaw" in call_args.kwargs["system"]
        assert "Relevant Memories" in call_args.kwargs["system"]

    @pytest.mark.asyncio
    async def test_generate_claude_response_without_high_score_memories(
        self, agent_with_claude, mock_claude_client
    ):
        """Test Claude response when memories have low scores."""
        recent_messages = []
        relevant_memories = [
            {"content": "Low score memory", "score": 0.5},  # Below 0.7 threshold
        ]

        response = await agent_with_claude._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=recent_messages,
            relevant_memories=relevant_memories,
        )

        assert response == "Test response from Claude"

        # System prompt should not include memories
        call_args = mock_claude_client.messages.create.call_args
        assert "Relevant Memories" not in call_args.kwargs["system"]

    @pytest.mark.asyncio
    async def test_generate_claude_response_connection_error(
        self, agent_with_claude, mock_claude_client
    ):
        """Test Claude response falls back on connection error."""
        mock_claude_client.messages.create = AsyncMock(
            side_effect=APIConnectionError("Connection failed")
        )

        # Mock the router's simple response
        agent_with_claude._router.generate_simple_response = AsyncMock(
            return_value="Fallback response"
        )

        response = await agent_with_claude._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"
        agent_with_claude._router.generate_simple_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_claude_response_without_client(self, agent_with_claude):
        """Test Claude response when client is None."""
        agent_with_claude._claude_client = None
        agent_with_claude._router.generate_simple_response = AsyncMock(
            return_value="Simple response"
        )

        response = await agent_with_claude._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Simple response"


class TestOpenAIResponseGeneration:
    """Tests for OpenAI response generation."""

    @pytest.fixture
    def agent_with_openai(
        self, mock_qdrant_client, mock_embeddings_client, mock_openai_client, monkeypatch
    ):
        """Create agent with OpenAI client."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
            patch("secureclaw.agent.core.openai.AsyncOpenAI", return_value=mock_openai_client),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            agent = Agent(memory)
            agent._openai_client = mock_openai_client
            return agent

    @pytest.mark.asyncio
    async def test_generate_openai_response_success(self, agent_with_openai, mock_openai_client):
        """Test successful OpenAI response generation."""
        recent_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        relevant_memories = [
            {"content": "User likes Python", "score": 0.9},
        ]

        response = await agent_with_openai._generate_openai_response(
            user_id=123,
            channel_id=456,
            message="Tell me about Python",
            recent_messages=recent_messages,
            relevant_memories=relevant_memories,
        )

        assert response == "Test response from OpenAI"
        mock_openai_client.chat.completions.create.assert_called_once()

        # Verify the call arguments
        call_args = mock_openai_client.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "gpt-4o-mini"
        assert call_args.kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_generate_openai_response_connection_error(
        self, agent_with_openai, mock_openai_client
    ):
        """Test OpenAI response falls back on connection error."""
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=OpenAIConnectionError("Connection failed")
        )

        agent_with_openai._router.generate_simple_response = AsyncMock(
            return_value="Fallback response"
        )

        response = await agent_with_openai._generate_openai_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"

    @pytest.mark.asyncio
    async def test_generate_openai_response_without_client(self, agent_with_openai):
        """Test OpenAI response when client is None."""
        agent_with_openai._openai_client = None
        agent_with_openai._router.generate_simple_response = AsyncMock(
            return_value="Simple response"
        )

        response = await agent_with_openai._generate_openai_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Simple response"


class TestFallbackToGeminiFlash:
    """Tests for fallback to Gemini Flash when Claude/OpenAI unavailable."""

    @pytest.fixture
    def agent_flash_only(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent with only Gemini Flash (no Claude/OpenAI)."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_fallback_to_flash_for_complex_task(self, agent_flash_only):
        """Test that complex tasks fall back to Flash when no other models available."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        routing = RoutingDecision(
            intent=MessageIntent.COMPLEX_TASK,
            confidence=0.9,
            reasoning="Code generation task",
            use_claude=True,
        )

        agent_flash_only._memory.get_recent_context = AsyncMock(return_value=[])
        agent_flash_only._memory.search_memories = AsyncMock(return_value=[])
        agent_flash_only._router.generate_simple_response = AsyncMock(
            return_value="Flash fallback response"
        )

        response = await agent_flash_only._handle_complex_task(
            user_id=123,
            channel_id=456,
            message="Write a Python function",
            routing=routing,
        )

        assert response == "Flash fallback response"
        agent_flash_only._router.generate_simple_response.assert_called_once()


class TestMemoryHandlers:
    """Tests for memory storage and retrieval handlers."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_handle_memory_store(self, agent):
        """Test memory storage handler."""
        agent._router.generate_simple_response = AsyncMock(return_value="Dark mode preference")
        agent._memory.store_memory = AsyncMock()

        response = await agent._handle_memory_store("Remember that I prefer dark mode")

        assert "I'll remember" in response
        assert "Dark mode preference" in response
        agent._memory.store_memory.assert_called_once_with(
            content="Dark mode preference",
            memory_type="user_request",
        )

    @pytest.mark.asyncio
    async def test_handle_memory_recall_with_results(self, agent):
        """Test memory recall handler with results found."""
        agent._memory.search_memories = AsyncMock(
            return_value=[
                {"content": "User prefers dark mode", "score": 0.9},
            ]
        )
        agent._memory.search_conversations = AsyncMock(
            return_value=[
                {"role": "user", "content": "I like dark mode", "score": 0.85},
            ]
        )
        agent._router.generate_simple_response = AsyncMock(
            return_value="You prefer dark mode based on previous conversations."
        )

        response = await agent._handle_memory_recall(
            user_id=123,
            query="What are my preferences?",
        )

        assert "dark mode" in response.lower()
        agent._memory.search_memories.assert_called_once()
        agent._memory.search_conversations.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_memory_recall_no_results(self, agent):
        """Test memory recall handler when no memories found."""
        agent._memory.search_memories = AsyncMock(return_value=[])
        agent._memory.search_conversations = AsyncMock(return_value=[])

        response = await agent._handle_memory_recall(
            user_id=123,
            query="What do you know about quantum physics?",
        )

        assert "don't have any memories" in response
        assert "Would you like to tell me" in response

    @pytest.mark.asyncio
    async def test_handle_memory_recall_low_score_results(self, agent):
        """Test memory recall handler with low-score results."""
        agent._memory.search_memories = AsyncMock(
            return_value=[
                {"content": "Vague memory", "score": 0.3},  # Below 0.5 threshold
            ]
        )
        agent._memory.search_conversations = AsyncMock(return_value=[])

        response = await agent._handle_memory_recall(
            user_id=123,
            query="Test query",
        )

        assert "vague matches" in response.lower() or "more specific" in response.lower()

    @pytest.mark.asyncio
    async def test_store_memory_from_request(self, agent):
        """Test explicit memory storage from request."""
        agent._memory.store_memory = AsyncMock()

        response = await agent.store_memory_from_request(
            content="User's birthday is March 15",
            memory_type="fact",
        )

        assert "stored that in my memory" in response
        assert "March 15" in response
        agent._memory.store_memory.assert_called_once_with(
            content="User's birthday is March 15",
            memory_type="fact",
        )


class TestSystemCommandHandler:
    """Tests for system command handler."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_handle_help_command(self, agent):
        """Test help command response."""
        response = await agent._handle_system_command("help")

        assert "SecureClaw" in response
        assert "Chat & Questions" in response
        assert "Memory" in response
        assert "Commands" in response

    @pytest.mark.asyncio
    async def test_handle_what_can_you_do(self, agent):
        """Test 'what can you do' response."""
        response = await agent._handle_system_command("What can you do?")

        assert "SecureClaw" in response
        assert "Commands" in response

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self, agent):
        """Test unknown command response."""
        response = await agent._handle_system_command("unknown command xyz")

        assert "not sure" in response.lower()
        assert "help" in response.lower()


class TestGenerateResponse:
    """Tests for main generate_response method."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_generate_response_simple_query(self, agent):
        """Test generate_response for simple query."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        # Mock routing
        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.95,
                reasoning="Simple greeting",
                use_claude=False,
            )
        )
        agent._router.generate_simple_response = AsyncMock(return_value="Hello!")
        agent._memory.store_message = AsyncMock()

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Hi there",
        )

        assert response == "Hello!"
        # Should store both user message and response
        assert agent._memory.store_message.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_response_memory_store(self, agent):
        """Test generate_response for memory store intent."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.MEMORY_STORE,
                confidence=0.9,
                reasoning="User wants to store a preference",
                use_claude=False,
            )
        )
        agent._router.generate_simple_response = AsyncMock(return_value="Dark mode")
        agent._memory.store_memory = AsyncMock()
        agent._memory.store_message = AsyncMock()

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Remember I like dark mode",
        )

        assert "I'll remember" in response
        agent._memory.store_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_response_memory_recall(self, agent):
        """Test generate_response for memory recall intent."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.MEMORY_RECALL,
                confidence=0.88,
                reasoning="User asking about past",
                use_claude=False,
            )
        )
        agent._memory.search_memories = AsyncMock(
            return_value=[
                {"content": "User prefers Python", "score": 0.9},
            ]
        )
        agent._memory.search_conversations = AsyncMock(return_value=[])
        agent._router.generate_simple_response = AsyncMock(
            return_value="You prefer Python for programming."
        )
        agent._memory.store_message = AsyncMock()

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="What do you know about my programming preferences?",
        )

        assert "Python" in response

    @pytest.mark.asyncio
    async def test_generate_response_system_command(self, agent):
        """Test generate_response for system command."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.SYSTEM_COMMAND,
                confidence=0.98,
                reasoning="Help request",
                use_claude=False,
            )
        )
        agent._memory.store_message = AsyncMock()

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="help",
        )

        assert "SecureClaw" in response
        assert "Commands" in response

    @pytest.mark.asyncio
    async def test_generate_response_complex_task_with_claude(self, agent, mock_claude_client):
        """Test generate_response for complex task routed to Claude."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.COMPLEX_TASK,
                confidence=0.92,
                reasoning="Code generation task",
                use_claude=True,
            )
        )
        agent._claude_client = mock_claude_client
        agent._memory.get_recent_context = AsyncMock(return_value=[])
        agent._memory.search_memories = AsyncMock(return_value=[])
        agent._memory.store_message = AsyncMock()

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Write a Python function to sort a list",
        )

        assert response == "Test response from Claude"
        mock_claude_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_response_stores_metadata(self, agent):
        """Test that generate_response stores intent metadata."""
        from secureclaw.agent.router import MessageIntent, RoutingDecision

        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.9,
                reasoning="Greeting",
                use_claude=False,
            )
        )
        agent._router.generate_simple_response = AsyncMock(return_value="Hi!")
        agent._memory.store_message = AsyncMock()

        await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Hello",
        )

        # Check that first call (user message) includes metadata
        first_call = agent._memory.store_message.call_args_list[0]
        assert first_call.kwargs["metadata"]["intent"] == "simple_query"


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
            patch("secureclaw.agent.router.genai.Client", return_value=Mock()),
        ):
            from secureclaw.agent.core import Agent
            from secureclaw.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_claude_api_error_fallback(self, agent, mock_claude_client):
        """Test that Claude API errors fall back to Flash."""
        import anthropic

        agent._claude_client = mock_claude_client
        mock_claude_client.messages.create = AsyncMock(side_effect=anthropic.APIError("API Error"))
        agent._router.generate_simple_response = AsyncMock(return_value="Fallback response")

        response = await agent._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"

    @pytest.mark.asyncio
    async def test_openai_timeout_error_fallback(self, agent, mock_openai_client):
        """Test that OpenAI timeout errors fall back to Flash."""
        agent._openai_client = mock_openai_client
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=OpenAITimeoutError("Timeout")
        )
        agent._router.generate_simple_response = AsyncMock(return_value="Fallback response")

        response = await agent._generate_openai_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"

    @pytest.mark.asyncio
    async def test_unexpected_error_in_claude(self, agent, mock_claude_client):
        """Test that unexpected errors in Claude are handled."""
        agent._claude_client = mock_claude_client
        mock_claude_client.messages.create = AsyncMock(side_effect=Exception("Unexpected error"))
        agent._router.generate_simple_response = AsyncMock(return_value="Fallback response")

        response = await agent._generate_claude_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"

    @pytest.mark.asyncio
    async def test_unexpected_error_in_openai(self, agent, mock_openai_client):
        """Test that unexpected errors in OpenAI are handled."""
        agent._openai_client = mock_openai_client
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Unexpected error")
        )
        agent._router.generate_simple_response = AsyncMock(return_value="Fallback response")

        response = await agent._generate_openai_response(
            user_id=123,
            channel_id=456,
            message="Test",
            recent_messages=[],
            relevant_memories=[],
        )

        assert response == "Fallback response"
