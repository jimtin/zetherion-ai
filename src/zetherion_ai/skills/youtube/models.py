"""Data models for the YouTube skills.

Defines all dataclasses, enums, and type aliases shared across the
Intelligence, Management, and Strategy skills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TrustLevel(Enum):
    """Scaling trust levels for auto-reply behaviour."""

    SUPERVISED = 0  # All replies need human approval
    GUIDED = 1  # Routine replies auto-approved
    AUTONOMOUS = 2  # Most auto-approved; sensitive need approval
    FULL_AUTO = 3  # All auto-approved; human reviews retroactively


class ReplyStatus(Enum):
    """Lifecycle states for a reply draft."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"


class ReplyCategory(Enum):
    """Classification categories for comments/replies."""

    THANK_YOU = "thank_you"
    FAQ = "faq"
    QUESTION = "question"
    FEEDBACK = "feedback"
    COMPLAINT = "complaint"
    SPAM = "spam"


class CommentSentiment(Enum):
    """Sentiment classification for a comment."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class AssumptionSource(Enum):
    """How an assumption was created."""

    CONFIRMED = "confirmed"  # User explicitly confirmed
    INFERRED = "inferred"  # Derived from data analysis
    INVALIDATED = "invalidated"  # Contradicted by data
    NEEDS_REVIEW = "needs_review"  # Requires human review


class AssumptionCategory(Enum):
    """Broad category for an assumption."""

    AUDIENCE = "audience"
    CONTENT = "content"
    TONE = "tone"
    SCHEDULE = "schedule"
    TOPIC = "topic"
    COMPETITOR = "competitor"
    PERFORMANCE = "performance"


class GrowthTrend(Enum):
    """Direction of channel growth."""

    INCREASING = "increasing"
    STABLE = "stable"
    DECLINING = "declining"


class RecommendationPriority(Enum):
    """Priority levels for recommendations."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RecommendationCategory(Enum):
    """Category of a recommendation."""

    CONTENT = "content"
    ENGAGEMENT = "engagement"
    SEO = "seo"
    SCHEDULE = "schedule"
    COMMUNITY = "community"


class HealthIssueSeverity(Enum):
    """Severity of a channel health issue."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TagRecommendationStatus(Enum):
    """Lifecycle status for a tag recommendation."""

    PENDING = "pending"
    APPLIED = "applied"
    DISMISSED = "dismissed"


class StrategyType(Enum):
    """Type of strategy document."""

    FULL = "full"
    CONTENT_ONLY = "content_only"
    GROWTH_ONLY = "growth_only"


class IntelligenceReportType(Enum):
    """Type of intelligence report."""

    FULL = "full"
    COMMENTS_ONLY = "comments_only"
    PERFORMANCE_ONLY = "performance_only"


class OnboardingCategory(Enum):
    """Categories that must be completed during onboarding."""

    TOPICS = "topics"
    AUDIENCE = "audience"
    TONE = "tone"
    EXCLUSIONS = "exclusions"
    SCHEDULE = "schedule"
    DOCUMENTS = "documents"


# ---------------------------------------------------------------------------
# Ingested data models (pushed by CGS)
# ---------------------------------------------------------------------------


@dataclass
class YouTubeChannel:
    """A registered YouTube channel linked to a tenant."""

    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    channel_youtube_id: str = ""
    channel_name: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    onboarding_complete: bool = False
    trust_level: int = TrustLevel.SUPERVISED.value
    trust_stats: dict[str, Any] = field(
        default_factory=lambda: {"total": 0, "approved": 0, "rejected": 0}
    )
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "channel_youtube_id": self.channel_youtube_id,
            "channel_name": self.channel_name,
            "config": self.config,
            "onboarding_complete": self.onboarding_complete,
            "trust_level": self.trust_level,
            "trust_stats": self.trust_stats,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class YouTubeVideo:
    """A video pushed from CGS."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    video_youtube_id: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)  # views, likes, comments count
    published_at: datetime | None = None
    ingested_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "video_youtube_id": self.video_youtube_id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "stats": self.stats,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "ingested_at": self.ingested_at.isoformat(),
        }


@dataclass
class YouTubeComment:
    """A comment pushed from CGS."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    video_id: UUID | None = None
    comment_youtube_id: str = ""
    author: str = ""
    text: str = ""
    like_count: int = 0
    published_at: datetime | None = None
    parent_comment_id: str | None = None  # YouTube parent comment ID for replies
    ingested_at: datetime = field(default_factory=datetime.utcnow)
    # Analysis fields (populated by Intelligence skill)
    sentiment: str | None = None
    category: str | None = None
    topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "video_id": str(self.video_id) if self.video_id else None,
            "comment_youtube_id": self.comment_youtube_id,
            "author": self.author,
            "text": self.text,
            "like_count": self.like_count,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "parent_comment_id": self.parent_comment_id,
            "ingested_at": self.ingested_at.isoformat(),
            "sentiment": self.sentiment,
            "category": self.category,
            "topics": self.topics,
        }


@dataclass
class YouTubeChannelStats:
    """A channel stats snapshot pushed from CGS."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    snapshot: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "snapshot": self.snapshot,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass
class YouTubeChannelDocument:
    """A client document (brand guide, etc.) for RAG."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    title: str = ""
    content: str = ""
    doc_type: str = ""  # brand_guide, audience_research, content_guidelines, etc.
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "title": self.title,
            "content": self.content,
            "doc_type": self.doc_type,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Skill output models
# ---------------------------------------------------------------------------


@dataclass
class IntelligenceReport:
    """Structured channel intelligence report."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    report_type: str = IntelligenceReportType.FULL.value
    report: dict[str, Any] = field(default_factory=dict)
    model_used: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": str(self.id),
            "channel_id": str(self.channel_id),
            "report_type": self.report_type,
            "report": self.report,
            "model_used": self.model_used,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class ReplyDraft:
    """An auto-generated reply draft for a YouTube comment."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    comment_id: str = ""  # YouTube comment ID
    video_id: str = ""  # YouTube video ID
    original_comment: str = ""
    draft_reply: str = ""
    confidence: float = 0.0
    category: str = ReplyCategory.FEEDBACK.value
    status: str = ReplyStatus.PENDING.value
    auto_approved: bool = False
    model_used: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None = None
    posted_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_id": str(self.id),
            "channel_id": str(self.channel_id),
            "comment_id": self.comment_id,
            "video_id": self.video_id,
            "original_comment": self.original_comment,
            "draft_reply": self.draft_reply,
            "confidence": self.confidence,
            "category": self.category,
            "status": self.status,
            "auto_approved": self.auto_approved,
            "model_used": self.model_used,
            "created_at": self.created_at.isoformat(),
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


@dataclass
class TagRecommendation:
    """A tag recommendation for a video."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    video_id: str = ""  # YouTube video ID
    current_tags: list[str] = field(default_factory=list)
    suggested_tags: list[str] = field(default_factory=list)
    reason: str = ""
    status: str = TagRecommendationStatus.PENDING.value
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "video_id": self.video_id,
            "current_tags": self.current_tags,
            "suggested_tags": self.suggested_tags,
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class StrategyDocument:
    """A generated channel strategy."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    strategy_type: str = StrategyType.FULL.value
    strategy: dict[str, Any] = field(default_factory=dict)
    model_used: str = ""
    valid_until: datetime | None = None
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.id),
            "channel_id": str(self.channel_id),
            "strategy_type": self.strategy_type,
            "strategy": self.strategy,
            "model_used": self.model_used,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class ChannelAssumption:
    """A tracked assumption about a channel."""

    id: UUID = field(default_factory=uuid4)
    channel_id: UUID = field(default_factory=uuid4)
    category: str = AssumptionCategory.CONTENT.value
    statement: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = AssumptionSource.INFERRED.value
    confirmed_at: datetime | None = None
    last_validated: datetime = field(default_factory=datetime.utcnow)
    next_validation: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "channel_id": str(self.channel_id),
            "category": self.category,
            "statement": self.statement,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "source": self.source,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "last_validated": self.last_validated.isoformat(),
            "next_validation": self.next_validation.isoformat(),
        }


@dataclass
class HealthIssue:
    """A channel health issue found during audit."""

    issue_type: str = ""
    severity: str = HealthIssueSeverity.LOW.value
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.issue_type,
            "severity": self.severity,
            "suggestion": self.suggestion,
        }


@dataclass
class ManagementState:
    """Current management state for a channel."""

    channel_id: UUID = field(default_factory=uuid4)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    onboarding_complete: bool = False
    trust_level: int = TrustLevel.SUPERVISED.value
    trust_label: str = TrustLevel.SUPERVISED.name
    trust_stats: dict[str, Any] = field(
        default_factory=lambda: {"total": 0, "approved": 0, "rejected": 0, "rate": 0.0}
    )
    next_level_at: int = 50
    auto_reply_enabled: bool = False
    auto_categories: list[str] = field(default_factory=list)
    review_categories: list[str] = field(default_factory=list)
    pending_count: int = 0
    posted_today: int = 0
    health_issues: list[HealthIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": str(self.channel_id),
            "updated_at": self.updated_at.isoformat(),
            "onboarding_complete": self.onboarding_complete,
            "trust": {
                "level": self.trust_level,
                "label": self.trust_label,
                "stats": self.trust_stats,
                "next_level_at": self.next_level_at,
            },
            "auto_reply": {
                "enabled": self.auto_reply_enabled,
                "auto_categories": self.auto_categories,
                "review_categories": self.review_categories,
                "pending_count": self.pending_count,
                "posted_today": self.posted_today,
            },
            "health_issues": [h.to_dict() for h in self.health_issues],
        }


@dataclass
class OnboardingQuestion:
    """A question to ask during channel onboarding."""

    category: str = ""
    question: str = ""
    hint: str = ""
    required: bool = True
    answered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "question": self.question,
            "hint": self.hint,
            "required": self.required,
            "answered": self.answered,
        }


# ---------------------------------------------------------------------------
# Comment analysis result (intermediate, used by Intelligence pipeline)
# ---------------------------------------------------------------------------


@dataclass
class CommentAnalysis:
    """Analysis result for a single comment (from Ollama classification)."""

    comment_id: str = ""
    sentiment: str = CommentSentiment.NEUTRAL.value
    category: str = ReplyCategory.FEEDBACK.value
    topics: list[str] = field(default_factory=list)
    is_question: bool = False
    entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "sentiment": self.sentiment,
            "category": self.category,
            "topics": self.topics,
            "is_question": self.is_question,
            "entities": self.entities,
        }
