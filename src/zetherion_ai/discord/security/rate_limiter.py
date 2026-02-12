"""Discord rate limiting utilities.

Moved from the former ``security.py`` module into this dedicated file
as part of the security package refactor.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class RateLimitState:
    """Track rate limit state for a user."""

    message_timestamps: list[float] = field(default_factory=list)
    last_warning: float = 0.0


class RateLimiter:
    """Rate limiter for Discord messages."""

    def __init__(
        self,
        max_messages: int = 10,
        window_seconds: float = 60.0,
        warning_cooldown: float = 30.0,
    ) -> None:
        """Initialize rate limiter.

        Args:
            max_messages: Maximum messages allowed per window.
            window_seconds: Time window in seconds.
            warning_cooldown: Minimum time between warnings.
        """
        self._max_messages = max_messages
        self._window_seconds = window_seconds
        self._warning_cooldown = warning_cooldown
        self._states: dict[int, RateLimitState] = defaultdict(RateLimitState)

    def check(self, user_id: int) -> tuple[bool, str | None]:
        """Check if a user is rate limited.

        Args:
            user_id: The Discord user ID.

        Returns:
            Tuple of (is_allowed, warning_message).
        """
        now = time.time()
        state = self._states[user_id]

        # Clean old timestamps
        state.message_timestamps = [
            ts for ts in state.message_timestamps if now - ts < self._window_seconds
        ]

        # Check limit
        if len(state.message_timestamps) >= self._max_messages:
            warning = None
            if now - state.last_warning > self._warning_cooldown:
                warning = (
                    "You're sending messages too quickly. "
                    "Please wait a moment before trying again."
                )
                state.last_warning = now
            return False, warning

        # Record this message
        state.message_timestamps.append(now)
        return True, None
