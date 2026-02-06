"""Provider capability matrix for smart multi-provider routing.

Defines task types and maps them to the optimal LLM provider based on
benchmark performance, cost, and latency characteristics.
"""

from dataclasses import dataclass
from enum import Enum

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.agent.providers")


class TaskType(Enum):
    """Types of tasks that can be routed to different providers.

    Each task type has different requirements for quality, speed, and cost.
    The InferenceBroker uses these to select the optimal provider.
    """

    # Code-related tasks - Claude excels
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_DEBUGGING = "code_debugging"

    # Reasoning tasks - OpenAI excels
    COMPLEX_REASONING = "complex_reasoning"
    MATH_ANALYSIS = "math_analysis"

    # Long-form tasks - Gemini excels (1M context)
    LONG_DOCUMENT = "long_document"
    SUMMARIZATION = "summarization"

    # Creative tasks - OpenAI excels
    CREATIVE_WRITING = "creative_writing"

    # Lightweight tasks - Ollama preferred (free, local)
    SIMPLE_QA = "simple_qa"
    CLASSIFICATION = "classification"
    DATA_EXTRACTION = "data_extraction"
    CONVERSATION = "conversation"

    # Internal tasks - Ollama preferred (keeps data local)
    PROFILE_EXTRACTION = "profile_extraction"
    TASK_PARSING = "task_parsing"
    HEARTBEAT_DECISION = "heartbeat_decision"


class Provider(Enum):
    """Available LLM providers."""

    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class OllamaTier(Enum):
    """Ollama model capability tiers based on size/performance."""

    SMALL = "small"  # 7B-8B models: llama3.1:8b, phi-3, mistral:7b
    MEDIUM = "medium"  # 32B-70B models: llama3.1:70b, qwen2.5:32b
    LARGE = "large"  # 405B+ models: llama3.1:405b, deepseek-r1:70b


@dataclass
class ProviderConfig:
    """Configuration for a provider selection."""

    provider: Provider
    rationale: str
    fallbacks: list[Provider]


# Provider capability matrix based on 2026 benchmarks
# Maps task type to ordered list of providers (best first)
CAPABILITY_MATRIX: dict[TaskType, ProviderConfig] = {
    # Code tasks - Claude dominates (80.9% SWE-bench, Terminal-Bench leader)
    TaskType.CODE_GENERATION: ProviderConfig(
        provider=Provider.CLAUDE,
        rationale="80.9% SWE-bench, dominates Terminal-Bench",
        fallbacks=[Provider.OPENAI, Provider.OLLAMA],
    ),
    TaskType.CODE_REVIEW: ProviderConfig(
        provider=Provider.CLAUDE,
        rationale="Highest precision on compliance and audit tasks",
        fallbacks=[Provider.OPENAI],
    ),
    TaskType.CODE_DEBUGGING: ProviderConfig(
        provider=Provider.CLAUDE,
        rationale="Best at understanding code context and suggesting fixes",
        fallbacks=[Provider.OPENAI, Provider.OLLAMA],
    ),
    # Reasoning tasks - OpenAI excels (100% AIME 2025, best ARC-AGI)
    TaskType.COMPLEX_REASONING: ProviderConfig(
        provider=Provider.OPENAI,
        rationale="100% AIME 2025, best abstract reasoning",
        fallbacks=[Provider.CLAUDE, Provider.OLLAMA],
    ),
    TaskType.MATH_ANALYSIS: ProviderConfig(
        provider=Provider.OPENAI,
        rationale="Best mathematical reasoning benchmarks",
        fallbacks=[Provider.CLAUDE, Provider.GEMINI],
    ),
    # Long-form tasks - Gemini excels (1M context, 68.2% LongBench)
    TaskType.LONG_DOCUMENT: ProviderConfig(
        provider=Provider.GEMINI,
        rationale="1M token context, 68.2% LongBench vs 54.5% GPT",
        fallbacks=[Provider.CLAUDE],
    ),
    TaskType.SUMMARIZATION: ProviderConfig(
        provider=Provider.GEMINI,
        rationale="Fast, cheap, handles large inputs natively",
        fallbacks=[Provider.OLLAMA],
    ),
    # Creative tasks - OpenAI excels
    TaskType.CREATIVE_WRITING: ProviderConfig(
        provider=Provider.OPENAI,
        rationale="Strongest narrative and style flexibility",
        fallbacks=[Provider.CLAUDE],
    ),
    # Lightweight tasks - Ollama preferred (free, local, fast)
    TaskType.SIMPLE_QA: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Free, no API cost, low latency",
        fallbacks=[Provider.GEMINI],
    ),
    TaskType.CLASSIFICATION: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Free, fast, already proven in router",
        fallbacks=[Provider.GEMINI],
    ),
    TaskType.DATA_EXTRACTION: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Free, fast, sufficient quality for structured extraction",
        fallbacks=[Provider.GEMINI],
    ),
    TaskType.CONVERSATION: ProviderConfig(
        provider=Provider.CLAUDE,
        rationale="Most natural conversational tone",
        fallbacks=[Provider.OPENAI, Provider.OLLAMA],
    ),
    # Internal tasks - Ollama keeps data local
    TaskType.PROFILE_EXTRACTION: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Lightweight structured extraction, keeps data local",
        fallbacks=[Provider.GEMINI],
    ),
    TaskType.TASK_PARSING: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Entity extraction, sufficient quality locally",
        fallbacks=[Provider.GEMINI],
    ),
    TaskType.HEARTBEAT_DECISION: ProviderConfig(
        provider=Provider.OLLAMA,
        rationale="Simple yes/no decisions, no API cost",
        fallbacks=[Provider.GEMINI],
    ),
}


# Ollama model tier classification
# Maps model name patterns to capability tiers
OLLAMA_MODEL_TIERS: dict[str, OllamaTier] = {
    # Small models (7B-8B)
    "llama3.1:8b": OllamaTier.SMALL,
    "llama3.2:3b": OllamaTier.SMALL,
    "phi-3": OllamaTier.SMALL,
    "phi3": OllamaTier.SMALL,
    "mistral:7b": OllamaTier.SMALL,
    "mistral-nemo": OllamaTier.SMALL,
    "qwen2.5:7b": OllamaTier.SMALL,
    "gemma2:9b": OllamaTier.SMALL,
    # Medium models (32B-70B)
    "llama3.1:70b": OllamaTier.MEDIUM,
    "qwen2.5:32b": OllamaTier.MEDIUM,
    "mixtral:8x7b": OllamaTier.MEDIUM,
    "command-r": OllamaTier.MEDIUM,
    # Large models (405B+)
    "llama3.1:405b": OllamaTier.LARGE,
    "deepseek-r1:70b": OllamaTier.LARGE,
    "deepseek-r1": OllamaTier.LARGE,
}


def get_ollama_tier(model_name: str) -> OllamaTier:
    """Determine the capability tier of an Ollama model.

    Args:
        model_name: The Ollama model name (e.g., "llama3.1:8b").

    Returns:
        The capability tier of the model.
    """
    # Check exact match first
    if model_name in OLLAMA_MODEL_TIERS:
        return OLLAMA_MODEL_TIERS[model_name]

    # Check prefix match (handles version variants)
    for pattern, tier in OLLAMA_MODEL_TIERS.items():
        if model_name.startswith(pattern.split(":")[0]):
            return tier

    # Default to small for unknown models (conservative)
    log.warning("unknown_ollama_model", model=model_name, defaulting_to="small")
    return OllamaTier.SMALL


# Task types that can be handled by each Ollama tier
OLLAMA_TIER_CAPABILITIES: dict[OllamaTier, set[TaskType]] = {
    OllamaTier.SMALL: {
        TaskType.SIMPLE_QA,
        TaskType.CLASSIFICATION,
        TaskType.DATA_EXTRACTION,
        TaskType.PROFILE_EXTRACTION,
        TaskType.TASK_PARSING,
        TaskType.HEARTBEAT_DECISION,
    },
    OllamaTier.MEDIUM: {
        TaskType.SIMPLE_QA,
        TaskType.CLASSIFICATION,
        TaskType.DATA_EXTRACTION,
        TaskType.PROFILE_EXTRACTION,
        TaskType.TASK_PARSING,
        TaskType.HEARTBEAT_DECISION,
        TaskType.SUMMARIZATION,
        TaskType.CONVERSATION,
    },
    OllamaTier.LARGE: {
        TaskType.SIMPLE_QA,
        TaskType.CLASSIFICATION,
        TaskType.DATA_EXTRACTION,
        TaskType.PROFILE_EXTRACTION,
        TaskType.TASK_PARSING,
        TaskType.HEARTBEAT_DECISION,
        TaskType.SUMMARIZATION,
        TaskType.CONVERSATION,
        TaskType.CODE_GENERATION,
        TaskType.CODE_DEBUGGING,
        TaskType.COMPLEX_REASONING,
        TaskType.CREATIVE_WRITING,
    },
}


def can_ollama_handle(task_type: TaskType, ollama_model: str) -> bool:
    """Check if the current Ollama model can handle a task type.

    Args:
        task_type: The task type to check.
        ollama_model: The Ollama model name.

    Returns:
        True if Ollama can handle this task type, False otherwise.
    """
    tier = get_ollama_tier(ollama_model)
    return task_type in OLLAMA_TIER_CAPABILITIES.get(tier, set())


def get_provider_for_task(
    task_type: TaskType,
    ollama_model: str | None = None,
    available_providers: set[Provider] | None = None,
    force_cloud: set[TaskType] | None = None,
    force_ollama: set[TaskType] | None = None,
) -> Provider:
    """Get the optimal provider for a task type.

    Args:
        task_type: The type of task to handle.
        ollama_model: The Ollama model name (for tier checking).
        available_providers: Set of providers that are configured and available.
        force_cloud: Task types that must use cloud providers.
        force_ollama: Task types that must use Ollama.

    Returns:
        The optimal available provider for this task.
    """
    if available_providers is None:
        available_providers = set(Provider)

    force_cloud = force_cloud or set()
    force_ollama = force_ollama or set()

    # Check forced overrides first
    if task_type in force_ollama and Provider.OLLAMA in available_providers:
        return Provider.OLLAMA
    if task_type in force_cloud:
        # Remove Ollama from consideration
        available_providers = available_providers - {Provider.OLLAMA}

    # Get the recommended provider from the matrix
    config = CAPABILITY_MATRIX.get(task_type)
    if config is None:
        log.warning("unknown_task_type", task_type=task_type.value)
        # Default to Claude for unknown tasks
        config = ProviderConfig(
            provider=Provider.CLAUDE,
            rationale="Unknown task type, defaulting to most capable",
            fallbacks=[Provider.OPENAI, Provider.GEMINI, Provider.OLLAMA],
        )

    # Check if Ollama can handle this task at the current tier
    if (
        config.provider == Provider.OLLAMA
        and ollama_model
        and not can_ollama_handle(task_type, ollama_model)
    ):
        # Ollama can't handle this at current tier, use first available fallback
        for fallback in config.fallbacks:
            if fallback in available_providers:
                log.debug(
                    "ollama_tier_insufficient",
                    task_type=task_type.value,
                    model=ollama_model,
                    using=fallback.value,
                )
                return fallback

    # Return primary provider if available
    if config.provider in available_providers:
        return config.provider

    # Otherwise, return first available fallback
    for fallback in config.fallbacks:
        if fallback in available_providers:
            log.debug(
                "using_fallback_provider",
                task_type=task_type.value,
                primary=config.provider.value,
                fallback=fallback.value,
            )
            return fallback

    # Last resort: return any available provider
    if available_providers:
        fallback = next(iter(available_providers))
        log.warning(
            "no_optimal_provider",
            task_type=task_type.value,
            using=fallback.value,
        )
        return fallback

    # No providers available at all
    raise RuntimeError(f"No providers available for task type: {task_type.value}")
