"""Unit tests for InferenceBroker and provider capability matrix."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.agent.inference import (
    COST_PER_MILLION_TOKENS,
    CostTracker,
    InferenceBroker,
    InferenceResult,
    ProviderHealth,
)
from zetherion_ai.agent.providers import (
    CAPABILITY_MATRIX,
    OLLAMA_TIER_CAPABILITIES,
    OllamaTier,
    Provider,
    ProviderConfig,
    TaskType,
    can_ollama_handle,
    get_ollama_tier,
    get_provider_for_task,
)


class TestTaskType:
    """Tests for TaskType enum."""

    def test_all_task_types_exist(self):
        """Verify all expected task types exist."""
        expected_types = [
            "CODE_GENERATION",
            "CODE_REVIEW",
            "CODE_DEBUGGING",
            "COMPLEX_REASONING",
            "MATH_ANALYSIS",
            "LONG_DOCUMENT",
            "SUMMARIZATION",
            "CREATIVE_WRITING",
            "SIMPLE_QA",
            "CLASSIFICATION",
            "DATA_EXTRACTION",
            "CONVERSATION",
            "PROFILE_EXTRACTION",
            "TASK_PARSING",
            "HEARTBEAT_DECISION",
        ]
        for type_name in expected_types:
            assert hasattr(TaskType, type_name)

    def test_task_type_values_are_lowercase(self):
        """Verify task type values are lowercase strings."""
        for task_type in TaskType:
            assert task_type.value == task_type.value.lower()
            assert isinstance(task_type.value, str)


class TestProvider:
    """Tests for Provider enum."""

    def test_all_providers_exist(self):
        """Verify all expected providers exist."""
        expected = ["CLAUDE", "OPENAI", "GEMINI", "OLLAMA"]
        for provider_name in expected:
            assert hasattr(Provider, provider_name)

    def test_provider_values(self):
        """Verify provider values are lowercase strings."""
        assert Provider.CLAUDE.value == "claude"
        assert Provider.OPENAI.value == "openai"
        assert Provider.GEMINI.value == "gemini"
        assert Provider.OLLAMA.value == "ollama"


class TestOllamaTier:
    """Tests for OllamaTier enum."""

    def test_ollama_tiers_exist(self):
        """Verify all expected tiers exist."""
        assert OllamaTier.SMALL.value == "small"
        assert OllamaTier.MEDIUM.value == "medium"
        assert OllamaTier.LARGE.value == "large"


class TestCapabilityMatrix:
    """Tests for the provider capability matrix."""

    def test_all_task_types_have_config(self):
        """Verify every task type has a provider config."""
        for task_type in TaskType:
            assert task_type in CAPABILITY_MATRIX, f"Missing config for {task_type}"

    def test_code_tasks_use_claude(self):
        """Code generation tasks should use Claude as primary."""
        code_types = [
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.CODE_DEBUGGING,
        ]
        for task_type in code_types:
            config = CAPABILITY_MATRIX[task_type]
            assert config.provider == Provider.CLAUDE

    def test_reasoning_tasks_use_openai(self):
        """Complex reasoning tasks should use OpenAI as primary."""
        reasoning_types = [TaskType.COMPLEX_REASONING, TaskType.MATH_ANALYSIS]
        for task_type in reasoning_types:
            config = CAPABILITY_MATRIX[task_type]
            assert config.provider == Provider.OPENAI

    def test_long_document_uses_gemini(self):
        """Long document tasks should use Gemini (1M context)."""
        config = CAPABILITY_MATRIX[TaskType.LONG_DOCUMENT]
        assert config.provider == Provider.GEMINI

    def test_lightweight_tasks_use_ollama(self):
        """Lightweight tasks should prefer Ollama (free, local)."""
        lightweight_types = [
            TaskType.SIMPLE_QA,
            TaskType.CLASSIFICATION,
            TaskType.DATA_EXTRACTION,
            TaskType.PROFILE_EXTRACTION,
            TaskType.TASK_PARSING,
            TaskType.HEARTBEAT_DECISION,
        ]
        for task_type in lightweight_types:
            config = CAPABILITY_MATRIX[task_type]
            assert config.provider == Provider.OLLAMA

    def test_all_configs_have_fallbacks(self):
        """Verify every config has at least one fallback."""
        for task_type, config in CAPABILITY_MATRIX.items():
            assert len(config.fallbacks) > 0, f"No fallbacks for {task_type}"

    def test_all_configs_have_rationale(self):
        """Verify every config has a non-empty rationale."""
        for task_type, config in CAPABILITY_MATRIX.items():
            assert len(config.rationale) > 0, f"No rationale for {task_type}"


class TestOllamaModelTiers:
    """Tests for Ollama model tier classification."""

    def test_small_models(self):
        """Verify small models are classified correctly."""
        small_models = ["llama3.1:8b", "phi-3", "mistral:7b", "qwen2.5:7b"]
        for model in small_models:
            assert get_ollama_tier(model) == OllamaTier.SMALL

    def test_medium_models(self):
        """Verify medium models are classified correctly."""
        medium_models = ["llama3.1:70b", "qwen2.5:32b", "mixtral:8x7b"]
        for model in medium_models:
            assert get_ollama_tier(model) == OllamaTier.MEDIUM

    def test_large_models(self):
        """Verify large models are classified correctly."""
        large_models = ["llama3.1:405b", "deepseek-r1:70b", "deepseek-r1"]
        for model in large_models:
            assert get_ollama_tier(model) == OllamaTier.LARGE

    def test_unknown_model_defaults_to_small(self):
        """Unknown models should default to small tier (conservative)."""
        assert get_ollama_tier("unknown-model:latest") == OllamaTier.SMALL
        assert get_ollama_tier("custom-model") == OllamaTier.SMALL

    def test_prefix_matching(self):
        """Verify prefix matching works for version variants."""
        # llama3.1:8b-instruct should match llama3.1 prefix
        assert get_ollama_tier("llama3.1:8b-instruct") == OllamaTier.SMALL


class TestOllamaTierCapabilities:
    """Tests for Ollama tier capability mapping."""

    def test_small_tier_capabilities(self):
        """Small tier should handle only lightweight tasks."""
        small_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.SMALL]
        assert TaskType.SIMPLE_QA in small_caps
        assert TaskType.CLASSIFICATION in small_caps
        assert TaskType.DATA_EXTRACTION in small_caps
        # Should NOT handle complex tasks
        assert TaskType.CODE_GENERATION not in small_caps
        assert TaskType.COMPLEX_REASONING not in small_caps

    def test_medium_tier_extends_small(self):
        """Medium tier should extend small tier capabilities."""
        small_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.SMALL]
        medium_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.MEDIUM]
        # Medium includes all small capabilities
        for task in small_caps:
            assert task in medium_caps
        # Plus additional tasks
        assert TaskType.SUMMARIZATION in medium_caps
        assert TaskType.CONVERSATION in medium_caps

    def test_large_tier_handles_most_tasks(self):
        """Large tier should handle most task types."""
        large_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.LARGE]
        assert TaskType.CODE_GENERATION in large_caps
        assert TaskType.CODE_DEBUGGING in large_caps
        assert TaskType.COMPLEX_REASONING in large_caps
        assert TaskType.CREATIVE_WRITING in large_caps


class TestCanOllamaHandle:
    """Tests for can_ollama_handle function."""

    def test_small_model_simple_qa(self):
        """Small Ollama model can handle simple Q&A."""
        assert can_ollama_handle(TaskType.SIMPLE_QA, "llama3.1:8b") is True

    def test_small_model_cannot_handle_code(self):
        """Small Ollama model cannot handle code generation."""
        assert can_ollama_handle(TaskType.CODE_GENERATION, "llama3.1:8b") is False

    def test_large_model_handles_code(self):
        """Large Ollama model can handle code generation."""
        assert can_ollama_handle(TaskType.CODE_GENERATION, "llama3.1:405b") is True


class TestGetProviderForTask:
    """Tests for get_provider_for_task function."""

    def test_returns_primary_when_available(self):
        """Returns primary provider when available."""
        provider = get_provider_for_task(
            task_type=TaskType.CODE_GENERATION,
            available_providers={Provider.CLAUDE, Provider.OPENAI},
        )
        assert provider == Provider.CLAUDE

    def test_returns_fallback_when_primary_unavailable(self):
        """Returns first available fallback when primary unavailable."""
        provider = get_provider_for_task(
            task_type=TaskType.CODE_GENERATION,
            available_providers={Provider.OPENAI, Provider.GEMINI},  # No Claude
        )
        assert provider == Provider.OPENAI  # First fallback

    def test_ollama_fallback_when_tier_insufficient(self):
        """Falls back from Ollama when tier can't handle task."""
        # Simple Q&A prefers Ollama, but if we specify an upgrade is needed...
        # Actually, let's test that small model Ollama gets fallback for code
        provider = get_provider_for_task(
            task_type=TaskType.CODE_GENERATION,
            ollama_model="llama3.1:8b",  # Small tier
            available_providers={Provider.OLLAMA, Provider.CLAUDE},
        )
        # Should return Claude since small Ollama can't handle code
        assert provider == Provider.CLAUDE

    def test_force_ollama_override(self):
        """force_ollama parameter forces Ollama selection."""
        provider = get_provider_for_task(
            task_type=TaskType.CODE_GENERATION,
            available_providers={Provider.CLAUDE, Provider.OLLAMA},
            force_ollama={TaskType.CODE_GENERATION},
        )
        assert provider == Provider.OLLAMA

    def test_force_cloud_removes_ollama(self):
        """force_cloud parameter removes Ollama from consideration."""
        provider = get_provider_for_task(
            task_type=TaskType.SIMPLE_QA,
            available_providers={Provider.OLLAMA, Provider.GEMINI},
            force_cloud={TaskType.SIMPLE_QA},
        )
        # Should not use Ollama even though it's preferred
        assert provider == Provider.GEMINI

    def test_returns_any_available_when_no_optimal(self):
        """Returns any available provider when no optimal available."""
        provider = get_provider_for_task(
            task_type=TaskType.CODE_GENERATION,
            available_providers={Provider.GEMINI},  # Only Gemini, not in fallbacks for code
        )
        assert provider == Provider.GEMINI

    def test_raises_when_no_providers(self):
        """Raises RuntimeError when no providers available."""
        with pytest.raises(RuntimeError, match="No providers available"):
            get_provider_for_task(
                task_type=TaskType.CODE_GENERATION,
                available_providers=set(),
            )


class TestInferenceResult:
    """Tests for InferenceResult dataclass."""

    def test_create_result(self):
        """Verify InferenceResult creation."""
        result = InferenceResult(
            content="Hello world",
            provider=Provider.CLAUDE,
            task_type=TaskType.CODE_GENERATION,
            model="claude-sonnet-4-20250514",
            input_tokens=100,
            output_tokens=50,
            latency_ms=1500.5,
            estimated_cost_usd=0.00225,
        )
        assert result.content == "Hello world"
        assert result.provider == Provider.CLAUDE
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_default_values(self):
        """Verify default values for optional fields."""
        result = InferenceResult(
            content="test",
            provider=Provider.OLLAMA,
            task_type=TaskType.SIMPLE_QA,
            model="llama3.1:8b",
        )
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.latency_ms == 0.0
        assert result.estimated_cost_usd == 0.0


class TestProviderHealth:
    """Tests for ProviderHealth dataclass."""

    def test_create_health_status(self):
        """Verify ProviderHealth creation."""
        health = ProviderHealth(
            available=True,
            last_check=1234567890.0,
            error_message="",
        )
        assert health.available is True
        assert health.last_check == 1234567890.0
        assert health.error_message == ""


class TestCostTracker:
    """Tests for CostTracker dataclass."""

    def test_default_values(self):
        """Verify default values."""
        tracker = CostTracker()
        assert tracker.total_calls == 0
        assert tracker.total_input_tokens == 0
        assert tracker.total_output_tokens == 0
        assert tracker.total_cost_usd == 0.0
        assert tracker.by_task_type == {}


class TestCostPerMillionTokens:
    """Tests for cost constants."""

    def test_all_providers_have_costs(self):
        """Verify all providers have cost data."""
        for provider in Provider:
            assert provider in COST_PER_MILLION_TOKENS

    def test_ollama_is_free(self):
        """Ollama should have zero cost."""
        input_rate, output_rate = COST_PER_MILLION_TOKENS[Provider.OLLAMA]
        assert input_rate == 0.0
        assert output_rate == 0.0

    def test_claude_is_most_expensive(self):
        """Claude should be the most expensive provider."""
        claude_input, claude_output = COST_PER_MILLION_TOKENS[Provider.CLAUDE]
        for provider in [Provider.OPENAI, Provider.GEMINI, Provider.OLLAMA]:
            p_input, p_output = COST_PER_MILLION_TOKENS[provider]
            assert claude_output >= p_output


class TestInferenceBrokerInit:
    """Tests for InferenceBroker initialization."""

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_init_with_all_providers(self, mock_get_settings):
        """InferenceBroker initializes with all providers when keys available."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = MagicMock()
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-ant-test"
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "sk-openai-test"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "AIza-test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.claude_model = "claude-sonnet-4-20250514"
        mock_settings.openai_model = "gpt-4o"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic"),
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI"),
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            broker = InferenceBroker()

        assert Provider.CLAUDE in broker.available_providers
        assert Provider.OPENAI in broker.available_providers
        assert Provider.GEMINI in broker.available_providers
        assert Provider.OLLAMA in broker.available_providers

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_init_without_anthropic_key(self, mock_get_settings):
        """InferenceBroker works without Anthropic key."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None  # No Claude
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "AIza-test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.openai_model = "gpt-4o"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI"),
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            broker = InferenceBroker()

        assert Provider.CLAUDE not in broker.available_providers
        assert Provider.OPENAI in broker.available_providers

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_ollama_tier_detection(self, mock_get_settings):
        """InferenceBroker correctly detects Ollama tier based on generation model."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "AIza-test"
        mock_settings.ollama_generation_model = "llama3.1:70b"  # Medium tier (generation model)
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client"):
            broker = InferenceBroker()

        assert broker._ollama_tier == OllamaTier.MEDIUM


class TestInferenceBrokerCostTracking:
    """Tests for InferenceBroker cost tracking."""

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_estimate_cost_claude(self, mock_get_settings):
        """Verify cost estimation for Claude uses pricing module."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client"):
            broker = InferenceBroker()

        # Claude Sonnet: $3/M input, $15/M output
        cost, estimated = broker._estimate_cost(
            provider=Provider.CLAUDE,
            model="claude-sonnet-4-5",
            input_tokens=1000,
            output_tokens=500,
        )
        # Expected: (1000 * 3 + 500 * 15) / 1_000_000 = 0.0105
        assert abs(cost - 0.0105) < 0.0001
        assert estimated is False  # Known model

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_estimate_cost_ollama(self, mock_get_settings):
        """Verify Ollama is free."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client"):
            broker = InferenceBroker()

        cost, estimated = broker._estimate_cost(
            provider=Provider.OLLAMA,
            model="llama3.1:8b",
            input_tokens=10000,
            output_tokens=5000,
        )
        assert cost == 0.0
        assert estimated is False  # Ollama is always known (free)

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_track_cost_updates_tracker(self, mock_get_settings):
        """Verify cost tracking updates the tracker correctly."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client"):
            broker = InferenceBroker()

        result = InferenceResult(
            content="test",
            provider=Provider.CLAUDE,
            task_type=TaskType.CODE_GENERATION,
            model="claude-sonnet",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.01,
        )

        broker._track_cost(result)

        tracker = broker._cost_tracker[Provider.CLAUDE]
        assert tracker.total_calls == 1
        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.total_cost_usd == 0.01
        assert tracker.by_task_type["code_generation"] == 0.01

    @patch("zetherion_ai.agent.inference.get_settings")
    def test_get_cost_summary(self, mock_get_settings):
        """Verify cost summary generation."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client"):
            broker = InferenceBroker()

        # Track some costs
        result1 = InferenceResult(
            content="test",
            provider=Provider.CLAUDE,
            task_type=TaskType.CODE_GENERATION,
            model="claude",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.01,
        )
        result2 = InferenceResult(
            content="test",
            provider=Provider.OLLAMA,
            task_type=TaskType.SIMPLE_QA,
            model="llama",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.0,
        )

        broker._track_cost(result1)
        broker._track_cost(result2)

        summary = broker.get_cost_summary()

        assert "claude" in summary
        assert summary["claude"]["calls"] == 1
        assert summary["total_cost_usd"] == 0.01

        assert "ollama" in summary
        assert summary["ollama"]["calls"] == 1
        assert summary["ollama"]["cost_usd"] == 0.0


class TestInferenceBrokerInfer:
    """Tests for InferenceBroker.infer method."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_infer_selects_correct_provider(self, mock_get_settings):
        """Verify infer selects the correct provider based on task type."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = MagicMock()
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.claude_model = "claude-sonnet"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic") as mock_claude,
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Generated code")]
            mock_response.usage = MagicMock(input_tokens=50, output_tokens=100)
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_claude.return_value = mock_client

            broker = InferenceBroker()
            result = await broker.infer(
                prompt="Write a Python function",
                task_type=TaskType.CODE_GENERATION,
            )

        assert result.provider == Provider.CLAUDE
        assert result.content == "Generated code"
        assert result.task_type == TaskType.CODE_GENERATION

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_infer_falls_back_on_failure(self, mock_get_settings):
        """Verify infer falls back to another provider on failure."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = MagicMock()
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "sk-openai"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.claude_model = "claude-sonnet"
        mock_settings.openai_model = "gpt-4o"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic") as mock_claude,
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI") as mock_openai,
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            # Claude fails
            mock_claude_client = AsyncMock()
            mock_claude_client.messages.create = AsyncMock(side_effect=Exception("Claude error"))
            mock_claude.return_value = mock_claude_client

            # OpenAI succeeds
            mock_openai_client = AsyncMock()
            mock_openai_response = MagicMock()
            mock_openai_response.choices = [MagicMock(message=MagicMock(content="OpenAI response"))]
            mock_openai_response.usage = MagicMock(prompt_tokens=50, completion_tokens=100)
            mock_openai_client.chat.completions.create = AsyncMock(
                return_value=mock_openai_response
            )
            mock_openai.return_value = mock_openai_client

            broker = InferenceBroker()
            result = await broker.infer(
                prompt="Write a Python function",
                task_type=TaskType.CODE_GENERATION,
            )

        # Should have fallen back to OpenAI
        assert result.provider == Provider.OPENAI
        assert result.content == "OpenAI response"

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_infer_tracks_latency(self, mock_get_settings):
        """Verify infer tracks latency."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "Gemini response"
            mock_client.models.generate_content.return_value = mock_response
            mock_gemini.return_value = mock_client

            broker = InferenceBroker()
            # Remove Ollama from available providers to force Gemini
            broker._available_providers.discard(Provider.OLLAMA)

            result = await broker.infer(
                prompt="Simple question",
                task_type=TaskType.SUMMARIZATION,  # Would normally use Gemini
            )

        assert result.latency_ms > 0


class TestInferenceBrokerHealthCheck:
    """Tests for InferenceBroker health checks."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_ollama_health_check(self, mock_get_settings):
        """Verify Ollama health check uses the shared httpx client."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        mock_httpx_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_instance.get = AsyncMock(return_value=mock_response)

        with (
            patch("zetherion_ai.agent.inference.genai.Client"),
            patch(
                "zetherion_ai.agent.inference.httpx.AsyncClient",
                return_value=mock_httpx_instance,
            ),
        ):
            broker = InferenceBroker()
            is_healthy = await broker.health_check(Provider.OLLAMA)

        assert is_healthy is True
        mock_httpx_instance.get.assert_called_once()

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_health_check_returns_false_on_error(self, mock_get_settings):
        """Verify health check returns False on error."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.genai.Client"),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient") as mock_httpx,
        ):
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Connection error"))
            mock_httpx.return_value.__aenter__.return_value = mock_client

            broker = InferenceBroker()
            is_healthy = await broker.health_check(Provider.OLLAMA)

        assert is_healthy is False


class TestProviderConfig:
    """Tests for ProviderConfig dataclass."""

    def test_create_config(self):
        """Verify ProviderConfig creation."""
        config = ProviderConfig(
            provider=Provider.CLAUDE,
            rationale="Best for code",
            fallbacks=[Provider.OPENAI, Provider.OLLAMA],
        )
        assert config.provider == Provider.CLAUDE
        assert config.rationale == "Best for code"
        assert len(config.fallbacks) == 2


class TestInferenceBrokerClose:
    """Tests for InferenceBroker.close() method."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_close_calls_httpx_aclose(self, mock_get_settings):
        """Test that close() calls self._httpx_client.aclose()."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.genai.Client"),
            patch("zetherion_ai.agent.inference.httpx.AsyncClient") as mock_httpx_cls,
        ):
            mock_httpx_instance = AsyncMock()
            mock_httpx_cls.return_value = mock_httpx_instance

            broker = InferenceBroker()
            await broker.close()

            mock_httpx_instance.aclose.assert_called_once()


class TestHealthCheckNoInference:
    """Tests verifying health checks don't call inference endpoints."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_openai_health_check_uses_models_list(self, mock_get_settings):
        """Verify OpenAI health check uses models.list, not chat completions."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.openai_model = "gpt-4o"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI") as mock_openai_cls,
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            mock_openai_client = AsyncMock()
            mock_openai_client.models.list = AsyncMock(return_value=[])
            mock_openai_cls.return_value = mock_openai_client

            broker = InferenceBroker()
            result = await broker.health_check(Provider.OPENAI)

        assert result is True
        mock_openai_client.models.list.assert_called_once()
        # Should NOT call chat.completions.create
        mock_openai_client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_gemini_health_check_uses_models_list(self, mock_get_settings):
        """Verify Gemini health check uses models.list, not generate_content."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini_cls:
            mock_gemini_client = MagicMock()
            mock_gemini_client.models.list.return_value = iter([MagicMock()])
            mock_gemini_cls.return_value = mock_gemini_client

            broker = InferenceBroker()
            result = await broker.health_check(Provider.GEMINI)

        assert result is True
        mock_gemini_client.models.list.assert_called()
        # Should NOT call generate_content
        mock_gemini_client.models.generate_content.assert_not_called()

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_claude_health_check_just_checks_client_init(self, mock_get_settings):
        """Verify Claude health check just checks client initialization (no API call)."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = MagicMock()
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-ant-test"
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.claude_model = "claude-sonnet"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic") as mock_claude_cls,
            patch("zetherion_ai.agent.inference.genai.Client"),
        ):
            mock_claude_client = AsyncMock()
            mock_claude_cls.return_value = mock_claude_client

            broker = InferenceBroker()
            result = await broker.health_check(Provider.CLAUDE)

        assert result is True
        # Claude health check should NOT call messages.create
        mock_claude_client.messages.create.assert_not_called()


class TestFallbackDiscardNoRaise:
    """Tests for _try_fallbacks discard behavior."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_discard_in_fallback_does_not_raise_on_missing_key(self, mock_get_settings):
        """Test that discard() in fallback chain doesn't raise on missing key.

        When all providers fail, _try_fallbacks calls remaining.discard(fallback)
        for each. This must not raise KeyError even if the provider was already
        removed from the set.
        """
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = MagicMock()
        mock_settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "sk-openai"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.claude_model = "claude-sonnet"
        mock_settings.openai_model = "gpt-4o"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with (
            patch("zetherion_ai.agent.inference.anthropic.AsyncAnthropic") as mock_claude_cls,
            patch("zetherion_ai.agent.inference.openai.AsyncOpenAI") as mock_openai_cls,
            patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini_cls,
        ):
            # All providers fail so discard is called on each
            mock_claude_client = AsyncMock()
            mock_claude_client.messages.create = AsyncMock(side_effect=Exception("Claude fail"))
            mock_claude_cls.return_value = mock_claude_client

            mock_openai_client = AsyncMock()
            mock_openai_client.chat.completions.create = AsyncMock(
                side_effect=Exception("OpenAI fail")
            )
            mock_openai_cls.return_value = mock_openai_client

            # Gemini also fails
            mock_gemini_client = MagicMock()
            mock_gemini_client.models.generate_content.side_effect = Exception("Gemini fail")
            mock_gemini_cls.return_value = mock_gemini_client

            broker = InferenceBroker()

            # Mock Ollama to also fail (uses httpx)
            broker._httpx_client.post = AsyncMock(side_effect=Exception("Ollama fail"))

            # All providers will fail - _try_fallbacks discards each failed provider
            # and should not raise KeyError, only RuntimeError at the end
            with pytest.raises(RuntimeError, match="All providers failed"):
                await broker._try_fallbacks(
                    task_type=TaskType.CODE_GENERATION,
                    prompt="test",
                    system_prompt=None,
                    messages=None,
                    max_tokens=100,
                    temperature=0.7,
                    failed_provider=Provider.CLAUDE,
                )


class TestGeminiUsageMetadata:
    """Tests for Gemini actual usage_metadata token counts."""

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_gemini_uses_usage_metadata_when_available(self, mock_get_settings):
        """Test that Gemini uses actual usage_metadata token counts when available."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini_cls:
            mock_gemini_client = MagicMock()

            # Create response with actual usage_metadata
            mock_response = MagicMock()
            mock_response.text = "Gemini response text"
            mock_usage = MagicMock()
            mock_usage.prompt_token_count = 150
            mock_usage.candidates_token_count = 75
            mock_response.usage_metadata = mock_usage
            mock_gemini_client.models.generate_content.return_value = mock_response
            mock_gemini_cls.return_value = mock_gemini_client

            broker = InferenceBroker()
            # Remove Ollama to force Gemini selection
            broker._available_providers.discard(Provider.OLLAMA)

            result = await broker._call_gemini(
                prompt="Test prompt",
                task_type=TaskType.SUMMARIZATION,
                system_prompt=None,
                messages=None,
                max_tokens=1024,
                temperature=0.7,
            )

        assert result.input_tokens == 150
        assert result.output_tokens == 75
        assert result.content == "Gemini response text"

    @pytest.mark.asyncio
    @patch("zetherion_ai.agent.inference.get_settings")
    async def test_gemini_falls_back_to_heuristic_without_usage_metadata(self, mock_get_settings):
        """Test that Gemini falls back to heuristic when usage_metadata is absent."""
        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test"
        mock_settings.ollama_router_model = "llama3.1:8b"
        mock_settings.ollama_url = "http://localhost:11434"
        mock_settings.router_model = "gemini-2.0-flash"
        mock_get_settings.return_value = mock_settings

        with patch("zetherion_ai.agent.inference.genai.Client") as mock_gemini_cls:
            mock_gemini_client = MagicMock()

            # Create response WITHOUT usage_metadata
            mock_response = MagicMock()
            mock_response.text = "word1 word2 word3"
            mock_response.usage_metadata = None
            mock_gemini_client.models.generate_content.return_value = mock_response
            mock_gemini_cls.return_value = mock_gemini_client

            broker = InferenceBroker()
            broker._available_providers.discard(Provider.OLLAMA)

            result = await broker._call_gemini(
                prompt="hello world",
                task_type=TaskType.SIMPLE_QA,
                system_prompt=None,
                messages=None,
                max_tokens=1024,
                temperature=0.7,
            )

        # Without usage_metadata, should use heuristic: len(words) * 2
        assert result.input_tokens == len(["hello", "world"]) * 2
        assert result.output_tokens == len(["word1", "word2", "word3"]) * 2
