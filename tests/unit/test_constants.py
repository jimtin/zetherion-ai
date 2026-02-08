"""Unit tests for the constants module.

Verifies that all centralized constants exist with correct types and values.
"""

from zetherion_ai.constants import (
    CONTEXT_HISTORY_LIMIT,
    DEFAULT_MAX_TOKENS,
    HEALTH_CHECK_TIMEOUT,
    KEEP_WARM_INTERVAL_SECONDS,
    MAX_DISCORD_MESSAGE_LENGTH,
    MEMORY_SCORE_THRESHOLD,
)


class TestConstants:
    """Tests for centralized application constants."""

    def test_max_discord_message_length_value(self) -> None:
        """MAX_DISCORD_MESSAGE_LENGTH should be 2000."""
        assert MAX_DISCORD_MESSAGE_LENGTH == 2000

    def test_max_discord_message_length_type(self) -> None:
        """MAX_DISCORD_MESSAGE_LENGTH should be an int."""
        assert isinstance(MAX_DISCORD_MESSAGE_LENGTH, int)

    def test_keep_warm_interval_seconds_value(self) -> None:
        """KEEP_WARM_INTERVAL_SECONDS should be 300."""
        assert KEEP_WARM_INTERVAL_SECONDS == 300

    def test_keep_warm_interval_seconds_type(self) -> None:
        """KEEP_WARM_INTERVAL_SECONDS should be an int."""
        assert isinstance(KEEP_WARM_INTERVAL_SECONDS, int)

    def test_context_history_limit_value(self) -> None:
        """CONTEXT_HISTORY_LIMIT should be 10."""
        assert CONTEXT_HISTORY_LIMIT == 10

    def test_context_history_limit_type(self) -> None:
        """CONTEXT_HISTORY_LIMIT should be an int."""
        assert isinstance(CONTEXT_HISTORY_LIMIT, int)

    def test_memory_score_threshold_value(self) -> None:
        """MEMORY_SCORE_THRESHOLD should be 0.7."""
        assert MEMORY_SCORE_THRESHOLD == 0.7

    def test_memory_score_threshold_type(self) -> None:
        """MEMORY_SCORE_THRESHOLD should be a float."""
        assert isinstance(MEMORY_SCORE_THRESHOLD, float)

    def test_default_max_tokens_value(self) -> None:
        """DEFAULT_MAX_TOKENS should be 2048."""
        assert DEFAULT_MAX_TOKENS == 2048

    def test_default_max_tokens_type(self) -> None:
        """DEFAULT_MAX_TOKENS should be an int."""
        assert isinstance(DEFAULT_MAX_TOKENS, int)

    def test_health_check_timeout_value(self) -> None:
        """HEALTH_CHECK_TIMEOUT should be 5."""
        assert HEALTH_CHECK_TIMEOUT == 5

    def test_health_check_timeout_type(self) -> None:
        """HEALTH_CHECK_TIMEOUT should be an int."""
        assert isinstance(HEALTH_CHECK_TIMEOUT, int)
