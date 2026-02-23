"""Personality and relationship signal schema for message analysis.

Defines the multi-dimensional personality output extracted from a single
message.  Works bidirectionally — for messages FROM the owner (learning
about the owner's style) and messages TO the owner (learning about
contacts).  Designed for JSON-serializable benchmark validation against
ground truth persona definitions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuthorRole(StrEnum):
    """Whether the message author is the Zetherion owner or a contact."""

    OWNER = "owner"
    CONTACT = "contact"


class Formality(StrEnum):
    """Formality level of writing."""

    VERY_FORMAL = "very_formal"
    FORMAL = "formal"
    SEMI_FORMAL = "semi_formal"
    CASUAL = "casual"
    VERY_CASUAL = "very_casual"


class SentenceLength(StrEnum):
    """Typical sentence length."""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class VocabularyLevel(StrEnum):
    """Vocabulary complexity."""

    SIMPLE = "simple"
    STANDARD = "standard"
    TECHNICAL = "technical"
    ACADEMIC = "academic"


class CommunicationTrait(StrEnum):
    """Primary communication personality trait."""

    DIRECT = "direct"
    DIPLOMATIC = "diplomatic"
    VERBOSE = "verbose"
    TERSE = "terse"
    ANALYTICAL = "analytical"
    EMOTIONAL = "emotional"


class EmotionalTone(StrEnum):
    """Emotional tone of communication."""

    WARM = "warm"
    NEUTRAL = "neutral"
    RESERVED = "reserved"
    ENTHUSIASTIC = "enthusiastic"


class PowerDynamic(StrEnum):
    """Power dynamic between author and recipient."""

    SUBORDINATE = "subordinate"
    PEER = "peer"
    SUPERIOR = "superior"
    CLIENT = "client"
    VENDOR = "vendor"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class WritingStyle(BaseModel):
    """Observable writing patterns from a single message."""

    formality: Formality = Field(
        default=Formality.SEMI_FORMAL,
        description="Formality level of the writing",
    )
    avg_sentence_length: SentenceLength = Field(
        default=SentenceLength.MEDIUM,
        description="Typical sentence length",
    )
    uses_greeting: bool = Field(default=True, description="Message starts with a greeting")
    greeting_style: str = Field(
        default="",
        description="Exact greeting used, e.g. 'Hi Sarah,' or 'Dear Mr. Chen,'",
    )
    uses_signoff: bool = Field(default=True, description="Message ends with a sign-off")
    signoff_style: str = Field(
        default="",
        description="Exact sign-off used, e.g. 'Best regards,' or 'Cheers,'",
    )
    uses_emoji: bool = Field(default=False, description="Message contains emoji")
    uses_bullet_points: bool = Field(
        default=False,
        description="Message uses bullet points or numbered lists",
    )
    vocabulary_level: VocabularyLevel = Field(
        default=VocabularyLevel.STANDARD,
        description="Vocabulary complexity level",
    )


class CommunicationProfile(BaseModel):
    """Personality traits inferred from communication patterns."""

    primary_trait: CommunicationTrait = Field(
        default=CommunicationTrait.DIRECT,
        description="Dominant communication trait",
    )
    secondary_trait: CommunicationTrait | None = Field(
        default=None,
        description="Secondary communication trait, if apparent",
    )
    emotional_tone: EmotionalTone = Field(
        default=EmotionalTone.NEUTRAL,
        description="Overall emotional tone",
    )
    assertiveness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0=passive, 1=assertive",
    )
    responsiveness_signal: str = Field(
        default="",
        description="Signal about expected response speed, e.g. 'expects quick reply'",
    )


class RelationshipDynamics(BaseModel):
    """Signals about the relationship between author and recipient."""

    familiarity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0=stranger, 1=close relationship",
    )
    power_dynamic: PowerDynamic = Field(
        default=PowerDynamic.PEER,
        description="Power dynamic of the author relative to the recipient",
    )
    trust_level: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0=guarded, 1=fully trusting",
    )
    rapport_indicators: list[str] = Field(
        default_factory=list,
        description="Evidence of rapport, e.g. 'uses first name', 'references shared context'",
    )

    @field_validator("rapport_indicators")
    @classmethod
    def _cap_rapport_indicators(cls, v: list[str]) -> list[str]:
        """Deduplicate and cap rapport indicators."""
        seen: set[str] = set()
        result: list[str] = []
        for item in v:
            if not isinstance(item, str):
                continue
            normalised = item.strip().lower()
            if normalised and normalised not in seen:
                seen.add(normalised)
                result.append(normalised)
        return result[:10]


# ---------------------------------------------------------------------------
# Top-level personality signal
# ---------------------------------------------------------------------------


class PersonalitySignal(BaseModel):
    """Personality and relationship signals extracted from a single message.

    Works bidirectionally — for messages FROM the owner (learning about owner)
    and messages TO the owner (learning about the contact).
    """

    # Who wrote this message
    author_role: AuthorRole = Field(description="Whether author is the owner or a contact")
    author_name: str = Field(default="", description="Author display name")
    author_email: str = Field(default="", description="Author email address")

    # Writing style (observable patterns)
    writing_style: WritingStyle = Field(default_factory=WritingStyle)

    # Communication personality (inferred traits)
    communication: CommunicationProfile = Field(default_factory=CommunicationProfile)

    # Relationship dynamics (between author and recipient)
    relationship: RelationshipDynamics = Field(default_factory=RelationshipDynamics)

    # Preferences revealed in this message
    preferences_revealed: list[str] = Field(
        default_factory=list,
        description="Preferences revealed, e.g. 'prefers morning meetings'",
    )
    schedule_signals: list[str] = Field(
        default_factory=list,
        description="Schedule signals, e.g. 'works late evenings'",
    )

    # Obligations and expectations
    commitments_made: list[str] = Field(
        default_factory=list,
        description="What the author committed to doing",
    )
    expectations_set: list[str] = Field(
        default_factory=list,
        description="What the author expects from the recipient",
    )

    # Confidence and reasoning
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Model confidence in the extraction",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of personality analysis",
    )

    # -- validators ----------------------------------------------------------

    @field_validator(
        "preferences_revealed", "schedule_signals", "commitments_made", "expectations_set"
    )
    @classmethod
    def _normalise_string_list(cls, v: list[str]) -> list[str]:
        """Normalise, deduplicate, and cap string lists."""
        seen: set[str] = set()
        result: list[str] = []
        for item in v:
            if not isinstance(item, str):
                continue
            normalised = item.strip()
            key = normalised.lower()
            if key and key not in seen:
                seen.add(key)
                result.append(normalised)
        return result[:10]

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage / JSON export."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalitySignal:
        """Deserialise, tolerating partial or slightly malformed data."""
        return cls.model_validate(data)
