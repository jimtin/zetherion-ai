"""Pydantic models for the personal understanding layer.

Defines data structures for user profiles, contacts, action policies,
and learning records that back the personal model stored in PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Relationship(StrEnum):
    """Types of relationships between the user and a contact."""

    COLLEAGUE = "colleague"
    CLIENT = "client"
    FRIEND = "friend"
    MANAGER = "manager"
    VENDOR = "vendor"
    FAMILY = "family"
    ACQUAINTANCE = "acquaintance"
    OTHER = "other"


class PolicyDomain(StrEnum):
    """Domains where the bot can take autonomous actions."""

    EMAIL = "email"
    CALENDAR = "calendar"
    TASKS = "tasks"
    GENERAL = "general"
    DISCORD_OBSERVE = "discord_observe"


class PolicyMode(StrEnum):
    """Execution modes for bot actions."""

    AUTO = "auto"  # Execute immediately, log to audit
    DRAFT = "draft"  # Create draft, notify user for review
    ASK = "ask"  # Ask user for approval before executing
    NEVER = "never"  # Block entirely, log attempt


class LearningCategory(StrEnum):
    """Categories for learned facts about the user."""

    PREFERENCE = "preference"
    CONTACT = "contact"
    SCHEDULE = "schedule"
    POLICY = "policy"
    CORRECTION = "correction"
    FACT = "fact"


class LearningSource(StrEnum):
    """How a learning was acquired."""

    EXPLICIT = "explicit"  # User told the bot directly
    INFERRED = "inferred"  # Bot inferred from conversation
    EMAIL = "email"  # Extracted from email
    CALENDAR = "calendar"  # Extracted from calendar
    DISCORD = "discord"  # Extracted from Discord messages


# ---------------------------------------------------------------------------
# Communication style model
# ---------------------------------------------------------------------------


class CommunicationStyle(BaseModel):
    """Describes the user's communication preferences."""

    formality: float = Field(default=0.5, ge=0.0, le=1.0, description="0=casual, 1=formal")
    verbosity: float = Field(default=0.5, ge=0.0, le=1.0, description="0=terse, 1=detailed")
    emoji_usage: float = Field(default=0.3, ge=0.0, le=1.0, description="0=never, 1=frequent")
    humor: float = Field(default=0.3, ge=0.0, le=1.0, description="0=serious, 1=playful")


# ---------------------------------------------------------------------------
# Working hours model
# ---------------------------------------------------------------------------


class WorkingHours(BaseModel):
    """Defines when the user is typically working."""

    start: str = Field(default="09:00", description="Start time HH:MM")
    end: str = Field(default="17:00", description="End time HH:MM")
    days: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4, 5],
        description="ISO weekday numbers (1=Monday, 7=Sunday)",
    )

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Validate HH:MM time format."""
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Time must be HH:MM, got: {v}")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError as err:
            raise ValueError(f"Time must contain numeric HH:MM, got: {v}") from err
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Invalid time: {v}")
        return v

    @field_validator("days")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        """Validate ISO weekday numbers."""
        for d in v:
            if not 1 <= d <= 7:
                raise ValueError(f"Day must be 1-7 (ISO weekday), got: {d}")
        return sorted(set(v))


# ---------------------------------------------------------------------------
# Personal profile
# ---------------------------------------------------------------------------


class PersonalProfile(BaseModel):
    """Core identity and preferences for a user."""

    user_id: int = Field(description="Discord user ID")
    display_name: str | None = Field(default=None, description="Preferred display name")
    timezone: str = Field(default="UTC", description="IANA timezone")
    locale: str = Field(default="en", description="Language/locale code")
    working_hours: WorkingHours | None = Field(default=None, description="Typical working schedule")
    communication_style: CommunicationStyle | None = Field(
        default=None, description="Communication preferences"
    )
    goals: list[str] = Field(default_factory=list, description="User's current goals")
    preferences: dict[str, Any] = Field(
        default_factory=dict, description="Miscellaneous preferences"
    )
    updated_at: datetime = Field(default_factory=datetime.now)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to a flat dict suitable for PostgreSQL insertion."""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "timezone": self.timezone,
            "locale": self.locale,
            "working_hours": self.working_hours.model_dump() if self.working_hours else None,
            "communication_style": (
                self.communication_style.model_dump() if self.communication_style else None
            ),
            "goals": self.goals,
            "preferences": self.preferences,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> PersonalProfile:
        """Create from a PostgreSQL row dict."""
        working_hours = None
        if row.get("working_hours"):
            wh = row["working_hours"]
            working_hours = WorkingHours(**wh) if isinstance(wh, dict) else None

        comm_style = None
        if row.get("communication_style"):
            cs = row["communication_style"]
            comm_style = CommunicationStyle(**cs) if isinstance(cs, dict) else None

        goals = row.get("goals") or []
        if isinstance(goals, str):
            import json

            goals = json.loads(goals)

        preferences = row.get("preferences") or {}
        if isinstance(preferences, str):
            import json

            preferences = json.loads(preferences)

        return cls(
            user_id=row["user_id"],
            display_name=row.get("display_name"),
            timezone=row.get("timezone", "UTC"),
            locale=row.get("locale", "en"),
            working_hours=working_hours,
            communication_style=comm_style,
            goals=goals,
            preferences=preferences,
            updated_at=row.get("updated_at", datetime.now()),
        )


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------


class PersonalContact(BaseModel):
    """A contact the user interacts with."""

    id: int | None = Field(default=None, description="DB primary key (auto-generated)")
    user_id: int = Field(description="Owner's Discord user ID")
    contact_email: str | None = Field(default=None, description="Contact email address")
    contact_name: str | None = Field(default=None, description="Contact display name")
    relationship: Relationship = Field(
        default=Relationship.OTHER, description="Nature of the relationship"
    )
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Importance score")
    company: str | None = Field(default=None, description="Company/organization")
    notes: str | None = Field(default=None, description="Free-form notes")
    last_interaction: datetime | None = Field(default=None, description="Last interaction time")
    interaction_count: int = Field(default=0, ge=0, description="Number of interactions")
    updated_at: datetime = Field(default_factory=datetime.now)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to a flat dict for PostgreSQL insertion."""
        return {
            "user_id": self.user_id,
            "contact_email": self.contact_email,
            "contact_name": self.contact_name,
            "relationship": self.relationship.value,
            "importance": self.importance,
            "company": self.company,
            "notes": self.notes,
            "last_interaction": self.last_interaction,
            "interaction_count": self.interaction_count,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> PersonalContact:
        """Create from a PostgreSQL row dict."""
        rel = row.get("relationship", "other")
        try:
            relationship = Relationship(rel)
        except ValueError:
            relationship = Relationship.OTHER

        return cls(
            id=row.get("id"),
            user_id=row["user_id"],
            contact_email=row.get("contact_email"),
            contact_name=row.get("contact_name"),
            relationship=relationship,
            importance=row.get("importance", 0.5),
            company=row.get("company"),
            notes=row.get("notes"),
            last_interaction=row.get("last_interaction"),
            interaction_count=row.get("interaction_count", 0),
            updated_at=row.get("updated_at", datetime.now()),
        )


# ---------------------------------------------------------------------------
# Action policy
# ---------------------------------------------------------------------------


class PersonalPolicy(BaseModel):
    """Defines what the bot can do autonomously in a given domain."""

    id: int | None = Field(default=None, description="DB primary key (auto-generated)")
    user_id: int = Field(description="Owner's Discord user ID")
    domain: PolicyDomain = Field(description="Action domain")
    action: str = Field(description="Specific action identifier")
    mode: PolicyMode = Field(default=PolicyMode.ASK, description="Execution mode")
    conditions: dict[str, Any] | None = Field(
        default=None, description="Optional conditions for the policy"
    )
    trust_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Learned trust score")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to a flat dict for PostgreSQL insertion."""
        return {
            "user_id": self.user_id,
            "domain": self.domain.value,
            "action": self.action,
            "mode": self.mode.value,
            "conditions": self.conditions,
            "trust_score": self.trust_score,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> PersonalPolicy:
        """Create from a PostgreSQL row dict."""
        try:
            domain = PolicyDomain(row["domain"])
        except ValueError:
            domain = PolicyDomain.GENERAL

        try:
            mode = PolicyMode(row.get("mode", "ask"))
        except ValueError:
            mode = PolicyMode.ASK

        conditions = row.get("conditions")
        if isinstance(conditions, str):
            import json

            conditions = json.loads(conditions)

        return cls(
            id=row.get("id"),
            user_id=row["user_id"],
            domain=domain,
            action=row["action"],
            mode=mode,
            conditions=conditions,
            trust_score=row.get("trust_score", 0.0),
            created_at=row.get("created_at", datetime.now()),
            updated_at=row.get("updated_at", datetime.now()),
        )


# ---------------------------------------------------------------------------
# Learning record
# ---------------------------------------------------------------------------


class PersonalLearning(BaseModel):
    """A fact the bot has learned about the user."""

    id: int | None = Field(default=None, description="DB primary key (auto-generated)")
    user_id: int = Field(description="Owner's Discord user ID")
    category: LearningCategory = Field(description="Type of learning")
    content: str = Field(min_length=1, description="What was learned")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this learning")
    source: LearningSource = Field(description="How this was learned")
    confirmed: bool = Field(default=False, description="Whether the user confirmed this")
    created_at: datetime = Field(default_factory=datetime.now)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to a flat dict for PostgreSQL insertion."""
        return {
            "user_id": self.user_id,
            "category": self.category.value,
            "content": self.content,
            "confidence": self.confidence,
            "source": self.source.value,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> PersonalLearning:
        """Create from a PostgreSQL row dict."""
        try:
            category = LearningCategory(row["category"])
        except ValueError:
            category = LearningCategory.FACT

        try:
            source = LearningSource(row.get("source", "inferred"))
        except ValueError:
            source = LearningSource.INFERRED

        return cls(
            id=row.get("id"),
            user_id=row["user_id"],
            category=category,
            content=row["content"],
            confidence=row.get("confidence", 0.5),
            source=source,
            confirmed=row.get("confirmed", False),
            created_at=row.get("created_at", datetime.now()),
        )
