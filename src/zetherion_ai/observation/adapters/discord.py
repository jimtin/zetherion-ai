"""Discord source adapter for the observation pipeline.

Converts discord.Message events into ObservationEvent format and feeds
them into the observation pipeline for entity extraction.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.models import ObservationEvent

if TYPE_CHECKING:
    import discord

log = get_logger("zetherion_ai.observation.adapters.discord")

# ---------------------------------------------------------------------------
# Rate limiter for observation (avoid processing every single message)
# ---------------------------------------------------------------------------

# Minimum seconds between processing messages from the same channel
DEFAULT_CHANNEL_COOLDOWN = 5.0

# Maximum conversation history to include for context
MAX_HISTORY_MESSAGES = 5


class DiscordObservationAdapter:
    """Converts Discord messages into ObservationEvents for the pipeline.

    Filters messages based on opt-in channels, owner-only mode, and
    rate limiting to avoid overwhelming the extraction pipeline.
    """

    def __init__(
        self,
        *,
        owner_user_id: int,
        observed_channels: set[int] | None = None,
        channel_cooldown: float = DEFAULT_CHANNEL_COOLDOWN,
        owner_only: bool = True,
    ) -> None:
        """Initialize the adapter.

        Args:
            owner_user_id: Discord user ID of the bot owner.
            observed_channels: Set of channel IDs to observe.
                If None, no channels are observed (must be opted in).
            channel_cooldown: Minimum seconds between processing
                messages from the same channel.
            owner_only: Only observe the bot owner's messages
                (not other users in the channel).
        """
        self._owner_user_id = owner_user_id
        self._observed_channels: set[int] = observed_channels or set()
        self._channel_cooldown = channel_cooldown
        self._owner_only = owner_only
        self._last_processed: dict[int, float] = {}  # channel_id → timestamp

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    def add_channel(self, channel_id: int) -> None:
        """Add a channel to the observation list."""
        self._observed_channels.add(channel_id)
        log.info("channel_added_to_observation", channel_id=channel_id)

    def remove_channel(self, channel_id: int) -> None:
        """Remove a channel from the observation list."""
        self._observed_channels.discard(channel_id)
        self._last_processed.pop(channel_id, None)
        log.info("channel_removed_from_observation", channel_id=channel_id)

    def list_channels(self) -> set[int]:
        """Return the set of observed channel IDs."""
        return set(self._observed_channels)

    def is_observed(self, channel_id: int) -> bool:
        """Check if a channel is being observed."""
        return channel_id in self._observed_channels

    # ------------------------------------------------------------------
    # Message filtering
    # ------------------------------------------------------------------

    def should_process(self, message: discord.Message) -> bool:
        """Determine if a message should be processed by the observation pipeline.

        Filters:
        1. Skip bot messages
        2. Skip if channel not in observed set
        3. Skip if owner_only and author is not the owner
        4. Skip if within channel cooldown period
        5. Skip empty/command messages
        """
        # Skip bot messages
        if message.author.bot:
            return False

        # Skip channels not being observed
        if message.channel.id not in self._observed_channels:
            return False

        # Owner-only mode
        if self._owner_only and message.author.id != self._owner_user_id:
            return False

        # Rate limiting per channel
        now = time.monotonic()
        last = self._last_processed.get(message.channel.id, 0.0)
        if now - last < self._channel_cooldown:
            return False

        # Skip empty messages
        if not message.content or not message.content.strip():
            return False

        # Skip slash commands (start with /)
        return not message.content.startswith("/")

    # ------------------------------------------------------------------
    # Adaptation
    # ------------------------------------------------------------------

    def adapt(self, message: discord.Message) -> ObservationEvent | None:
        """Convert a Discord message to an ObservationEvent.

        Returns None if the message should not be processed (fails
        filtering). Callers should use this as the single entry point.
        """
        if not self.should_process(message):
            return None

        # Update cooldown timestamp
        self._last_processed[message.channel.id] = time.monotonic()

        # Build context
        context: dict[str, Any] = {
            "channel_id": message.channel.id,
            "guild_id": getattr(message.guild, "id", None),
        }

        # Include thread info if applicable
        if hasattr(message.channel, "parent_id"):
            context["thread_id"] = message.channel.id
            context["parent_channel_id"] = getattr(message.channel, "parent_id", None)

        # Include reply context
        if message.reference and message.reference.message_id:
            context["reply_to_message_id"] = message.reference.message_id

        # Build conversation history from recent messages in cache
        history = self._build_history(message)

        event = ObservationEvent(
            source="discord",
            source_id=str(message.id),
            user_id=self._owner_user_id,
            author=str(message.author),
            author_is_owner=message.author.id == self._owner_user_id,
            content=message.content,
            timestamp=message.created_at.replace(tzinfo=None)
            if message.created_at.tzinfo
            else message.created_at,
            context=context,
            conversation_history=history,
        )

        log.debug(
            "discord_message_adapted",
            source_id=event.source_id,
            channel_id=message.channel.id,
            content_length=len(message.content),
        )

        return event

    def _build_history(self, message: discord.Message) -> list[str]:
        """Build conversation history from the message's channel cache.

        Uses discord.py's internal message cache for recent messages.
        Returns up to MAX_HISTORY_MESSAGES recent messages.
        """
        history: list[str] = []

        # discord.py caches recent messages per channel
        if hasattr(message.channel, "history"):
            # We can't await here (sync method), so use cached messages
            cached = getattr(message.channel, "_state", None)
            if cached and hasattr(cached, "_messages"):
                # Get messages from the deque, filter to this channel
                for msg in list(cached._messages)[-MAX_HISTORY_MESSAGES * 2 :]:
                    if (
                        getattr(msg, "channel", None)
                        and msg.channel.id == message.channel.id
                        and msg.id != message.id
                        and msg.content
                    ):
                        history.append(f"{msg.author}: {msg.content}")
                        if len(history) >= MAX_HISTORY_MESSAGES:
                            break

        return history
