"""Data models for the user profile system.

Defines:
- ProfileCategory: Categories of user knowledge
- ProfileEntry: A single profile entry with confidence scoring
- ProfileUpdate: A proposed update to the profile
- ProfileSource: How the information was obtained
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4


class ProfileCategory(Enum):
    """Categories of user knowledge."""

    IDENTITY = "identity"  # name, role, location, timezone
    PREFERENCES = "preferences"  # communication style, tools, formats
    SCHEDULE = "schedule"  # work hours, meeting patterns
    PROJECTS = "projects"  # active work, goals, deadlines
    RELATIONSHIPS = "relationships"  # people, teams, organizations
    SKILLS = "skills"  # technical abilities, domains
    GOALS = "goals"  # short/long-term aspirations
    HABITS = "habits"  # work patterns, routines


class ProfileSource(Enum):
    """How profile information was obtained."""

    EXPLICIT = "explicit"  # User directly stated it
    CONVERSATION = "conversation"  # Extracted from conversation
    INFERRED = "inferred"  # Inferred from patterns
    CONFIRMED = "confirmed"  # User confirmed an inference


@dataclass
class ProfileEntry:
    """A single entry in the user profile.

    Each entry has a confidence score that decays over time.
    Entries below 0.2 confidence are flagged for re-confirmation.
    """

    id: UUID
    user_id: str
    category: ProfileCategory
    key: str  # e.g., "timezone", "preferred_language"
    value: Any  # The actual information
    confidence: float  # 0.0 to 1.0
    source: ProfileSource
    created_at: datetime
    last_confirmed: datetime
    decay_rate: float = 0.01  # How fast confidence degrades per day

    def __post_init__(self) -> None:
        """Validate the entry."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")
        if not 0.0 <= self.decay_rate <= 1.0:
            raise ValueError(f"Decay rate must be between 0.0 and 1.0, got {self.decay_rate}")

    @classmethod
    def create(
        cls,
        user_id: str,
        category: ProfileCategory,
        key: str,
        value: Any,
        confidence: float,
        source: ProfileSource,
        decay_rate: float = 0.01,
    ) -> "ProfileEntry":
        """Create a new profile entry with auto-generated ID and timestamps."""
        now = datetime.now()
        return cls(
            id=uuid4(),
            user_id=user_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            created_at=now,
            last_confirmed=now,
            decay_rate=decay_rate,
        )

    def apply_decay(self, days_elapsed: float) -> float:
        """Calculate decayed confidence.

        Args:
            days_elapsed: Days since last confirmation.

        Returns:
            The decayed confidence value.
        """
        decayed = self.confidence - (self.decay_rate * days_elapsed)
        return max(0.0, decayed)

    def get_current_confidence(self) -> float:
        """Get the current confidence accounting for decay."""
        days_elapsed = (datetime.now() - self.last_confirmed).total_seconds() / 86400
        return self.apply_decay(days_elapsed)

    def needs_confirmation(self, threshold: float = 0.2) -> bool:
        """Check if this entry needs user confirmation.

        Args:
            threshold: Confidence threshold below which confirmation is needed.

        Returns:
            True if current confidence is below threshold.
        """
        return self.get_current_confidence() < threshold

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "category": self.category.value,
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source.value,
            "created_at": self.created_at.isoformat(),
            "last_confirmed": self.last_confirmed.isoformat(),
            "decay_rate": self.decay_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileEntry":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]),
            user_id=data["user_id"],
            category=ProfileCategory(data["category"]),
            key=data["key"],
            value=data["value"],
            confidence=data["confidence"],
            source=ProfileSource(data["source"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_confirmed=datetime.fromisoformat(data["last_confirmed"]),
            decay_rate=data.get("decay_rate", 0.01),
        )


@dataclass
class ProfileUpdate:
    """A proposed update to either User Profile or Employment Profile.

    Updates have confidence scores and can require confirmation before applying.
    """

    profile: Literal["user", "employment"]
    field_name: str
    action: Literal["set", "increase", "decrease", "append", "increment"] = "set"
    value: Any = None
    confidence: float = 0.5
    requires_confirmation: bool = False
    reason: str | None = None
    source_tier: int = 1  # Which inference tier produced this (1-4)
    category: ProfileCategory | None = None  # For user profile updates
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        """Validate the update."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")
        if self.source_tier not in (1, 2, 3, 4):
            raise ValueError(f"Source tier must be 1-4, got {self.source_tier}")

    def should_apply(self, threshold: float = 0.6) -> bool:
        """Whether this update should be applied automatically.

        Args:
            threshold: Minimum confidence for auto-apply.

        Returns:
            True if confidence meets threshold and no confirmation required.
        """
        return self.confidence >= threshold and not self.requires_confirmation

    def to_confirmation_prompt(self) -> str:
        """Generate a natural confirmation question."""
        if self.profile == "user":
            return f"I noticed you might {self._describe_change()}. Is that right?"
        return f"Should I {self._describe_change()}?"

    def _describe_change(self) -> str:
        """Describe the change in natural language."""
        if self.action == "set":
            return f"set your {self.field_name} to {self.value}"
        elif self.action == "increase":
            return f"increase your {self.field_name}"
        elif self.action == "decrease":
            return f"decrease your {self.field_name}"
        elif self.action == "append":
            return f"add {self.value} to your {self.field_name}"
        elif self.action == "increment":
            return f"update your {self.field_name}"
        return f"update your {self.field_name}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "profile": self.profile,
            "field_name": self.field_name,
            "action": self.action,
            "value": self.value,
            "confidence": self.confidence,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
            "source_tier": self.source_tier,
            "category": self.category.value if self.category else None,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileUpdate":
        """Create from dictionary."""
        return cls(
            profile=data["profile"],
            field_name=data["field_name"],
            action=data.get("action", "set"),
            value=data.get("value"),
            confidence=data.get("confidence", 0.5),
            requires_confirmation=data.get("requires_confirmation", False),
            reason=data.get("reason"),
            source_tier=data.get("source_tier", 1),
            category=ProfileCategory(data["category"]) if data.get("category") else None,
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else datetime.now(),
        )


# Confidence thresholds
CONFIDENCE_AUTO_APPLY = 0.9  # Apply immediately, no confirmation
CONFIDENCE_LOG_ONLY = 0.7  # Apply immediately, log for review
CONFIDENCE_FLAG_CONFIRM = 0.5  # Apply but flag for confirmation in heartbeat
CONFIDENCE_QUEUE_CONFIRM = 0.3  # Don't apply, queue for explicit confirmation
# Below 0.3 is discarded as too uncertain
