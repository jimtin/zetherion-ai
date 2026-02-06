"""Model tier classification and heuristics.

Tiers allow users to configure quality/cost tradeoffs without specifying
exact model versions. The system automatically resolves tiers to the best
available model for each provider.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.models.tiers")


class Tier(Enum):
    """Model capability tiers.

    - QUALITY: Best capability, highest cost (Opus, GPT-4.1, Gemini Pro)
    - BALANCED: Good tradeoff (Sonnet, GPT-4o, Gemini Flash)
    - FAST: Cheapest/fastest (Haiku, GPT-4o-mini, Gemini Flash-Lite)
    """

    QUALITY = "quality"
    BALANCED = "balanced"
    FAST = "fast"


@dataclass
class ModelInfo:
    """Information about a discovered model."""

    id: str
    provider: str
    tier: Tier
    display_name: str | None = None
    context_window: int | None = None
    created_at: datetime | None = None
    discovered_at: datetime | None = None
    # Rate limit info (from API headers or known defaults)
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    last_rate_limit_hit: datetime | None = None
    # Deprecation tracking
    deprecated: bool = False
    deprecated_at: datetime | None = None


# Name patterns for tier inference
_QUALITY_PATTERNS = [
    "opus",
    "pro",
    "turbo",
    "4.1",
    "4-1",
    "ultra",
    "advanced",
]

_FAST_PATTERNS = [
    "mini",
    "lite",
    "haiku",
    "flash-lite",
    "nano",
    "small",
    "instant",
]

_BALANCED_PATTERNS = [
    "sonnet",
    "4o",
    "flash",
    "standard",
]

# Context window thresholds for tier inference
_QUALITY_CONTEXT_THRESHOLD = 200_000  # Models with >200k context are likely quality
_FAST_CONTEXT_THRESHOLD = 32_000  # Models with <32k context are likely fast


def infer_tier(model_id: str, metadata: dict[str, Any] | None = None) -> Tier:
    """Infer the tier of a model from its ID and metadata.

    Uses heuristics based on:
    1. Model name patterns (opus, sonnet, haiku, mini, etc.)
    2. Context window size (larger = more capable)
    3. Default to BALANCED for unknown models

    Args:
        model_id: The model identifier (e.g., "claude-opus-4-5-20251101").
        metadata: Optional metadata dict with keys like "context_window".

    Returns:
        The inferred Tier for this model.
    """
    id_lower = model_id.lower()
    metadata = metadata or {}

    # Check quality patterns first (highest priority)
    if any(pattern in id_lower for pattern in _QUALITY_PATTERNS):
        log.debug("tier_inferred", model=model_id, tier="quality", reason="name_pattern")
        return Tier.QUALITY

    # Check fast patterns
    if any(pattern in id_lower for pattern in _FAST_PATTERNS):
        log.debug("tier_inferred", model=model_id, tier="fast", reason="name_pattern")
        return Tier.FAST

    # Check balanced patterns
    if any(pattern in id_lower for pattern in _BALANCED_PATTERNS):
        log.debug("tier_inferred", model=model_id, tier="balanced", reason="name_pattern")
        return Tier.BALANCED

    # Fall back to context window heuristics
    context_window = metadata.get("context_window") or metadata.get("inputTokenLimit")
    if context_window:
        if context_window > _QUALITY_CONTEXT_THRESHOLD:
            log.debug(
                "tier_inferred",
                model=model_id,
                tier="quality",
                reason="large_context",
                context_window=context_window,
            )
            return Tier.QUALITY
        if context_window < _FAST_CONTEXT_THRESHOLD:
            log.debug(
                "tier_inferred",
                model=model_id,
                tier="fast",
                reason="small_context",
                context_window=context_window,
            )
            return Tier.FAST

    # Default to balanced for unknown models
    log.debug("tier_inferred", model=model_id, tier="balanced", reason="default")
    return Tier.BALANCED


def tier_from_string(tier_str: str) -> Tier:
    """Parse a tier string into a Tier enum.

    Args:
        tier_str: String like "quality", "balanced", or "fast".

    Returns:
        The corresponding Tier enum value.

    Raises:
        ValueError: If the string is not a valid tier.
    """
    try:
        return Tier(tier_str.lower())
    except ValueError as e:
        valid_tiers = [t.value for t in Tier]
        raise ValueError(f"Invalid tier '{tier_str}'. Must be one of: {valid_tiers}") from e
