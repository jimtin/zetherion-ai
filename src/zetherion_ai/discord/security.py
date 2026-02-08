"""Discord rate limiting and security utilities."""

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.discord.security")


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
                    "You're sending messages too quickly. Please wait a moment before trying again."
                )
                state.last_warning = now
            return False, warning

        # Record this message
        state.message_timestamps.append(now)
        return True, None


class UserAllowlist:
    """Manage allowed Discord users."""

    def __init__(self) -> None:
        """Initialize the allowlist."""
        settings = get_settings()
        self._allowed_ids: set[int] = set(settings.allowed_user_ids)
        self._allow_all = len(self._allowed_ids) == 0 and settings.allow_all_users

        if len(self._allowed_ids) == 0 and not settings.allow_all_users:
            log.error(
                "allowlist_misconfigured",
                message=(
                    "No allowed users configured and ALLOW_ALL_USERS is False. "
                    "All users will be blocked."
                ),
            )
        elif self._allow_all:
            log.warning(
                "allowlist_open",
                message="ALLOW_ALL_USERS is True with no allowlist — all users can interact",
            )
        else:
            log.info("allowlist_configured", count=len(self._allowed_ids))

    def is_allowed(self, user_id: int) -> bool:
        """Check if a user is allowed.

        Args:
            user_id: The Discord user ID.

        Returns:
            True if allowed, False otherwise.
        """
        if self._allow_all:
            return True
        return user_id in self._allowed_ids

    def add(self, user_id: int) -> None:
        """Add a user to the allowlist.

        Args:
            user_id: The Discord user ID to add.
        """
        self._allowed_ids.add(user_id)
        self._allow_all = False
        log.info("user_added_to_allowlist", user_id=user_id)

    def remove(self, user_id: int) -> None:
        """Remove a user from the allowlist.

        Args:
            user_id: The Discord user ID to remove.
        """
        self._allowed_ids.discard(user_id)
        log.info("user_removed_from_allowlist", user_id=user_id)


def detect_prompt_injection(content: str) -> bool:
    """Enhanced detection of common prompt injection patterns using regex.

    Args:
        content: The message content to check.

    Returns:
        True if potential injection detected, False otherwise.
    """
    # Convert to lowercase for checking
    lower_content = content.lower()

    # Regex patterns for more robust detection (handles spacing, punctuation variations)
    regex_patterns = [
        r"\bignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions?|commands?|prompts?)",
        r"\bdisregard\s+(?:your|all|the)\s+(?:instructions?|commands?|rules?)",
        r"\bforget\s+(?:your|all|the)\s+(?:instructions?|commands?|rules?|prompts?)",
        r"\boverride\s+(?:your|all|the|system)\s+(?:instructions?|commands?|settings?)",
        r"\byou\s+are\s+now\s+(?:a|an|in)",
        r"\bact\s+as\s+(?:if|though|my|the|a\s+different|an?\s+unrestricted)\b",
        r"\bpretend\s+(?:you\s+are|to\s+be|that)",
        r"\bnew\s+(?:instructions?|commands?|rules?)[\s:]+",
        r"\bsystem\s+(?:prompt|message|instruction)[\s:]+",
        r"\bjailbreak(?:ing)?",
        r"\bdan\s+mode",
        r"\b(?:enable|activate)\s+developer\s+mode",
        r"\bdeveloper\s+mode\s+(?:enable|on|activated?)",
        r"\brole[\s:]?\s*system",
        r"\bbegin\s+new\s+(?:task|role|persona)",
        r"\bignor(?:e|ing)\s+(?:all\s+)?(?:safeguards?|safety)",
        r"\bdisable\s+(?:all\s+)?(?:filters?|safety|restrictions?)",
        r"\bbypass\s+(?:all\s+)?(?:filters?|safety|restrictions?)",
    ]

    # Check regex patterns
    for pattern in regex_patterns:
        match = re.search(pattern, lower_content, re.IGNORECASE)
        if match:
            log.warning(
                "potential_prompt_injection_detected",
                pattern=pattern,
                matched_text=match.group(0),
                content_preview=content[:100],
            )
            return True

    # Additional heuristic: check for excessive role-play markers
    roleplay_markers = lower_content.count("[") + lower_content.count("(system")
    if roleplay_markers > 5:
        log.warning(
            "potential_prompt_injection_detected",
            reason="excessive_roleplay_markers",
            count=roleplay_markers,
            content_preview=content[:100],
        )
        return True

    # Check for Unicode obfuscation attempts (homoglyphs)
    # Normalize and check if significantly different from original
    try:
        import unicodedata

        normalized = unicodedata.normalize("NFKC", content)
        if (
            len(normalized) != len(content)
            and abs(len(normalized) - len(content)) > len(content) * 0.1
        ):
            log.warning(
                "potential_prompt_injection_detected",
                reason="unicode_obfuscation_detected",
                content_preview=content[:100],
            )
            return True
    except Exception as e:
        # Skip unicode check if it fails (graceful degradation)
        log.debug("unicode_normalization_check_skipped", error=str(e))

    return False
