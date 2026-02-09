"""Message router using Gemini Flash for intent classification."""

import asyncio
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from google import genai  # type: ignore[attr-defined]

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.agent.router")


class RouterBackend(Protocol):
    """Protocol defining the interface for router backends."""

    async def classify(self, message: str) -> "RoutingDecision":
        """Classify a message."""
        ...

    async def generate_simple_response(self, message: str) -> str:
        """Generate a simple response."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class MessageIntent(Enum):
    """Classified intent types for routing."""

    SIMPLE_QUERY = "simple_query"  # Quick factual questions, greetings
    COMPLEX_TASK = "complex_task"  # Code generation, analysis, creative tasks
    MEMORY_STORE = "memory_store"  # User wants to remember something
    MEMORY_RECALL = "memory_recall"  # User asking about past conversations
    SYSTEM_COMMAND = "system_command"  # Bot commands, settings
    # Skill intents (Phase 5G)
    TASK_MANAGEMENT = "task_management"  # Task creation, updates, listing
    CALENDAR_QUERY = "calendar_query"  # Calendar/schedule queries
    PROFILE_QUERY = "profile_query"  # Profile viewing/updating
    # Phase 9 intents
    PERSONAL_MODEL = "personal_model"  # Personal understanding queries
    # Phase 8 intents
    EMAIL_MANAGEMENT = "email_management"  # Email checking, drafts, digests


@dataclass
class RoutingDecision:
    """Result of routing classification."""

    intent: MessageIntent
    confidence: float
    reasoning: str
    use_claude: (
        bool  # Whether to use a complex model (Claude/OpenAI) vs lightweight (Gemini/Ollama)
    )

    @property
    def requires_complex_model(self) -> bool:
        """Check if this routing requires a complex model.

        Returns:
            True if Claude or OpenAI should be used, False for lightweight models.
        """
        return self.use_claude


ROUTER_PROMPT = """You are a message router. Classify the user's message into one of these intents:

1. SIMPLE_QUERY - Greetings, quick factual questions, simple requests
   Examples: "Hi", "What's 2+2?", "What day is it?", "Thanks!"

2. COMPLEX_TASK - Code generation, detailed analysis, creative writing, multi-step tasks
   Examples: "Write a Python script to...", "Explain how transformers work in detail", \
"Help me debug this code..."

3. MEMORY_STORE - User explicitly wants you to remember something
   Examples: "Remember that I prefer dark mode", "My birthday is March 15", "Note that..."

4. MEMORY_RECALL - User asking about previously stored personal information or past conversations
   Examples: "What's my favorite color?", "What did we talk about yesterday?", \
"What do you know about me?", "What's my birthday?", "What are my preferences?", "Where do I live?"

5. SYSTEM_COMMAND - Bot commands, settings, help requests
   Examples: "Help", "What can you do?", "List commands", "Settings"

6. TASK_MANAGEMENT - Creating, listing, updating, or completing tasks and todos
   Examples: "Add a task to buy groceries", "What are my tasks?", "Mark the report task as done", \
"Create a todo for tomorrow", "Show my overdue tasks", "Delete the shopping task"

7. CALENDAR_QUERY - Schedule, events, availability, and calendar-related queries
   Examples: "What's on my calendar today?", "Am I free at 3pm?", "Schedule a meeting for Friday", \
"What events do I have this week?", "Set my work hours to 9-5"

8. PROFILE_QUERY - Viewing, updating, or managing what the bot knows about the user
   Examples: "What do you know about me?", "Update my timezone to EST", "Forget my location", \
"Show my profile", "Export my data", "What's your confidence in my preferences?"

9. PERSONAL_MODEL - Deep personal understanding queries, contact management, and policy control
   Examples: "Show my contacts", "Who are my important contacts?", "My timezone is PST", \
"Forget that I like coffee", "Show my policies", "Export my personal data", \
"What have you learned about me?"

10. EMAIL_MANAGEMENT - Email checking, reading, drafts, digests, Gmail management
   Examples: "Check my emails", "Any urgent emails?", "Show unread emails", \
"Review my drafts", "Give me a morning digest", "Weekly email summary", \
"Search emails from Alice", "Gmail status", "How many emails today?"

Respond with ONLY a JSON object:
{"intent": "INTENT_NAME", "confidence": 0.0-1.0, "reasoning": "brief reason"}
"""


class GeminiRouterBackend:
    """Router backend using Gemini Flash."""

    def __init__(self) -> None:
        """Initialize the Gemini router backend."""
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = settings.router_model
        log.info("gemini_router_initialized", model=self._model)

    async def classify(self, message: str) -> RoutingDecision:
        """Classify a message and determine routing.

        Args:
            message: The user's message to classify.

        Returns:
            RoutingDecision with intent and routing info.
        """
        try:
            # Wrap synchronous Gemini call to avoid blocking event loop
            def _sync_classify() -> Any:
                return self._client.models.generate_content(
                    model=self._model,
                    contents=f"{ROUTER_PROMPT}\n\nUser message: {message}",
                    config={
                        "temperature": 0.1,  # Low temperature for consistent classification
                        "max_output_tokens": 150,
                    },
                )

            response = await asyncio.to_thread(_sync_classify)

            result_text = (response.text or "").strip()

            # Extract JSON using regex to handle markdown code blocks robustly
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result_text, re.DOTALL)
            if json_match:
                result_text = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r"\{.*?\}", result_text, re.DOTALL)
                if json_match:
                    result_text = json_match.group(0)

            result_text = result_text.strip()

            # Parse JSON
            try:
                result = json.loads(result_text)
            except json.JSONDecodeError as e:
                log.warning(
                    "json_parse_failed",
                    error=str(e),
                    response_text=result_text[:200],
                )
                raise ValueError(f"Invalid JSON response: {e}") from e

            # Validate schema
            if "intent" not in result:
                raise ValueError("Missing 'intent' field in response")

            # Parse intent with error handling
            try:
                intent = MessageIntent(result["intent"].lower())
            except (ValueError, KeyError) as e:
                log.warning(
                    "invalid_intent",
                    intent_value=result.get("intent"),
                    error=str(e),
                )
                raise ValueError(f"Invalid intent value: {result.get('intent')}") from e

            confidence = float(result.get("confidence", 0.8))
            reasoning = result.get("reasoning", "")

            # Validate confidence range
            if not 0.0 <= confidence <= 1.0:
                log.warning("invalid_confidence", confidence=confidence)
                confidence = max(0.0, min(1.0, confidence))

            # Determine if we need Claude (expensive) or can use Flash (cheap)
            use_claude = intent == MessageIntent.COMPLEX_TASK and confidence > 0.7

            decision = RoutingDecision(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning,
                use_claude=use_claude,
            )

            log.debug(
                "message_classified",
                intent=intent.value,
                confidence=confidence,
                use_claude=use_claude,
            )

            return decision

        except json.JSONDecodeError as e:
            log.warning(
                "json_decode_error",
                error=str(e),
                message=message[:50],
            )
            # Default to simple query on JSON errors (likely model issue)
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="JSON decode failed, using simple query as fallback",
                use_claude=False,
            )
        except ValueError as e:
            log.warning(
                "validation_error",
                error=str(e),
                message=message[:50],
            )
            # Default to simple query on validation errors
            return RoutingDecision(
                intent=MessageIntent.SIMPLE_QUERY,
                confidence=0.5,
                reasoning="Validation failed, using simple query as fallback",
                use_claude=False,
            )
        except Exception as e:
            log.error(
                "classification_failed",
                error=str(e),
                error_type=type(e).__name__,
                message=message[:50],
            )
            # Default to complex task with Claude only on unexpected errors
            return RoutingDecision(
                intent=MessageIntent.COMPLEX_TASK,
                confidence=0.5,
                reasoning="Classification failed unexpectedly, defaulting to complex task",
                use_claude=True,
            )

    async def generate_simple_response(self, message: str) -> str:
        """Generate a response for simple queries using Gemini Flash.

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        try:
            # Wrap synchronous Gemini call to avoid blocking event loop
            def _sync_generate() -> Any:
                return self._client.models.generate_content(
                    model=self._model,
                    contents=message,
                    config={
                        "temperature": 0.7,
                        "max_output_tokens": 500,
                    },
                )

            response = await asyncio.to_thread(_sync_generate)
            return response.text or ""
        except Exception as e:
            log.error("flash_generation_failed", error=str(e))
            return "I'm having trouble processing that. Could you try again?"

    async def health_check(self) -> bool:
        """Check if Gemini is healthy and available.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            # Wrap synchronous Gemini call to avoid blocking event loop
            def _sync_health_check() -> Any:
                return self._client.models.generate_content(
                    model=self._model,
                    contents="test",
                    config={
                        "temperature": 0.1,
                        "max_output_tokens": 10,
                    },
                )

            response = await asyncio.to_thread(_sync_health_check)
            is_healthy = bool(response.text)
            if is_healthy:
                log.info("gemini_health_check_passed", model=self._model)
            return is_healthy
        except Exception as e:
            log.error("gemini_health_check_failed", error=str(e))
            return False


class MessageRouter:
    """Message router with pluggable backend support.

    This wrapper class maintains backward compatibility while allowing
    different backend implementations (Gemini, Ollama, etc.).
    """

    def __init__(self, backend: RouterBackend | None = None) -> None:
        """Initialize the message router.

        Args:
            backend: Router backend to use. If None, uses GeminiRouterBackend.
        """
        self._backend: RouterBackend = backend or GeminiRouterBackend()
        log.info("message_router_initialized", backend=type(self._backend).__name__)

    async def classify(self, message: str) -> RoutingDecision:
        """Classify a message using the configured backend.

        Args:
            message: The user's message to classify.

        Returns:
            RoutingDecision with intent and routing info.
        """
        return await self._backend.classify(message)

    async def generate_simple_response(self, message: str) -> str:
        """Generate a simple response using the configured backend.

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        return await self._backend.generate_simple_response(message)

    async def health_check(self) -> bool:
        """Check if the backend is healthy.

        Returns:
            True if healthy, False otherwise.
        """
        return await self._backend.health_check()
