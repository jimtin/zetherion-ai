"""Model registry with tier-based resolution.

The registry caches discovered models and provides tier-based selection:
users configure tiers (quality, balanced, fast) instead of specific model IDs.

The registry auto-refreshes every 24 hours to catch new models.
"""

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.models.discovery import ModelDiscovery, check_deprecation
from zetherion_ai.models.pricing import has_pricing
from zetherion_ai.models.tiers import ModelInfo, Tier

log = get_logger("zetherion_ai.models.registry")

# Refresh interval
REFRESH_INTERVAL_HOURS = 24


class ModelRegistry:
    """Registry of available models with tier-based selection.

    The registry:
    - Discovers models from all configured providers at startup
    - Caches model info in memory
    - Auto-refreshes every 24 hours
    - Provides tier-based model resolution
    - Tracks rate limits and deprecations
    """

    def __init__(
        self,
        discovery: ModelDiscovery,
        provider_tiers: dict[str, Tier] | None = None,
    ):
        """Initialize the model registry.

        Args:
            discovery: ModelDiscovery instance for API queries.
            provider_tiers: Optional dict mapping provider to tier preference.
                           E.g., {"anthropic": Tier.QUALITY, "openai": Tier.BALANCED}
        """
        self._discovery = discovery
        self._provider_tiers = provider_tiers or {}

        # Model cache: provider -> model_id -> ModelInfo
        self._models: dict[str, dict[str, ModelInfo]] = {}

        # Track discovery state
        self._last_refresh: datetime | None = None
        self._refresh_lock = asyncio.Lock()

        # Track deprecations (model_id -> disappeared_at)
        self._deprecated_tracking: dict[str, datetime] = {}

        # Background refresh task
        self._refresh_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Initialize the registry with initial discovery.

        Call this at startup to populate the model cache.
        """
        await self.refresh()

        # Start background refresh task
        self._refresh_task = asyncio.create_task(self._background_refresh())

    async def close(self) -> None:
        """Close the registry and stop background tasks."""
        if self._refresh_task:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

        await self._discovery.close()

    async def refresh(self) -> None:
        """Refresh the model cache from provider APIs.

        This is safe to call concurrently - only one refresh runs at a time.
        """
        async with self._refresh_lock:
            # Get current model IDs for deprecation tracking
            previous_models: set[str] = set()
            for provider_models in self._models.values():
                previous_models.update(provider_models.keys())

            # Discover models from all providers
            discovered = await self._discovery.discover_all()

            # Update cache
            current_models: set[str] = set()
            for provider, models in discovered.items():
                self._models[provider] = {m.id: m for m in models}
                current_models.update(m.id for m in models)

            # Check deprecations
            if previous_models:
                newly_deprecated, self._deprecated_tracking = check_deprecation(
                    current_models,
                    previous_models,
                    self._deprecated_tracking,
                )

                # Mark deprecated models
                for model_id in newly_deprecated:
                    for provider_models in self._models.values():
                        if model_id in provider_models:
                            provider_models[model_id].deprecated = True
                            provider_models[model_id].deprecated_at = datetime.now()

            self._last_refresh = datetime.now()

            # Log models missing pricing
            self._log_missing_pricing()

    async def _background_refresh(self) -> None:
        """Background task to refresh models periodically."""
        interval = timedelta(hours=REFRESH_INTERVAL_HOURS)

        while True:
            try:
                # Wait for refresh interval
                await asyncio.sleep(interval.total_seconds())
                await self.refresh()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("background_refresh_failed", error=str(e))
                # Continue running despite errors

    def _log_missing_pricing(self) -> None:
        """Log models that don't have pricing data."""
        for provider, models in self._models.items():
            for model_id in models:
                if not has_pricing(model_id) and provider != "ollama":
                    log.info(
                        "model_missing_pricing",
                        provider=provider,
                        model=model_id,
                    )

    def get_model(self, model_id: str) -> ModelInfo | None:
        """Get model info by ID.

        Args:
            model_id: The model identifier.

        Returns:
            ModelInfo if found, None otherwise.
        """
        for provider_models in self._models.values():
            if model_id in provider_models:
                return provider_models[model_id]
        return None

    def get_models_by_provider(self, provider: str) -> list[ModelInfo]:
        """Get all models for a provider.

        Args:
            provider: The provider name (openai, anthropic, google, ollama).

        Returns:
            List of ModelInfo for the provider.
        """
        return list(self._models.get(provider, {}).values())

    def get_models_by_tier(self, tier: Tier) -> list[ModelInfo]:
        """Get all models matching a tier.

        Args:
            tier: The tier to filter by.

        Returns:
            List of ModelInfo matching the tier.
        """
        models = []
        for provider_models in self._models.values():
            for model in provider_models.values():
                if model.tier == tier and not model.deprecated:
                    models.append(model)
        return models

    def resolve_tier(
        self,
        provider: str,
        tier: Tier | None = None,
    ) -> ModelInfo | None:
        """Resolve a tier to the best available model for a provider.

        Uses the configured tier preference if tier is not specified.

        Args:
            provider: The provider name.
            tier: Optional tier override.

        Returns:
            Best ModelInfo for the tier, or None if no models available.
        """
        effective_tier = tier or self._provider_tiers.get(provider, Tier.BALANCED)
        provider_models = self._models.get(provider, {})

        # Filter to non-deprecated models in the tier
        candidates = [
            m for m in provider_models.values() if m.tier == effective_tier and not m.deprecated
        ]

        if not candidates:
            # Fall back to any non-deprecated model
            candidates = [m for m in provider_models.values() if not m.deprecated]
            if candidates:
                log.warning(
                    "tier_fallback",
                    provider=provider,
                    requested_tier=effective_tier.value,
                    using=candidates[0].id,
                )

        if not candidates:
            return None

        # Prefer models with known pricing
        with_pricing = [m for m in candidates if has_pricing(m.id)]
        if with_pricing:
            candidates = with_pricing

        # Prefer larger context windows
        candidates.sort(key=lambda m: m.context_window or 0, reverse=True)

        return candidates[0]

    def resolve_model_id(
        self,
        provider: str,
        tier: Tier | None = None,
    ) -> str | None:
        """Resolve a tier to a model ID.

        Convenience method that returns just the model ID.

        Args:
            provider: The provider name.
            tier: Optional tier override.

        Returns:
            Model ID string, or None if no models available.
        """
        model = self.resolve_tier(provider, tier)
        return model.id if model else None

    def record_rate_limit(self, model_id: str) -> None:
        """Record a rate limit hit for a model.

        Args:
            model_id: The model that hit a rate limit.
        """
        model = self.get_model(model_id)
        if model:
            model.last_rate_limit_hit = datetime.now()
            log.warning("rate_limit_hit", model=model_id)

    def is_rate_limited(
        self,
        model_id: str,
        cooldown_seconds: int = 60,
    ) -> bool:
        """Check if a model is currently rate limited.

        Args:
            model_id: The model to check.
            cooldown_seconds: Seconds to wait after a rate limit hit.

        Returns:
            True if the model recently hit a rate limit.
        """
        model = self.get_model(model_id)
        if not model or not model.last_rate_limit_hit:
            return False

        cooldown = timedelta(seconds=cooldown_seconds)
        return datetime.now() - model.last_rate_limit_hit < cooldown

    def get_all_models(self) -> list[ModelInfo]:
        """Get all models from all providers.

        Returns:
            List of all ModelInfo objects.
        """
        models: list[ModelInfo] = []
        for provider_models in self._models.values():
            models.extend(provider_models.values())
        return models

    def get_new_models(
        self,
        since: datetime,
    ) -> list[ModelInfo]:
        """Get models discovered after a timestamp.

        Note: This requires the registry to track discovery timestamps,
        which we don't currently do. For now, returns empty list.
        This is a placeholder for future enhancement.

        Args:
            since: Datetime threshold.

        Returns:
            List of newly discovered models.
        """
        # TODO: Track discovery timestamps per model
        return []

    def get_deprecated_models(self) -> list[ModelInfo]:
        """Get all deprecated models.

        Returns:
            List of deprecated ModelInfo objects.
        """
        models = []
        for provider_models in self._models.values():
            for model in provider_models.values():
                if model.deprecated:
                    models.append(model)
        return models

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics.

        Returns:
            Dict with model counts, last refresh time, etc.
        """
        total = 0
        by_provider: dict[str, int] = {}
        by_tier: dict[str, int] = {t.value: 0 for t in Tier}
        deprecated = 0
        missing_pricing = 0

        for provider, provider_models in self._models.items():
            count = len(provider_models)
            total += count
            by_provider[provider] = count

            for model in provider_models.values():
                by_tier[model.tier.value] += 1
                if model.deprecated:
                    deprecated += 1
                if not has_pricing(model.id) and provider != "ollama":
                    missing_pricing += 1

        return {
            "total_models": total,
            "by_provider": by_provider,
            "by_tier": by_tier,
            "deprecated": deprecated,
            "missing_pricing": missing_pricing,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }
