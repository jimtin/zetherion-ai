"""InferenceBroker - Smart multi-provider LLM dispatch.

Central class through which ALL LLM calls flow, enabling smart provider
selection based on task type, provider capabilities, and availability.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import openai
from google import genai  # type: ignore[attr-defined]

from zetherion_ai.agent.providers import (
    Provider,
    TaskType,
    get_ollama_tier,
    get_provider_for_task,
)
from zetherion_ai.config import get_dynamic, get_settings
from zetherion_ai.constants import DEFAULT_MAX_TOKENS, HEALTH_CHECK_TIMEOUT
from zetherion_ai.logging import get_logger
from zetherion_ai.models.pricing import get_cost

if TYPE_CHECKING:
    from zetherion_ai.costs.tracker import CostTracker as PersistentCostTracker
    from zetherion_ai.models.registry import ModelRegistry

log = get_logger("zetherion_ai.agent.inference")


@dataclass
class InferenceResult:
    """Result of an inference call."""

    content: str
    provider: Provider
    task_type: TaskType
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0


@dataclass
class StreamChunk:
    """A single chunk from a streaming inference call."""

    content: str
    done: bool = False
    # Populated only on the final (done=True) chunk:
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class ProviderHealth:
    """Health status of a provider."""

    available: bool
    last_check: float = 0.0
    error_message: str = ""


@dataclass
class CostTracker:
    """Tracks costs per provider."""

    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    by_task_type: dict[str, float] = field(default_factory=dict)


# Approximate cost per 1M tokens (input/output) for each provider
# These are estimates and should be updated periodically
COST_PER_MILLION_TOKENS: dict[Provider, tuple[float, float]] = {
    Provider.CLAUDE: (3.0, 15.0),  # Claude Sonnet 4.5
    Provider.OPENAI: (2.5, 10.0),  # GPT-5.2
    Provider.GEMINI: (0.075, 0.30),  # Gemini 2.5 Flash
    Provider.OLLAMA: (0.0, 0.0),  # Free (local)
}


class InferenceBroker:
    """Central LLM dispatch with smart provider selection.

    All LLM calls in Zetherion AI flow through this broker, which:
    - Selects the optimal provider for each task type
    - Handles fallback when providers are unavailable
    - Tracks costs and usage per provider
    - Prefers Ollama where possible to reduce API costs
    """

    def __init__(
        self,
        model_registry: ModelRegistry | None = None,
        cost_tracker: PersistentCostTracker | None = None,
    ) -> None:
        """Initialize the inference broker.

        Args:
            model_registry: Optional ModelRegistry for dynamic model resolution.
            cost_tracker: Optional CostTracker for persistent cost storage.
        """
        settings = get_settings()

        # Optional integrations (Phase 5B.1)
        self._model_registry = model_registry
        self._persistent_cost_tracker = cost_tracker

        # Provider availability
        self._available_providers: set[Provider] = set()
        self._provider_health: dict[Provider, ProviderHealth] = {}

        # Cost tracking
        self._cost_tracker: dict[Provider, CostTracker] = {p: CostTracker() for p in Provider}

        # Ollama configuration (uses generation container, not router container)
        self._ollama_model = settings.ollama_generation_model
        self._ollama_url = settings.ollama_url  # Generation container URL
        self._ollama_tier = get_ollama_tier(self._ollama_model)

        # Initialize provider clients
        self._claude_client: anthropic.AsyncAnthropic | None = None
        self._openai_client: openai.AsyncOpenAI | None = None
        self._gemini_client: genai.Client | None = None

        # Claude
        if settings.anthropic_api_key:
            self._claude_client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value()
            )
            self._claude_model = settings.claude_model
            self._available_providers.add(Provider.CLAUDE)

        # OpenAI
        if settings.openai_api_key:
            self._openai_client = openai.AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value()
            )
            self._openai_model = settings.openai_model
            self._available_providers.add(Provider.OPENAI)

        # Gemini (always available if we have the API key)
        if settings.gemini_api_key:
            self._gemini_client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
            self._gemini_model = settings.router_model
            self._available_providers.add(Provider.GEMINI)

        # Ollama (check availability asynchronously later)
        # For now, assume available - health check will update this
        self._available_providers.add(Provider.OLLAMA)

        # Shared HTTP client for Ollama and health checks
        self._httpx_client = httpx.AsyncClient(timeout=settings.ollama_timeout)

        log.info(
            "inference_broker_initialized",
            available_providers=[p.value for p in self._available_providers],
            ollama_model=self._ollama_model,
            ollama_tier=self._ollama_tier.value,
        )

    async def infer(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
    ) -> InferenceResult:
        """Make an inference call with smart provider selection.

        Args:
            prompt: The user's prompt/message.
            task_type: Type of task for provider selection.
            system_prompt: Optional system prompt.
            messages: Optional conversation history.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            InferenceResult with the response and metadata.
        """
        start_time = time.time()

        # Get the optimal provider for this task
        provider = get_provider_for_task(
            task_type=task_type,
            ollama_model=self._ollama_model,
            available_providers=self._available_providers,
        )

        log.debug(
            "provider_selected",
            task_type=task_type.value,
            provider=provider.value,
        )

        # Make the inference call
        try:
            result = await self._call_provider(
                provider=provider,
                prompt=prompt,
                task_type=task_type,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            log.warning(
                "provider_call_failed",
                provider=provider.value,
                error=str(e),
            )
            # Try fallbacks
            result = await self._try_fallbacks(
                task_type=task_type,
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                failed_provider=provider,
            )

        # Calculate latency
        result.latency_ms = (time.time() - start_time) * 1000

        # Estimate cost using pricing module
        cost_usd, cost_estimated = self._estimate_cost(
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        result.estimated_cost_usd = cost_usd

        # Track costs
        self._track_cost(result, cost_estimated=cost_estimated)

        log.info(
            "inference_complete",
            task_type=task_type.value,
            provider=result.provider.value,
            model=result.model,
            latency_ms=round(result.latency_ms, 2),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=round(result.estimated_cost_usd, 6),
        )

        return result

    async def infer_stream(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream an inference call, yielding text chunks as they arrive.

        Yields StreamChunk objects with ``done=False`` for each text fragment,
        followed by a single ``done=True`` chunk carrying metadata (model,
        tokens, cost).

        Native streaming is used for Claude, OpenAI, and Ollama.
        Gemini falls back to simulated streaming (full response, then chunked).
        """
        start_time = time.time()

        provider = get_provider_for_task(
            task_type=task_type,
            ollama_model=self._ollama_model,
            available_providers=self._available_providers,
        )

        full_content: list[str] = []
        input_tokens = 0
        output_tokens = 0
        model = ""

        try:
            async for chunk in self._stream_provider(
                provider=provider,
                prompt=prompt,
                task_type=task_type,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                if chunk.done:
                    model = chunk.model
                    input_tokens = chunk.input_tokens
                    output_tokens = chunk.output_tokens
                else:
                    full_content.append(chunk.content)
                    yield chunk
        except Exception as e:
            log.warning(
                "stream_provider_failed",
                provider=provider.value,
                error=str(e),
            )
            # Fall back to non-streaming with simulated chunks
            result = await self._try_fallbacks(
                task_type=task_type,
                prompt=prompt,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                failed_provider=provider,
            )
            model = result.model
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            provider = result.provider
            for word in result.content.split(" "):
                token = word + " "
                full_content.append(token)
                yield StreamChunk(content=token)

        latency_ms = (time.time() - start_time) * 1000
        cost_usd, cost_estimated = self._estimate_cost(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        # Track cost
        content_str = "".join(full_content)
        result_obj = InferenceResult(
            content=content_str,
            provider=provider,
            task_type=task_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost_usd,
        )
        self._track_cost(result_obj, cost_estimated=cost_estimated)

        # Final metadata chunk
        yield StreamChunk(
            content="",
            done=True,
            model=model,
            provider=provider.value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost_usd,
        )

    async def _stream_provider(
        self,
        provider: Provider,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Dispatch to the provider-specific streaming method."""
        match provider:
            case Provider.CLAUDE:
                async for chunk in self._stream_claude(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                ):
                    yield chunk
            case Provider.OPENAI:
                async for chunk in self._stream_openai(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                ):
                    yield chunk
            case Provider.OLLAMA:
                async for chunk in self._stream_ollama(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                ):
                    yield chunk
            case Provider.GEMINI:
                # Gemini doesn't support async streaming — simulate it
                result = await self._call_gemini(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                )
                words = result.content.split(" ")
                for i, word in enumerate(words):
                    token = (" " if i > 0 else "") + word
                    yield StreamChunk(content=token)
                yield StreamChunk(
                    content="",
                    done=True,
                    model=result.model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            case _:
                raise ValueError(f"Unknown provider: {provider}")

    async def _stream_claude(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream from Claude API."""
        if not self._claude_client:
            raise RuntimeError("Claude client not initialized")

        api_messages: list[dict[str, Any]] = []
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = get_dynamic("models", "claude_model", self._claude_model)

        async with self._claude_client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt or "",
            messages=api_messages,  # type: ignore[arg-type]
        ) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(content=text)

            final = await stream.get_final_message()
            yield StreamChunk(
                content="",
                done=True,
                model=model,
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            )

    async def _stream_openai(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream from OpenAI API."""
        if not self._openai_client:
            raise RuntimeError("OpenAI client not initialized")

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = get_dynamic("models", "openai_model", self._openai_model)
        stream = await self._openai_client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
            messages=api_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

        input_tokens = 0
        output_tokens = 0
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(content=chunk.choices[0].delta.content)
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

        yield StreamChunk(
            content="",
            done=True,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _stream_ollama(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream from Ollama API."""
        api_messages: list[dict[str, str]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            api_messages.extend(messages)
        api_messages.append({"role": "user", "content": prompt})

        ollama_model = get_dynamic("models", "ollama_generation_model", self._ollama_model)

        async with self._httpx_client.stream(
            "POST",
            f"{self._ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": api_messages,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
        ) as response:
            response.raise_for_status()
            input_tokens = 0
            output_tokens = 0
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = _json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield StreamChunk(content=content)
                if data.get("done"):
                    input_tokens = data.get("prompt_eval_count", 0)
                    output_tokens = data.get("eval_count", 0)

        yield StreamChunk(
            content="",
            done=True,
            model=ollama_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _call_provider(
        self,
        provider: Provider,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult:
        """Call a specific provider.

        Args:
            provider: The provider to call.
            prompt: The user's prompt.
            task_type: Task type for metadata.
            system_prompt: Optional system prompt.
            messages: Optional conversation history.
            max_tokens: Maximum tokens.
            temperature: Sampling temperature.

        Returns:
            InferenceResult from the provider.
        """
        match provider:
            case Provider.CLAUDE:
                return await self._call_claude(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                )
            case Provider.OPENAI:
                return await self._call_openai(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                )
            case Provider.GEMINI:
                return await self._call_gemini(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                )
            case Provider.OLLAMA:
                return await self._call_ollama(
                    prompt, task_type, system_prompt, messages, max_tokens, temperature
                )
            case _:
                raise ValueError(f"Unknown provider: {provider}")

    async def _call_claude(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult:
        """Call Claude API."""
        if not self._claude_client:
            raise RuntimeError("Claude client not initialized")

        # Build messages
        api_messages: list[dict[str, Any]] = []
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = get_dynamic("models", "claude_model", self._claude_model)
        response = await self._claude_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt or "",
            messages=api_messages,  # type: ignore[arg-type]
        )

        return InferenceResult(
            content=response.content[0].text,  # type: ignore[union-attr]
            provider=Provider.CLAUDE,
            task_type=task_type,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def _call_openai(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult:
        """Call OpenAI API."""
        if not self._openai_client:
            raise RuntimeError("OpenAI client not initialized")

        # Build messages
        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = get_dynamic("models", "openai_model", self._openai_model)
        response = await self._openai_client.chat.completions.create(
            model=model,
            messages=api_messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return InferenceResult(
            content=response.choices[0].message.content or "",
            provider=Provider.OPENAI,
            task_type=task_type,
            model=model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    async def _call_gemini(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult:
        """Call Gemini API."""
        if not self._gemini_client:
            raise RuntimeError("Gemini client not initialized")

        # Build content
        content = prompt
        if system_prompt:
            content = f"{system_prompt}\n\n{prompt}"

        # Wrap synchronous Gemini call in thread to avoid blocking event loop
        gemini_model = get_dynamic("models", "router_model", self._gemini_model)

        def _sync_generate() -> Any:
            return self._gemini_client.models.generate_content(  # type: ignore[union-attr]
                model=gemini_model,
                contents=content,
                config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
            )

        # NOTE: asyncio.to_thread uses the default ThreadPoolExecutor (usually 5 workers).
        # Under high concurrency, Gemini calls may queue behind each other.
        response = await asyncio.to_thread(_sync_generate)

        # Use actual token counts from Gemini if available
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
        # Fall back to heuristic if metadata not available
        if not input_tokens:
            input_tokens = len(content.split()) * 2
        if not output_tokens:
            output_tokens = len((response.text or "").split()) * 2

        return InferenceResult(
            content=response.text or "",
            provider=Provider.GEMINI,
            task_type=task_type,
            model=gemini_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _call_ollama(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> InferenceResult:
        """Call Ollama API."""
        # Build messages for chat endpoint
        api_messages: list[dict[str, str]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            api_messages.extend(messages)
        api_messages.append({"role": "user", "content": prompt})

        ollama_model = get_dynamic("models", "ollama_generation_model", self._ollama_model)
        response = await self._httpx_client.post(
            f"{self._ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": api_messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
        )
        response.raise_for_status()
        data = response.json()

        content = data.get("message", {}).get("content", "")

        return InferenceResult(
            content=content,
            provider=Provider.OLLAMA,
            task_type=task_type,
            model=ollama_model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    async def _try_fallbacks(
        self,
        task_type: TaskType,
        prompt: str,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
        failed_provider: Provider,
    ) -> InferenceResult:
        """Try fallback providers when the primary fails."""
        # Get remaining available providers
        remaining = self._available_providers - {failed_provider}

        for fallback in [Provider.CLAUDE, Provider.OPENAI, Provider.GEMINI, Provider.OLLAMA]:
            if fallback in remaining:
                try:
                    log.info("trying_fallback", provider=fallback.value)
                    return await self._call_provider(
                        provider=fallback,
                        prompt=prompt,
                        task_type=task_type,
                        system_prompt=system_prompt,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                except Exception as e:
                    log.warning("fallback_failed", provider=fallback.value, error=str(e))
                    remaining.discard(fallback)

        # All providers failed
        raise RuntimeError("All providers failed")

    def _estimate_cost(
        self,
        provider: Provider,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> tuple[float, bool]:
        """Estimate the cost of an API call.

        Uses the pricing module for accurate cost calculation with fallback.

        Returns:
            Tuple of (cost_usd, is_estimated).
        """
        cost_result = get_cost(model, input_tokens, output_tokens)
        return cost_result.cost_usd, cost_result.estimated

    def _track_cost(self, result: InferenceResult, cost_estimated: bool = False) -> None:
        """Track costs for a completed inference.

        Args:
            result: The inference result to track.
            cost_estimated: Whether the cost was estimated (pricing unknown).
        """
        # In-memory tracking
        tracker = self._cost_tracker[result.provider]
        tracker.total_calls += 1
        tracker.total_input_tokens += result.input_tokens
        tracker.total_output_tokens += result.output_tokens
        tracker.total_cost_usd += result.estimated_cost_usd

        task_key = result.task_type.value
        tracker.by_task_type[task_key] = (
            tracker.by_task_type.get(task_key, 0.0) + result.estimated_cost_usd
        )

        # Persistent tracking via CostTracker (Phase 5B.1)
        if self._persistent_cost_tracker:
            self._persistent_cost_tracker.record(
                provider=result.provider.value,
                model=result.model,
                tokens_input=result.input_tokens,
                tokens_output=result.output_tokens,
                cost_usd=result.estimated_cost_usd,
                cost_estimated=cost_estimated,
                task_type=task_key,
                latency_ms=int(result.latency_ms) if result.latency_ms else None,
            )

    def get_cost_summary(self) -> dict[str, Any]:
        """Get a summary of costs by provider.

        Returns:
            Dictionary with cost breakdown per provider.
        """
        summary: dict[str, Any] = {}
        total_cost = 0.0

        for provider, tracker in self._cost_tracker.items():
            if tracker.total_calls > 0:
                summary[provider.value] = {
                    "calls": tracker.total_calls,
                    "input_tokens": tracker.total_input_tokens,
                    "output_tokens": tracker.total_output_tokens,
                    "cost_usd": round(tracker.total_cost_usd, 4),
                    "by_task_type": {k: round(v, 6) for k, v in tracker.by_task_type.items()},
                }
                total_cost += tracker.total_cost_usd

        summary["total_cost_usd"] = round(total_cost, 4)
        return summary

    async def health_check(self, provider: Provider) -> bool:
        """Check if a provider is healthy.

        Args:
            provider: The provider to check.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            if provider == Provider.OLLAMA:
                response = await self._httpx_client.get(
                    f"{self._ollama_url}/api/tags", timeout=HEALTH_CHECK_TIMEOUT
                )
                return response.status_code == 200
            elif provider == Provider.GEMINI and self._gemini_client:

                def _sync_health_check() -> bool:
                    list(self._gemini_client.models.list())  # type: ignore[union-attr]
                    return True

                return await asyncio.to_thread(_sync_health_check)
            elif provider == Provider.CLAUDE and self._claude_client:
                return True  # Client initialized = available (no free list endpoint)
            elif provider == Provider.OPENAI and self._openai_client:
                await self._openai_client.models.list()
                return True
        except Exception as e:
            log.warning("health_check_failed", provider=provider.value, error=str(e))
        return False

    async def close(self) -> None:
        """Close shared HTTP clients."""
        await self._httpx_client.aclose()

    @property
    def available_providers(self) -> set[Provider]:
        """Get the set of available providers."""
        return self._available_providers.copy()
