"""Employment Profile for modeling the bot's role and relationship with the user.

The Employment Profile answers "What is my job?" — it co-evolves alongside
the User Profile to define the bot's role, communication style, and relationship.

Key components:
- Role Definition: primary roles, secondary capabilities, boundaries
- Communication Style: formality, verbosity, proactivity, tone
- Relationship Context: when it started, trust level, milestones
- Skill Priorities: which skills are most used/important
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.profile.employment")


class TrustLevel(Enum):
    """Levels of trust the user has granted the bot."""

    MINIMAL = "minimal"  # New relationship, always confirm everything
    BUILDING = "building"  # Starting to trust, confirm significant actions
    ESTABLISHED = "established"  # Solid trust, can take routine actions
    HIGH = "high"  # High trust, can be proactive and autonomous
    FULL = "full"  # Complete trust, minimal confirmation needed


class Milestone(Enum):
    """Relationship milestones that affect trust and behavior."""

    FIRST_INTERACTION = "first_interaction"
    FIRST_TASK_COMPLETED = "first_task_completed"
    FIRST_WEEK = "first_week"
    FIRST_MONTH = "first_month"
    HUNDRED_INTERACTIONS = "hundred_interactions"
    FIRST_PROACTIVE_ACTION = "first_proactive_action"
    FIRST_DELEGATION = "first_delegation"  # User explicitly delegated a task
    TRUST_GRANTED = "trust_granted"  # User said "I trust you" or similar
    CORRECTION_ACCEPTED = "correction_accepted"  # Bot accepted user correction
    PREFERENCE_LEARNED = "preference_learned"  # Bot learned a user preference


@dataclass
class RoleDefinition:
    """Defines the bot's role for a specific user."""

    primary_roles: list[str] = field(default_factory=list)
    secondary_capabilities: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)  # Things the bot shouldn't do
    current_focus: str | None = None  # What the user is currently working on

    def add_role(self, role: str, primary: bool = True) -> None:
        """Add a role to the definition."""
        target = self.primary_roles if primary else self.secondary_capabilities
        if role not in target:
            target.append(role)
            log.debug("role_added", role=role, primary=primary)

    def remove_role(self, role: str) -> None:
        """Remove a role from the definition."""
        if role in self.primary_roles:
            self.primary_roles.remove(role)
        if role in self.secondary_capabilities:
            self.secondary_capabilities.remove(role)

    def add_boundary(self, boundary: str) -> None:
        """Add a boundary (something the bot shouldn't do)."""
        if boundary not in self.boundaries:
            self.boundaries.append(boundary)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "primary_roles": self.primary_roles,
            "secondary_capabilities": self.secondary_capabilities,
            "boundaries": self.boundaries,
            "current_focus": self.current_focus,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleDefinition":
        """Create from dictionary."""
        return cls(
            primary_roles=data.get("primary_roles", []),
            secondary_capabilities=data.get("secondary_capabilities", []),
            boundaries=data.get("boundaries", []),
            current_focus=data.get("current_focus"),
        )


@dataclass
class CommunicationStyle:
    """Defines how the bot should communicate with the user.

    All float values are 0.0 to 1.0 scales.
    """

    formality: float = 0.5  # 0=casual, 1=formal
    verbosity: float = 0.5  # 0=terse, 1=detailed
    proactivity: float = 0.3  # 0=reactive, 1=proactive
    tone: str = "professional"  # friendly, professional, casual, formal
    humor_level: float = 0.2  # 0=serious, 1=playful
    emoji_usage: float = 0.0  # 0=never, 1=frequently

    def __post_init__(self) -> None:
        """Validate float values."""
        for attr in ["formality", "verbosity", "proactivity", "humor_level", "emoji_usage"]:
            value = getattr(self, attr)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{attr} must be between 0.0 and 1.0, got {value}")

    def adjust(self, attribute: str, delta: float) -> float:
        """Adjust a style attribute by delta, clamped to [0, 1].

        Returns the new value.
        """
        if not hasattr(self, attribute):
            raise ValueError(f"Unknown attribute: {attribute}")

        current = getattr(self, attribute)
        if not isinstance(current, float):
            raise ValueError(f"Attribute {attribute} is not a float")

        new_value = max(0.0, min(1.0, current + delta))
        setattr(self, attribute, new_value)
        log.debug("style_adjusted", attribute=attribute, old=current, new=new_value)
        return new_value

    def describe(self) -> str:
        """Generate a human-readable description of the communication style."""
        parts = []

        # Formality
        if self.formality < 0.3:
            parts.append("casual")
        elif self.formality > 0.7:
            parts.append("formal")
        else:
            parts.append("professional")

        # Verbosity
        if self.verbosity < 0.3:
            parts.append("concise")
        elif self.verbosity > 0.7:
            parts.append("detailed")
        else:
            parts.append("balanced")

        # Tone
        parts.append(self.tone)

        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "formality": self.formality,
            "verbosity": self.verbosity,
            "proactivity": self.proactivity,
            "tone": self.tone,
            "humor_level": self.humor_level,
            "emoji_usage": self.emoji_usage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommunicationStyle":
        """Create from dictionary."""
        return cls(
            formality=data.get("formality", 0.5),
            verbosity=data.get("verbosity", 0.5),
            proactivity=data.get("proactivity", 0.3),
            tone=data.get("tone", "professional"),
            humor_level=data.get("humor_level", 0.2),
            emoji_usage=data.get("emoji_usage", 0.0),
        )


@dataclass
class SkillUsage:
    """Tracks usage of a skill for priority ordering."""

    skill_name: str
    invocation_count: int = 0
    last_used: datetime | None = None
    success_rate: float = 1.0  # How often the skill completed successfully

    def record_use(self, success: bool = True) -> None:
        """Record a use of this skill."""
        self.invocation_count += 1
        self.last_used = datetime.now()

        # Update rolling success rate (exponential moving average)
        alpha = 0.1  # Weight for new observation
        self.success_rate = alpha * (1.0 if success else 0.0) + (1 - alpha) * self.success_rate

    def days_since_used(self) -> int | None:
        """Return days since last use, or None if never used."""
        if self.last_used is None:
            return None
        delta = datetime.now() - self.last_used
        return delta.days

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill_name": self.skill_name,
            "invocation_count": self.invocation_count,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "success_rate": self.success_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillUsage":
        """Create from dictionary."""
        return cls(
            skill_name=data["skill_name"],
            invocation_count=data.get("invocation_count", 0),
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            success_rate=data.get("success_rate", 1.0),
        )


@dataclass
class EmploymentProfile:
    """The bot's employment profile for a specific user.

    This models the bot's evolving role and relationship with the user,
    co-evolving alongside the User Profile.
    """

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    version: int = 1

    # Role definition
    role: RoleDefinition = field(default_factory=RoleDefinition)

    # Communication style
    style: CommunicationStyle = field(default_factory=CommunicationStyle)

    # Relationship context
    relationship_started: datetime = field(default_factory=datetime.now)
    total_interactions: int = 0
    trust_level: float = 0.3  # 0.0 to 1.0
    trust_enum: TrustLevel = TrustLevel.BUILDING
    milestones_achieved: list[str] = field(default_factory=list)

    # Skill priorities
    skill_usage: dict[str, SkillUsage] = field(default_factory=dict)
    priority_order: list[str] = field(default_factory=list)  # Skills in priority order
    underutilized_skills: list[str] = field(default_factory=list)

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        """Validate the profile."""
        if not 0.0 <= self.trust_level <= 1.0:
            raise ValueError(f"Trust level must be between 0.0 and 1.0, got {self.trust_level}")

    def record_interaction(self) -> None:
        """Record an interaction and check for milestones."""
        self.total_interactions += 1
        self.last_updated = datetime.now()

        # Check for interaction milestones
        if self.total_interactions == 1:
            self.achieve_milestone(Milestone.FIRST_INTERACTION)
        elif self.total_interactions == 100:
            self.achieve_milestone(Milestone.HUNDRED_INTERACTIONS)

        # Check time-based milestones
        days_active = (datetime.now() - self.relationship_started).days
        if days_active >= 7 and Milestone.FIRST_WEEK.value not in self.milestones_achieved:
            self.achieve_milestone(Milestone.FIRST_WEEK)
        if days_active >= 30 and Milestone.FIRST_MONTH.value not in self.milestones_achieved:
            self.achieve_milestone(Milestone.FIRST_MONTH)

    def achieve_milestone(self, milestone: Milestone) -> bool:
        """Mark a milestone as achieved.

        Returns True if this was a new milestone.
        """
        if milestone.value in self.milestones_achieved:
            return False

        self.milestones_achieved.append(milestone.value)
        self.last_updated = datetime.now()

        # Milestones affect trust
        trust_boost = {
            Milestone.FIRST_INTERACTION: 0.0,
            Milestone.FIRST_TASK_COMPLETED: 0.05,
            Milestone.FIRST_WEEK: 0.05,
            Milestone.FIRST_MONTH: 0.1,
            Milestone.HUNDRED_INTERACTIONS: 0.1,
            Milestone.FIRST_PROACTIVE_ACTION: 0.05,
            Milestone.FIRST_DELEGATION: 0.1,
            Milestone.TRUST_GRANTED: 0.15,
            Milestone.CORRECTION_ACCEPTED: 0.02,
            Milestone.PREFERENCE_LEARNED: 0.02,
        }

        boost = trust_boost.get(milestone, 0.0)
        if boost > 0:
            self.adjust_trust(boost)

        log.info("milestone_achieved", milestone=milestone.value, trust_level=self.trust_level)
        return True

    def adjust_trust(self, delta: float) -> float:
        """Adjust trust level by delta, clamped to [0, 1].

        Also updates the trust enum.
        Returns the new trust level.
        """
        self.trust_level = max(0.0, min(1.0, self.trust_level + delta))
        self.last_updated = datetime.now()

        # Update trust enum
        if self.trust_level < 0.2:
            self.trust_enum = TrustLevel.MINIMAL
        elif self.trust_level < 0.4:
            self.trust_enum = TrustLevel.BUILDING
        elif self.trust_level < 0.6:
            self.trust_enum = TrustLevel.ESTABLISHED
        elif self.trust_level < 0.8:
            self.trust_enum = TrustLevel.HIGH
        else:
            self.trust_enum = TrustLevel.FULL

        return self.trust_level

    def record_skill_use(self, skill_name: str, success: bool = True) -> None:
        """Record usage of a skill."""
        if skill_name not in self.skill_usage:
            self.skill_usage[skill_name] = SkillUsage(skill_name=skill_name)

        self.skill_usage[skill_name].record_use(success)
        self.last_updated = datetime.now()

        # Update priority order
        self._update_priority_order()

    def _update_priority_order(self) -> None:
        """Update skill priority order based on usage."""
        # Sort by invocation count descending
        sorted_skills = sorted(
            self.skill_usage.values(),
            key=lambda s: s.invocation_count,
            reverse=True,
        )
        self.priority_order = [s.skill_name for s in sorted_skills]

        # Identify underutilized skills (not used in 14+ days)
        self.underutilized_skills = [
            s.skill_name
            for s in self.skill_usage.values()
            if s.days_since_used() is not None and s.days_since_used() >= 14  # type: ignore[operator]
        ]

    def get_trust_description(self) -> str:
        """Get a human-readable description of the current trust level."""
        descriptions = {
            TrustLevel.MINIMAL: "New relationship - always confirm before acting",
            TrustLevel.BUILDING: "Building trust - confirm significant actions",
            TrustLevel.ESTABLISHED: "Established trust - can handle routine tasks autonomously",
            TrustLevel.HIGH: "High trust - authorized to be proactive",
            TrustLevel.FULL: "Full trust - minimal confirmation needed",
        }
        return descriptions[self.trust_enum]

    def to_prompt_fragment(self) -> str:
        """Generate a system prompt fragment from this employment profile."""
        parts = []

        # Roles
        if self.role.primary_roles:
            parts.append(f"Primary roles: {', '.join(self.role.primary_roles)}")
        if self.role.current_focus:
            parts.append(f"Current focus: {self.role.current_focus}")

        # Communication style
        parts.append(f"Communication style: {self.style.describe()}")

        # Trust level
        parts.append(f"Trust level: {self.get_trust_description()}")

        # Relationship context
        days_active = (datetime.now() - self.relationship_started).days
        parts.append(f"Working together for {days_active} days ({self.total_interactions} msgs)")

        # Boundaries
        if self.role.boundaries:
            parts.append(f"Boundaries: {', '.join(self.role.boundaries)}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "version": self.version,
            "role": self.role.to_dict(),
            "style": self.style.to_dict(),
            "relationship_started": self.relationship_started.isoformat(),
            "total_interactions": self.total_interactions,
            "trust_level": self.trust_level,
            "trust_enum": self.trust_enum.value,
            "milestones_achieved": self.milestones_achieved,
            "skill_usage": {k: v.to_dict() for k, v in self.skill_usage.items()},
            "priority_order": self.priority_order,
            "underutilized_skills": self.underutilized_skills,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmploymentProfile":
        """Create from dictionary."""
        profile = cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            version=data.get("version", 1),
            role=RoleDefinition.from_dict(data.get("role", {})),
            style=CommunicationStyle.from_dict(data.get("style", {})),
            relationship_started=datetime.fromisoformat(data["relationship_started"])
            if data.get("relationship_started")
            else datetime.now(),
            total_interactions=data.get("total_interactions", 0),
            trust_level=data.get("trust_level", 0.3),
            trust_enum=TrustLevel(data.get("trust_enum", "building")),
            milestones_achieved=data.get("milestones_achieved", []),
            priority_order=data.get("priority_order", []),
            underutilized_skills=data.get("underutilized_skills", []),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            last_updated=datetime.fromisoformat(data["last_updated"])
            if data.get("last_updated")
            else datetime.now(),
        )

        # Restore skill usage
        for name, usage_data in data.get("skill_usage", {}).items():
            profile.skill_usage[name] = SkillUsage.from_dict(usage_data)

        return profile


def create_default_profile(user_id: str) -> EmploymentProfile:
    """Create a default employment profile for a new user.

    Uses settings from config for initial values.
    """
    from zetherion_ai.config import get_settings

    settings = get_settings()
    profile = EmploymentProfile(
        user_id=user_id,
        style=CommunicationStyle(
            formality=settings.default_formality,
            verbosity=settings.default_verbosity,
            proactivity=settings.default_proactivity,
        ),
        trust_level=0.3,
    )

    # Set default roles
    profile.role.add_role("assistant", primary=True)
    profile.role.add_role("information retrieval", primary=False)

    log.info("default_profile_created", user_id=user_id)
    return profile
