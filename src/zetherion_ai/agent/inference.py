"""InferenceBroker - Smart multi-provider LLM dispatch.

Central class through which ALL LLM calls flow, enabling smart provider
selection based on task type, provider capabilities, and availability.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import openai
from google import genai  # type: ignore[attr-defined]

from zetherion_ai.agent.providers import (
    CAPABILITY_MATRIX,
    Provider,
    TaskType,
    get_ollama_tier,
    get_provider_for_task,
)
from zetherion_ai.config import get_dynamic, get_secret, get_settings
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
class ProviderIssueState:
    """Tracks a persistent provider issue for reminder throttling."""

    issue_type: str
    first_seen: float
    last_seen: float
    last_notified: float
    fail_count: int = 1
    last_error: str = ""


@dataclass
class ProviderIssueAlert:
    """Structured provider issue alert payload."""

    provider: Provider
    issue_type: str
    error: str
    fail_count: int
    first_seen: float
    last_seen: float
    task_type: str = "unknown"
    model: str = ""


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
    Provider.GROQ: (0.59, 0.79),  # llama-3.3-70b-versatile
}


class InferenceBroker:
    """Central LLM dispatch with smart provider selection.

    All LLM calls in Zetherion AI flow through this broker, which:
    - Selects the optimal provider for each task type
    - Handles fallback when providers are unavailable
    - Tracks costs and usage per provider
    - Tracks Groq-first rollout counters for inbound tasks
    - Prefers Ollama where possible to reduce API costs
    """

    def __init__(
        self,
        model_registry: ModelRegistry | None = None,
        cost_tracker: PersistentCostTracker | None = None,
        provider_issue_handler: (
            Callable[[ProviderIssueAlert], Awaitable[None] | None] | None
        ) = None,
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
        self._provider_issue_state: dict[Provider, ProviderIssueState] = {}
        self._provider_issue_handler = provider_issue_handler
        alerts_enabled_raw = getattr(settings, "provider_issue_alerts_enabled", True)
        self._provider_issue_alerts_enabled = (
            bool(alerts_enabled_raw) if isinstance(alerts_enabled_raw, bool) else True
        )
        cooldown_raw = getattr(settings, "provider_issue_alert_cooldown_seconds", 3600)
        self._provider_issue_alert_cooldown_seconds = 3600
        if isinstance(cooldown_raw, int):
            self._provider_issue_alert_cooldown_seconds = max(60, cooldown_raw)

        # Cost tracking
        self._cost_tracker: dict[Provider, CostTracker] = {p: CostTracker() for p in Provider}
        self._groq_rollout_eligible_requests = 0
        self._groq_rollout_successes = 0
        self._groq_rollout_fallback_uses = 0

        # Ollama configuration (uses generation container, not router container)
        self._ollama_model = settings.ollama_generation_model
        self._ollama_url = settings.ollama_url  # Generation container URL
        self._ollama_tier = get_ollama_tier(self._ollama_model)

        # Initialize provider clients
        self._claude_client: anthropic.AsyncAnthropic | None = None
        self._openai_client: openai.AsyncOpenAI | None = None
        self._gemini_client: genai.Client | None = None
        self._groq_client: openai.AsyncOpenAI | None = None

        # Track current API keys for lazy hot-reload
        self._current_anthropic_key: str | None = None
        self._current_openai_key: str | None = None
        self._current_gemini_key: str | None = None
        self._current_groq_key: str | None = None

        # Claude
        if settings.anthropic_api_key:
            self._current_anthropic_key = settings.anthropic_api_key.get_secret_value()
            self._claude_client = anthropic.AsyncAnthropic(api_key=self._current_anthropic_key)
            self._claude_model = settings.claude_model
            self._available_providers.add(Provider.CLAUDE)

        # OpenAI
        if settings.openai_api_key:
            self._current_openai_key = settings.openai_api_key.get_secret_value()
            self._openai_client = openai.AsyncOpenAI(api_key=self._current_openai_key)
            self._openai_model = settings.openai_model
            self._available_providers.add(Provider.OPENAI)

        # Gemini (always available if we have the API key)
        if settings.gemini_api_key:
            self._current_gemini_key = settings.gemini_api_key.get_secret_value()
            self._gemini_client = genai.Client(api_key=self._current_gemini_key)
            self._gemini_model = settings.router_model
            self._available_providers.add(Provider.GEMINI)

        # Groq (OpenAI-compatible API with different base_url)
        self._groq_base_url = getattr(settings, "groq_base_url", "https://api.groq.com/openai/v1")
        if settings.groq_api_key:
            self._current_groq_key = settings.groq_api_key.get_secret_value()
            self._groq_client = openai.AsyncOpenAI(
                api_key=self._current_groq_key,
                base_url=self._groq_base_url,
            )
            self._groq_model = settings.groq_model
            self._available_providers.add(Provider.GROQ)

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

    def _check_api_key_updates(self) -> None:
        """Lazy-check for rotated API keys via SecretResolver.

        Reads from the in-memory cache (dict lookup) so this is effectively
        free to call on every ``infer()``.
        """
        new_key = get_secret("anthropic_api_key")
        if new_key and new_key != self._current_anthropic_key:
            self._claude_client = anthropic.AsyncAnthropic(api_key=new_key)
            self._current_anthropic_key = new_key
            self._available_providers.add(Provider.CLAUDE)
            self._clear_provider_issue(Provider.CLAUDE)
            log.info("anthropic_client_reinitialized")

        new_key = get_secret("openai_api_key")
        if new_key and new_key != self._current_openai_key:
            self._openai_client = openai.AsyncOpenAI(api_key=new_key)
            self._current_openai_key = new_key
            self._available_providers.add(Provider.OPENAI)
            self._clear_provider_issue(Provider.OPENAI)
            log.info("openai_client_reinitialized")

        new_key = get_secret("gemini_api_key")
        if new_key and new_key != self._current_gemini_key:
            self._gemini_client = genai.Client(api_key=new_key)
            self._current_gemini_key = new_key
            self._available_providers.add(Provider.GEMINI)
            self._clear_provider_issue(Provider.GEMINI)
            log.info("gemini_client_reinitialized")

        new_key = get_secret("groq_api_key")
        if new_key and new_key != self._current_groq_key:
            self._groq_client = openai.AsyncOpenAI(
                api_key=new_key,
                base_url=self._groq_base_url,
            )
            self._current_groq_key = new_key
            self._available_providers.add(Provider.GROQ)
            self._clear_provider_issue(Provider.GROQ)
            log.info("groq_client_reinitialized")

    def set_provider_issue_handler(
        self,
        handler: Callable[[ProviderIssueAlert], Awaitable[None] | None] | None,
    ) -> None:
        """Set/replace the callback used when paid provider issues are detected."""
        self._provider_issue_handler = handler

    @staticmethod
    def _classify_provider_issue(error: str) -> str | None:
        """Classify high-signal paid-provider errors."""
        text = error.lower()

        billing_markers = (
            "credit balance is too low",
            "insufficient credit",
            "insufficient credits",
            "out of credits",
            "insufficient_quota",
            "quota exceeded",
            "billing",
            "payment required",
            "hard limit",
        )
        if any(marker in text for marker in billing_markers):
            return "billing"

        auth_markers = (
            "invalid api key",
            "authentication",
            "unauthorized",
            "forbidden",
            "api key not found",
            "expired api key",
            "permission denied",
            "status code 401",
            "status code 403",
        )
        if any(marker in text for marker in auth_markers):
            return "auth"

        rate_limit_markers = ("rate limit", "too many requests", "status code 429")
        if any(marker in text for marker in rate_limit_markers):
            return "rate_limit"

        return None

    async def _emit_provider_issue_alert(self, alert: ProviderIssueAlert) -> None:
        """Emit a provider issue alert through the configured callback."""
        handler = self._provider_issue_handler
        if handler is None:
            log.warning(
                "provider_issue_alert_unhandled",
                provider=alert.provider.value,
                issue_type=alert.issue_type,
                error=alert.error[:240],
            )
            return

        try:
            maybe = handler(alert)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:
            log.exception(
                "provider_issue_alert_handler_failed",
                provider=alert.provider.value,
                issue_type=alert.issue_type,
            )

    def _clear_provider_issue(self, provider: Provider) -> None:
        """Clear issue state once a provider succeeds again."""
        if provider in self._provider_issue_state:
            del self._provider_issue_state[provider]
            log.info("provider_issue_cleared", provider=provider.value)

    async def _record_provider_issue(
        self,
        provider: Provider,
        error: Exception,
        *,
        task_type: TaskType | None,
        model: str = "",
    ) -> None:
        """Track/alert paid-provider issues (billing/auth/rate-limit) with throttling."""
        if not self._provider_issue_alerts_enabled:
            return
        if provider == Provider.OLLAMA:
            return

        issue_type = self._classify_provider_issue(str(error))
        if issue_type is None:
            return

        now = time.time()
        current = self._provider_issue_state.get(provider)
        if current is None or current.issue_type != issue_type:
            state = ProviderIssueState(
                issue_type=issue_type,
                first_seen=now,
                last_seen=now,
                last_notified=0.0,
                fail_count=1,
                last_error=str(error),
            )
            self._provider_issue_state[provider] = state
        else:
            state = current
            state.last_seen = now
            state.fail_count += 1
            state.last_error = str(error)

        if issue_type in {"billing", "auth"}:
            # Remove hard-failing paid providers from primary selection until a
            # successful probe or key rotation re-enables them.
            self._available_providers.discard(provider)

        should_notify = (
            state.last_notified <= 0
            or (now - state.last_notified) >= self._provider_issue_alert_cooldown_seconds
        )
        if not should_notify:
            return

        state.last_notified = now
        await self._emit_provider_issue_alert(
            ProviderIssueAlert(
                provider=provider,
                issue_type=issue_type,
                error=state.last_error,
                fail_count=state.fail_count,
                first_seen=state.first_seen,
                last_seen=state.last_seen,
                task_type=task_type.value if task_type is not None else "probe",
                model=model,
            )
        )

    async def _probe_provider(self, provider: Provider) -> None:
        """Run a low-cost readiness probe for a paid provider."""
        if provider == Provider.CLAUDE:
            if not self._claude_client:
                raise RuntimeError("Claude client not initialized")
            model = get_dynamic("models", "claude_model", self._claude_model)
            await self._claude_client.messages.create(
                model=model,
                max_tokens=1,
                system="Health check",
                messages=[{"role": "user", "content": "Reply with OK"}],  # type: ignore[arg-type]
            )
            self._available_providers.add(Provider.CLAUDE)
            self._clear_provider_issue(Provider.CLAUDE)
            return

        if provider == Provider.OPENAI:
            if not self._openai_client:
                raise RuntimeError("OpenAI client not initialized")
            model = get_dynamic("models", "openai_model", self._openai_model)
            await self._openai_client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=1,
                temperature=0.0,
            )
            self._available_providers.add(Provider.OPENAI)
            self._clear_provider_issue(Provider.OPENAI)
            return

        if provider == Provider.GROQ:
            if not self._groq_client:
                raise RuntimeError("Groq client not initialized")
            model = get_dynamic("models", "groq_model", self._groq_model)
            await self._groq_client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=1,
                temperature=0.0,
            )
            self._available_providers.add(Provider.GROQ)
            self._clear_provider_issue(Provider.GROQ)
            return

        if provider == Provider.GEMINI:
            if not self._gemini_client:
                raise RuntimeError("Gemini client not initialized")

            model = get_dynamic("models", "router_model", self._gemini_model)

            def _sync_probe() -> None:
                self._gemini_client.models.generate_content(  # type: ignore[union-attr]
                    model=model,
                    contents="Reply with OK",
                    config={"temperature": 0.0, "max_output_tokens": 1},
                )

            await asyncio.to_thread(_sync_probe)
            self._available_providers.add(Provider.GEMINI)
            self._clear_provider_issue(Provider.GEMINI)
            return

        raise ValueError(f"Unsupported probe provider: {provider}")

    async def probe_paid_providers(self) -> dict[str, bool]:
        """Run readiness probes for all configured paid providers."""
        results: dict[str, bool] = {}
        for provider in (Provider.CLAUDE, Provider.OPENAI, Provider.GROQ, Provider.GEMINI):
            client_available = (
                (provider == Provider.CLAUDE and self._claude_client is not None)
                or (provider == Provider.OPENAI and self._openai_client is not None)
                or (provider == Provider.GROQ and self._groq_client is not None)
                or (provider == Provider.GEMINI and self._gemini_client is not None)
            )
            if not client_available:
                continue

            try:
                await self._probe_provider(provider)
                results[provider.value] = True
            except Exception as exc:
                results[provider.value] = False
                await self._record_provider_issue(provider, exc, task_type=None)

        return results

    async def infer(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        forced_provider: Provider | None = None,
        forced_model: str | None = None,
    ) -> InferenceResult:
        """Make an inference call with smart provider selection.

        Args:
            prompt: The user's prompt/message.
            task_type: Type of task for provider selection.
            system_prompt: Optional system prompt.
            messages: Optional conversation history.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            forced_provider: Optional explicit provider override.
            forced_model: Optional explicit model override.

        Returns:
            InferenceResult with the response and metadata.
        """
        # Lazy-check for rotated API keys (dict lookup, ~0 cost)
        self._check_api_key_updates()

        start_time = time.time()

        # Get the optimal provider for this task
        provider = forced_provider or get_provider_for_task(
            task_type=task_type,
            ollama_model=self._ollama_model,
            available_providers=self._available_providers,
        )

        if forced_provider is not None and forced_provider not in self._available_providers:
            raise RuntimeError(f"Forced provider is not available: {forced_provider.value}")

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
                model_override=forced_model,
            )
        except Exception as e:
            await self._record_provider_issue(provider, e, task_type=task_type)
            log.warning(
                "provider_call_failed",
                provider=provider.value,
                error=str(e),
            )
            if forced_provider is not None:
                raise
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

        self._clear_provider_issue(result.provider)

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
        self._track_groq_rollout(task_type=task_type, result_provider=result.provider)

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
            await self._record_provider_issue(provider, e, task_type=task_type)
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

        self._clear_provider_issue(provider)

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
        self._track_groq_rollout(task_type=task_type, result_provider=provider)

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
            case Provider.GROQ:
                async for chunk in self._stream_groq(
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

    async def _stream_groq(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream from Groq API (OpenAI-compatible)."""
        if not self._groq_client:
            raise RuntimeError("Groq client not initialized")

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = get_dynamic("models", "groq_model", self._groq_model)
        stream = await self._groq_client.chat.completions.create(  # type: ignore[call-overload]
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
        model_override: str | None = None,
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
                    prompt,
                    task_type,
                    system_prompt,
                    messages,
                    max_tokens,
                    temperature,
                    model_override=model_override,
                )
            case Provider.OPENAI:
                return await self._call_openai(
                    prompt,
                    task_type,
                    system_prompt,
                    messages,
                    max_tokens,
                    temperature,
                    model_override=model_override,
                )
            case Provider.GROQ:
                return await self._call_groq(
                    prompt,
                    task_type,
                    system_prompt,
                    messages,
                    max_tokens,
                    temperature,
                    model_override=model_override,
                )
            case Provider.GEMINI:
                return await self._call_gemini(
                    prompt,
                    task_type,
                    system_prompt,
                    messages,
                    max_tokens,
                    temperature,
                    model_override=model_override,
                )
            case Provider.OLLAMA:
                return await self._call_ollama(
                    prompt,
                    task_type,
                    system_prompt,
                    messages,
                    max_tokens,
                    temperature,
                    model_override=model_override,
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
        model_override: str | None = None,
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

        model = model_override or get_dynamic("models", "claude_model", self._claude_model)
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
        model_override: str | None = None,
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

        model = model_override or get_dynamic("models", "openai_model", self._openai_model)
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

    async def _call_groq(
        self,
        prompt: str,
        task_type: TaskType,
        system_prompt: str | None,
        messages: list[dict[str, str]] | None,
        max_tokens: int,
        temperature: float,
        model_override: str | None = None,
    ) -> InferenceResult:
        """Call Groq API (OpenAI-compatible)."""
        if not self._groq_client:
            raise RuntimeError("Groq client not initialized")

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            for msg in messages:
                api_messages.append({"role": msg["role"], "content": msg["content"]})
        api_messages.append({"role": "user", "content": prompt})

        model = model_override or get_dynamic("models", "groq_model", self._groq_model)
        response = await self._groq_client.chat.completions.create(
            model=model,
            messages=api_messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return InferenceResult(
            content=response.choices[0].message.content or "",
            provider=Provider.GROQ,
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
        model_override: str | None = None,
    ) -> InferenceResult:
        """Call Gemini API."""
        if not self._gemini_client:
            raise RuntimeError("Gemini client not initialized")

        # Build content
        content = prompt
        if system_prompt:
            content = f"{system_prompt}\n\n{prompt}"

        # Wrap synchronous Gemini call in thread to avoid blocking event loop
        gemini_model = model_override or get_dynamic("models", "router_model", self._gemini_model)

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
        model_override: str | None = None,
    ) -> InferenceResult:
        """Call Ollama API."""
        # Build messages for chat endpoint
        api_messages: list[dict[str, str]] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        if messages:
            api_messages.extend(messages)
        api_messages.append({"role": "user", "content": prompt})

        ollama_model = model_override or get_dynamic(
            "models", "ollama_generation_model", self._ollama_model
        )
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

        # Prefer task-specific fallback ordering from capability matrix.
        fallback_order: list[Provider] = []
        task_config = CAPABILITY_MATRIX.get(task_type)
        if task_config is not None:
            fallback_order.extend(task_config.fallbacks)

        # Ensure we still have deterministic coverage for providers not listed
        # in the task config fallbacks.
        default_order = [
            Provider.GROQ,
            Provider.GEMINI,
            Provider.CLAUDE,
            Provider.OPENAI,
            Provider.OLLAMA,
        ]
        for provider in default_order:
            if provider not in fallback_order:
                fallback_order.append(provider)

        for fallback in fallback_order:
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
                    await self._record_provider_issue(fallback, e, task_type=task_type)
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

    def _track_groq_rollout(self, *, task_type: TaskType, result_provider: Provider) -> None:
        """Track Groq-first rollout counters for inbound task types."""
        config = CAPABILITY_MATRIX.get(task_type)
        if config is None or config.provider != Provider.GROQ:
            return

        self._groq_rollout_eligible_requests += 1
        used_groq = result_provider == Provider.GROQ
        if used_groq:
            self._groq_rollout_successes += 1
        else:
            self._groq_rollout_fallback_uses += 1

        stats = self.get_groq_rollout_stats()
        event = "groq_rollout_success" if used_groq else "groq_rollout_fallback"
        log.info(
            event,
            task_type=task_type.value,
            result_provider=result_provider.value,
            eligible_requests=stats["eligible_requests"],
            groq_successes=stats["groq_successes"],
            fallback_uses=stats["fallback_uses"],
            groq_success_rate=stats["groq_success_rate"],
            fallback_rate=stats["fallback_rate"],
        )

    def get_groq_rollout_stats(self) -> dict[str, Any]:
        """Return Groq-first rollout counters and rates for inbound task types."""
        total = self._groq_rollout_eligible_requests
        success_rate = (self._groq_rollout_successes / total) if total else 0.0
        fallback_rate = (self._groq_rollout_fallback_uses / total) if total else 0.0
        return {
            "eligible_requests": total,
            "groq_successes": self._groq_rollout_successes,
            "fallback_uses": self._groq_rollout_fallback_uses,
            "groq_success_rate": round(success_rate, 6),
            "fallback_rate": round(fallback_rate, 6),
        }

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
            elif provider == Provider.GROQ and self._groq_client:
                await self._groq_client.models.list()
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
