"""Tests for Agent core functionality."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from zetherion_ai.agent.inference import InferenceResult
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.agent.router import MessageIntent, RoutingDecision


class TestBuildContext:
    """Tests for _build_context method."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_build_context_parallel_fetch(self, agent):
        """Test that context is fetched in parallel."""
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
            user_id=None,
        )


class TestMemoryHandlers:
    """Tests for memory storage and retrieval handlers."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_handle_memory_store(self, agent):
        """Test memory storage handler."""
        agent._router.generate_simple_response = AsyncMock(return_value="Dark mode preference")
        agent._memory.store_memory = AsyncMock()

        response = await agent._handle_memory_store("Remember that I prefer dark mode", user_id=123)

        assert "I'll remember" in response
        assert "Dark mode preference" in response
        agent._memory.store_memory.assert_called_once_with(
            content="Dark mode preference",
            memory_type="user_request",
            user_id=123,
        )

    @pytest.mark.asyncio
    async def test_handle_memory_store_without_user_id(self, agent):
        """Test memory storage handler without user_id."""
        agent._router.generate_simple_response = AsyncMock(return_value="Some fact")
        agent._memory.store_memory = AsyncMock()

        response = await agent._handle_memory_store("Remember some fact")

        assert "I'll remember" in response
        agent._memory.store_memory.assert_called_once_with(
            content="Some fact",
            memory_type="user_request",
            user_id=None,
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
            user_id=None,
        )

    @pytest.mark.asyncio
    async def test_store_memory_from_request_passes_user_id(self, agent):
        """Test that store_memory_from_request passes user_id to memory."""
        agent._memory.store_memory = AsyncMock()

        response = await agent.store_memory_from_request(
            content="Favorite language is Rust",
            memory_type="preference",
            user_id=42,
        )

        assert "stored that in my memory" in response
        agent._memory.store_memory.assert_called_once_with(
            content="Favorite language is Rust",
            memory_type="preference",
            user_id=42,
        )


class TestSystemCommandHandler:
    """Tests for system command handler."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_handle_help_command(self, agent):
        """Test help command response."""
        response = await agent._handle_system_command("help")

        assert "Zetherion" in response
        assert "Chat & Questions" in response
        assert "Memory" in response
        assert "Commands" in response

    @pytest.mark.asyncio
    async def test_handle_what_can_you_do(self, agent):
        """Test 'what can you do' response."""
        response = await agent._handle_system_command("What can you do?")

        assert "Zetherion" in response
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

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_generate_response_simple_query(self, agent):
        """Test generate_response for simple query (no message storage)."""
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
        # SIMPLE_QUERY should NOT store messages
        agent._memory.store_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_response_memory_store(self, agent):
        """Test generate_response for memory store intent."""
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
        """Test generate_response for system command (no message storage)."""
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

        assert "Zetherion" in response
        assert "Commands" in response
        # SYSTEM_COMMAND should NOT store messages
        agent._memory.store_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_response_complex_task_delegates_to_broker(self, agent):
        """Test generate_response for complex task routes through InferenceBroker."""
        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.COMPLEX_TASK,
                confidence=0.92,
                reasoning="Code generation task",
                use_claude=True,
            )
        )
        agent._memory.get_recent_context = AsyncMock(return_value=[])
        agent._memory.search_memories = AsyncMock(return_value=[])
        agent._memory.store_message = AsyncMock()

        # Mock the inference broker
        mock_result = InferenceResult(
            content="Generated code response",
            provider=Provider.CLAUDE,
            task_type=TaskType.CODE_GENERATION,
            model="claude-sonnet",
            input_tokens=50,
            output_tokens=100,
        )
        agent._inference_broker.infer = AsyncMock(return_value=mock_result)

        response = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Write a Python function to sort a list",
        )

        assert response == "Generated code response"
        agent._inference_broker.infer.assert_called_once()


class TestHandleComplexTask:
    """Tests for _handle_complex_task method."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_handle_complex_task_delegates_to_inference_broker(self, agent):
        """Test that _handle_complex_task always delegates to InferenceBroker."""
        agent._memory.get_recent_context = AsyncMock(return_value=[])
        agent._memory.search_memories = AsyncMock(return_value=[])

        mock_result = InferenceResult(
            content="Broker response",
            provider=Provider.CLAUDE,
            task_type=TaskType.CODE_GENERATION,
            model="claude-sonnet",
            input_tokens=50,
            output_tokens=100,
        )
        agent._inference_broker.infer = AsyncMock(return_value=mock_result)

        routing = RoutingDecision(
            intent=MessageIntent.COMPLEX_TASK,
            confidence=0.9,
            reasoning="Code task",
            use_claude=True,
        )

        response = await agent._handle_complex_task(
            user_id=123,
            channel_id=456,
            message="Write a Python sort function",
            routing=routing,
        )

        assert response == "Broker response"
        agent._inference_broker.infer.assert_called_once()
        # Verify the broker was called with correct task type and prompt
        call_kwargs = agent._inference_broker.infer.call_args[1]
        assert call_kwargs["prompt"] == "Write a Python sort function"
        assert call_kwargs["task_type"] == TaskType.CODE_GENERATION

    @pytest.mark.asyncio
    async def test_handle_complex_task_includes_context_in_system_prompt(self, agent):
        """Test that context is included in the system prompt for complex tasks."""
        agent._memory.get_recent_context = AsyncMock(
            return_value=[
                {"role": "user", "content": "Previous message"},
                {"role": "assistant", "content": "Previous response"},
            ]
        )
        agent._memory.search_memories = AsyncMock(
            return_value=[
                {"content": "User is a Python expert", "score": 0.95},
            ]
        )

        mock_result = InferenceResult(
            content="Response with context",
            provider=Provider.CLAUDE,
            task_type=TaskType.CONVERSATION,
            model="claude-sonnet",
        )
        agent._inference_broker.infer = AsyncMock(return_value=mock_result)

        routing = RoutingDecision(
            intent=MessageIntent.COMPLEX_TASK,
            confidence=0.9,
            reasoning="General task",
            use_claude=True,
        )

        await agent._handle_complex_task(
            user_id=123,
            channel_id=456,
            message="Tell me about Python",
            routing=routing,
        )

        call_kwargs = agent._inference_broker.infer.call_args[1]
        assert "Relevant Memories" in call_kwargs["system_prompt"]
        assert "Python expert" in call_kwargs["system_prompt"]


class TestClassifyTaskType:
    """Tests for _classify_task_type method."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    def test_classify_code_generation(self, agent):
        """Test code generation classification."""
        result = agent._classify_task_type("Write a Python function to parse JSON")
        assert result == TaskType.CODE_GENERATION

    def test_classify_code_review(self, agent):
        """Test code review classification."""
        result = agent._classify_task_type("Review this Python code for bugs")
        assert result == TaskType.CODE_REVIEW

    def test_classify_code_debugging(self, agent):
        """Test code debugging classification."""
        result = agent._classify_task_type("Debug this Python error")
        assert result == TaskType.CODE_DEBUGGING

    def test_classify_math_analysis(self, agent):
        """Test math analysis classification."""
        result = agent._classify_task_type("Calculate the derivative of x^2")
        assert result == TaskType.MATH_ANALYSIS

    def test_classify_complex_reasoning(self, agent):
        """Test complex reasoning classification."""
        result = agent._classify_task_type("Explain in detail why the sky is blue")
        assert result == TaskType.COMPLEX_REASONING

    def test_classify_creative_writing(self, agent):
        """Test creative writing classification."""
        result = agent._classify_task_type("Write a short story about a wizard")
        assert result == TaskType.CREATIVE_WRITING

    def test_classify_summarization(self, agent):
        """Test summarization classification."""
        result = agent._classify_task_type("Summarize this article for me")
        assert result == TaskType.SUMMARIZATION

    def test_classify_conversation_default(self, agent):
        """Test default conversation classification."""
        result = agent._classify_task_type("What is the weather like today?")
        assert result == TaskType.CONVERSATION

    def test_uses_frozenset_keyword_constants(self):
        """Test that keyword constants are frozensets (not mutable sets or lists)."""
        from zetherion_ai.agent.core import (
            CODE_DEBUG_KEYWORDS,
            CODE_KEYWORDS,
            CODE_REVIEW_KEYWORDS,
            CREATIVE_KEYWORDS,
            MATH_KEYWORDS,
            MATH_SPECIFIC_KEYWORDS,
            SUMMARIZATION_KEYWORDS,
        )

        assert isinstance(CODE_KEYWORDS, frozenset)
        assert isinstance(CODE_REVIEW_KEYWORDS, frozenset)
        assert isinstance(CODE_DEBUG_KEYWORDS, frozenset)
        assert isinstance(MATH_KEYWORDS, frozenset)
        assert isinstance(MATH_SPECIFIC_KEYWORDS, frozenset)
        assert isinstance(CREATIVE_KEYWORDS, frozenset)
        assert isinstance(SUMMARIZATION_KEYWORDS, frozenset)


class TestMessageStorageSkipping:
    """Tests for message storage being skipped for lightweight intents."""

    @pytest.fixture
    def agent(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create agent for testing."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
            ),
            patch("zetherion_ai.agent.router.genai.Client", return_value=Mock()),
        ):
            from zetherion_ai.agent.core import Agent
            from zetherion_ai.memory.qdrant import QdrantMemory

            memory = QdrantMemory()
            return Agent(memory)

    @pytest.mark.asyncio
    async def test_simple_query_skips_message_storage(self, agent):
        """Test that message storage is skipped for SIMPLE_QUERY intent."""
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

        await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Hi there",
        )

        agent._memory.store_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_command_skips_message_storage(self, agent):
        """Test that message storage is skipped for SYSTEM_COMMAND intent."""
        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.SYSTEM_COMMAND,
                confidence=0.98,
                reasoning="Help request",
                use_claude=False,
            )
        )
        agent._memory.store_message = AsyncMock()

        await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="help",
        )

        agent._memory.store_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_memory_store_intent_does_store_messages(self, agent):
        """Test that MEMORY_STORE intent does store messages."""
        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.MEMORY_STORE,
                confidence=0.9,
                reasoning="User wants to remember",
                use_claude=False,
            )
        )
        agent._router.generate_simple_response = AsyncMock(return_value="A fact")
        agent._memory.store_memory = AsyncMock()
        agent._memory.store_message = AsyncMock()

        await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Remember that I like cats",
        )

        # MEMORY_STORE should store messages
        assert agent._memory.store_message.call_count == 2

    @pytest.mark.asyncio
    async def test_complex_task_does_store_messages(self, agent):
        """Test that COMPLEX_TASK intent does store messages."""
        agent._router.classify = AsyncMock(
            return_value=RoutingDecision(
                intent=MessageIntent.COMPLEX_TASK,
                confidence=0.92,
                reasoning="Complex question",
                use_claude=True,
            )
        )
        agent._memory.get_recent_context = AsyncMock(return_value=[])
        agent._memory.search_memories = AsyncMock(return_value=[])
        agent._memory.store_message = AsyncMock()

        mock_result = InferenceResult(
            content="Complex response",
            provider=Provider.CLAUDE,
            task_type=TaskType.CONVERSATION,
            model="claude-sonnet",
        )
        agent._inference_broker.infer = AsyncMock(return_value=mock_result)

        await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Explain quantum computing in detail",
        )

        # COMPLEX_TASK should store messages
        assert agent._memory.store_message.call_count == 2
