"""Rich email classification schema for single-call LLM analysis.

Defines the multi-dimensional classification output that replaces the
5-tag ``RouteTag`` triage with category + action + urgency + contact + topics.
The schema is designed to be JSON-serializable for both storage and
benchmark validation against the production Pydantic model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EmailCategory(StrEnum):
    """Seed email categories.  Extensible via user-specific learned categories."""

    PERSONAL = "personal"
    WORK_COLLEAGUE = "work_colleague"
    WORK_CLIENT = "work_client"
    WORK_VENDOR = "work_vendor"
    TRANSACTIONAL = "transactional"
    NEWSLETTER = "newsletter"
    MARKETING = "marketing"
    SUPPORT_INBOUND = "support_inbound"
    SUPPORT_OUTBOUND = "support_outbound"
    FINANCIAL = "financial"
    CALENDAR_INVITE = "calendar_invite"
    SOCIAL = "social"
    AUTOMATED = "automated"
    RECRUITMENT = "recruitment"
    UNKNOWN = "unknown"


class EmailAction(StrEnum):
    """Required action for the email."""

    REPLY_URGENT = "reply_urgent"
    REPLY_NORMAL = "reply_normal"
    ACTION_REQUIRED = "action_required"
    CREATE_TASK = "create_task"
    CREATE_EVENT = "create_event"
    READ_ONLY = "read_only"
    ARCHIVE = "archive"
    IGNORE = "ignore"


class EmailSentiment(StrEnum):
    """Detected sentiment of the email."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class UrgencyTrend(StrEnum):
    """Thread urgency trajectory."""

    ESCALATING = "escalating"
    STABLE = "stable"
    DEESCALATING = "deescalating"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ThreadContext(BaseModel):
    """Thread-level analysis."""

    is_thread: bool = False
    thread_position: int = Field(
        default=1,
        ge=1,
        description="Position in thread (1 = first message)",
    )
    thread_summary: str = ""
    urgency_trend: UrgencyTrend = UrgencyTrend.STABLE


class ContactSignal(BaseModel):
    """Contact information extracted from the email sender."""

    name: str = ""
    email: str = ""
    role: str = ""
    company: str = ""
    relationship: str = "unknown"
    communication_style: str = ""
    sentiment: EmailSentiment = EmailSentiment.NEUTRAL
    importance_signal: float = Field(default=0.5, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Top-level classification output
# ---------------------------------------------------------------------------

# Backward-compatibility mapping: EmailAction -> RouteTag value
_ACTION_TO_ROUTE_TAG: dict[EmailAction, str] = {
    EmailAction.REPLY_URGENT: "reply_candidate",
    EmailAction.REPLY_NORMAL: "reply_candidate",
    EmailAction.ACTION_REQUIRED: "task_candidate",
    EmailAction.CREATE_TASK: "task_candidate",
    EmailAction.CREATE_EVENT: "calendar_candidate",
    EmailAction.READ_ONLY: "digest_only",
    EmailAction.ARCHIVE: "ignore",
    EmailAction.IGNORE: "ignore",
}

# Backward-compatibility mapping: EmailAction -> RouteMode value
_ACTION_TO_ROUTE_MODE: dict[EmailAction, str] = {
    EmailAction.REPLY_URGENT: "draft",
    EmailAction.REPLY_NORMAL: "draft",
    EmailAction.ACTION_REQUIRED: "ask",
    EmailAction.CREATE_TASK: "draft",
    EmailAction.CREATE_EVENT: "draft",
    EmailAction.READ_ONLY: "skip",
    EmailAction.ARCHIVE: "skip",
    EmailAction.IGNORE: "skip",
}


class EmailClassification(BaseModel):
    """Complete single-call classification output for one email.

    All fields are produced by a single LLM call.  The schema is designed
    to be JSON-serializable for both storage and benchmark validation.
    """

    # Core classification
    category: str = Field(
        default="unknown",
        description="Email category from seed list or learned",
    )
    action: EmailAction = Field(
        default=EmailAction.READ_ONLY,
        description="Required action",
    )
    urgency: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0=none, 1=critical",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Model confidence in classification",
    )

    # Content analysis
    sentiment: EmailSentiment = EmailSentiment.NEUTRAL
    topics: list[str] = Field(
        default_factory=list,
        description="Free-form topic tags",
    )
    summary: str = Field(default="", description="One-line summary")

    # Thread context
    thread: ThreadContext = Field(default_factory=ThreadContext)

    # Contact profiling
    contact: ContactSignal = Field(default_factory=ContactSignal)

    # LLM reasoning (for audit / debugging)
    reasoning: str = Field(
        default="",
        description="Brief LLM reasoning for classification",
    )

    # -- validators ----------------------------------------------------------

    @field_validator("category")
    @classmethod
    def _normalise_category(cls, v: str) -> str:
        """Accept any string but normalise to lowercase."""
        return v.strip().lower() if isinstance(v, str) else "unknown"

    @field_validator("topics")
    @classmethod
    def _normalise_topics(cls, v: list[str]) -> list[str]:
        """Normalise, deduplicate, and cap topics."""
        seen: set[str] = set()
        result: list[str] = []
        for topic in v:
            if not isinstance(topic, str):
                continue
            normalised = topic.strip().lower()
            if normalised and normalised not in seen:
                seen.add(normalised)
                result.append(normalised)
        return result[:10]

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage / JSON export."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailClassification:
        """Deserialise, tolerating partial or slightly malformed data."""
        return cls.model_validate(data)

    # -- backward compatibility ----------------------------------------------

    def to_route_tag(self) -> str:
        """Map *action* to legacy ``RouteTag`` value."""
        return _ACTION_TO_ROUTE_TAG.get(self.action, "ignore")

    def to_route_mode(self) -> str:
        """Map *action* to legacy ``RouteMode`` value."""
        return _ACTION_TO_ROUTE_MODE.get(self.action, "draft")

    def is_urgent(self, threshold: float = 0.7) -> bool:
        """Check whether this email warrants an urgent notification."""
        return self.urgency >= threshold or self.action == EmailAction.REPLY_URGENT
