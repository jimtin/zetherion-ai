"""Model discovery from provider APIs.

Queries each provider's API to discover available models. Provider APIs
do NOT return pricing, so discovered models are enriched with pricing
data from the pricing module.

Discovery runs at startup and every 24 hours to catch new models.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

from zetherion_ai.logging import get_logger
from zetherion_ai.models.tiers import ModelInfo, infer_tier

log = get_logger("zetherion_ai.models.discovery")

# Grace period before marking a model as deprecated
DEPRECATION_GRACE_DAYS = 7


class DiscoveryError(Exception):
    """Error during model discovery."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.message = message
        super().__init__(f"{provider}: {message}")


class ModelDiscovery:
    """Discovers available models from provider APIs.

    Each provider's discovery is isolated - a failure in one provider
    does not affect others.
    """

    def __init__(
        self,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        google_api_key: str | None = None,
        ollama_host: str = "http://localhost:11434",
    ):
        """Initialize the model discovery.

        Args:
            openai_api_key: OpenAI API key (optional).
            anthropic_api_key: Anthropic API key (optional).
            google_api_key: Google/Gemini API key (optional).
            ollama_host: Ollama API host URL.
        """
        self._openai_key = openai_api_key
        self._anthropic_key = anthropic_api_key
        self._google_key = google_api_key
        self._ollama_host = ollama_host.rstrip("/")
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def discover_all(self) -> dict[str, list[ModelInfo]]:
        """Discover models from all configured providers.

        Returns a dict mapping provider name to list of discovered models.
        Failed providers return empty lists (errors are logged but don't
        propagate).
        """
        results: dict[str, list[ModelInfo]] = {}

        # Run all discoveries concurrently
        tasks = []
        providers = []

        if self._openai_key:
            tasks.append(self._discover_openai())
            providers.append("openai")

        if self._anthropic_key:
            tasks.append(self._discover_anthropic())
            providers.append("anthropic")

        if self._google_key:
            tasks.append(self._discover_google())
            providers.append("google")

        # Ollama doesn't need an API key
        tasks.append(self._discover_ollama())
        providers.append("ollama")

        # Gather results, catching individual failures
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for provider, result in zip(providers, task_results, strict=False):
            if isinstance(result, BaseException):
                log.warning(
                    "discovery_failed",
                    provider=provider,
                    error=str(result),
                )
                results[provider] = []
            else:
                results[provider] = result
                log.info(
                    "discovery_complete",
                    provider=provider,
                    model_count=len(result),
                )

        return results

    async def _discover_openai(self) -> list[ModelInfo]:
        """Discover models from OpenAI API."""
        if not self._openai_key:
            return []

        client = await self._get_client()
        try:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {self._openai_key}"},
            )
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")

                # Skip non-chat models
                if not self._is_chat_model_openai(model_id):
                    continue

                # Extract metadata
                metadata = self._extract_openai_metadata(model_data)

                models.append(
                    ModelInfo(
                        id=model_id,
                        provider="openai",
                        tier=infer_tier(model_id, metadata),
                        display_name=model_id,
                        context_window=metadata.get("context_window"),
                    )
                )

            return models

        except httpx.HTTPStatusError as e:
            raise DiscoveryError("openai", f"HTTP {e.response.status_code}") from e
        except Exception as e:
            raise DiscoveryError("openai", str(e)) from e

    async def _discover_anthropic(self) -> list[ModelInfo]:
        """Discover models from Anthropic API."""
        if not self._anthropic_key:
            return []

        client = await self._get_client()
        try:
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("data", []):
                model_id = model_data.get("id", "")
                display_name = model_data.get("display_name", model_id)

                # Extract metadata
                metadata = self._extract_anthropic_metadata(model_data)

                models.append(
                    ModelInfo(
                        id=model_id,
                        provider="anthropic",
                        tier=infer_tier(model_id, metadata),
                        display_name=display_name,
                        context_window=metadata.get("context_window"),
                    )
                )

            return models

        except httpx.HTTPStatusError as e:
            raise DiscoveryError("anthropic", f"HTTP {e.response.status_code}") from e
        except Exception as e:
            raise DiscoveryError("anthropic", str(e)) from e

    async def _discover_google(self) -> list[ModelInfo]:
        """Discover models from Google Generative AI API."""
        if not self._google_key:
            return []

        client = await self._get_client()
        try:
            response = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={self._google_key}",
            )
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("models", []):
                # Model name format: "models/gemini-1.5-pro"
                full_name = model_data.get("name", "")
                model_id = full_name.replace("models/", "")

                # Skip non-generative models
                if not self._is_generative_model_google(model_data):
                    continue

                # Extract metadata
                metadata = self._extract_google_metadata(model_data)

                models.append(
                    ModelInfo(
                        id=model_id,
                        provider="google",
                        tier=infer_tier(model_id, metadata),
                        display_name=model_data.get("displayName", model_id),
                        context_window=metadata.get("context_window"),
                    )
                )

            return models

        except httpx.HTTPStatusError as e:
            raise DiscoveryError("google", f"HTTP {e.response.status_code}") from e
        except Exception as e:
            raise DiscoveryError("google", str(e)) from e

    async def _discover_ollama(self) -> list[ModelInfo]:
        """Discover models from local Ollama instance."""
        client = await self._get_client()
        try:
            response = await client.get(f"{self._ollama_host}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("models", []):
                model_id = model_data.get("name", "")

                # Extract metadata
                metadata = self._extract_ollama_metadata(model_data)

                models.append(
                    ModelInfo(
                        id=model_id,
                        provider="ollama",
                        tier=infer_tier(model_id, metadata),
                        display_name=model_id,
                        context_window=metadata.get("context_window"),
                    )
                )

            return models

        except httpx.ConnectError:
            # Ollama not running is expected in some environments
            log.debug("ollama_not_available", host=self._ollama_host)
            return []
        except httpx.HTTPStatusError as e:
            raise DiscoveryError("ollama", f"HTTP {e.response.status_code}") from e
        except Exception as e:
            raise DiscoveryError("ollama", str(e)) from e

    def _is_chat_model_openai(self, model_id: str) -> bool:
        """Check if an OpenAI model is a chat model."""
        # Include GPT, O1 series
        chat_prefixes = ("gpt-", "o1", "o3")
        excluded = ("instruct", "embedding", "tts", "whisper", "dall-e")

        id_lower = model_id.lower()
        if any(id_lower.startswith(p) for p in chat_prefixes):
            return not any(e in id_lower for e in excluded)
        return False

    def _is_generative_model_google(self, model_data: dict[str, Any]) -> bool:
        """Check if a Google model supports text generation."""
        methods = model_data.get("supportedGenerationMethods", [])
        return "generateContent" in methods

    def _extract_openai_metadata(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Extract metadata from OpenAI model data."""
        # OpenAI doesn't return context window in the models endpoint
        # We use known values based on model ID patterns
        model_id = model_data.get("id", "").lower()

        context_window = None
        if "gpt-4o" in model_id or "gpt-4.1" in model_id or "gpt-4-turbo" in model_id:
            context_window = 128_000
        elif "gpt-4" in model_id:
            context_window = 8_192
        elif "gpt-3.5-turbo" in model_id:
            context_window = 16_385
        elif "o1" in model_id or "o3" in model_id:
            context_window = 128_000

        return {"context_window": context_window}

    def _extract_anthropic_metadata(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Extract metadata from Anthropic model data."""
        # Anthropic returns max_tokens in the model data
        return {"context_window": model_data.get("max_tokens")}

    def _extract_google_metadata(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Extract metadata from Google model data."""
        return {"context_window": model_data.get("inputTokenLimit")}

    def _extract_ollama_metadata(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Extract metadata from Ollama model data."""
        # Ollama returns details with parameter count
        details = model_data.get("details", {})
        param_size = details.get("parameter_size", "")

        # Estimate context window from model family
        context_window = 8_192  # Default
        model_id = model_data.get("name", "").lower()
        if "llama3" in model_id:
            context_window = 128_000
        elif "qwen" in model_id or "mistral" in model_id or "mixtral" in model_id:
            context_window = 32_768

        return {
            "context_window": context_window,
            "parameter_size": param_size,
        }


def check_deprecation(
    current_models: set[str],
    previous_models: set[str],
    previous_deprecated: dict[str, datetime],
) -> tuple[set[str], dict[str, datetime]]:
    """Check for deprecated models with grace period.

    Models that disappear from the API get a 7-day grace period before
    being marked as deprecated. This handles temporary API issues.

    Args:
        current_models: Set of model IDs from current discovery.
        previous_models: Set of model IDs from previous discovery.
        previous_deprecated: Dict mapping model ID to deprecation timestamp.

    Returns:
        Tuple of (newly_deprecated, updated_deprecated_dict).
    """
    now = datetime.now()
    grace_period = timedelta(days=DEPRECATION_GRACE_DAYS)

    # Models that disappeared
    disappeared = previous_models - current_models

    # Update deprecated dict
    updated_deprecated = dict(previous_deprecated)
    newly_deprecated: set[str] = set()

    for model_id in disappeared:
        if model_id not in updated_deprecated:
            # Start grace period
            updated_deprecated[model_id] = now
            log.info(
                "model_disappeared",
                model=model_id,
                grace_period_days=DEPRECATION_GRACE_DAYS,
            )
        elif now - updated_deprecated[model_id] >= grace_period:
            # Grace period expired
            newly_deprecated.add(model_id)
            log.warning("model_deprecated", model=model_id)

    # Models that reappeared (within grace period)
    reappeared = current_models & set(updated_deprecated.keys())
    for model_id in reappeared:
        del updated_deprecated[model_id]
        log.info("model_reappeared", model=model_id)

    return newly_deprecated, updated_deprecated
