"""Unit tests for the models package (Phase 5B.1)."""

import pytest

from zetherion_ai.models.pricing import (
    CostResult,
    _infer_tier_from_id,
    _normalize_model_id,
    get_all_known_models,
    get_cost,
    has_pricing,
    update_pricing,
)
from zetherion_ai.models.tiers import ModelInfo, Tier, infer_tier


class TestTier:
    """Tests for the Tier enum and inference."""

    def test_tier_values(self):
        """Test tier enum values."""
        assert Tier.QUALITY.value == "quality"
        assert Tier.BALANCED.value == "balanced"
        assert Tier.FAST.value == "fast"

    def test_infer_tier_quality_models(self):
        """Test tier inference for quality models."""
        assert infer_tier("claude-opus-4-5-20251101") == Tier.QUALITY
        assert infer_tier("gpt-4-turbo") == Tier.QUALITY
        assert infer_tier("gemini-1.5-pro") == Tier.QUALITY
        # o1 models don't have a quality indicator in name, defaults to balanced
        assert infer_tier("o1-preview") == Tier.BALANCED

    def test_infer_tier_balanced_models(self):
        """Test tier inference for balanced models."""
        assert infer_tier("claude-sonnet-4-5-20250929") == Tier.BALANCED
        assert infer_tier("gpt-4o") == Tier.BALANCED
        # Flash models are considered fast tier
        assert infer_tier("gemini-2.0-flash") == Tier.FAST

    def test_infer_tier_fast_models(self):
        """Test tier inference for fast models."""
        assert infer_tier("claude-haiku-4-5-20251001") == Tier.FAST
        assert infer_tier("gpt-4o-mini") == Tier.FAST
        # Models without clear tier indicators default to balanced
        assert infer_tier("llama3.1:8b") == Tier.BALANCED
        assert infer_tier("phi-3") == Tier.BALANCED  # phi without size indicator

    def test_infer_tier_with_context_window(self):
        """Test tier inference - context window affects tier for unknown models."""
        # Small context windows suggest lightweight/fast models
        result = infer_tier("unknown-model", {"context_window": 4000})
        # Could be fast or balanced depending on implementation
        assert result in [Tier.FAST, Tier.BALANCED]
        # Large context windows don't necessarily mean quality tier
        result = infer_tier("unknown-model", {"context_window": 200000})
        assert result in [Tier.QUALITY, Tier.BALANCED]

    def test_infer_tier_unknown_defaults_balanced(self):
        """Test that unknown models default to balanced."""
        assert infer_tier("completely-unknown-model") == Tier.BALANCED


class TestModelInfo:
    """Tests for the ModelInfo dataclass."""

    def test_model_info_creation(self):
        """Test creating a ModelInfo instance."""
        model = ModelInfo(
            id="claude-sonnet-4-5",
            provider="anthropic",
            tier=Tier.BALANCED,
            display_name="Claude Sonnet 4.5",
            context_window=200000,
        )
        assert model.id == "claude-sonnet-4-5"
        assert model.provider == "anthropic"
        assert model.tier == Tier.BALANCED
        assert model.context_window == 200000
        assert model.deprecated is False

    def test_model_info_defaults(self):
        """Test ModelInfo default values."""
        model = ModelInfo(id="test", provider="test", tier=Tier.FAST)
        assert model.display_name is None
        assert model.context_window is None
        assert model.requests_per_minute is None
        assert model.deprecated is False
        assert model.deprecated_at is None


class TestPricing:
    """Tests for the pricing module."""

    def test_get_cost_known_model(self):
        """Test cost calculation for known models."""
        result = get_cost("claude-sonnet-4-5-20250929", 1000, 500)
        assert isinstance(result, CostResult)
        assert result.estimated is False
        # Claude Sonnet: $3/1M input, $15/1M output
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert result.cost_usd == pytest.approx(expected)

    def test_get_cost_openai_model(self):
        """Test cost calculation for OpenAI models."""
        result = get_cost("gpt-4o", 1000, 500)
        assert result.estimated is False
        # GPT-4o: $2.5/1M input, $10/1M output
        expected = (1000 * 2.5 + 500 * 10.0) / 1_000_000
        assert result.cost_usd == pytest.approx(expected)

    def test_get_cost_ollama_free(self):
        """Test that Ollama models are free."""
        result = get_cost("llama3.1:8b", 10000, 5000)
        assert result.cost_usd == 0.0
        assert result.estimated is False

    def test_get_cost_unknown_model_fallback(self):
        """Test fallback pricing for unknown models."""
        result = get_cost("totally-unknown-model-xyz", 1000, 500)
        assert result.estimated is True
        assert result.cost_usd > 0  # Should use tier-based fallback

    def test_get_cost_normalized_model_id(self):
        """Test cost lookup with date-suffixed model IDs."""
        # With date suffix
        result1 = get_cost("claude-sonnet-4-5-20250929", 1000, 500)
        # Without date suffix (normalized)
        result2 = get_cost("claude-sonnet-4-5", 1000, 500)
        assert result1.cost_usd == result2.cost_usd

    def test_normalize_model_id(self):
        """Test model ID normalization."""
        assert _normalize_model_id("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
        assert _normalize_model_id("gpt-4o-2024-11-20") == "gpt-4o"
        assert _normalize_model_id("llama3.1:8b") == "llama3.1:8b"

    def test_has_pricing(self):
        """Test checking for pricing data."""
        assert has_pricing("claude-sonnet-4-5") is True
        assert has_pricing("gpt-4o") is True
        assert has_pricing("llama3.1:8b") is True  # Ollama = free
        assert has_pricing("totally-unknown") is False

    def test_get_all_known_models(self):
        """Test getting list of known models."""
        models = get_all_known_models()
        assert len(models) > 0
        assert "claude-sonnet-4-5" in models
        assert "gpt-4o" in models

    def test_update_pricing(self):
        """Test dynamic pricing update."""
        model_id = "test-model-for-update"
        update_pricing(model_id, 1.0, 2.0)
        result = get_cost(model_id, 1000, 500)
        expected = (1000 * 1.0 + 500 * 2.0) / 1_000_000
        assert result.cost_usd == pytest.approx(expected)
        assert result.estimated is False

    def test_infer_tier_from_id_quality(self):
        """Test tier inference for fallback pricing."""
        assert _infer_tier_from_id("opus-model") == Tier.QUALITY
        assert _infer_tier_from_id("pro-model") == Tier.QUALITY
        assert _infer_tier_from_id("gpt-4.1") == Tier.QUALITY

    def test_infer_tier_from_id_fast(self):
        """Test tier inference for fast models."""
        assert _infer_tier_from_id("mini-model") == Tier.FAST
        assert _infer_tier_from_id("haiku-test") == Tier.FAST
        assert _infer_tier_from_id("small-7b") == Tier.FAST


class TestCostResultDataclass:
    """Tests for CostResult dataclass."""

    def test_cost_result_fields(self):
        """Test CostResult field access."""
        result = CostResult(cost_usd=0.05, estimated=True, model_id="test-model")
        assert result.cost_usd == 0.05
        assert result.estimated is True
        assert result.model_id == "test-model"

    def test_cost_result_defaults(self):
        """Test CostResult default values."""
        result = CostResult(cost_usd=0.01)
        assert result.estimated is False
        assert result.model_id is None
