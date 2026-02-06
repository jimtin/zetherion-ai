"""Model pricing data and cost calculation.

Provider APIs do NOT return pricing information, so we maintain a manual
pricing table here. This should be updated when pricing changes.

When pricing is unknown for a model, we use conservative tier-based fallback
estimates rather than returning None, ensuring costs are always tracked.
"""

from dataclasses import dataclass
from datetime import datetime

from zetherion_ai.logging import get_logger
from zetherion_ai.models.tiers import Tier

log = get_logger("zetherion_ai.models.pricing")


@dataclass
class CostResult:
    """Result of a cost calculation."""

    cost_usd: float
    estimated: bool = False  # True if pricing was unknown and fallback was used
    model_id: str | None = None


@dataclass
class ModelPricing:
    """Pricing information for a model."""

    model_id: str
    provider: str
    cost_per_million_input: float
    cost_per_million_output: float
    last_updated: datetime | None = None


# Pricing table - costs per 1 million tokens
# Updated: 2026-02-06
# Source: Provider pricing pages (manually maintained)
PRICING: dict[str, dict[str, float]] = {
    # Anthropic Claude models
    "claude-opus-4-5-20251101": {"input": 5.00, "output": 25.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    # Legacy Claude models
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 1.00, "output": 5.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet-20240229": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    # OpenAI models
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-2024-11-20": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o-mini-2024-07-18": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4-turbo-preview": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-preview": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    # Google Gemini models
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash-exp": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.0-pro": {"input": 0.50, "output": 1.50},
    "gemini-pro": {"input": 0.50, "output": 1.50},
    # Ollama models (free - local inference)
    "llama3.1:8b": {"input": 0.0, "output": 0.0},
    "llama3.1:70b": {"input": 0.0, "output": 0.0},
    "llama3.1:405b": {"input": 0.0, "output": 0.0},
    "llama3.2:3b": {"input": 0.0, "output": 0.0},
    "phi-3": {"input": 0.0, "output": 0.0},
    "phi3": {"input": 0.0, "output": 0.0},
    "mistral:7b": {"input": 0.0, "output": 0.0},
    "mistral-nemo": {"input": 0.0, "output": 0.0},
    "qwen2.5:7b": {"input": 0.0, "output": 0.0},
    "qwen2.5:32b": {"input": 0.0, "output": 0.0},
    "gemma2:9b": {"input": 0.0, "output": 0.0},
    "mixtral:8x7b": {"input": 0.0, "output": 0.0},
    "command-r": {"input": 0.0, "output": 0.0},
    "deepseek-r1:70b": {"input": 0.0, "output": 0.0},
    "deepseek-r1": {"input": 0.0, "output": 0.0},
}

# Fallback pricing by tier (per 1 million tokens)
# Used when a model's pricing is unknown - conservative estimates
TIER_FALLBACK_PRICING: dict[Tier, dict[str, float]] = {
    Tier.QUALITY: {"input": 15.00, "output": 60.00},  # Assume expensive
    Tier.BALANCED: {"input": 3.00, "output": 15.00},  # Assume mid-range
    Tier.FAST: {"input": 0.50, "output": 2.00},  # Assume cheap
}

# Provider prefixes for fallback tier detection
_PROVIDER_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "gemini": "google",
    "llama": "ollama",
    "phi": "ollama",
    "mistral": "ollama",
    "qwen": "ollama",
    "gemma": "ollama",
    "mixtral": "ollama",
    "deepseek": "ollama",
    "command": "ollama",
}


def _detect_provider(model_id: str) -> str | None:
    """Detect the provider from a model ID."""
    id_lower = model_id.lower()
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if id_lower.startswith(prefix):
            return provider
    return None


def _infer_tier_from_id(model_id: str) -> Tier:
    """Infer tier from model ID for fallback pricing.

    This is a simplified version that doesn't require the full tiers module
    to avoid circular imports.
    """
    id_lower = model_id.lower()

    # Quality indicators
    if any(x in id_lower for x in ["opus", "pro", "turbo", "4.1", "ultra"]):
        return Tier.QUALITY

    # Fast indicators
    if any(x in id_lower for x in ["mini", "lite", "haiku", "nano", "small", "3b", "7b", "8b"]):
        return Tier.FAST

    # Default to balanced
    return Tier.BALANCED


def get_cost(
    model_id: str,
    tokens_input: int,
    tokens_output: int,
    tier: Tier | None = None,
) -> CostResult:
    """Calculate the cost of an API call.

    If pricing is known for the model, uses exact pricing.
    If pricing is unknown, uses conservative tier-based fallback estimates.

    Args:
        model_id: The model identifier.
        tokens_input: Number of input tokens.
        tokens_output: Number of output tokens.
        tier: Optional tier override for fallback pricing.

    Returns:
        CostResult with the calculated cost and estimation flag.
    """
    # Check for exact pricing first
    if model_id in PRICING:
        pricing = PRICING[model_id]
        cost = (tokens_input * pricing["input"] + tokens_output * pricing["output"]) / 1_000_000
        return CostResult(cost_usd=cost, estimated=False, model_id=model_id)

    # Try normalized model ID (without date suffixes)
    normalized_id = _normalize_model_id(model_id)
    if normalized_id in PRICING:
        pricing = PRICING[normalized_id]
        cost = (tokens_input * pricing["input"] + tokens_output * pricing["output"]) / 1_000_000
        return CostResult(cost_usd=cost, estimated=False, model_id=model_id)

    # Check if this is an Ollama model (local = free)
    provider = _detect_provider(model_id)
    if provider == "ollama":
        return CostResult(cost_usd=0.0, estimated=False, model_id=model_id)

    # Fallback to tier-based pricing
    if tier is None:
        tier = _infer_tier_from_id(model_id)

    fallback = TIER_FALLBACK_PRICING[tier]
    cost = (tokens_input * fallback["input"] + tokens_output * fallback["output"]) / 1_000_000

    log.warning(
        "unknown_pricing",
        model=model_id,
        tier=tier.value,
        using_fallback=True,
        estimated_cost=cost,
    )

    return CostResult(cost_usd=cost, estimated=True, model_id=model_id)


def _normalize_model_id(model_id: str) -> str:
    """Normalize a model ID by removing date suffixes.

    Examples:
        "claude-sonnet-4-5-20250929" -> "claude-sonnet-4-5"
        "gpt-4o-2024-11-20" -> "gpt-4o"
    """
    import re

    # Remove date patterns like -20250929 or -2024-11-20
    normalized = re.sub(r"-\d{8}$", "", model_id)
    normalized = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)
    return normalized


def has_pricing(model_id: str) -> bool:
    """Check if we have pricing data for a model.

    Args:
        model_id: The model identifier.

    Returns:
        True if exact pricing is known, False otherwise.
    """
    if model_id in PRICING:
        return True

    normalized = _normalize_model_id(model_id)
    if normalized in PRICING:
        return True

    # Ollama models are always "known" (free)
    provider = _detect_provider(model_id)
    return provider == "ollama"


def get_all_known_models() -> list[str]:
    """Get a list of all models with known pricing.

    Returns:
        List of model IDs with pricing data.
    """
    return list(PRICING.keys())


def update_pricing(model_id: str, input_cost: float, output_cost: float) -> None:
    """Update pricing for a model.

    This is primarily for testing or dynamic pricing updates.

    Args:
        model_id: The model identifier.
        input_cost: Cost per 1M input tokens.
        output_cost: Cost per 1M output tokens.
    """
    PRICING[model_id] = {"input": input_cost, "output": output_cost}
    log.info("pricing_updated", model=model_id, input=input_cost, output=output_cost)
