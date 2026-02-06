"""In-memory cache for user profile data.

Provides fast access to frequently-used profile data,
avoiding repeated Qdrant queries.

Uses TTLCache for automatic expiration:
- User profiles: 5 minute TTL
- Employment profiles: 10 minute TTL
- Profile summaries: 2 minute TTL
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cachetools import TTLCache  # type: ignore[import-untyped]

from secureclaw.logging import get_logger
from secureclaw.profile.models import ProfileCategory, ProfileEntry

log = get_logger("secureclaw.profile.cache")


@dataclass
class UserProfileSummary:
    """Summary of a user's profile for system prompt injection."""

    user_id: str
    name: str | None = None
    timezone: str | None = None
    role: str | None = None
    preferences: dict[str, Any] = field(default_factory=dict)
    current_focus: str | None = None
    high_confidence_entries: int = 0
    total_entries: int = 0
    last_updated: datetime = field(default_factory=datetime.now)

    def to_prompt_fragment(self) -> str:
        """Generate a system prompt fragment from the summary."""
        parts = []

        if self.name:
            parts.append(f"User's name: {self.name}")
        if self.role:
            parts.append(f"Role: {self.role}")
        if self.timezone:
            parts.append(f"Timezone: {self.timezone}")
        if self.current_focus:
            parts.append(f"Currently focused on: {self.current_focus}")

        if self.preferences:
            pref_str = ", ".join(f"{k}: {v}" for k, v in self.preferences.items())
            parts.append(f"Preferences: {pref_str}")

        if not parts:
            return "No profile information available yet."

        return "\n".join(parts)


@dataclass
class EmploymentProfileSummary:
    """Summary of the bot's employment profile."""

    user_id: str
    primary_roles: list[str] = field(default_factory=list)
    formality: float = 0.5
    verbosity: float = 0.5
    proactivity: float = 0.3
    trust_level: float = 0.3
    tone: str = "professional"
    relationship_started: datetime | None = None
    total_interactions: int = 0

    def to_prompt_fragment(self) -> str:
        """Generate a system prompt fragment from the employment profile."""
        parts = []

        if self.primary_roles:
            parts.append(f"Primary roles: {', '.join(self.primary_roles)}")

        # Describe communication style
        verbosity_desc = (
            "concise"
            if self.verbosity < 0.4
            else "detailed"
            if self.verbosity > 0.7
            else "balanced"
        )
        formality_desc = (
            "casual"
            if self.formality < 0.4
            else "formal"
            if self.formality > 0.7
            else "professional"
        )
        parts.append(f"Communication style: {formality_desc}, {verbosity_desc}, {self.tone}")

        # Describe trust level
        if self.trust_level > 0.8:
            trust_desc = "High - authorized to take action without confirmation for routine tasks"
        elif self.trust_level > 0.5:
            trust_desc = "Medium - confirm before taking significant actions"
        else:
            trust_desc = "Building - always confirm before acting"
        parts.append(f"Trust level: {trust_desc}")

        if self.relationship_started:
            parts.append(
                f"Working together since {self.relationship_started:%B %Y} "
                f"({self.total_interactions} interactions)"
            )

        return "\n".join(parts)


class ProfileCache:
    """In-memory cache for frequently accessed profile data.

    Uses TTLCache to automatically expire stale entries.
    """

    def __init__(
        self,
        user_cache_ttl: int = 300,
        employment_cache_ttl: int = 600,
        summary_cache_ttl: int = 120,
        max_users: int = 100,
    ):
        """Initialize the profile cache.

        Args:
            user_cache_ttl: TTL for user profiles in seconds (default 5 min).
            employment_cache_ttl: TTL for employment profiles in seconds (default 10 min).
            summary_cache_ttl: TTL for profile summaries in seconds (default 2 min).
            max_users: Maximum number of users to cache.
        """
        # User profiles: list of ProfileEntry per user
        self._user_cache: TTLCache[str, list[ProfileEntry]] = TTLCache(
            maxsize=max_users, ttl=user_cache_ttl
        )

        # Employment profiles: single summary per user
        self._employment_cache: TTLCache[str, EmploymentProfileSummary] = TTLCache(
            maxsize=max_users, ttl=employment_cache_ttl
        )

        # High-confidence summaries for system prompt injection
        self._summary_cache: TTLCache[str, UserProfileSummary] = TTLCache(
            maxsize=max_users, ttl=summary_cache_ttl
        )

        log.info(
            "profile_cache_initialized",
            user_ttl=user_cache_ttl,
            employment_ttl=employment_cache_ttl,
            summary_ttl=summary_cache_ttl,
            max_users=max_users,
        )

    def get_user_profile(self, user_id: str) -> list[ProfileEntry] | None:
        """Get cached user profile entries.

        Args:
            user_id: The user's ID.

        Returns:
            List of profile entries, or None if not cached.
        """
        return self._user_cache.get(user_id)  # type: ignore[no-any-return]

    def set_user_profile(self, user_id: str, entries: list[ProfileEntry]) -> None:
        """Cache user profile entries.

        Args:
            user_id: The user's ID.
            entries: List of profile entries.
        """
        self._user_cache[user_id] = entries
        log.debug("user_profile_cached", user_id=user_id, entries=len(entries))

    def get_employment_profile(self, user_id: str) -> EmploymentProfileSummary | None:
        """Get cached employment profile.

        Args:
            user_id: The user's ID.

        Returns:
            Employment profile summary, or None if not cached.
        """
        return self._employment_cache.get(user_id)  # type: ignore[no-any-return]

    def set_employment_profile(self, user_id: str, profile: EmploymentProfileSummary) -> None:
        """Cache employment profile.

        Args:
            user_id: The user's ID.
            profile: Employment profile summary.
        """
        self._employment_cache[user_id] = profile
        log.debug("employment_profile_cached", user_id=user_id)

    def get_summary(self, user_id: str) -> UserProfileSummary | None:
        """Get cached profile summary for system prompt injection.

        Args:
            user_id: The user's ID.

        Returns:
            User profile summary, or None if not cached.
        """
        return self._summary_cache.get(user_id)  # type: ignore[no-any-return]

    def set_summary(self, user_id: str, summary: UserProfileSummary) -> None:
        """Cache profile summary.

        Args:
            user_id: The user's ID.
            summary: User profile summary.
        """
        self._summary_cache[user_id] = summary
        log.debug("profile_summary_cached", user_id=user_id)

    def build_summary(self, entries: list[ProfileEntry]) -> UserProfileSummary:
        """Build a profile summary from entries.

        Args:
            entries: List of profile entries.

        Returns:
            User profile summary.
        """
        if not entries:
            return UserProfileSummary(user_id="unknown")

        user_id = entries[0].user_id
        summary = UserProfileSummary(
            user_id=user_id,
            total_entries=len(entries),
        )

        # Count high-confidence entries
        high_conf_entries = [e for e in entries if e.get_current_confidence() >= 0.7]
        summary.high_confidence_entries = len(high_conf_entries)

        # Extract key fields
        for entry in high_conf_entries:
            if entry.category == ProfileCategory.IDENTITY:
                if entry.key == "name":
                    summary.name = str(entry.value)
                elif entry.key == "timezone":
                    summary.timezone = str(entry.value)
                elif entry.key == "role":
                    summary.role = str(entry.value)
            elif entry.category == ProfileCategory.PROJECTS:
                if entry.key == "current_focus":
                    summary.current_focus = str(entry.value)
            elif entry.category == ProfileCategory.PREFERENCES:
                summary.preferences[entry.key] = entry.value

        return summary

    def invalidate(self, user_id: str) -> None:
        """Invalidate all caches for a user after an update.

        Args:
            user_id: The user's ID.
        """
        self._user_cache.pop(user_id, None)
        self._employment_cache.pop(user_id, None)
        self._summary_cache.pop(user_id, None)
        log.debug("profile_cache_invalidated", user_id=user_id)

    def invalidate_all(self) -> None:
        """Invalidate all cached data."""
        self._user_cache.clear()
        self._employment_cache.clear()
        self._summary_cache.clear()
        log.info("profile_cache_cleared")

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats.
        """
        return {
            "user_cache_size": len(self._user_cache),
            "user_cache_max": self._user_cache.maxsize,
            "employment_cache_size": len(self._employment_cache),
            "employment_cache_max": self._employment_cache.maxsize,
            "summary_cache_size": len(self._summary_cache),
            "summary_cache_max": self._summary_cache.maxsize,
        }
