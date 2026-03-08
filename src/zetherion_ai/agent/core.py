"""Agent core - LLM interaction and response generation with routing."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zetherion_ai.agent.docs_knowledge import DocsKnowledgeBase
from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.prompts import SYSTEM_PROMPT
from zetherion_ai.agent.providers import TaskType
from zetherion_ai.agent.router import MessageIntent, RoutingDecision
from zetherion_ai.agent.router_factory import create_router_sync
from zetherion_ai.config import get_dynamic, get_settings
from zetherion_ai.constants import CONTEXT_HISTORY_LIMIT, MEMORY_SCORE_THRESHOLD
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import LONG_TERM_MEMORY_COLLECTION, QdrantMemory
from zetherion_ai.skills.base import SkillRequest, SkillResponse
from zetherion_ai.skills.client import SkillsClient, SkillsClientError
from zetherion_ai.trust.scope import (
    DataScope,
    ScopedPrincipal,
    TrustDomain,
    assemble_prompt_fragments,
    prompt_fragment,
)
from zetherion_ai.utils import timed_operation

log = get_logger("zetherion_ai.agent.core")

# ---------------------------------------------------------------------------
# Keyword sets for task-type classification (Phase 3.2)
# ---------------------------------------------------------------------------
CODE_KEYWORDS = frozenset(
    {
        "code",
        "script",
        "function",
        "class",
        "debug",
        "fix",
        "implement",
        "python",
        "javascript",
        "typescript",
        "java",
        "rust",
        "go",
        "programming",
        "algorithm",
        "api",
        "database",
        "sql",
    }
)
CODE_REVIEW_KEYWORDS = frozenset({"review", "audit", "check"})
CODE_DEBUG_KEYWORDS = frozenset({"debug", "fix", "error", "bug"})
MATH_KEYWORDS = frozenset(
    {
        "math",
        "calculate",
        "equation",
        "prove",
        "theorem",
        "logic",
        "reasoning",
        "analyze",
        "why",
        "how does",
        "explain in detail",
    }
)
MATH_SPECIFIC_KEYWORDS = frozenset({"math", "calculate", "equation"})
CREATIVE_KEYWORDS = frozenset(
    {
        "write",
        "story",
        "poem",
        "creative",
        "imagine",
        "fiction",
        "narrative",
        "character",
        "plot",
    }
)
SUMMARIZATION_KEYWORDS = frozenset({"summarize", "summary", "tldr", "condense"})
TASK_LIST_DISPLAY_LIMIT = 10
USER_KNOWLEDGE_FACT_DISPLAY_LIMIT = 10
USER_KNOWLEDGE_QUERY_HINTS = frozenset(
    {
        "what do you know about me",
        "what have you learned about me",
        "what do you remember about me",
        "summary of what you know about me",
        "tell me what you know about me",
    }
)
OWNER_ROUTING_GUARDRAIL_CONFIDENCE = 0.9
OWNER_ROUTING_TRACE_HISTORY_LIMIT = 6
OWNER_REPAIR_REQUEST_HINTS = frozenset(
    {
        "what happened with that response",
        "what happened with that answer",
        "why did you respond like that",
        "why did you answer like that",
        "explain that response",
        "explain that answer",
    }
)
OWNER_CONVERSATION_REFERENCE_MARKERS = frozenset(
    {
        "that",
        "this",
        "it",
        "response",
        "answer",
        "again",
        "instead",
        "meant",
        "wrong",
        "earlier",
        "before",
        "follow up",
        "follow-up",
        "clarify",
        "rephrase",
    }
)
OWNER_DEV_WATCHER_HINTS = frozenset(
    {
        "dev",
        "code",
        "coding",
        "repo",
        "repository",
        "pull request",
        "pr",
        "commit",
        "branch",
        "deployment",
        "deploy",
        "release",
        "ci",
        "pipeline",
        "build",
        "journal",
        "milestone",
        "work on next",
        "what should i work on",
        "what did i code",
        "what did i build",
    }
)
OWNER_MEMORY_RECALL_HINTS = frozenset(
    {
        "remember",
        "remember about me",
        "what do you know about me",
        "what have you learned about me",
        "what do you remember about me",
        "what's my",
        "what is my",
        "what are my",
        "favorite",
        "preferences",
        "birthday",
        "where do i live",
        "where am i from",
        "what did we talk about",
        "do you remember",
    }
)


@dataclass
class OwnerRoutingGuardrailResult:
    """Result of the owner-only routing guardrail pass."""

    routing: RoutingDecision
    routing_trace: dict[str, Any]
    early_response: str | None = None
    should_store_exchange: bool = False


class Agent:
    """Core agent that handles LLM interactions with intelligent routing."""

    def __init__(self, memory: QdrantMemory) -> None:
        """Initialize the agent.

        Args:
            memory: The memory system for context retrieval.
        """
        settings = get_settings()
        self._memory = memory
        self._router = create_router_sync()

        # Initialize InferenceBroker for smart multi-provider routing
        self._inference_broker = InferenceBroker()

        # Initialize Skills Client for skill-based intents (Phase 5G)
        self._skills_client: SkillsClient | None = None
        self._skills_enabled = False
        # Skills client is initialized lazily when first needed

        # Docs-backed setup/help knowledge (vectorized from local docs).
        self._docs_knowledge: DocsKnowledgeBase | None = None
        if settings.docs_knowledge_enabled:
            self._docs_knowledge = DocsKnowledgeBase(
                memory=self._memory,
                inference_broker=self._inference_broker,
                docs_root=settings.docs_knowledge_root,
                state_file=settings.docs_knowledge_state_path,
                gap_log_file=settings.docs_knowledge_gap_log_path,
                sync_interval_seconds=settings.docs_knowledge_sync_interval_seconds,
                max_hits=settings.docs_knowledge_max_hits,
                min_score=settings.docs_knowledge_min_score,
            )

        log.info(
            "agent_initialized",
            inference_broker_enabled=True,
            docs_knowledge_enabled=self._docs_knowledge is not None,
        )

    async def warmup(self) -> bool:
        """Warm up the router's LLM model to avoid cold start delays.

        Should be called during bot initialization after agent creation.

        Returns:
            True if warmup succeeded, False otherwise.
        """
        # Check if router backend has warmup capability
        backend = getattr(self._router, "_backend", None)
        if backend and hasattr(backend, "warmup"):
            result: bool = await backend.warmup()
            return result
        return True

    async def keep_warm(self) -> bool:
        """Send a keep-alive ping to prevent model unloading.

        Should be called periodically (e.g., every 5 minutes) to keep
        the Ollama model in memory.

        Returns:
            True if ping succeeded, False otherwise.
        """
        backend = getattr(self._router, "_backend", None)
        if backend and hasattr(backend, "keep_warm"):
            result: bool = await backend.keep_warm()
            return result
        return True

    async def generate_response(
        self,
        user_id: int,
        channel_id: int,
        message: str,
    ) -> str:
        """Generate a response to a user message with intelligent routing.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.

        Returns:
            The generated response.
        """
        total_start = time.perf_counter()

        # Step 1: Classify the message intent
        async with timed_operation("message_routing") as t:
            routing = await self._router.classify(message)
        initial_routing = routing
        guardrail = await self._apply_owner_conversation_guardrails(
            user_id=user_id,
            channel_id=channel_id,
            message=message,
            routing=routing,
        )
        routing = guardrail.routing
        log.info(
            "message_routed",
            intent=routing.intent.value,
            initial_intent=initial_routing.intent.value,
            use_claude=routing.use_claude,
            confidence=routing.confidence,
            initial_confidence=initial_routing.confidence,
            guardrail_action=guardrail.routing_trace.get("guardrail_action"),
            duration_ms=t["elapsed_ms"],
        )

        # Step 2: Handle based on intent
        async with timed_operation("intent_handling") as t:
            if guardrail.early_response is not None:
                response = guardrail.early_response
            else:
                docs_response = await self._maybe_answer_from_docs(
                    user_id=user_id,
                    message=message,
                    routing=routing,
                )
                if docs_response is not None:
                    response = docs_response
                else:
                    match routing.intent:
                        case MessageIntent.MEMORY_STORE:
                            response = await self._handle_memory_store(message, user_id=user_id)
                        case MessageIntent.MEMORY_RECALL:
                            if self._is_user_knowledge_summary_query(message):
                                response = await self._handle_user_knowledge_summary(
                                    user_id,
                                    message,
                                )
                            else:
                                response = await self._handle_memory_recall(user_id, message)
                        case MessageIntent.USER_KNOWLEDGE_SUMMARY:
                            response = await self._handle_user_knowledge_summary(user_id, message)
                        case MessageIntent.SYSTEM_COMMAND:
                            response = await self._handle_system_command(message)
                        case MessageIntent.SIMPLE_QUERY:
                            response = await self._handle_simple_query(message)
                        case MessageIntent.SYSTEM_HEALTH:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "health_analyzer",
                            )
                        case MessageIntent.COMPLEX_TASK:
                            response = await self._handle_complex_task(
                                user_id,
                                channel_id,
                                message,
                                routing,
                            )
                        # Skill intents (Phase 5G)
                        case MessageIntent.TASK_MANAGEMENT:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "task_manager",
                            )
                        case MessageIntent.CALENDAR_QUERY:
                            response = await self._handle_skill_intent(user_id, message, "calendar")
                        case MessageIntent.PROFILE_QUERY:
                            if self._is_user_knowledge_summary_query(message):
                                response = await self._handle_user_knowledge_summary(
                                    user_id,
                                    message,
                                )
                            else:
                                response = await self._handle_skill_intent(
                                    user_id,
                                    message,
                                    "profile_manager",
                                )
                        case MessageIntent.PERSONAL_MODEL:
                            if self._is_user_knowledge_summary_query(message):
                                response = await self._handle_user_knowledge_summary(
                                    user_id,
                                    message,
                                )
                            else:
                                response = await self._handle_skill_intent(
                                    user_id,
                                    message,
                                    "personal_model",
                                )
                        case MessageIntent.EMAIL_MANAGEMENT:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "email",
                            )
                        case MessageIntent.UPDATE_MANAGEMENT:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "update_checker",
                            )
                        case MessageIntent.DEV_WATCHER:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "dev_watcher",
                            )
                        case MessageIntent.MILESTONE_MANAGEMENT:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "milestone_tracker",
                            )
                        # YouTube skill intents (Phase 12)
                        case MessageIntent.YOUTUBE_INTELLIGENCE:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "youtube_intelligence",
                            )
                        case MessageIntent.YOUTUBE_MANAGEMENT:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "youtube_management",
                            )
                        case MessageIntent.YOUTUBE_STRATEGY:
                            response = await self._handle_skill_intent(
                                user_id,
                                message,
                                "youtube_strategy",
                            )
                        case _:
                            response = await self._handle_complex_task(
                                user_id,
                                channel_id,
                                message,
                                routing,
                            )
        log.info(
            "intent_handled",
            intent=routing.intent.value,
            duration_ms=t["elapsed_ms"],
            response_length=len(response),
        )

        # Step 3: Store the exchange in memory (skip for lightweight intents)
        should_store_exchange = (
            routing.intent not in (MessageIntent.SIMPLE_QUERY, MessageIntent.SYSTEM_COMMAND)
            or guardrail.should_store_exchange
        )
        if should_store_exchange:
            try:
                async with timed_operation("memory_storage") as t:
                    await self._memory.store_message(
                        user_id=user_id,
                        channel_id=channel_id,
                        role="user",
                        content=message,
                        metadata={
                            "intent": routing.intent.value,
                            "routing_trace": guardrail.routing_trace,
                        },
                    )
                    await self._memory.store_message(
                        user_id=user_id,
                        channel_id=channel_id,
                        role="assistant",
                        content=response,
                        metadata={
                            "intent": routing.intent.value,
                            "routing_trace": guardrail.routing_trace,
                        },
                    )
                log.debug("messages_stored", duration_ms=t["elapsed_ms"])
            except Exception as exc:
                log.warning(
                    "memory_storage_failed",
                    intent=routing.intent.value,
                    user_id=user_id,
                    channel_id=channel_id,
                    error=str(exc),
                )

        total_end = time.perf_counter()
        log.info(
            "generate_response_complete",
            intent=routing.intent.value,
            total_duration_ms=round((total_end - total_start) * 1000, 2),
            message_length=len(message),
        )

        return response

    async def _apply_owner_conversation_guardrails(
        self,
        *,
        user_id: int,
        channel_id: int,
        message: str,
        routing: RoutingDecision,
    ) -> OwnerRoutingGuardrailResult:
        """Apply owner-only conversational guardrails before skill dispatch."""
        routing_trace = {
            "original_intent": routing.intent.value,
            "original_confidence": routing.confidence,
            "original_reasoning": routing.reasoning,
            "final_intent": routing.intent.value,
            "guardrail_action": "pass_through",
            "guardrail_reason": "",
        }

        if not self._needs_owner_guardrail_review(message, routing):
            return OwnerRoutingGuardrailResult(routing=routing, routing_trace=routing_trace)

        recent_messages = await self._load_recent_messages_for_guardrails(
            user_id=user_id,
            channel_id=channel_id,
        )

        if self._is_owner_repair_request(message):
            repair_response = self._build_owner_repair_response(
                recent_messages=recent_messages,
            )
            routing_trace.update(
                {
                    "final_intent": MessageIntent.SIMPLE_QUERY.value,
                    "guardrail_action": "repair_response_explanation",
                    "guardrail_reason": "Detected follow-up question about the previous reply",
                }
            )
            return OwnerRoutingGuardrailResult(
                routing=RoutingDecision(
                    intent=MessageIntent.SIMPLE_QUERY,
                    confidence=1.0,
                    reasoning="Owner repair follow-up",
                    use_claude=False,
                ),
                routing_trace=routing_trace,
                early_response=repair_response,
                should_store_exchange=True,
            )

        if self._should_fallback_dev_watcher_route(
            message=message,
            routing=routing,
            recent_messages=recent_messages,
        ):
            routing_trace.update(
                {
                    "final_intent": MessageIntent.COMPLEX_TASK.value,
                    "guardrail_action": "fallback_to_conversation",
                    "guardrail_reason": "Low-confidence dev_watcher route lacked dev-specific cues",
                }
            )
            return OwnerRoutingGuardrailResult(
                routing=RoutingDecision(
                    intent=MessageIntent.COMPLEX_TASK,
                    confidence=max(routing.confidence, 0.6),
                    reasoning="Owner conversation guardrail overrode dev_watcher route",
                    use_claude=False,
                ),
                routing_trace=routing_trace,
            )

        if self._should_fallback_memory_recall_route(
            message=message,
            routing=routing,
            recent_messages=recent_messages,
        ):
            routing_trace.update(
                {
                    "final_intent": MessageIntent.COMPLEX_TASK.value,
                    "guardrail_action": "fallback_to_conversation",
                    "guardrail_reason": (
                        "Low-confidence memory_recall route looked like a "
                        "conversational follow-up"
                    ),
                }
            )
            return OwnerRoutingGuardrailResult(
                routing=RoutingDecision(
                    intent=MessageIntent.COMPLEX_TASK,
                    confidence=max(routing.confidence, 0.6),
                    reasoning="Owner conversation guardrail overrode memory_recall route",
                    use_claude=False,
                ),
                routing_trace=routing_trace,
            )

        return OwnerRoutingGuardrailResult(routing=routing, routing_trace=routing_trace)

    @staticmethod
    def _normalize_owner_guardrail_message(message: str) -> str:
        """Normalize message text for conversational guardrail checks."""
        return " ".join(message.strip().lower().split())

    def _needs_owner_guardrail_review(self, message: str, routing: RoutingDecision) -> bool:
        """Return True when owner-only follow-up guardrails should inspect the turn."""
        if routing.intent in (MessageIntent.DEV_WATCHER, MessageIntent.MEMORY_RECALL):
            return True
        return self._is_owner_repair_request(message)

    async def _load_recent_messages_for_guardrails(
        self,
        *,
        user_id: int,
        channel_id: int,
    ) -> list[dict[str, Any]]:
        """Load recent owner conversation turns used by routing guardrails."""
        try:
            recent_messages = await self._memory.get_recent_context(
                user_id=user_id,
                channel_id=channel_id,
                limit=OWNER_ROUTING_TRACE_HISTORY_LIMIT,
            )
        except Exception as exc:
            log.debug(
                "owner_guardrail_context_unavailable",
                user_id=user_id,
                channel_id=channel_id,
                error=str(exc),
            )
            return []
        if isinstance(recent_messages, list):
            return recent_messages
        return []

    def _should_fallback_dev_watcher_route(
        self,
        *,
        message: str,
        routing: RoutingDecision,
        recent_messages: list[dict[str, Any]],
    ) -> bool:
        """Return True when an owner dev-watcher route should stay conversational."""
        if routing.intent is not MessageIntent.DEV_WATCHER:
            return False
        if self._looks_like_dev_watcher_request(message):
            return False
        return routing.confidence < OWNER_ROUTING_GUARDRAIL_CONFIDENCE or (
            self._is_owner_conversation_continuation(message, recent_messages)
        )

    def _should_fallback_memory_recall_route(
        self,
        *,
        message: str,
        routing: RoutingDecision,
        recent_messages: list[dict[str, Any]],
    ) -> bool:
        """Return True when an owner memory-recall route should stay conversational."""
        if routing.intent is not MessageIntent.MEMORY_RECALL:
            return False
        if self._looks_like_memory_recall_request(message):
            return False
        return routing.confidence < OWNER_ROUTING_GUARDRAIL_CONFIDENCE or (
            self._is_owner_conversation_continuation(message, recent_messages)
        )

    def _is_owner_repair_request(self, message: str) -> bool:
        """Detect follow-up turns asking why the previous answer was off-track."""
        normalized = self._normalize_owner_guardrail_message(message)
        if not normalized:
            return False
        if any(hint in normalized for hint in OWNER_REPAIR_REQUEST_HINTS):
            return True
        return (
            ("what happened" in normalized or "why did you" in normalized)
            and ("response" in normalized or "answer" in normalized)
        )

    def _is_owner_conversation_continuation(
        self,
        message: str,
        recent_messages: list[dict[str, Any]],
    ) -> bool:
        """Detect short owner follow-ups that should remain in the current conversation."""
        if not recent_messages:
            return False

        last_assistant = self._find_last_message_by_role(recent_messages, "assistant")
        if last_assistant is None:
            return False

        normalized = self._normalize_owner_guardrail_message(message)
        if not normalized:
            return False
        if self._is_owner_repair_request(message):
            return True

        tokens = normalized.split()
        if len(tokens) > 18:
            return False

        return any(marker in normalized for marker in OWNER_CONVERSATION_REFERENCE_MARKERS)

    def _looks_like_dev_watcher_request(self, message: str) -> bool:
        """Return True when the message clearly belongs to dev_watcher."""
        normalized = self._normalize_owner_guardrail_message(message)
        return any(hint in normalized for hint in OWNER_DEV_WATCHER_HINTS)

    def _looks_like_memory_recall_request(self, message: str) -> bool:
        """Return True when the message clearly asks for remembered owner context."""
        normalized = self._normalize_owner_guardrail_message(message)
        if any(hint in normalized for hint in OWNER_MEMORY_RECALL_HINTS):
            return True
        return self._is_user_knowledge_summary_query(message)

    @staticmethod
    def _find_last_message_by_role(
        recent_messages: list[dict[str, Any]],
        role: str,
    ) -> dict[str, Any] | None:
        """Find the most recent message with the given role."""
        for entry in reversed(recent_messages):
            if isinstance(entry, dict) and entry.get("role") == role:
                return entry
        return None

    @staticmethod
    def _extract_routing_trace(message_entry: dict[str, Any] | None) -> dict[str, Any] | None:
        """Extract a stored routing trace from a conversation record when present."""
        if not isinstance(message_entry, dict):
            return None
        routing_trace = message_entry.get("routing_trace")
        if isinstance(routing_trace, dict):
            return routing_trace
        return None

    def _find_previous_user_turn(
        self,
        recent_messages: list[dict[str, Any]],
        *,
        before_message: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Find the most recent user turn before a given assistant reply."""
        if before_message is None:
            return self._find_last_message_by_role(recent_messages, "user")

        for index in range(len(recent_messages) - 1, -1, -1):
            if recent_messages[index] is before_message:
                for candidate in reversed(recent_messages[:index]):
                    if isinstance(candidate, dict) and candidate.get("role") == "user":
                        return candidate
                break
        return self._find_last_message_by_role(recent_messages, "user")

    @staticmethod
    def _humanize_routing_intent(intent_name: str | None) -> str:
        """Render an intent enum value as a human-readable phrase."""
        if not intent_name:
            return "specialist route"
        return intent_name.replace("_", " ")

    def _build_owner_repair_response(
        self,
        *,
        recent_messages: list[dict[str, Any]],
    ) -> str:
        """Explain an off-track owner reply in plain conversational language."""
        last_assistant = self._find_last_message_by_role(recent_messages, "assistant")
        last_user = self._find_previous_user_turn(recent_messages, before_message=last_assistant)
        last_trace = self._extract_routing_trace(last_assistant)

        route_name = self._humanize_routing_intent(
            (last_trace or {}).get("final_intent") or (last_trace or {}).get("original_intent")
        )
        prior_reasoning = str((last_trace or {}).get("original_reasoning") or "").strip()

        if last_trace is None:
            opening = "I over-interpreted the previous turn and answered too narrowly."
        else:
            opening = (
                f"I treated the previous turn like a {route_name} request, "
                "which was the wrong call."
            )

        if last_user is not None:
            prior_message = str(last_user.get("content") or "").strip()
            if prior_message:
                snippet = prior_message[:120]
                if len(prior_message) > 120:
                    snippet += "..."
                opening += f' You were still talking about "{snippet}".'

        if prior_reasoning:
            opening += f" The router likely fixated on {prior_reasoning.lower().rstrip('.')}."

        return (
            f"{opening} I should have stayed in the current conversation and answered your "
            "actual follow-up directly."
        )

    async def _handle_simple_query(self, message: str) -> str:
        """Handle simple queries with Gemini Flash (cheap/fast).

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        log.debug("handling_simple_query")
        return await self._router.generate_simple_response(message)

    def _should_use_docs_knowledge(self, message: str, routing: RoutingDecision) -> bool:
        """Whether this message should first try docs-backed answering."""
        if self._docs_knowledge is None:
            return False

        if not DocsKnowledgeBase.should_handle_question(message):
            return False

        return routing.intent in (
            MessageIntent.SYSTEM_COMMAND,
            MessageIntent.EMAIL_MANAGEMENT,
            MessageIntent.SIMPLE_QUERY,
            MessageIntent.PROFILE_QUERY,
            MessageIntent.TASK_MANAGEMENT,
            MessageIntent.CALENDAR_QUERY,
        )

    async def _maybe_answer_from_docs(
        self,
        *,
        user_id: int,
        message: str,
        routing: RoutingDecision,
    ) -> str | None:
        """Try docs-backed answering for setup/help style queries."""
        docs = self._docs_knowledge
        if docs is None or not self._should_use_docs_knowledge(message, routing):
            return None

        try:
            return await docs.maybe_answer(
                question=message,
                user_id=user_id,
                intent=routing.intent.value,
            )
        except Exception:
            log.exception(
                "docs_knowledge_query_failed",
                intent=routing.intent.value,
            )
            return None

    async def _get_skills_client(self) -> SkillsClient | None:
        """Get or create the skills client.

        Returns:
            Skills client, or None if unavailable.
        """
        if self._skills_client is None:
            try:
                settings = get_settings()
                api_secret = None
                if settings.skills_api_secret:
                    api_secret = settings.skills_api_secret.get_secret_value()

                self._skills_client = SkillsClient(
                    base_url=settings.skills_service_url,
                    api_secret=api_secret,
                    timeout=float(settings.skills_request_timeout),
                )
                self._skills_enabled = True
                log.info("skills_client_initialized", url=settings.skills_service_url)
            except Exception as e:
                log.warning("skills_client_init_failed", error=str(e))
                self._skills_enabled = False
                return None

        return self._skills_client

    async def _handle_skill_intent(
        self,
        user_id: int,
        message: str,
        skill_name: str,
    ) -> str:
        """Handle skill-based intents by delegating to the skills service.

        Args:
            user_id: Discord user ID.
            message: The user's message.
            skill_name: The skill to route to.

        Returns:
            Response from the skill or fallback message.
        """
        log.debug("handling_skill_intent", skill=skill_name, user_id=user_id)

        client = await self._get_skills_client()
        if not client:
            return (
                "I'm having trouble connecting to my skills service. Please try again in a moment."
            )

        # Determine intent based on skill name
        intent_map = {
            "task_manager": self._parse_task_intent(message),
            "calendar": self._parse_calendar_intent(message),
            "profile_manager": self._parse_profile_intent(message),
            "personal_model": self._parse_personal_model_intent(message),
            "health_analyzer": self._parse_health_intent(message),
            "email": self._parse_email_router_intent(message),
            "gmail": self._parse_email_intent(message),
            "update_checker": self._parse_update_intent(message),
            "dev_watcher": self._parse_dev_watcher_intent(message),
            "milestone_tracker": self._parse_milestone_intent(message),
            "youtube_intelligence": self._parse_youtube_intent(message, "intelligence"),
            "youtube_management": self._parse_youtube_intent(message, "management"),
            "youtube_strategy": self._parse_youtube_intent(message, "strategy"),
        }

        intent = intent_map.get(skill_name, "unknown")

        # Create skill request
        request = SkillRequest(
            user_id=str(user_id),
            intent=intent,
            message=message,
            context={"skill_name": skill_name},
        )

        try:
            response = await client.handle_request(request)

            if response.success:
                result_msg = self.format_skill_response(
                    skill_name=skill_name,
                    intent=intent,
                    response=response,
                )
                log.debug(
                    "skill_execution_success",
                    skill=skill_name,
                    response_length=len(result_msg),
                )
                return result_msg
            else:
                log.warning(
                    "skill_request_failed",
                    skill=skill_name,
                    error=response.error,
                )
                return f"I had trouble with that: {response.error}"

        except SkillsClientError as e:
            log.error("skills_client_error", skill=skill_name, error=str(e))
            return "I'm having trouble processing that request. Please try again."

    @staticmethod
    def _is_user_knowledge_summary_query(message: str) -> bool:
        """Return True when message asks for a unified personal-knowledge summary."""
        msg_lower = " ".join(message.lower().split())
        return any(hint in msg_lower for hint in USER_KNOWLEDGE_QUERY_HINTS)

    async def _request_skill_response(
        self,
        *,
        client: SkillsClient,
        user_id: int,
        skill_name: str,
        intent: str,
        message: str,
    ) -> SkillResponse | None:
        """Issue a skill request and return the raw response when possible."""
        request = SkillRequest(
            user_id=str(user_id),
            intent=intent,
            message=message,
            context={"skill_name": skill_name},
        )
        try:
            return await client.handle_request(request)
        except SkillsClientError as exc:
            log.warning(
                "user_knowledge_skill_request_failed",
                skill=skill_name,
                intent=intent,
                error=str(exc),
            )
            return None

    @staticmethod
    def _is_empty_profile_summary_text(text: str) -> bool:
        """Detect standard empty-summary responses from skills."""
        lowered = text.lower()
        return (
            "i don't have a profile for you yet" in lowered
            or "i don't have any profile data for you yet" in lowered
            or "no profile data" in lowered
        )

    @staticmethod
    def _format_profile_entries_for_summary(raw_entries: Any) -> list[str]:
        """Render profile entries returned by profile_manager:profile_view."""
        if not isinstance(raw_entries, list):
            return []

        formatted: list[str] = []
        seen: set[str] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("value") or "").strip()
            if not value:
                continue

            key = str(entry.get("key") or "").strip()
            line = f"- {key}: {value}" if key else f"- {value}"
            fingerprint = line.casefold()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            formatted.append(line)
        return formatted

    async def _list_long_term_memories_for_user(self, user_id: int) -> list[dict[str, Any]]:
        """Load long-term memories for user_id, supporting int/str payload variants."""
        candidates: list[int | str] = [user_id, str(user_id)]
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for candidate in candidates:
            try:
                records = await self._memory.filter_scoped_by_field(
                    collection_name=LONG_TERM_MEMORY_COLLECTION,
                    field="user_id",
                    value=candidate,
                    limit=200,
                )
            except Exception as exc:
                log.debug(
                    "user_knowledge_memory_filter_failed",
                    user_id=user_id,
                    candidate_type=type(candidate).__name__,
                    error=str(exc),
                )
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                record_id = str(record.get("id") or "").strip()
                if record_id and record_id in seen_ids:
                    continue
                if record_id:
                    seen_ids.add(record_id)
                merged.append(record)
        return merged

    async def _collect_profile_memory_facts(self, user_id: int) -> list[str]:
        """Collect normalized user memory facts suitable for summary output."""
        try:
            memories = await self._list_long_term_memories_for_user(user_id)
        except Exception as exc:
            log.warning("user_knowledge_memory_fetch_failed", user_id=user_id, error=str(exc))
            return []

        allowed_types = {"", "general", "user_request", "profile", "fact", "preference", "identity"}
        normalized: list[str] = []
        seen: set[str] = set()
        for memory in memories:
            memory_type = str(memory.get("type") or "").strip().lower()
            if memory_type not in allowed_types:
                continue
            content = str(memory.get("content") or "").strip()
            if not content:
                continue
            collapsed = " ".join(content.split())
            key = collapsed.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(collapsed)
        return normalized[:USER_KNOWLEDGE_FACT_DISPLAY_LIMIT]

    async def _handle_user_knowledge_summary(self, user_id: int, message: str) -> str:
        """Build a canonical summary from profile store and long-term memories."""
        profile_lines: list[str] = []
        personal_summary: str | None = None

        client = await self._get_skills_client()
        if client is not None:
            profile_response = await self._request_skill_response(
                client=client,
                user_id=user_id,
                skill_name="profile_manager",
                intent="profile_view",
                message=message,
            )
            if profile_response is not None and profile_response.success:
                profile_lines = self._format_profile_entries_for_summary(
                    profile_response.data.get("entries")
                )

            personal_response = await self._request_skill_response(
                client=client,
                user_id=user_id,
                skill_name="personal_model",
                intent="personal_summary",
                message=message,
            )
            if personal_response is not None and personal_response.success:
                raw_summary = (personal_response.message or "").strip()
                if raw_summary and not self._is_empty_profile_summary_text(raw_summary):
                    personal_summary = raw_summary

        memory_facts = await self._collect_profile_memory_facts(user_id)

        sections: list[str] = []
        if profile_lines:
            sections.append("Profile entries:\n" + "\n".join(profile_lines))
        if memory_facts:
            memory_lines = [f"{idx}. {fact}" for idx, fact in enumerate(memory_facts, start=1)]
            sections.append("Remembered facts:\n" + "\n".join(memory_lines))
        if personal_summary:
            sections.append("Personal model:\n" + personal_summary)

        if not sections:
            return "I don't have any profile data for you yet."

        return "Here's what I know about you:\n\n" + "\n\n".join(sections)

    def format_skill_response(
        self,
        *,
        skill_name: str,
        intent: str,
        response: SkillResponse,
    ) -> str:
        """Format a skill response into user-facing text."""
        if skill_name == "task_manager" and intent == "list_tasks":
            return self._format_task_list_response(response)
        return response.message or "Done!"

    def _format_task_list_response(self, response: SkillResponse) -> str:
        """Render task list responses with a useful summary and task details."""
        raw_tasks = response.data.get("tasks")
        if not isinstance(raw_tasks, list):
            return response.message or "Done!"

        tasks = [task for task in raw_tasks if isinstance(task, dict)]
        if not tasks:
            return "You have no tasks right now."

        active_count = 0
        overdue_count = 0
        now = datetime.now()
        for task in tasks:
            status_raw = str(task.get("status", "")).strip().lower()
            is_closed = status_raw in {"done", "cancelled"}
            if not is_closed:
                active_count += 1

            due = self._parse_deadline(task.get("deadline"))
            if due is not None and not is_closed and due < now:
                overdue_count += 1

        lines = [f"You have {active_count} active task(s)."]
        if overdue_count > 0:
            lines.append(f"Overdue: {overdue_count}.")
        lines.append("Here are your tasks:")

        displayed = tasks[:TASK_LIST_DISPLAY_LIMIT]
        for idx, task in enumerate(displayed, start=1):
            title = str(task.get("title") or "Untitled task").strip()
            status = self._format_task_status(task.get("status"))
            priority = self._format_task_priority(task.get("priority"))
            line = f"{idx}. {title} - {status} - {priority}"
            due_label = self._format_deadline(task.get("deadline"))
            if due_label:
                line += f" - due {due_label}"
            lines.append(line)

        remaining = len(tasks) - len(displayed)
        if remaining > 0:
            lines.append(f"+{remaining} more")

        return "\n".join(lines)

    @staticmethod
    def _format_task_status(raw_status: Any) -> str:
        """Normalize task status values to title-cased labels."""
        status = str(raw_status or "todo").strip().replace("_", " ")
        if not status:
            return "Todo"
        return status.title()

    @staticmethod
    def _format_task_priority(raw_priority: Any) -> str:
        """Normalize task priority values from int/str to labels."""
        if isinstance(raw_priority, int | float):
            mapping = {4: "Critical", 3: "High", 2: "Medium", 1: "Low"}
            return mapping.get(int(raw_priority), "Medium")

        text = str(raw_priority or "").strip()
        if not text:
            return "Medium"

        if text.isdigit():
            text_mapping: dict[str, str] = {
                "4": "Critical",
                "3": "High",
                "2": "Medium",
                "1": "Low",
            }
            return text_mapping.get(text, "Medium")

        return text.replace("_", " ").title()

    @staticmethod
    def _parse_deadline(raw_deadline: Any) -> datetime | None:
        """Parse a deadline value if present."""
        if not isinstance(raw_deadline, str) or not raw_deadline.strip():
            return None
        try:
            return datetime.fromisoformat(raw_deadline.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    def _format_deadline(self, raw_deadline: Any) -> str | None:
        """Format a deadline value for display."""
        parsed = self._parse_deadline(raw_deadline)
        if parsed is None:
            return None
        return parsed.strftime("%Y-%m-%d")

    def _parse_task_intent(self, message: str) -> str:
        """Parse specific task intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["add", "create", "new", "make"]):
            return "create_task"
        elif any(w in msg_lower for w in ["list", "show", "what are", "my tasks"]):
            return "list_tasks"
        elif any(w in msg_lower for w in ["complete", "done", "finish", "mark"]):
            return "complete_task"
        elif any(w in msg_lower for w in ["delete", "remove", "cancel"]):
            return "delete_task"
        elif any(w in msg_lower for w in ["update", "change", "modify", "edit"]):
            return "update_task"
        elif any(w in msg_lower for w in ["summary", "overview", "status"]):
            return "task_summary"
        return "list_tasks"

    def _parse_calendar_intent(self, message: str) -> str:
        """Parse specific calendar intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["schedule", "add", "create", "book"]):
            return "schedule_event"
        elif any(w in msg_lower for w in ["free", "available", "availability"]):
            return "check_availability"
        elif any(w in msg_lower for w in ["today", "today's"]):
            return "today_schedule"
        elif any(w in msg_lower for w in ["work hours", "working hours"]):
            return "set_work_hours"
        elif any(w in msg_lower for w in ["list", "show", "events", "calendar"]):
            return "list_events"
        return "today_schedule"

    def _parse_profile_intent(self, message: str) -> str:
        """Parse specific profile intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["update", "change", "set"]):
            return "profile_update"
        elif any(w in msg_lower for w in ["forget", "delete", "remove"]):
            return "profile_delete"
        elif any(w in msg_lower for w in ["export", "download", "gdpr"]):
            return "profile_export"
        elif any(w in msg_lower for w in ["confidence", "certain", "sure"]):
            return "profile_confidence"
        elif any(w in msg_lower for w in ["what", "show", "know", "about me"]):
            return "profile_summary"
        return "profile_summary"

    def _parse_personal_model_intent(self, message: str) -> str:
        """Parse specific personal model intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["contact", "contacts", "who do i know"]):
            return "personal_contacts"
        elif any(w in msg_lower for w in ["forget", "delete learning", "remove learning"]):
            return "personal_forget"
        elif any(w in msg_lower for w in ["export", "download", "gdpr"]):
            return "personal_export"
        elif any(w in msg_lower for w in ["policy", "policies", "trust score"]):
            return "personal_policies"
        elif any(
            w in msg_lower
            for w in ["timezone", "locale", "my name is", "call me", "set my", "add goal"]
        ):
            return "personal_update"
        elif any(
            w in msg_lower
            for w in ["know about me", "learned", "summary", "what do you know", "show me"]
        ):
            return "personal_summary"
        return "personal_summary"

    def _parse_email_intent(self, message: str) -> str:
        """Parse specific email/Gmail intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["draft", "drafts", "review draft", "pending draft"]):
            return "email_drafts"
        elif any(w in msg_lower for w in ["digest", "briefing", "summary", "weekly"]):
            return "email_digest"
        elif any(w in msg_lower for w in ["status", "connected", "account"]):
            return "email_status"
        elif any(w in msg_lower for w in ["search", "find email", "look for"]):
            return "email_search"
        elif any(w in msg_lower for w in ["calendar", "events today", "schedule"]):
            return "email_calendar"
        elif any(w in msg_lower for w in ["unread", "new email", "urgent"]):
            return "email_unread"
        return "email_check"

    def _parse_email_router_intent(self, message: str) -> str:
        """Parse provider-agnostic email-router intents."""
        msg_lower = message.lower()
        if any(
            phrase in msg_lower
            for phrase in [
                "connect email",
                "connect gmail",
                "add email account",
                "link email",
                "link gmail",
            ]
        ):
            return "email_connect"
        if any(
            phrase in msg_lower
            for phrase in [
                "disconnect email",
                "disconnect gmail",
                "remove email account",
                "unlink email",
                "stop monitoring",
            ]
        ):
            return "email_disconnect"
        if "primary calendar" in msg_lower or "default calendar" in msg_lower:
            return "email_set_primary_calendar"
        if "primary task" in msg_lower or "default task list" in msg_lower:
            return "email_set_primary_task_list"
        if any(
            phrase in msg_lower
            for phrase in [
                "email queue status",
                "queue status",
                "routing queue status",
                "pending email queue",
            ]
        ):
            return "email_queue_status"
        if any(
            phrase in msg_lower
            for phrase in [
                "resume email queue",
                "drain email queue",
                "retry queued emails",
                "resume queue",
            ]
        ):
            return "email_queue_resume"
        if any(w in msg_lower for w in ["status", "connected", "provider", "account"]):
            return "email_status"
        return "email_route"

    def _parse_health_intent(self, message: str) -> str:
        """Parse specific health-analyzer intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["report", "daily", "yesterday"]):
            return "health_report"
        if any(w in msg_lower for w in ["system status", "metrics", "diagnostic", "details"]):
            return "system_status"
        return "health_check"

    def _parse_update_intent(self, message: str) -> str:
        """Parse specific update-management intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["resume", "unpause"]):
            return "resume_updates"
        if any(w in msg_lower for w in ["rollback", "roll back", "revert"]):
            return "rollback_update"
        if any(
            w in msg_lower
            for w in [
                "apply",
                "install",
                "deploy",
                "upgrade",
                "update now",
            ]
        ):
            return "apply_update"
        if any(w in msg_lower for w in ["status", "version", "current version"]):
            return "update_status"
        return "check_update"

    def _parse_dev_watcher_intent(self, message: str) -> str:
        """Parse specific dev watcher intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["next", "should i work", "what to do"]):
            return "dev_next"
        elif any(w in msg_lower for w in ["idea", "ideas"]):
            return "dev_ideas"
        elif any(
            w in msg_lower
            for w in ["release", "deploy", "deployment", "ci", "pipeline", "build status"]
        ):
            return "dev_release_summary"
        elif any(w in msg_lower for w in ["journal", "log", "this week", "today", "yesterday"]):
            return "dev_journal"
        elif any(w in msg_lower for w in ["summary", "overview", "recap"]):
            return "dev_summary"
        return "dev_status"

    def _parse_milestone_intent(self, message: str) -> str:
        """Parse specific milestone intent from message."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["approve", "publish", "accept"]):
            return "milestone_approve"
        elif any(w in msg_lower for w in ["reject", "dismiss", "skip"]):
            return "milestone_reject"
        elif any(w in msg_lower for w in ["draft", "drafts", "promo", "post"]):
            return "milestone_drafts"
        elif any(w in msg_lower for w in ["setting", "config", "threshold"]):
            return "milestone_settings"
        return "milestone_list"

    def _parse_youtube_intent(self, message: str, skill: str) -> str:
        """Parse specific YouTube skill intent from message."""
        msg_lower = message.lower()
        if skill == "intelligence":
            if any(w in msg_lower for w in ["analyze", "analysis", "report"]):
                return "yt_analyze_channel"
            elif any(w in msg_lower for w in ["history", "past reports"]):
                return "yt_intelligence_history"
            return "yt_get_intelligence"
        elif skill == "management":
            if any(w in msg_lower for w in ["reply", "replies", "comment"]):
                return "yt_review_replies"
            elif any(w in msg_lower for w in ["tag", "tags", "seo"]):
                return "yt_get_tag_recommendations"
            elif any(w in msg_lower for w in ["health", "audit"]):
                return "yt_channel_health"
            elif any(w in msg_lower for w in ["setup", "onboard", "configure"]):
                return "yt_configure_management"
            elif any(w in msg_lower for w in ["state", "status"]):
                return "yt_get_management_state"
            return "yt_manage_channel"
        elif skill == "strategy":
            if any(w in msg_lower for w in ["generate", "create", "new"]):
                return "yt_generate_strategy"
            elif any(w in msg_lower for w in ["history", "past"]):
                return "yt_strategy_history"
            return "yt_get_strategy"
        return "unknown"

    async def _build_context(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        memory_limit: int = 5,
        history_limit: int = CONTEXT_HISTORY_LIMIT,
        user_id_filter: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Build context from recent messages and relevant memories.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.
            memory_limit: Maximum number of memories to retrieve.
            history_limit: Maximum number of recent messages to retrieve.
            user_id_filter: Optional user ID for scoping memory searches.

        Returns:
            Tuple of (recent_messages, relevant_memories).
        """
        # Fetch context in parallel for better performance
        recent_messages, relevant_memories = await asyncio.gather(
            self._memory.get_recent_context(
                user_id=user_id,
                channel_id=channel_id,
                limit=history_limit,
            ),
            self._memory.search_memories(query=message, limit=memory_limit, user_id=user_id_filter),
        )
        return recent_messages, relevant_memories

    async def _handle_complex_task(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        routing: RoutingDecision,
    ) -> str:
        """Handle complex tasks with intelligent provider routing.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.
            routing: The routing decision.

        Returns:
            Generated response.
        """
        # Fetch context once for reuse across models
        recent_messages: list[dict[str, Any]] = []
        relevant_memories: list[dict[str, Any]] = []
        context_duration_ms: float | None = None
        try:
            async with timed_operation("context_retrieval") as ctx_t:
                recent_messages, relevant_memories = await self._build_context(
                    user_id,
                    channel_id,
                    message,
                    user_id_filter=user_id,
                )
            context_duration_ms = ctx_t["elapsed_ms"]
        except Exception as exc:
            log.warning(
                "context_retrieval_failed",
                user_id=user_id,
                channel_id=channel_id,
                error=str(exc),
                fallback="empty_context",
            )

        log.info(
            "context_built",
            duration_ms=context_duration_ms,
            memories_found=len(relevant_memories),
            messages_found=len(recent_messages),
        )

        # Build system prompt with context
        prompt_principal = ScopedPrincipal(
            principal_id=str(user_id),
            principal_type="owner_user",
            trust_domain=TrustDomain.OWNER_PERSONAL,
        )
        system_prompt_fragments = [
            prompt_fragment(
                SYSTEM_PROMPT,
                scope=DataScope.CONTROL_PLANE,
                source="zetherion_ai.agent.prompts.SYSTEM_PROMPT",
            )
        ]
        score_threshold = get_dynamic("tuning", "memory_score_threshold", MEMORY_SCORE_THRESHOLD)
        context_limit = get_dynamic("tuning", "context_history_limit", CONTEXT_HISTORY_LIMIT)

        if relevant_memories:
            memory_text = "\n".join(
                f"- {m['content']}" for m in relevant_memories if m["score"] > score_threshold
            )
            if memory_text:
                system_prompt_fragments.append(
                    prompt_fragment(
                        f"## Relevant Memories\n{memory_text}",
                        scope=DataScope.OWNER_PERSONAL,
                        source="zetherion_ai.agent.core.relevant_memories",
                    )
                )

        system_prompt = assemble_prompt_fragments(
            system_prompt_fragments,
            purpose="agent.owner.system_prompt",
            principal=prompt_principal,
        )

        # Format conversation history
        messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in recent_messages[-context_limit:]
        ]

        # Classify task type for optimal provider selection
        task_type = self._classify_task_type(message)
        log.debug("task_type_classified", task_type=task_type.value)

        async with timed_operation("inference") as infer_t:
            result = await self._inference_broker.infer(
                prompt=message,
                task_type=task_type,
                system_prompt=system_prompt,
                messages=messages,
            )
        log.info(
            "inference_complete",
            duration_ms=infer_t["elapsed_ms"],
            provider=result.provider.value,
            model=result.model,
        )
        return result.content

    def _classify_task_type(self, message: str) -> TaskType:
        """Classify the task type from the message content.

        Args:
            message: The user's message.

        Returns:
            The classified TaskType for provider selection.
        """
        lower_msg = message.lower()

        # Code-related patterns
        if any(kw in lower_msg for kw in CODE_KEYWORDS):
            if any(kw in lower_msg for kw in CODE_REVIEW_KEYWORDS):
                return TaskType.CODE_REVIEW
            elif any(kw in lower_msg for kw in CODE_DEBUG_KEYWORDS):
                return TaskType.CODE_DEBUGGING
            return TaskType.CODE_GENERATION

        # Math/reasoning patterns
        if any(kw in lower_msg for kw in MATH_KEYWORDS):
            if any(kw in lower_msg for kw in MATH_SPECIFIC_KEYWORDS):
                return TaskType.MATH_ANALYSIS
            return TaskType.COMPLEX_REASONING

        # Creative patterns
        if any(kw in lower_msg for kw in CREATIVE_KEYWORDS):
            return TaskType.CREATIVE_WRITING

        # Long document patterns
        if any(kw in lower_msg for kw in SUMMARIZATION_KEYWORDS):
            return TaskType.SUMMARIZATION

        # Default to conversation for general queries
        return TaskType.CONVERSATION

    async def _handle_memory_store(self, message: str, *, user_id: int | None = None) -> str:
        """Handle memory storage requests.

        Args:
            message: The message containing what to remember.
            user_id: Optional user ID for scoping the memory.

        Returns:
            Confirmation message.
        """
        # Extract what to remember (simple approach - store the whole thing)
        # Could use Flash to extract the key info
        log.debug("handling_memory_store")

        # Use Flash to extract the memory content
        extraction_prompt = (
            f"""The user wants to remember something. """
            f"""Extract just the key information to store.

User message: {message}

Respond with ONLY the fact/preference to remember, nothing else."""
        )

        extracted = await self._router.generate_simple_response(extraction_prompt)

        await self._memory.store_memory(
            content=extracted.strip(),
            memory_type="user_request",
            user_id=user_id,
        )

        return f"Got it! I'll remember: {extracted.strip()}"

    async def _handle_memory_recall(self, user_id: int, query: str) -> str:
        """Handle memory recall requests.

        Args:
            user_id: Discord user ID.
            query: The recall query.

        Returns:
            Retrieved memories or response.
        """
        log.debug("handling_memory_recall")

        # Search memories
        memories = await self._memory.search_memories(query=query, limit=5, user_id=user_id)
        conversations = await self._memory.search_conversations(
            query=query, user_id=user_id, limit=5
        )

        if not memories and not conversations:
            return "I don't have any memories related to that. Would you like to tell me about it?"

        # Format and summarize using Flash
        context_parts = []

        if memories:
            mem_text = "\n".join(f"- {m['content']}" for m in memories if m["score"] > 0.5)
            if mem_text:
                context_parts.append(f"Stored memories:\n{mem_text}")

        if conversations:
            conv_text = "\n".join(
                f"- [{c['role']}]: {c['content'][:100]}..."
                for c in conversations
                if c["score"] > 0.5
            )
            if conv_text:
                context_parts.append(f"Past conversations:\n{conv_text}")

        if not context_parts:
            return (
                "I found some vague matches, but nothing strongly related. "
                "Could you be more specific?"
            )

        context_text = "\n".join(context_parts)
        summary_prompt = f"""The user is asking: {query}

Here's what I found in my memory:
{context_text}

Summarize what I know about this in a helpful, conversational way."""

        return await self._router.generate_simple_response(summary_prompt)

    async def _handle_system_command(self, message: str) -> str:
        """Handle system commands and help requests.

        Args:
            message: The system command.

        Returns:
            Help or command response.
        """
        log.debug("handling_system_command")

        lower_msg = message.lower().strip()

        if "help" in lower_msg or "what can you do" in lower_msg:
            return (
                """Hi! I'm Zetherion, your personal AI assistant. Here's what I can do:

**Chat & Questions**
- Ask me anything - simple questions use fast responses, complex tasks get deeper analysis

**Memory**
- Say "remember that..." to store information
- Ask "what do you know about..." to recall memories

**Commands**
- `/ask` - Ask me a question
- `/remember` - Store a memory
- `/search` - Search your memories
- `/ping` - Check if I'm online

I route messages intelligently - simple queries are fast and free, """
                """complex tasks use more capable models."""
            )

        return "I'm not sure what you're asking. Try saying 'help' to see what I can do!"

    async def store_memory_from_request(
        self,
        content: str,
        memory_type: str = "general",
        user_id: int | None = None,
    ) -> str:
        """Store a memory based on explicit user request.

        Args:
            content: The memory content to store.
            memory_type: Type of memory.
            user_id: Optional user ID for scoping the memory.

        Returns:
            Confirmation message.
        """
        await self._memory.store_memory(
            content=content,
            memory_type=memory_type,
            user_id=user_id,
        )
        return f"I've stored that in my memory: {content}"
