"""Agent core - LLM interaction and response generation with routing."""

import asyncio
import time
from typing import Any

from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.prompts import SYSTEM_PROMPT
from zetherion_ai.agent.providers import TaskType
from zetherion_ai.agent.router import MessageIntent, RoutingDecision
from zetherion_ai.agent.router_factory import create_router_sync
from zetherion_ai.config import get_dynamic, get_settings
from zetherion_ai.constants import CONTEXT_HISTORY_LIMIT, MEMORY_SCORE_THRESHOLD
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient, SkillsClientError
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


class Agent:
    """Core agent that handles LLM interactions with intelligent routing."""

    def __init__(self, memory: QdrantMemory) -> None:
        """Initialize the agent.

        Args:
            memory: The memory system for context retrieval.
        """
        self._memory = memory
        self._router = create_router_sync()

        # Initialize InferenceBroker for smart multi-provider routing
        self._inference_broker = InferenceBroker()

        # Initialize Skills Client for skill-based intents (Phase 5G)
        self._skills_client: SkillsClient | None = None
        self._skills_enabled = False
        # Skills client is initialized lazily when first needed

        log.info(
            "agent_initialized",
            inference_broker_enabled=True,
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
        log.info(
            "message_routed",
            intent=routing.intent.value,
            use_claude=routing.use_claude,
            confidence=routing.confidence,
            duration_ms=t["elapsed_ms"],
        )

        # Step 2: Handle based on intent
        async with timed_operation("intent_handling") as t:
            match routing.intent:
                case MessageIntent.MEMORY_STORE:
                    response = await self._handle_memory_store(message, user_id=user_id)
                case MessageIntent.MEMORY_RECALL:
                    response = await self._handle_memory_recall(user_id, message)
                case MessageIntent.SYSTEM_COMMAND:
                    response = await self._handle_system_command(message)
                case MessageIntent.SIMPLE_QUERY:
                    response = await self._handle_simple_query(message)
                case MessageIntent.COMPLEX_TASK:
                    response = await self._handle_complex_task(
                        user_id,
                        channel_id,
                        message,
                        routing,
                    )
                # Skill intents (Phase 5G)
                case MessageIntent.TASK_MANAGEMENT:
                    response = await self._handle_skill_intent(user_id, message, "task_manager")
                case MessageIntent.CALENDAR_QUERY:
                    response = await self._handle_skill_intent(user_id, message, "calendar")
                case MessageIntent.PROFILE_QUERY:
                    response = await self._handle_skill_intent(
                        user_id,
                        message,
                        "profile_manager",
                    )
                case MessageIntent.PERSONAL_MODEL:
                    response = await self._handle_skill_intent(
                        user_id,
                        message,
                        "personal_model",
                    )
                case MessageIntent.EMAIL_MANAGEMENT:
                    response = await self._handle_skill_intent(
                        user_id,
                        message,
                        "gmail",
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
        if routing.intent not in (MessageIntent.SIMPLE_QUERY, MessageIntent.SYSTEM_COMMAND):
            async with timed_operation("memory_storage") as t:
                await self._memory.store_message(
                    user_id=user_id,
                    channel_id=channel_id,
                    role="user",
                    content=message,
                    metadata={"intent": routing.intent.value},
                )
                await self._memory.store_message(
                    user_id=user_id,
                    channel_id=channel_id,
                    role="assistant",
                    content=response,
                )
            log.debug("messages_stored", duration_ms=t["elapsed_ms"])

        total_end = time.perf_counter()
        log.info(
            "generate_response_complete",
            intent=routing.intent.value,
            total_duration_ms=round((total_end - total_start) * 1000, 2),
            message_length=len(message),
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
                "I'm having trouble connecting to my skills service. Please try again in a moment."
            )

        # Determine intent based on skill name
        intent_map = {
            "task_manager": self._parse_task_intent(message),
            "calendar": self._parse_calendar_intent(message),
            "profile_manager": self._parse_profile_intent(message),
            "personal_model": self._parse_personal_model_intent(message),
            "gmail": self._parse_email_intent(message),
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
                result_msg = response.message or "Done!"
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
        async with timed_operation("context_retrieval") as ctx_t:
            recent_messages, relevant_memories = await self._build_context(
                user_id,
                channel_id,
                message,
                user_id_filter=user_id,
            )
        log.info(
            "context_built",
            duration_ms=ctx_t["elapsed_ms"],
            memories_found=len(relevant_memories),
            messages_found=len(recent_messages),
        )

        # Build system prompt with context
        system_prompt = SYSTEM_PROMPT
        score_threshold = get_dynamic("tuning", "memory_score_threshold", MEMORY_SCORE_THRESHOLD)
        context_limit = get_dynamic("tuning", "context_history_limit", CONTEXT_HISTORY_LIMIT)

        if relevant_memories:
            memory_text = "\n".join(
                f"- {m['content']}" for m in relevant_memories if m["score"] > score_threshold
            )
            if memory_text:
                system_prompt = f"{SYSTEM_PROMPT}\n\n## Relevant Memories\n{memory_text}"

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
