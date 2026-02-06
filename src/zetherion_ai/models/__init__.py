"""Model registry and tier-based selection for LLM providers."""

from zetherion_ai.models.discovery import DiscoveryError, ModelDiscovery
from zetherion_ai.models.pricing import CostResult, get_cost, has_pricing
from zetherion_ai.models.registry import ModelRegistry
from zetherion_ai.models.tiers import ModelInfo, Tier, infer_tier

__all__ = [
    "CostResult",
    "DiscoveryError",
    "ModelDiscovery",
    "ModelInfo",
    "ModelRegistry",
    "Tier",
    "get_cost",
    "has_pricing",
    "infer_tier",
]
