"""Task-type dispatchers for the priority message queue.

Each :class:`QueueTaskType` maps to a processor function that receives
the :class:`QueueItem` payload and a reference to the bot/services needed
to fulfil the request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from zetherion_ai.logging import get_logger
from zetherion_ai.queue.models import QueueTaskType
from zetherion_ai.utils import split_text_chunks

if TYPE_CHECKING:
    import discord

    from zetherion_ai.agent.core import Agent
    from zetherion_ai.scheduler.actions import ActionExecutor
    from zetherion_ai.skills.client import SkillsClient

log = get_logger("zetherion_ai.queue.processors")


# ---------------------------------------------------------------------------
# Protocols for loose coupling
# ---------------------------------------------------------------------------


class BotLike(Protocol):
    """Minimal bot interface needed by the queue processors."""

    def get_channel(self, channel_id: int, /) -> Any: ...


class ReplySender(Protocol):
    """Protocol for sending long replies split across messages."""

    async def send_long_reply(
        self,
        message: discord.Message,
        content: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Processor results
# ---------------------------------------------------------------------------


class ProcessorResult:
    """Outcome of processing a single queue item."""

    __slots__ = ("success", "error", "data")

    def __init__(
        self,
        success: bool = True,
        error: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.error = error
        self.data = data or {}


# ---------------------------------------------------------------------------
# Processor registry
# ---------------------------------------------------------------------------


class QueueProcessors:
    """Dispatch queue items to the appropriate handler by task type.

    Handlers are registered at construction time with the services they
    need.  Unknown task types are logged and treated as successful (no
    retry) to avoid infinite dead-letter loops.
    """

    def __init__(
        self,
        *,
        bot: BotLike | None = None,
        agent: Agent | None = None,
        skills_client: SkillsClient | None = None,
        action_executor: ActionExecutor | None = None,
    ) -> None:
        self._bot = bot
        self._agent = agent
        self._skills_client = skills_client
        self._action_executor = action_executor

    async def process(self, task_type: str, payload: dict[str, Any]) -> ProcessorResult:
        """Route a queue item to its handler.

        Args:
            task_type: The :class:`QueueTaskType` value.
            payload: The JSON payload stored in the queue row.

        Returns:
            A :class:`ProcessorResult`.
        """
        handlers = {
            QueueTaskType.DISCORD_MESSAGE: self._handle_discord_message,
            QueueTaskType.SKILL_REQUEST: self._handle_skill_request,
            QueueTaskType.HEARTBEAT_ACTION: self._handle_heartbeat_action,
            QueueTaskType.BULK_INGESTION: self._handle_bulk_ingestion,
        }

        try:
            key = QueueTaskType(task_type)
        except ValueError:
            log.warning("unknown_task_type", task_type=task_type)
            return ProcessorResult(success=True, error=f"Unknown task type: {task_type}")

        handler = handlers.get(key)
        if handler is None:
            log.warning("unknown_task_type", task_type=task_type)
            return ProcessorResult(success=True, error=f"Unknown task type: {task_type}")

        try:
            result: ProcessorResult = await handler(payload)
            return result
        except Exception as exc:
            log.exception("processor_error", task_type=task_type)
            return ProcessorResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Individual handlers
    # ------------------------------------------------------------------

    async def _handle_discord_message(self, payload: dict[str, Any]) -> ProcessorResult:
        """Process an interactive Discord message.

        Expected payload keys:
            channel_id (int): Discord channel ID.
            message_id (int): Discord message ID (to fetch the original).
            content (str): Pre-extracted message text (fallback if fetch fails).
            user_id (int): Author's Discord user ID.
            is_mention (bool): Whether the bot was mentioned.
        """
        if self._bot is None or self._agent is None:
            return ProcessorResult(success=False, error="Bot or agent not available")

        channel_id = payload.get("channel_id")
        message_id = payload.get("message_id")
        content = payload.get("content", "")
        user_id = payload.get("user_id", 0)

        if not content:
            return ProcessorResult(success=False, error="Empty message content")

        # Try to fetch the original message for replying
        message_obj: Any | None = None
        if channel_id and message_id:
            channel = self._bot.get_channel(channel_id)
            if channel is not None and hasattr(channel, "fetch_message"):
                try:
                    message_obj = await channel.fetch_message(message_id)
                except Exception:
                    log.debug("message_fetch_failed", message_id=message_id)

        # Generate response through the agent
        response = await self._agent.generate_response(
            user_id=user_id,
            channel_id=channel_id or 0,
            message=content,
        )

        # Reply to the original message or send to channel
        if message_obj is not None:
            await self._send_reply(message_obj, response)
        elif channel_id:
            channel = self._bot.get_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                for chunk in split_text_chunks(response, max_length=2000):
                    await channel.send(chunk)

        log.debug(
            "discord_message_processed",
            user_id=user_id,
            response_length=len(response),
        )
        return ProcessorResult(success=True)

    async def _handle_skill_request(self, payload: dict[str, Any]) -> ProcessorResult:
        """Process a skill request.

        Expected payload keys:
            user_id (str): The user requesting the skill.
            intent (str): Skill intent string.
            message (str): Original message text.
            context (dict): Additional context for the skill.
        """
        if self._skills_client is None:
            return ProcessorResult(success=False, error="Skills client not available")

        from zetherion_ai.skills.base import SkillRequest

        request = SkillRequest(
            user_id=str(payload.get("user_id", "")),
            intent=payload.get("intent", ""),
            message=payload.get("message", ""),
            context=payload.get("context", {}),
        )

        response = await self._skills_client.handle_request(request)
        return ProcessorResult(
            success=response.success,
            error=response.error if not response.success else None,
            data={"response": response.message},
        )

    async def _handle_heartbeat_action(self, payload: dict[str, Any]) -> ProcessorResult:
        """Process a heartbeat action.

        Expected payload keys:
            skill_name (str): Source skill.
            action_type (str): Action to execute.
            user_id (str): Target user.
            data (dict): Action-specific data.
            priority (int): Action priority.
        """
        if self._action_executor is None:
            return ProcessorResult(success=False, error="Action executor not available")

        from zetherion_ai.skills.base import HeartbeatAction

        action = HeartbeatAction(
            skill_name=payload.get("skill_name", ""),
            action_type=payload.get("action_type", ""),
            user_id=payload.get("user_id", ""),
            data=payload.get("data", {}),
            priority=payload.get("priority", 5),
        )

        result = await self._action_executor.execute(action)
        return ProcessorResult(
            success=result.success,
            error=result.error,
        )

    async def _handle_bulk_ingestion(self, payload: dict[str, Any]) -> ProcessorResult:
        """Process a bulk ingestion task.

        Expected payload keys:
            source (str): Ingestion source (e.g. "email", "youtube").
            operation (str): Specific operation to perform.
            data (dict): Source-specific data.
        """
        source = payload.get("source", "unknown")
        operation = payload.get("operation", "unknown")

        log.info(
            "bulk_ingestion_started",
            source=source,
            operation=operation,
        )

        # Route to the appropriate skill if available
        if self._skills_client is not None:
            from zetherion_ai.skills.base import SkillRequest

            request = SkillRequest(
                user_id=str(payload.get("user_id", "")),
                intent=f"{source}_{operation}",
                message="",
                context=payload.get("data", {}),
            )
            response = await self._skills_client.handle_request(request)
            return ProcessorResult(
                success=response.success,
                error=response.error if not response.success else None,
            )

        log.warning("bulk_ingestion_no_client", source=source)
        return ProcessorResult(success=False, error="No skills client for bulk ingestion")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _send_reply(message: Any, content: str, max_length: int = 2000) -> None:
        """Send a reply, splitting if necessary."""
        if len(content) <= max_length:
            await message.reply(content, mention_author=True)
            return

        parts = split_text_chunks(content, max_length=max_length)

        for i, part in enumerate(parts):
            if part:
                if i == 0:
                    await message.reply(part, mention_author=True)
                else:
                    await message.channel.send(part)
