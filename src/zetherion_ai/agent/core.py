"""Agent core - LLM interaction and response generation with routing."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic
import openai
from anthropic import APIConnectionError, APITimeoutError, RateLimitError
from openai import APIConnectionError as OpenAIConnectionError
from openai import APITimeoutError as OpenAITimeoutError
from openai import RateLimitError as OpenAIRateLimitError

from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.prompts import SYSTEM_PROMPT
from zetherion_ai.agent.providers import TaskType
from zetherion_ai.agent.router import MessageIntent, RoutingDecision
from zetherion_ai.agent.router_factory import create_router_sync
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient, SkillsClientError

log = get_logger("zetherion_ai.agent.core")


async def retry_with_exponential_backoff(
    func: Callable[[], Awaitable[Any]],
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
) -> Any:
    """Retry a function with exponential backoff.

    Args:
        func: Async function to retry.
        max_retries: Maximum number of retry attempts.
        initial_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        exponential_base: Base for exponential backoff.

    Returns:
        Result of the function call.

    Raises:
        The last exception if all retries fail.
    """
    delay = initial_delay
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            return await func()
        except (
            APIConnectionError,
            APITimeoutError,
            OpenAIConnectionError,
            OpenAITimeoutError,
        ) as e:
            last_exception = e
            if attempt < max_retries - 1:
                log.warning(
                    "api_call_failed_retrying",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)
                delay = min(delay * exponential_base, max_delay)
            else:
                log.error(
                    "api_call_failed_max_retries",
                    max_retries=max_retries,
                    error=str(e),
                )
        except (RateLimitError, OpenAIRateLimitError) as e:
            last_exception = e
            # For rate limits, use longer backoff
            if attempt < max_retries - 1:
                rate_limit_delay = min(delay * 2, max_delay)
                log.warning(
                    "rate_limit_hit_retrying",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay=rate_limit_delay,
                )
                await asyncio.sleep(rate_limit_delay)
                delay = min(delay * exponential_base, max_delay)
            else:
                log.error("rate_limit_max_retries", max_retries=max_retries)

    if last_exception:
        raise last_exception
    raise RuntimeError("Retry function failed without exception")


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

        # Initialize Anthropic client for complex tasks
        self._claude_client: anthropic.AsyncAnthropic | None
        if settings.anthropic_api_key:
            self._claude_client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value()
            )
            self._claude_model = settings.claude_model
            self._has_claude = True
        else:
            self._claude_client = None
            self._has_claude = False

        # Initialize OpenAI client as alternative
        self._openai_client: openai.AsyncOpenAI | None
        if settings.openai_api_key:
            self._openai_client = openai.AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value()
            )
            self._openai_model = settings.openai_model
            self._has_openai = True
        else:
            self._openai_client = None
            self._has_openai = False

        # Initialize InferenceBroker for smart multi-provider routing (Phase 5B)
        self._inference_broker: InferenceBroker | None = None
        if settings.inference_broker_enabled:
            self._inference_broker = InferenceBroker()

        # Initialize Skills Client for skill-based intents (Phase 5G)
        self._skills_client: SkillsClient | None = None
        self._skills_enabled = False
        # Skills client is initialized lazily when first needed

        log.info(
            "agent_initialized",
            has_claude=self._has_claude,
            has_openai=self._has_openai,
            inference_broker_enabled=self._inference_broker is not None,
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
        classify_start = time.perf_counter()
        routing = await self._router.classify(message)
        classify_end = time.perf_counter()
        log.info(
            "TIMING: message_routed",
            intent=routing.intent.value,
            use_claude=routing.use_claude,
            confidence=routing.confidence,
            classify_duration_ms=round((classify_end - classify_start) * 1000, 2),
        )

        # Step 2: Handle based on intent
        handle_start = time.perf_counter()
        match routing.intent:
            case MessageIntent.MEMORY_STORE:
                response = await self._handle_memory_store(message)
            case MessageIntent.MEMORY_RECALL:
                response = await self._handle_memory_recall(user_id, message)
            case MessageIntent.SYSTEM_COMMAND:
                response = await self._handle_system_command(message)
            case MessageIntent.SIMPLE_QUERY:
                response = await self._handle_simple_query(message)
            case MessageIntent.COMPLEX_TASK:
                response = await self._handle_complex_task(user_id, channel_id, message, routing)
            # Skill intents (Phase 5G)
            case MessageIntent.TASK_MANAGEMENT:
                response = await self._handle_skill_intent(user_id, message, "task_manager")
            case MessageIntent.CALENDAR_QUERY:
                response = await self._handle_skill_intent(user_id, message, "calendar")
            case MessageIntent.PROFILE_QUERY:
                response = await self._handle_skill_intent(user_id, message, "profile_manager")
            case _:
                response = await self._handle_complex_task(user_id, channel_id, message, routing)

        handle_end = time.perf_counter()
        log.info(
            "TIMING: intent_handled",
            intent=routing.intent.value,
            handle_duration_ms=round((handle_end - handle_start) * 1000, 2),
        )

        # Step 3: Store the exchange in memory
        store_start = time.perf_counter()
        await self._memory.store_message(
            user_id=user_id,
            channel_id=channel_id,
            role="user",
            content=message,
            metadata={"intent": routing.intent.value},
        )
        store_user_end = time.perf_counter()
        log.info(
            "TIMING: stored_user_message",
            duration_ms=round((store_user_end - store_start) * 1000, 2),
        )

        await self._memory.store_message(
            user_id=user_id,
            channel_id=channel_id,
            role="assistant",
            content=response,
        )
        store_assistant_end = time.perf_counter()
        log.info(
            "TIMING: stored_assistant_message",
            duration_ms=round((store_assistant_end - store_user_end) * 1000, 2),
        )

        total_end = time.perf_counter()
        log.info(
            "TIMING: generate_response_total",
            total_duration_ms=round((total_end - total_start) * 1000, 2),
            classify_ms=round((classify_end - classify_start) * 1000, 2),
            handle_ms=round((handle_end - handle_start) * 1000, 2),
            store_ms=round((store_assistant_end - store_start) * 1000, 2),
        )

        return response

    async def _handle_simple_query(self, message: str) -> str:
        """Handle simple queries with Gemini Flash (cheap/fast).

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        log.debug("handling_simple_query")
        return await self._router.generate_simple_response(message)

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
                "I'm having trouble connecting to my skills service. "
                "Please try again in a moment."
            )

        # Determine intent based on skill name
        intent_map = {
            "task_manager": self._parse_task_intent(message),
            "calendar": self._parse_calendar_intent(message),
            "profile_manager": self._parse_profile_intent(message),
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
                return response.message or "Done!"
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

    async def _build_context(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        memory_limit: int = 5,
        history_limit: int = 20,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Build context from recent messages and relevant memories.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.
            memory_limit: Maximum number of memories to retrieve.
            history_limit: Maximum number of recent messages to retrieve.

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
            self._memory.search_memories(query=message, limit=memory_limit),
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
        task_start = time.perf_counter()

        # Fetch context once for reuse across models
        context_start = time.perf_counter()
        recent_messages, relevant_memories = await self._build_context(user_id, channel_id, message)
        context_end = time.perf_counter()
        log.info(
            "TIMING: context_built",
            duration_ms=round((context_end - context_start) * 1000, 2),
            memories_found=len(relevant_memories),
            messages_found=len(recent_messages),
        )

        # Build system prompt with context
        system_prompt = SYSTEM_PROMPT
        if relevant_memories:
            memory_text = "\n".join(
                f"- {m['content']}" for m in relevant_memories if m["score"] > 0.7
            )
            if memory_text:
                system_prompt = f"{SYSTEM_PROMPT}\n\n## Relevant Memories\n{memory_text}"

        # Format conversation history
        messages = [
            {"role": msg["role"], "content": msg["content"]} for msg in recent_messages[-10:]
        ]

        # Use InferenceBroker if available (Phase 5B)
        if self._inference_broker:
            # Classify task type for optimal provider selection
            task_type = self._classify_task_type(message)
            log.debug("task_type_classified", task_type=task_type.value)

            infer_start = time.perf_counter()
            result = await self._inference_broker.infer(
                prompt=message,
                task_type=task_type,
                system_prompt=system_prompt,
                messages=messages,
            )
            infer_end = time.perf_counter()
            log.info(
                "TIMING: inference_complete",
                duration_ms=round((infer_end - infer_start) * 1000, 2),
                provider=result.provider.value,
                model=result.model,
                total_task_ms=round((infer_end - task_start) * 1000, 2),
            )
            return result.content

        # Fallback to legacy routing (Claude -> OpenAI -> Gemini)
        if routing.use_claude:
            if self._has_claude:
                return await self._generate_claude_response(
                    user_id, channel_id, message, recent_messages, relevant_memories
                )
            elif self._has_openai:
                return await self._generate_openai_response(
                    user_id, channel_id, message, recent_messages, relevant_memories
                )

        # Otherwise use Gemini Flash
        log.debug("using_flash_for_complex", reason="no_complex_model_available")
        return await self._router.generate_simple_response(message)

    def _classify_task_type(self, message: str) -> TaskType:
        """Classify the task type from the message content.

        Args:
            message: The user's message.

        Returns:
            The classified TaskType for provider selection.
        """
        lower_msg = message.lower()

        # Code-related patterns
        if any(
            kw in lower_msg
            for kw in [
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
            ]
        ):
            if any(kw in lower_msg for kw in ["review", "audit", "check"]):
                return TaskType.CODE_REVIEW
            elif any(kw in lower_msg for kw in ["debug", "fix", "error", "bug"]):
                return TaskType.CODE_DEBUGGING
            return TaskType.CODE_GENERATION

        # Math/reasoning patterns
        if any(
            kw in lower_msg
            for kw in [
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
            ]
        ):
            if any(kw in lower_msg for kw in ["math", "calculate", "equation"]):
                return TaskType.MATH_ANALYSIS
            return TaskType.COMPLEX_REASONING

        # Creative patterns
        if any(
            kw in lower_msg
            for kw in [
                "write",
                "story",
                "poem",
                "creative",
                "imagine",
                "fiction",
                "narrative",
                "character",
                "plot",
            ]
        ):
            return TaskType.CREATIVE_WRITING

        # Long document patterns
        if any(
            kw in lower_msg
            for kw in [
                "summarize",
                "summary",
                "tldr",
                "condense",
            ]
        ):
            return TaskType.SUMMARIZATION

        # Default to conversation for general queries
        return TaskType.CONVERSATION

    async def _generate_openai_response(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        recent_messages: list[dict[str, Any]],
        relevant_memories: list[dict[str, Any]],
    ) -> str:
        """Generate a response using OpenAI for complex tasks.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.
            recent_messages: Recent conversation history.
            relevant_memories: Relevant memories from search.

        Returns:
            Generated response.
        """
        if not self._openai_client:
            return await self._router.generate_simple_response(message)

        # Build context from provided data
        context_parts = []
        if relevant_memories:
            memory_text = "\n".join(
                f"- {m['content']}" for m in relevant_memories if m["score"] > 0.7
            )
            if memory_text:
                context_parts.append(f"## Relevant Memories\n{memory_text}")

        messages = []
        # System prompt
        system_content = SYSTEM_PROMPT
        if context_parts:
            system_content = f"{SYSTEM_PROMPT}\n\n" + "\n\n".join(context_parts)

        messages.append({"role": "system", "content": system_content})

        # History
        for msg in recent_messages[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": message})

        try:
            # Use retry logic for API calls
            async def make_request() -> Any:
                return await self._openai_client.chat.completions.create(  # type: ignore[union-attr]
                    model=self._openai_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=2048,
                )

            response = await retry_with_exponential_backoff(make_request)
            return response.choices[0].message.content or ""
        except (OpenAIConnectionError, OpenAITimeoutError, OpenAIRateLimitError) as e:
            log.error("openai_connection_error", error=str(e), error_type=type(e).__name__)
            return await self._router.generate_simple_response(message)
        except Exception as e:
            log.error("openai_api_error", error=str(e), error_type=type(e).__name__)
            return await self._router.generate_simple_response(message)

    async def _generate_claude_response(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        recent_messages: list[dict[str, Any]],
        relevant_memories: list[dict[str, Any]],
    ) -> str:
        """Generate a response using Claude for complex tasks.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            message: The user's message.
            recent_messages: Recent conversation history.
            relevant_memories: Relevant memories from search.

        Returns:
            Generated response.
        """
        if not self._claude_client:
            return await self._router.generate_simple_response(message)

        # Build context from provided data
        context_parts = []

        if relevant_memories:
            memory_text = "\n".join(
                f"- {m['content']}" for m in relevant_memories if m["score"] > 0.7
            )
            if memory_text:
                context_parts.append(f"## Relevant Memories\n{memory_text}")

        # Build message history for Claude
        messages = []
        for msg in recent_messages[-10:]:
            messages.append(
                {
                    "role": msg["role"],
                    "content": msg["content"],
                }
            )
        messages.append(
            {
                "role": "user",
                "content": message,
            }
        )

        # Build system prompt with context
        system = SYSTEM_PROMPT
        if context_parts:
            system = f"{SYSTEM_PROMPT}\n\n" + "\n\n".join(context_parts)

        try:
            # Use retry logic for API calls
            async def make_request() -> Any:
                return await self._claude_client.messages.create(  # type: ignore[union-attr]
                    model=self._claude_model,
                    max_tokens=2048,
                    system=system,
                    messages=messages,  # type: ignore[arg-type]
                )

            response = await retry_with_exponential_backoff(make_request)

            log.debug(
                "claude_response_generated",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            return response.content[0].text  # type: ignore[no-any-return]

        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            log.error("claude_connection_error", error=str(e), error_type=type(e).__name__)
            # Fallback to Flash
            return await self._router.generate_simple_response(message)
        except anthropic.APIError as e:
            log.error("claude_api_error", error=str(e), error_type=type(e).__name__)
            # Fallback to Flash
            return await self._router.generate_simple_response(message)
        except Exception as e:
            log.error("claude_unexpected_error", error=str(e), error_type=type(e).__name__)
            return await self._router.generate_simple_response(message)

    async def _handle_memory_store(self, message: str) -> str:
        """Handle memory storage requests.

        Args:
            message: The message containing what to remember.

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
        memories = await self._memory.search_memories(query=query, limit=5)
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
            return """Hi! I'm Zetherion, your personal AI assistant. Here's what I can do:

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

        return "I'm not sure what you're asking. Try saying 'help' to see what I can do!"

    async def store_memory_from_request(
        self,
        content: str,
        memory_type: str = "general",
    ) -> str:
        """Store a memory based on explicit user request.

        Args:
            content: The memory content to store.
            memory_type: Type of memory.

        Returns:
            Confirmation message.
        """
        await self._memory.store_memory(
            content=content,
            memory_type=memory_type,
        )
        return f"I've stored that in my memory: {content}"
