"""Unit tests for the model registry module."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from zetherion_ai.models.registry import ModelRegistry
from zetherion_ai.models.tiers import ModelInfo, Tier


@pytest.fixture
def mock_discovery():
    """Create a mock ModelDiscovery."""
    discovery = AsyncMock()
    discovery.discover_all = AsyncMock(
        return_value={
            "openai": [],
            "anthropic": [],
            "google": [],
            "ollama": [],
        }
    )
    discovery.close = AsyncMock()
    return discovery


@pytest.fixture
def sample_models():
    """Create sample ModelInfo objects."""
    return {
        "openai": [
            ModelInfo(id="gpt-4o", provider="openai", tier=Tier.BALANCED, context_window=128000),
            ModelInfo(id="gpt-4o-mini", provider="openai", tier=Tier.FAST, context_window=128000),
        ],
        "anthropic": [
            ModelInfo(
                id="claude-opus-4-5", provider="anthropic", tier=Tier.QUALITY, context_window=200000
            ),
            ModelInfo(
                id="claude-sonnet-4-5",
                provider="anthropic",
                tier=Tier.BALANCED,
                context_window=200000,
            ),
        ],
        "google": [
            ModelInfo(
                id="gemini-2.0-flash", provider="google", tier=Tier.FAST, context_window=1000000
            ),
        ],
        "ollama": [
            ModelInfo(
                id="llama3.1:8b", provider="ollama", tier=Tier.BALANCED, context_window=128000
            ),
        ],
    }


class TestModelRegistryInit:
    """Tests for ModelRegistry initialization."""

    def test_init_with_discovery(self, mock_discovery):
        """Test initialization with discovery."""
        registry = ModelRegistry(mock_discovery)
        assert registry._discovery == mock_discovery
        assert registry._models == {}
        assert registry._last_refresh is None

    def test_init_with_provider_tiers(self, mock_discovery):
        """Test initialization with provider tier preferences."""
        tiers = {"anthropic": Tier.QUALITY, "openai": Tier.BALANCED}
        registry = ModelRegistry(mock_discovery, provider_tiers=tiers)
        assert registry._provider_tiers == tiers


class TestModelRegistryInitialize:
    """Tests for registry initialization."""

    @pytest.mark.asyncio
    async def test_initialize_calls_refresh(self, mock_discovery, sample_models):
        """Test that initialize calls refresh."""
        mock_discovery.discover_all.return_value = sample_models
        registry = ModelRegistry(mock_discovery)

        # Stop background task from running
        with patch.object(registry, "_background_refresh", return_value=AsyncMock()):
            await registry.initialize()

        mock_discovery.discover_all.assert_called_once()
        assert registry._last_refresh is not None

    @pytest.mark.asyncio
    async def test_initialize_populates_cache(self, mock_discovery, sample_models):
        """Test that initialize populates the model cache."""
        mock_discovery.discover_all.return_value = sample_models
        registry = ModelRegistry(mock_discovery)

        with patch.object(registry, "_background_refresh", return_value=AsyncMock()):
            await registry.initialize()

        assert "openai" in registry._models
        assert "gpt-4o" in registry._models["openai"]


class TestModelRegistryClose:
    """Tests for registry close."""

    @pytest.mark.asyncio
    async def test_close_cancels_task(self, mock_discovery):
        """Test that close cancels the background task."""
        import asyncio

        registry = ModelRegistry(mock_discovery)

        # Create a real async task that we can cancel
        async def dummy_task():
            await asyncio.sleep(100)

        registry._refresh_task = asyncio.create_task(dummy_task())

        await registry.close()

        assert registry._refresh_task is None
        mock_discovery.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_task(self, mock_discovery):
        """Test that close works when no task is running."""
        registry = ModelRegistry(mock_discovery)
        registry._refresh_task = None

        await registry.close()

        mock_discovery.close.assert_called_once()


class TestModelRegistryGetModel:
    """Tests for get_model method."""

    def test_get_model_found(self, mock_discovery, sample_models):
        """Test getting a model that exists."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        model = registry.get_model("gpt-4o")
        assert model is not None
        assert model.id == "gpt-4o"
        assert model.provider == "openai"

    def test_get_model_not_found(self, mock_discovery):
        """Test getting a model that doesn't exist."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {"openai": {}}

        model = registry.get_model("nonexistent")
        assert model is None


class TestModelRegistryGetModelsByProvider:
    """Tests for get_models_by_provider method."""

    def test_get_models_by_provider(self, mock_discovery, sample_models):
        """Test getting models for a provider."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        models = registry.get_models_by_provider("openai")
        assert len(models) == 2
        assert all(m.provider == "openai" for m in models)

    def test_get_models_by_provider_empty(self, mock_discovery):
        """Test getting models for a provider with none."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {}

        models = registry.get_models_by_provider("openai")
        assert models == []


class TestModelRegistryGetModelsByTier:
    """Tests for get_models_by_tier method."""

    def test_get_models_by_tier(self, mock_discovery, sample_models):
        """Test getting models by tier."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        models = registry.get_models_by_tier(Tier.BALANCED)
        assert len(models) == 3  # gpt-4o, claude-sonnet, llama3.1:8b
        assert all(m.tier == Tier.BALANCED for m in models)

    def test_get_models_by_tier_excludes_deprecated(self, mock_discovery, sample_models):
        """Test that deprecated models are excluded."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        # Mark one as deprecated
        registry._models["openai"]["gpt-4o"].deprecated = True

        models = registry.get_models_by_tier(Tier.BALANCED)
        assert "gpt-4o" not in [m.id for m in models]


class TestModelRegistryResolveTier:
    """Tests for resolve_tier method."""

    def test_resolve_tier_finds_match(self, mock_discovery, sample_models):
        """Test resolving a tier finds a matching model."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        model = registry.resolve_tier("openai", Tier.BALANCED)
        assert model is not None
        assert model.tier == Tier.BALANCED
        assert model.provider == "openai"

    def test_resolve_tier_uses_default(self, mock_discovery, sample_models):
        """Test resolving uses configured default tier."""
        registry = ModelRegistry(mock_discovery, provider_tiers={"anthropic": Tier.QUALITY})
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        model = registry.resolve_tier("anthropic")  # No tier specified
        assert model is not None
        assert model.tier == Tier.QUALITY

    def test_resolve_tier_falls_back(self, mock_discovery, sample_models):
        """Test falling back when no tier match."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        # Request QUALITY tier for openai (only has BALANCED and FAST)
        model = registry.resolve_tier("openai", Tier.QUALITY)
        assert model is not None  # Should fall back to any available

    def test_resolve_tier_no_models(self, mock_discovery):
        """Test resolving when no models available."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {"openai": {}}

        model = registry.resolve_tier("openai", Tier.BALANCED)
        assert model is None

    def test_resolve_tier_prefers_larger_context(self, mock_discovery):
        """Test that larger context windows are preferred."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {
            "test": {
                "model-a": ModelInfo(
                    id="model-a", provider="test", tier=Tier.BALANCED, context_window=8000
                ),
                "model-b": ModelInfo(
                    id="model-b", provider="test", tier=Tier.BALANCED, context_window=128000
                ),
            }
        }

        model = registry.resolve_tier("test", Tier.BALANCED)
        assert model.id == "model-b"  # Larger context


class TestModelRegistryResolveModelId:
    """Tests for resolve_model_id method."""

    def test_resolve_model_id_found(self, mock_discovery, sample_models):
        """Test resolving to a model ID."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        model_id = registry.resolve_model_id("openai", Tier.BALANCED)
        assert model_id == "gpt-4o"

    def test_resolve_model_id_not_found(self, mock_discovery):
        """Test resolving when no models."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {"openai": {}}

        model_id = registry.resolve_model_id("openai", Tier.BALANCED)
        assert model_id is None


class TestModelRegistryRateLimits:
    """Tests for rate limit tracking."""

    def test_record_rate_limit(self, mock_discovery, sample_models):
        """Test recording a rate limit hit."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        registry.record_rate_limit("gpt-4o")

        model = registry.get_model("gpt-4o")
        assert model.last_rate_limit_hit is not None

    def test_record_rate_limit_unknown_model(self, mock_discovery):
        """Test recording rate limit for unknown model."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {}

        # Should not raise
        registry.record_rate_limit("unknown-model")

    def test_is_rate_limited_true(self, mock_discovery, sample_models):
        """Test checking if model is rate limited."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        # Set recent rate limit
        registry._models["openai"]["gpt-4o"].last_rate_limit_hit = datetime.now()

        assert registry.is_rate_limited("gpt-4o", cooldown_seconds=60) is True

    def test_is_rate_limited_false_cooldown_expired(self, mock_discovery, sample_models):
        """Test rate limit check after cooldown."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        # Set old rate limit
        registry._models["openai"]["gpt-4o"].last_rate_limit_hit = datetime.now() - timedelta(
            minutes=5
        )

        assert registry.is_rate_limited("gpt-4o", cooldown_seconds=60) is False

    def test_is_rate_limited_no_hit(self, mock_discovery, sample_models):
        """Test rate limit check with no previous hit."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        assert registry.is_rate_limited("gpt-4o") is False


class TestModelRegistryGetAllModels:
    """Tests for get_all_models method."""

    def test_get_all_models(self, mock_discovery, sample_models):
        """Test getting all models."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        models = registry.get_all_models()
        assert len(models) == 6  # Total across all providers


class TestModelRegistryGetDeprecatedModels:
    """Tests for get_deprecated_models method."""

    def test_get_deprecated_models(self, mock_discovery, sample_models):
        """Test getting deprecated models."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        # Mark some as deprecated
        registry._models["openai"]["gpt-4o"].deprecated = True
        registry._models["anthropic"]["claude-opus-4-5"].deprecated = True

        deprecated = registry.get_deprecated_models()
        assert len(deprecated) == 2
        assert all(m.deprecated for m in deprecated)


class TestModelRegistryGetStats:
    """Tests for get_stats method."""

    def test_get_stats(self, mock_discovery, sample_models):
        """Test getting registry statistics."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}
        registry._last_refresh = datetime.now()

        stats = registry.get_stats()

        assert stats["total_models"] == 6
        assert "by_provider" in stats
        assert stats["by_provider"]["openai"] == 2
        assert "by_tier" in stats
        assert "last_refresh" in stats

    def test_get_stats_counts_deprecated(self, mock_discovery, sample_models):
        """Test that stats count deprecated models."""
        registry = ModelRegistry(mock_discovery)
        registry._models = {k: {m.id: m for m in v} for k, v in sample_models.items()}

        registry._models["openai"]["gpt-4o"].deprecated = True

        stats = registry.get_stats()
        assert stats["deprecated"] == 1


class TestModelRegistryRefresh:
    """Tests for refresh method."""

    @pytest.mark.asyncio
    async def test_refresh_updates_cache(self, mock_discovery, sample_models):
        """Test that refresh updates the model cache."""
        mock_discovery.discover_all.return_value = sample_models
        registry = ModelRegistry(mock_discovery)

        await registry.refresh()

        assert len(registry._models["openai"]) == 2
        assert registry._last_refresh is not None

    @pytest.mark.asyncio
    async def test_refresh_tracks_deprecations(self, mock_discovery, sample_models):
        """Test that refresh tracks model deprecations."""
        registry = ModelRegistry(mock_discovery)

        # First refresh
        mock_discovery.discover_all.return_value = sample_models
        await registry.refresh()

        # Second refresh with one model missing
        modified_models = dict(sample_models)
        modified_models["openai"] = [sample_models["openai"][0]]  # Remove gpt-4o-mini
        mock_discovery.discover_all.return_value = modified_models

        await registry.refresh()

        # Should be tracking the disappeared model
        assert "gpt-4o-mini" in registry._deprecated_tracking
