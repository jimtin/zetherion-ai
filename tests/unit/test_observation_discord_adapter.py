"""Unit tests for the Discord observation adapter."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from zetherion_ai.observation.adapters.discord import (
    MAX_HISTORY_MESSAGES,
    DiscordObservationAdapter,
)
from zetherion_ai.observation.models import ObservationEvent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWNER_ID = 12345
BOT_ID = 99999


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    content="Hello world",
    author_id=OWNER_ID,
    channel_id=100,
    message_id=1000,
    guild_id=500,
    is_bot=False,
    has_reference=False,
):
    """Build a mock discord.Message with sensible defaults."""
    msg = MagicMock()
    msg.content = content
    msg.id = message_id
    msg.author.id = author_id
    msg.author.bot = is_bot
    msg.author.__str__ = lambda self: f"User#{author_id}"
    msg.channel.id = channel_id
    msg.guild.id = guild_id
    msg.created_at = datetime(2026, 1, 15, 10, 30, 0)
    msg.reference = None
    if has_reference:
        msg.reference = MagicMock()
        msg.reference.message_id = 999
    # No thread-like attributes by default
    del msg.channel.parent_id
    return msg


def _make_adapter(**kwargs):
    """Create a DiscordObservationAdapter with sensible defaults."""
    defaults = {"owner_user_id": OWNER_ID}
    defaults.update(kwargs)
    return DiscordObservationAdapter(**defaults)


# ===========================================================================
# TestConstructor
# ===========================================================================


class TestConstructor:
    """Tests for DiscordObservationAdapter.__init__."""

    def test_default_stores_owner_id(self):
        """Constructor stores the owner_user_id."""
        adapter = _make_adapter()
        assert adapter._owner_user_id == OWNER_ID

    def test_observed_channels_none_defaults_to_empty_set(self):
        """When observed_channels is None the internal set is empty."""
        adapter = _make_adapter(observed_channels=None)
        assert adapter._observed_channels == set()
        assert adapter.list_channels() == set()

    def test_custom_observed_channels_stored(self):
        """Explicit observed_channels are stored correctly."""
        channels = {100, 200, 300}
        adapter = _make_adapter(observed_channels=channels)
        assert adapter._observed_channels == channels


# ===========================================================================
# TestChannelManagement
# ===========================================================================


class TestChannelManagement:
    """Tests for add_channel / remove_channel / list_channels / is_observed."""

    def test_add_channel_adds_to_set(self):
        """add_channel makes the channel observed."""
        adapter = _make_adapter()
        adapter.add_channel(42)
        assert 42 in adapter._observed_channels

    def test_add_channel_idempotent(self):
        """Adding the same channel twice does not duplicate."""
        adapter = _make_adapter()
        adapter.add_channel(42)
        adapter.add_channel(42)
        assert adapter._observed_channels == {42}

    def test_remove_channel_removes(self):
        """remove_channel removes an observed channel."""
        adapter = _make_adapter(observed_channels={42, 43})
        adapter.remove_channel(42)
        assert 42 not in adapter._observed_channels
        assert 43 in adapter._observed_channels

    def test_remove_channel_nonexistent_no_error(self):
        """Removing a channel that was never added does not raise."""
        adapter = _make_adapter()
        adapter.remove_channel(999)  # should not raise

    def test_list_channels_returns_copy(self):
        """list_channels returns a *copy*, not the internal set."""
        adapter = _make_adapter(observed_channels={1, 2, 3})
        result = adapter.list_channels()
        assert result == {1, 2, 3}
        result.add(999)
        # Internal set must be untouched
        assert 999 not in adapter._observed_channels

    def test_is_observed_true_false(self):
        """is_observed returns True for added channels, False otherwise."""
        adapter = _make_adapter(observed_channels={10})
        assert adapter.is_observed(10) is True
        assert adapter.is_observed(20) is False


# ===========================================================================
# TestShouldProcess
# ===========================================================================


class TestShouldProcess:
    """Tests for the message filtering logic in should_process."""

    def _adapter_with_channel(self, channel_id=100, **kwargs):
        """Return an adapter that already observes *channel_id*."""
        kw = {"observed_channels": {channel_id}}
        kw.update(kwargs)
        return _make_adapter(**kw)

    # -- basic filtering ---------------------------------------------------

    def test_bot_message_rejected(self):
        """Messages from bot accounts are rejected."""
        adapter = self._adapter_with_channel()
        msg = _make_message(is_bot=True)
        assert adapter.should_process(msg) is False

    def test_channel_not_observed_rejected(self):
        """Messages from non-observed channels are rejected."""
        adapter = _make_adapter(observed_channels={200})
        msg = _make_message(channel_id=100)
        assert adapter.should_process(msg) is False

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_owner_message_in_observed_channel_accepted(self, mock_time):
        """Owner message in an observed channel passes all filters."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel()
        msg = _make_message(author_id=OWNER_ID)
        assert adapter.should_process(msg) is True

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_non_owner_rejected_in_owner_only_mode(self, mock_time):
        """Non-owner messages are rejected when owner_only=True."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel(owner_only=True)
        msg = _make_message(author_id=99999)
        assert adapter.should_process(msg) is False

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_non_owner_accepted_when_owner_only_false(self, mock_time):
        """Non-owner messages are accepted when owner_only=False."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel(owner_only=False)
        msg = _make_message(author_id=99999)
        assert adapter.should_process(msg) is True

    # -- cooldown ----------------------------------------------------------

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_within_cooldown_rejected(self, mock_time):
        """Message within channel cooldown is rejected."""
        mock_time.monotonic.side_effect = [100.0, 102.0]
        adapter = self._adapter_with_channel(channel_cooldown=5.0)
        # Manually set last-processed to simulate a prior message
        adapter._last_processed[100] = 100.0
        msg = _make_message()
        assert adapter.should_process(msg) is False

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_after_cooldown_accepted(self, mock_time):
        """Message after cooldown period passes."""
        mock_time.monotonic.return_value = 110.0
        adapter = self._adapter_with_channel(channel_cooldown=5.0)
        adapter._last_processed[100] = 100.0
        msg = _make_message()
        assert adapter.should_process(msg) is True

    # -- content filtering -------------------------------------------------

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_empty_content_rejected(self, mock_time):
        """Empty-string content is rejected."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel()
        msg = _make_message(content="")
        assert adapter.should_process(msg) is False

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_slash_command_rejected(self, mock_time):
        """Messages starting with '/' are treated as slash commands."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel()
        msg = _make_message(content="/ask something")
        assert adapter.should_process(msg) is False

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_whitespace_only_content_rejected(self, mock_time):
        """Whitespace-only content is treated as empty."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._adapter_with_channel()
        msg = _make_message(content="   \t\n  ")
        assert adapter.should_process(msg) is False


# ===========================================================================
# TestAdapt
# ===========================================================================


class TestAdapt:
    """Tests for adapt() — full message-to-ObservationEvent conversion."""

    def _ready_adapter(self, channel_id=100, **kwargs):
        """Return an adapter that will accept the next message."""
        kw = {"observed_channels": {channel_id}}
        kw.update(kwargs)
        return _make_adapter(**kw)

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_returns_none_for_unprocessable(self, mock_time):
        """adapt() returns None when should_process is False."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(is_bot=True)
        assert adapter.adapt(msg) is None

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_returns_observation_event(self, mock_time):
        """adapt() returns an ObservationEvent for valid messages."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message()
        result = adapter.adapt(msg)
        assert isinstance(result, ObservationEvent)

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_event_source_and_source_id(self, mock_time):
        """Event has source='discord' and source_id=str(message.id)."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(message_id=7777)
        event = adapter.adapt(msg)
        assert event.source == "discord"
        assert event.source_id == "7777"

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_event_user_and_author_fields(self, mock_time):
        """Event carries correct user_id, author, author_is_owner."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(author_id=OWNER_ID)
        event = adapter.adapt(msg)
        assert event.user_id == OWNER_ID
        assert event.author == str(msg.author)
        assert event.author_is_owner is True

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_event_content_and_timestamp(self, mock_time):
        """Event carries the message content and timestamp."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(content="Buy milk")
        event = adapter.adapt(msg)
        assert event.content == "Buy milk"
        assert event.timestamp == datetime(2026, 1, 15, 10, 30, 0)

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_event_context_channel_and_guild(self, mock_time):
        """Event context includes channel_id and guild_id."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(channel_id=100, guild_id=500)
        event = adapter.adapt(msg)
        assert event.context["channel_id"] == 100
        assert event.context["guild_id"] == 500

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_event_context_reply_to_message_id(self, mock_time):
        """reply_to_message_id is set when the message has a reference."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter()
        msg = _make_message(has_reference=True)
        event = adapter.adapt(msg)
        assert event.context["reply_to_message_id"] == 999

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_adapt_updates_cooldown(self, mock_time):
        """After adapt(), a second call within cooldown returns None."""
        mock_time.monotonic.side_effect = [
            1000.0,  # should_process check
            1000.0,  # adapt records timestamp
            1002.0,  # second should_process check (within 5s cooldown)
        ]
        adapter = self._ready_adapter(channel_cooldown=5.0)
        msg1 = _make_message()
        msg2 = _make_message(message_id=1001)
        first = adapter.adapt(msg1)
        second = adapter.adapt(msg2)
        assert first is not None
        assert second is None

    @patch("zetherion_ai.observation.adapters.discord.time")
    def test_thread_message_includes_thread_context(self, mock_time):
        """Channel with parent_id produces thread context keys."""
        mock_time.monotonic.return_value = 1000.0
        adapter = self._ready_adapter(channel_id=100)
        msg = _make_message(channel_id=100)
        # Simulate a thread channel by adding parent_id back
        msg.channel.parent_id = 50
        event = adapter.adapt(msg)
        assert event.context["thread_id"] == 100
        assert event.context["parent_channel_id"] == 50


# ===========================================================================
# TestBuildHistory
# ===========================================================================


class TestBuildHistory:
    """Tests for _build_history — conversation context from cache."""

    def test_no_cache_returns_empty(self):
        """Without _state on the channel, history is empty."""
        adapter = _make_adapter()
        msg = _make_message()
        # channel has no _state, but MagicMock auto-creates attrs.
        # Ensure `history` attribute exists but _state._messages doesn't.
        del msg.channel._state
        result = adapter._build_history(msg)
        assert result == []

    def test_with_cache_returns_recent_messages(self):
        """Cached messages are returned as 'author: content' strings."""
        adapter = _make_adapter()
        msg = _make_message(channel_id=100, message_id=1000)

        # Build fake cached messages
        cached_msg1 = MagicMock()
        cached_msg1.channel.id = 100
        cached_msg1.id = 998
        cached_msg1.content = "Hey there"
        cached_msg1.author = "Alice"

        cached_msg2 = MagicMock()
        cached_msg2.channel.id = 100
        cached_msg2.id = 999
        cached_msg2.content = "What's up?"
        cached_msg2.author = "Bob"

        # Wire up _state._messages
        msg.channel._state._messages = [cached_msg1, cached_msg2]

        result = adapter._build_history(msg)
        assert len(result) == 2
        assert "Alice: Hey there" in result
        assert "Bob: What's up?" in result

    def test_returns_at_most_max_history_messages(self):
        """History is capped at MAX_HISTORY_MESSAGES."""
        adapter = _make_adapter()
        msg = _make_message(channel_id=100, message_id=9999)

        # Create more cached messages than the limit
        cached = []
        for i in range(MAX_HISTORY_MESSAGES + 10):
            m = MagicMock()
            m.channel.id = 100
            m.id = i
            m.content = f"msg {i}"
            m.author = f"User{i}"
            cached.append(m)

        msg.channel._state._messages = cached
        result = adapter._build_history(msg)
        assert len(result) <= MAX_HISTORY_MESSAGES
