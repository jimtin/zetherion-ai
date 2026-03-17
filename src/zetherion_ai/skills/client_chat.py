"""Client chat skill — runtime conversation handler for tenant chatbots.

Wraps the InferenceBroker with tenant-specific configuration and
L1a critical signal detection.  Used by the public API chat endpoints
to generate AI responses for client website chatbots.

Responsibilities:
    1. L1a inline detection — urgency, safety, escalation (regex, fast)
    2. System prompt construction from tenant config
    3. LLM inference via InferenceBroker
    4. Returns response plus any detected signals
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet
from zetherion_ai.trust.scope import (
    DataScope,
    ScopedResource,
    TrustDomain,
    assemble_prompt_fragments,
    prompt_fragment,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from zetherion_ai.agent.inference import InferenceBroker, StreamChunk

log = get_logger("zetherion_ai.skills.client_chat")

# ---------------------------------------------------------------------------
# Default tenant system prompt
# ---------------------------------------------------------------------------

_DEFAULT_TENANT_PROMPT = """\
You are a helpful assistant for {tenant_name}.

## Instructions
- Be helpful, accurate, and concise.
- Answer questions related to {tenant_name} and their services.
- If you don't know something, say so honestly.
- Keep responses focused and professional.
"""

# ---------------------------------------------------------------------------
# L1a — Critical signal detection (inline, fast, regex-based)
# ---------------------------------------------------------------------------

# Patterns are compiled once at import time for performance.

_URGENCY_PATTERNS = re.compile(
    r"(?i)\b("
    r"urgent|emergency|asap|immediately|right\s+now|help\s+me|"
    r"desperate|critical|time\s+sensitive|flooding|leak(ing)?|"
    r"burst\s+pipe|fire|smoke|gas\s+leak|broken\s+down|"
    r"no\s+(hot\s+)?water|no\s+heat(ing)?|power\s+out"
    r")\b"
)

_SAFETY_PATTERNS = re.compile(
    r"(?i)\b("
    r"kill\s+(my)?self|suicide|self[- ]?harm|want\s+to\s+die|"
    r"end\s+(my\s+)?life|harm\s+(my)?self|"
    r"threat(en)?|attack|weapon"
    r")\b"
)

_ESCALATION_PATTERNS = re.compile(
    r"(?i)\b("
    r"speak\s+to\s+(a\s+)?(?:real\s+)?(?:person|human|manager|someone)|"
    r"real\s+person|human\s+agent|"
    r"not\s+a\s+(?:bot|robot|computer)|"
    r"talk\s+to\s+(?:a\s+)?(?:human|person|manager|someone)|"
    r"get\s+me\s+(?:a\s+)?(?:human|person|manager)"
    r")\b"
)

_RETURNING_CUSTOMER_PATTERNS = re.compile(
    r"(?i)\b("
    r"last\s+time|before|previously|again|follow[- ]?up|"
    r"my\s+previous|we\s+(?:spoke|talked|discussed)|came\s+back"
    r")\b"
)

_PROVIDER_ALIASES = {
    "anthropic": "claude",
}
_TASK_TYPE_ALIASES = {
    "chat": "conversation",
    "planning": "complex_reasoning",
}
_DEFAULT_SELECTION_MODE = "auto"
_ALLOWED_SELECTION_MODES = {"auto", "prefer", "lock"}


def _normalize_provider_override(value: str | None) -> Provider | None:
    if value is None:
        return None
    normalized = _PROVIDER_ALIASES.get(value.strip().lower(), value.strip().lower())
    if not normalized:
        return None
    try:
        return Provider(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported provider `{value}`") from exc


def _normalize_task_type(value: str | None) -> TaskType:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return TaskType.CONVERSATION
    normalized = _TASK_TYPE_ALIASES.get(normalized, normalized)
    try:
        return TaskType(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported task_type `{value}`") from exc


def _provider_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value) or "").strip() or None


def _resolve_model_selection(
    *,
    selection_mode: str | None,
    provider: str | None,
    model: str | None,
    task_type: str | None,
    agent_profile_id: str | None,
    fallback_allowed: bool | None,
) -> dict[str, Any]:
    normalized_mode = str(selection_mode or _DEFAULT_SELECTION_MODE).strip().lower()
    if normalized_mode not in _ALLOWED_SELECTION_MODES:
        raise ValueError(
            f"selection_mode must be one of {sorted(_ALLOWED_SELECTION_MODES)}"
        )

    forced_provider = _normalize_provider_override(provider)
    forced_model = str(model or "").strip() or None
    normalized_task_type = _normalize_task_type(task_type)
    normalized_agent_profile_id = str(agent_profile_id or "").strip() or None

    if forced_model and forced_provider is None:
        settings = get_settings()
        provider_defaults = {
            getattr(settings, "groq_model", ""): Provider.GROQ,
            getattr(settings, "openai_model", ""): Provider.OPENAI,
            getattr(settings, "claude_model", ""): Provider.CLAUDE,
        }
        forced_provider = provider_defaults.get(forced_model)
        if forced_provider is None:
            raise ValueError(
                "provider is required when model is not mapped to a known runtime default"
            )

    if normalized_mode in {"prefer", "lock"} and forced_provider is None and forced_model is None:
        raise ValueError("provider or model is required when selection_mode is prefer or lock")

    allow_forced_fallback = (
        bool(fallback_allowed)
        if fallback_allowed is not None
        else normalized_mode == "prefer"
    )
    if normalized_mode == "lock":
        allow_forced_fallback = False

    return {
        "forced_provider": forced_provider,
        "forced_model": forced_model,
        "task_type": normalized_task_type,
        "selection": {
            "selection_mode": normalized_mode,
            "requested_provider": forced_provider.value if forced_provider is not None else None,
            "requested_model": forced_model,
            "task_type": normalized_task_type.value,
            "agent_profile_id": normalized_agent_profile_id,
            "fallback_allowed": allow_forced_fallback,
        },
        "allow_forced_fallback": allow_forced_fallback,
    }


@dataclass
class L1aSignals:
    """Lightweight critical signals detected from a user message."""

    is_urgent: bool = False
    is_safety_concern: bool = False
    needs_escalation: bool = False
    is_returning: bool = False
    matched_patterns: list[str] = field(default_factory=list)

    @property
    def has_signals(self) -> bool:
        return self.is_urgent or self.is_safety_concern or self.needs_escalation

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_urgent": self.is_urgent,
            "is_safety_concern": self.is_safety_concern,
            "needs_escalation": self.needs_escalation,
            "is_returning": self.is_returning,
            "matched_patterns": self.matched_patterns,
        }


def detect_signals(message: str) -> L1aSignals:
    """Run L1a critical signal detection on a message.

    This is intentionally fast (regex-only, no LLM) so it can run
    inline before the bot generates a response.
    """
    signals = L1aSignals()

    urgency = _URGENCY_PATTERNS.search(message)
    if urgency:
        signals.is_urgent = True
        signals.matched_patterns.append(f"urgency:{urgency.group()}")

    safety = _SAFETY_PATTERNS.search(message)
    if safety:
        signals.is_safety_concern = True
        signals.matched_patterns.append(f"safety:{safety.group()}")

    escalation = _ESCALATION_PATTERNS.search(message)
    if escalation:
        signals.needs_escalation = True
        signals.matched_patterns.append(f"escalation:{escalation.group()}")

    returning = _RETURNING_CUSTOMER_PATTERNS.search(message)
    if returning:
        signals.is_returning = True
        signals.matched_patterns.append(f"returning:{returning.group()}")

    return signals


# ---------------------------------------------------------------------------
# System prompt construction
# ---------------------------------------------------------------------------


def build_system_prompt(
    tenant: dict[str, Any],
    signals: L1aSignals | None = None,
    *,
    context_notes: str | None = None,
) -> str:
    """Build a system prompt from tenant config, adjusted for L1a signals."""
    config = tenant.get("config", {}) or {}
    custom_prompt = config.get("system_prompt")
    base_prompt = custom_prompt or _DEFAULT_TENANT_PROMPT.format(
        tenant_name=tenant.get("name", "the company"),
    )
    resource = ScopedResource(
        resource_id=str(tenant.get("tenant_id") or tenant.get("name") or "tenant_chat"),
        resource_type="tenant_chat",
        trust_domain=TrustDomain.TENANT_RAW,
    )
    fragments = [
        prompt_fragment(
            base_prompt,
            scope=DataScope.TENANT_RAW,
            source="zetherion_ai.skills.client_chat.base_prompt",
        )
    ]

    if signals is not None and signals.has_signals:
        if signals.is_safety_concern:
            fragments.append(
                prompt_fragment(
                    "IMPORTANT: The user may be expressing distress. "
                    "Respond with empathy and provide appropriate crisis resources. "
                    "Suggest contacting emergency services (999/112/911) if immediate danger.",
                    scope=DataScope.CONTROL_PLANE,
                    source="zetherion_ai.skills.client_chat.safety_addendum",
                )
            )
        if signals.is_urgent:
            fragments.append(
                prompt_fragment(
                    "The user's message appears urgent. "
                    "Acknowledge the urgency and prioritise practical help.",
                    scope=DataScope.CONTROL_PLANE,
                    source="zetherion_ai.skills.client_chat.urgency_addendum",
                )
            )
        if signals.needs_escalation:
            fragments.append(
                prompt_fragment(
                    "The user wants to speak to a real person. "
                    "Acknowledge this and provide contact information if available.",
                    scope=DataScope.CONTROL_PLANE,
                    source="zetherion_ai.skills.client_chat.escalation_addendum",
                )
            )

    if context_notes:
        fragments.append(
            prompt_fragment(
                "Use this tenant-scoped conversation context when it is relevant:\n"
                + context_notes,
                scope=DataScope.TENANT_RAW,
                source="zetherion_ai.skills.client_chat.conversation_context",
            )
        )

    return assemble_prompt_fragments(
        fragments,
        purpose="skills.client_chat.system_prompt",
        resource=resource,
    )


# ---------------------------------------------------------------------------
# Chat response dataclass
# ---------------------------------------------------------------------------


@dataclass
class ChatResponse:
    """Result of a client_chat invocation."""

    content: str
    model: str | None = None
    provider: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    signals: L1aSignals = field(default_factory=L1aSignals)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"content": self.content}
        if self.model:
            d["model"] = self.model
        if self.provider:
            d["provider"] = self.provider
        if self.usage:
            d["usage"] = self.usage
        if self.selection:
            d["selection"] = self.selection
        if self.signals.has_signals:
            d["signals"] = self.signals.to_dict()
        return d


# ---------------------------------------------------------------------------
# ClientChatSkill
# ---------------------------------------------------------------------------

# Maximum conversation turns to include as LLM context.
_CONTEXT_WINDOW = 20


class ClientChatSkill(Skill):
    """Runtime conversation handler for tenant chatbots.

    Can be used either through the skill registry (``handle()``) or
    directly via ``generate_response()`` / ``generate_stream()``.
    """

    def __init__(
        self,
        inference_broker: InferenceBroker | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._broker = inference_broker

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="client_chat",
            description="Runtime conversation handler for tenant chatbots",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                }
            ),
            intents=["client_chat"],
        )

    async def initialize(self) -> bool:
        if self._broker is None:
            log.warning("client_chat_no_broker")
        log.info("client_chat_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a ``client_chat`` intent via the skill registry.

        Expected context:
            tenant: dict — the tenant record
            message: str — user's message
            history: list[dict] — conversation history [{role, content}, ...]
        """
        ctx = request.context
        tenant = ctx.get("tenant", {})
        message = ctx.get("message", request.message)
        history = ctx.get("history", [])
        context_notes = ctx.get("context_notes")

        if not message:
            return SkillResponse.error_response(request.id, "Message is required.")

        result = await self.generate_response(
            tenant=tenant,
            message=message,
            history=history,
            context_notes=context_notes,
        )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=result.content,
            data=result.to_dict(),
        )

    # ------------------------------------------------------------------
    # Direct-call API (used by chat routes)
    # ------------------------------------------------------------------

    async def generate_response(
        self,
        *,
        tenant: dict[str, Any],
        message: str,
        history: list[dict[str, str]] | None = None,
        context_notes: str | None = None,
        selection_mode: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        task_type: str | None = None,
        agent_profile_id: str | None = None,
        fallback_allowed: bool | None = None,
    ) -> ChatResponse:
        """Generate a chat response for a tenant's end-user.

        Steps:
            1. L1a signal detection (inline)
            2. Build system prompt (with signal adjustments)
            3. Call InferenceBroker.infer()
            4. Return ChatResponse

        Args:
            tenant: The tenant record dict.
            message: The user's message.
            history: Prior conversation messages [{role, content}, ...].

        Returns:
            ChatResponse with content, model, and signals.
        """
        signals = detect_signals(message)

        if signals.has_signals:
            log.info(
                "l1a_signals_detected",
                tenant_id=str(tenant.get("tenant_id", "")),
                signals=signals.to_dict(),
            )

        if self._broker is None:
            return ChatResponse(
                content="Chat is not configured. Please contact the administrator.",
                signals=signals,
            )

        system_prompt = build_system_prompt(tenant, signals, context_notes=context_notes)
        selection = _resolve_model_selection(
            selection_mode=selection_mode,
            provider=provider,
            model=model,
            task_type=task_type,
            agent_profile_id=agent_profile_id,
            fallback_allowed=fallback_allowed,
        )

        result = await self._broker.infer(
            prompt=message,
            task_type=selection["task_type"],
            system_prompt=system_prompt,
            messages=history or [],
            forced_provider=selection["forced_provider"],
            forced_model=selection["forced_model"],
            allow_forced_fallback=selection["allow_forced_fallback"],
        )

        provider_value = _provider_value(getattr(result, "provider", None))
        return ChatResponse(
            content=result.content,
            model=result.model,
            provider=provider_value,
            usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
                "estimated_cost_usd": result.estimated_cost_usd,
                "model": result.model,
                "provider": provider_value,
            },
            selection={
                **selection["selection"],
                "effective_provider": provider_value,
                "effective_model": result.model,
            },
            signals=signals,
        )

    async def generate_stream(
        self,
        *,
        tenant: dict[str, Any],
        message: str,
        history: list[dict[str, str]] | None = None,
        context_notes: str | None = None,
        selection_mode: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        task_type: str | None = None,
        agent_profile_id: str | None = None,
        fallback_allowed: bool | None = None,
    ) -> tuple[L1aSignals, AsyncGenerator[StreamChunk, None]]:
        """Stream a chat response for a tenant's end-user.

        Returns a tuple of (L1a signals, async generator of StreamChunks).
        Signals are returned immediately so the caller can log them
        before iterating over the stream.

        Raises:
            RuntimeError: If no InferenceBroker is configured.
        """
        signals = detect_signals(message)

        if signals.has_signals:
            log.info(
                "l1a_signals_detected",
                tenant_id=str(tenant.get("tenant_id", "")),
                signals=signals.to_dict(),
            )

        if self._broker is None:
            raise RuntimeError("No InferenceBroker configured for streaming.")

        system_prompt = build_system_prompt(tenant, signals, context_notes=context_notes)
        selection = _resolve_model_selection(
            selection_mode=selection_mode,
            provider=provider,
            model=model,
            task_type=task_type,
            agent_profile_id=agent_profile_id,
            fallback_allowed=fallback_allowed,
        )

        stream = self._broker.infer_stream(
            prompt=message,
            task_type=selection["task_type"],
            system_prompt=system_prompt,
            messages=history or [],
            forced_provider=selection["forced_provider"],
            forced_model=selection["forced_model"],
            allow_forced_fallback=selection["allow_forced_fallback"],
        )

        return signals, stream
