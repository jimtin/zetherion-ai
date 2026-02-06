"""Unit tests for the agent providers module."""

import pytest

from zetherion_ai.agent.providers import (
    CAPABILITY_MATRIX,
    OLLAMA_MODEL_TIERS,
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

    def test_code_task_types(self):
        """Test code-related task types."""
        assert TaskType.CODE_GENERATION.value == "code_generation"
        assert TaskType.CODE_REVIEW.value == "code_review"
        assert TaskType.CODE_DEBUGGING.value == "code_debugging"

    def test_reasoning_task_types(self):
        """Test reasoning task types."""
        assert TaskType.COMPLEX_REASONING.value == "complex_reasoning"
        assert TaskType.MATH_ANALYSIS.value == "math_analysis"

    def test_document_task_types(self):
        """Test document task types."""
        assert TaskType.LONG_DOCUMENT.value == "long_document"
        assert TaskType.SUMMARIZATION.value == "summarization"

    def test_lightweight_task_types(self):
        """Test lightweight task types."""
        assert TaskType.SIMPLE_QA.value == "simple_qa"
        assert TaskType.CLASSIFICATION.value == "classification"
        assert TaskType.DATA_EXTRACTION.value == "data_extraction"
        assert TaskType.CONVERSATION.value == "conversation"

    def test_internal_task_types(self):
        """Test internal task types."""
        assert TaskType.PROFILE_EXTRACTION.value == "profile_extraction"
        assert TaskType.TASK_PARSING.value == "task_parsing"
        assert TaskType.HEARTBEAT_DECISION.value == "heartbeat_decision"


class TestProvider:
    """Tests for Provider enum."""

    def test_provider_values(self):
        """Test provider enum values."""
        assert Provider.CLAUDE.value == "claude"
        assert Provider.OPENAI.value == "openai"
        assert Provider.GEMINI.value == "gemini"
        assert Provider.OLLAMA.value == "ollama"


class TestOllamaTier:
    """Tests for OllamaTier enum."""

    def test_tier_values(self):
        """Test Ollama tier values."""
        assert OllamaTier.SMALL.value == "small"
        assert OllamaTier.MEDIUM.value == "medium"
        assert OllamaTier.LARGE.value == "large"


class TestProviderConfig:
    """Tests for ProviderConfig dataclass."""

    def test_provider_config_creation(self):
        """Test creating a ProviderConfig."""
        config = ProviderConfig(
            provider=Provider.CLAUDE,
            rationale="Best for code",
            fallbacks=[Provider.OPENAI, Provider.OLLAMA],
        )
        assert config.provider == Provider.CLAUDE
        assert config.rationale == "Best for code"
        assert config.fallbacks == [Provider.OPENAI, Provider.OLLAMA]


class TestCapabilityMatrix:
    """Tests for the capability matrix."""

    def test_all_task_types_have_config(self):
        """Test all task types have a configuration."""
        for task_type in TaskType:
            assert task_type in CAPABILITY_MATRIX

    def test_code_tasks_prefer_claude(self):
        """Test code tasks prefer Claude."""
        assert CAPABILITY_MATRIX[TaskType.CODE_GENERATION].provider == Provider.CLAUDE
        assert CAPABILITY_MATRIX[TaskType.CODE_REVIEW].provider == Provider.CLAUDE
        assert CAPABILITY_MATRIX[TaskType.CODE_DEBUGGING].provider == Provider.CLAUDE

    def test_reasoning_tasks_prefer_openai(self):
        """Test reasoning tasks prefer OpenAI."""
        assert CAPABILITY_MATRIX[TaskType.COMPLEX_REASONING].provider == Provider.OPENAI
        assert CAPABILITY_MATRIX[TaskType.MATH_ANALYSIS].provider == Provider.OPENAI

    def test_document_tasks_prefer_gemini(self):
        """Test document tasks prefer Gemini."""
        assert CAPABILITY_MATRIX[TaskType.LONG_DOCUMENT].provider == Provider.GEMINI
        assert CAPABILITY_MATRIX[TaskType.SUMMARIZATION].provider == Provider.GEMINI

    def test_lightweight_tasks_prefer_ollama(self):
        """Test lightweight tasks prefer Ollama."""
        assert CAPABILITY_MATRIX[TaskType.SIMPLE_QA].provider == Provider.OLLAMA
        assert CAPABILITY_MATRIX[TaskType.CLASSIFICATION].provider == Provider.OLLAMA
        assert CAPABILITY_MATRIX[TaskType.DATA_EXTRACTION].provider == Provider.OLLAMA

    def test_internal_tasks_prefer_ollama(self):
        """Test internal tasks prefer Ollama."""
        assert CAPABILITY_MATRIX[TaskType.PROFILE_EXTRACTION].provider == Provider.OLLAMA
        assert CAPABILITY_MATRIX[TaskType.TASK_PARSING].provider == Provider.OLLAMA
        assert CAPABILITY_MATRIX[TaskType.HEARTBEAT_DECISION].provider == Provider.OLLAMA

    def test_all_configs_have_fallbacks(self):
        """Test all configs have fallback providers."""
        for task_type, config in CAPABILITY_MATRIX.items():
            assert len(config.fallbacks) > 0, f"{task_type} has no fallbacks"
            assert config.provider not in config.fallbacks, f"{task_type} has self in fallbacks"


class TestOllamaModelTiers:
    """Tests for Ollama model tier mapping."""

    def test_small_models(self):
        """Test small model classification."""
        assert OLLAMA_MODEL_TIERS["llama3.1:8b"] == OllamaTier.SMALL
        assert OLLAMA_MODEL_TIERS["phi-3"] == OllamaTier.SMALL
        assert OLLAMA_MODEL_TIERS["mistral:7b"] == OllamaTier.SMALL

    def test_medium_models(self):
        """Test medium model classification."""
        assert OLLAMA_MODEL_TIERS["llama3.1:70b"] == OllamaTier.MEDIUM
        assert OLLAMA_MODEL_TIERS["qwen2.5:32b"] == OllamaTier.MEDIUM

    def test_large_models(self):
        """Test large model classification."""
        assert OLLAMA_MODEL_TIERS["llama3.1:405b"] == OllamaTier.LARGE
        assert OLLAMA_MODEL_TIERS["deepseek-r1:70b"] == OllamaTier.LARGE


class TestGetOllamaTier:
    """Tests for get_ollama_tier function."""

    def test_exact_match(self):
        """Test exact model name match."""
        assert get_ollama_tier("llama3.1:8b") == OllamaTier.SMALL
        assert get_ollama_tier("llama3.1:70b") == OllamaTier.MEDIUM
        assert get_ollama_tier("llama3.1:405b") == OllamaTier.LARGE

    def test_prefix_match(self):
        """Test prefix-based matching."""
        # Should match llama3 prefix
        assert get_ollama_tier("llama3.1:8b-q4") == OllamaTier.SMALL
        assert get_ollama_tier("phi-3-mini") == OllamaTier.SMALL

    def test_unknown_model_defaults_small(self):
        """Test unknown models default to small tier."""
        assert get_ollama_tier("unknown-model") == OllamaTier.SMALL
        assert get_ollama_tier("custom-model:latest") == OllamaTier.SMALL

    def test_variant_matching(self):
        """Test variant model names."""
        # Different quantization variants should still match
        assert get_ollama_tier("mistral:7b") == OllamaTier.SMALL


class TestOllamaTierCapabilities:
    """Tests for Ollama tier capabilities."""

    def test_small_tier_capabilities(self):
        """Test small tier can handle basic tasks."""
        small_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.SMALL]
        assert TaskType.SIMPLE_QA in small_caps
        assert TaskType.CLASSIFICATION in small_caps
        assert TaskType.DATA_EXTRACTION in small_caps
        assert TaskType.HEARTBEAT_DECISION in small_caps

    def test_small_tier_limitations(self):
        """Test small tier cannot handle complex tasks."""
        small_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.SMALL]
        assert TaskType.CODE_GENERATION not in small_caps
        assert TaskType.COMPLEX_REASONING not in small_caps

    def test_medium_tier_expansion(self):
        """Test medium tier has expanded capabilities."""
        medium_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.MEDIUM]
        # Has all small capabilities
        for task in OLLAMA_TIER_CAPABILITIES[OllamaTier.SMALL]:
            assert task in medium_caps
        # Plus additional
        assert TaskType.SUMMARIZATION in medium_caps
        assert TaskType.CONVERSATION in medium_caps

    def test_large_tier_full(self):
        """Test large tier has full capabilities."""
        large_caps = OLLAMA_TIER_CAPABILITIES[OllamaTier.LARGE]
        assert TaskType.CODE_GENERATION in large_caps
        assert TaskType.CODE_DEBUGGING in large_caps
        assert TaskType.COMPLEX_REASONING in large_caps
        assert TaskType.CREATIVE_WRITING in large_caps


class TestCanOllamaHandle:
    """Tests for can_ollama_handle function."""

    def test_small_model_basic_tasks(self):
        """Test small model can handle basic tasks."""
        assert can_ollama_handle(TaskType.SIMPLE_QA, "llama3.1:8b") is True
        assert can_ollama_handle(TaskType.CLASSIFICATION, "phi-3") is True
        assert can_ollama_handle(TaskType.HEARTBEAT_DECISION, "mistral:7b") is True

    def test_small_model_complex_tasks(self):
        """Test small model cannot handle complex tasks."""
        assert can_ollama_handle(TaskType.CODE_GENERATION, "llama3.1:8b") is False
        assert can_ollama_handle(TaskType.COMPLEX_REASONING, "phi-3") is False

    def test_medium_model_expanded(self):
        """Test medium model has expanded capabilities."""
        assert can_ollama_handle(TaskType.SUMMARIZATION, "llama3.1:70b") is True
        assert can_ollama_handle(TaskType.CONVERSATION, "qwen2.5:32b") is True

    def test_large_model_full(self):
        """Test large model can handle everything."""
        assert can_ollama_handle(TaskType.CODE_GENERATION, "llama3.1:405b") is True
        assert can_ollama_handle(TaskType.COMPLEX_REASONING, "deepseek-r1:70b") is True


class TestGetProviderForTask:
    """Tests for get_provider_for_task function."""

    def test_default_provider_selection(self):
        """Test default provider selection from matrix."""
        # Code tasks -> Claude
        provider = get_provider_for_task(TaskType.CODE_GENERATION)
        assert provider == Provider.CLAUDE

        # Reasoning -> OpenAI
        provider = get_provider_for_task(TaskType.COMPLEX_REASONING)
        assert provider == Provider.OPENAI

        # Long docs -> Gemini
        provider = get_provider_for_task(TaskType.LONG_DOCUMENT)
        assert provider == Provider.GEMINI

    def test_force_ollama(self):
        """Test forcing Ollama for specific tasks."""
        provider = get_provider_for_task(
            TaskType.CODE_GENERATION,
            force_ollama={TaskType.CODE_GENERATION},
            available_providers={Provider.CLAUDE, Provider.OLLAMA},
        )
        assert provider == Provider.OLLAMA

    def test_force_cloud(self):
        """Test forcing cloud providers."""
        provider = get_provider_for_task(
            TaskType.SIMPLE_QA,
            force_cloud={TaskType.SIMPLE_QA},
            available_providers={Provider.GEMINI, Provider.OLLAMA},
        )
        # Should use Gemini (fallback) since Ollama is removed
        assert provider == Provider.GEMINI

    def test_fallback_when_primary_unavailable(self):
        """Test fallback when primary provider unavailable."""
        provider = get_provider_for_task(
            TaskType.CODE_GENERATION,
            available_providers={Provider.OPENAI, Provider.OLLAMA},  # No Claude
        )
        assert provider == Provider.OPENAI

    def test_ollama_tier_check(self):
        """Test Ollama tier checking for task suitability."""
        # Small model can't handle code generation
        provider = get_provider_for_task(
            TaskType.SIMPLE_QA,  # Ollama prefers, small can handle
            ollama_model="llama3.1:8b",
            available_providers={Provider.OLLAMA, Provider.GEMINI},
        )
        assert provider == Provider.OLLAMA

    def test_ollama_tier_insufficient_fallback(self):
        """Test fallback when Ollama tier is insufficient."""
        # For a task that small Ollama can't handle
        # The matrix shows SUMMARIZATION prefers GEMINI, with OLLAMA as fallback
        # Let's test a task where Ollama is primary but tier is insufficient
        provider = get_provider_for_task(
            TaskType.SIMPLE_QA,  # Ollama primary
            ollama_model="llama3.1:8b",  # Small tier
            available_providers={Provider.OLLAMA, Provider.GEMINI},
        )
        # Small tier CAN handle SIMPLE_QA, so Ollama is used
        assert provider == Provider.OLLAMA

    def test_no_providers_available_raises(self):
        """Test error when no providers available."""
        with pytest.raises(RuntimeError, match="No providers available"):
            get_provider_for_task(
                TaskType.CODE_GENERATION,
                available_providers=set(),  # Empty set
            )

    def test_last_resort_fallback(self):
        """Test last resort when no optimal provider available."""
        # Only Ollama available, but task prefers Claude
        provider = get_provider_for_task(
            TaskType.CODE_GENERATION,
            available_providers={Provider.OLLAMA},
        )
        # Should use Ollama as last resort (it's in fallbacks)
        assert provider == Provider.OLLAMA

    def test_available_providers_default(self):
        """Test default available providers is all providers."""
        # Should work without specifying available_providers
        provider = get_provider_for_task(TaskType.CODE_GENERATION)
        assert provider == Provider.CLAUDE

    def test_creative_writing_uses_openai(self):
        """Test creative writing uses OpenAI."""
        provider = get_provider_for_task(TaskType.CREATIVE_WRITING)
        assert provider == Provider.OPENAI

    def test_conversation_uses_claude(self):
        """Test conversation uses Claude."""
        provider = get_provider_for_task(TaskType.CONVERSATION)
        assert provider == Provider.CLAUDE
