"""Tests for YouTube models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from zetherion_ai.skills.youtube.models import (
    AssumptionCategory,
    AssumptionSource,
    ChannelAssumption,
    CommentAnalysis,
    CommentSentiment,
    GrowthTrend,
    HealthIssue,
    HealthIssueSeverity,
    IntelligenceReport,
    IntelligenceReportType,
    ManagementState,
    OnboardingCategory,
    OnboardingQuestion,
    RecommendationCategory,
    RecommendationPriority,
    ReplyCategory,
    ReplyDraft,
    ReplyStatus,
    StrategyDocument,
    StrategyType,
    TagRecommendation,
    TagRecommendationStatus,
    TrustLevel,
    YouTubeChannel,
    YouTubeChannelDocument,
    YouTubeChannelStats,
    YouTubeComment,
    YouTubeVideo,
)

# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestTrustLevel:
    """Tests for TrustLevel enum."""

    def test_all_members_exist(self) -> None:
        assert TrustLevel.SUPERVISED.value == 0
        assert TrustLevel.GUIDED.value == 1
        assert TrustLevel.AUTONOMOUS.value == 2
        assert TrustLevel.FULL_AUTO.value == 3

    def test_member_count(self) -> None:
        assert len(TrustLevel) == 4


class TestReplyStatus:
    """Tests for ReplyStatus enum."""

    def test_all_members_exist(self) -> None:
        assert ReplyStatus.PENDING.value == "pending"
        assert ReplyStatus.APPROVED.value == "approved"
        assert ReplyStatus.REJECTED.value == "rejected"
        assert ReplyStatus.POSTED.value == "posted"

    def test_member_count(self) -> None:
        assert len(ReplyStatus) == 4


class TestReplyCategory:
    """Tests for ReplyCategory enum."""

    def test_all_members_exist(self) -> None:
        assert ReplyCategory.THANK_YOU.value == "thank_you"
        assert ReplyCategory.FAQ.value == "faq"
        assert ReplyCategory.QUESTION.value == "question"
        assert ReplyCategory.FEEDBACK.value == "feedback"
        assert ReplyCategory.COMPLAINT.value == "complaint"
        assert ReplyCategory.SPAM.value == "spam"

    def test_member_count(self) -> None:
        assert len(ReplyCategory) == 6


class TestCommentSentiment:
    """Tests for CommentSentiment enum."""

    def test_all_members_exist(self) -> None:
        assert CommentSentiment.POSITIVE.value == "positive"
        assert CommentSentiment.NEUTRAL.value == "neutral"
        assert CommentSentiment.NEGATIVE.value == "negative"

    def test_member_count(self) -> None:
        assert len(CommentSentiment) == 3


class TestAssumptionSource:
    """Tests for AssumptionSource enum."""

    def test_all_members_exist(self) -> None:
        assert AssumptionSource.CONFIRMED.value == "confirmed"
        assert AssumptionSource.INFERRED.value == "inferred"
        assert AssumptionSource.INVALIDATED.value == "invalidated"
        assert AssumptionSource.NEEDS_REVIEW.value == "needs_review"

    def test_member_count(self) -> None:
        assert len(AssumptionSource) == 4


class TestAssumptionCategory:
    """Tests for AssumptionCategory enum."""

    def test_all_members_exist(self) -> None:
        assert AssumptionCategory.AUDIENCE.value == "audience"
        assert AssumptionCategory.CONTENT.value == "content"
        assert AssumptionCategory.TONE.value == "tone"
        assert AssumptionCategory.SCHEDULE.value == "schedule"
        assert AssumptionCategory.TOPIC.value == "topic"
        assert AssumptionCategory.COMPETITOR.value == "competitor"
        assert AssumptionCategory.PERFORMANCE.value == "performance"

    def test_member_count(self) -> None:
        assert len(AssumptionCategory) == 7


class TestGrowthTrend:
    """Tests for GrowthTrend enum."""

    def test_all_members_exist(self) -> None:
        assert GrowthTrend.INCREASING.value == "increasing"
        assert GrowthTrend.STABLE.value == "stable"
        assert GrowthTrend.DECLINING.value == "declining"

    def test_member_count(self) -> None:
        assert len(GrowthTrend) == 3


class TestRecommendationPriority:
    """Tests for RecommendationPriority enum."""

    def test_all_members_exist(self) -> None:
        assert RecommendationPriority.HIGH.value == "high"
        assert RecommendationPriority.MEDIUM.value == "medium"
        assert RecommendationPriority.LOW.value == "low"

    def test_member_count(self) -> None:
        assert len(RecommendationPriority) == 3


class TestRecommendationCategory:
    """Tests for RecommendationCategory enum."""

    def test_all_members_exist(self) -> None:
        assert RecommendationCategory.CONTENT.value == "content"
        assert RecommendationCategory.ENGAGEMENT.value == "engagement"
        assert RecommendationCategory.SEO.value == "seo"
        assert RecommendationCategory.SCHEDULE.value == "schedule"
        assert RecommendationCategory.COMMUNITY.value == "community"

    def test_member_count(self) -> None:
        assert len(RecommendationCategory) == 5


class TestHealthIssueSeverity:
    """Tests for HealthIssueSeverity enum."""

    def test_all_members_exist(self) -> None:
        assert HealthIssueSeverity.LOW.value == "low"
        assert HealthIssueSeverity.MEDIUM.value == "medium"
        assert HealthIssueSeverity.HIGH.value == "high"

    def test_member_count(self) -> None:
        assert len(HealthIssueSeverity) == 3


class TestTagRecommendationStatus:
    """Tests for TagRecommendationStatus enum."""

    def test_all_members_exist(self) -> None:
        assert TagRecommendationStatus.PENDING.value == "pending"
        assert TagRecommendationStatus.APPLIED.value == "applied"
        assert TagRecommendationStatus.DISMISSED.value == "dismissed"

    def test_member_count(self) -> None:
        assert len(TagRecommendationStatus) == 3


class TestStrategyType:
    """Tests for StrategyType enum."""

    def test_all_members_exist(self) -> None:
        assert StrategyType.FULL.value == "full"
        assert StrategyType.CONTENT_ONLY.value == "content_only"
        assert StrategyType.GROWTH_ONLY.value == "growth_only"

    def test_member_count(self) -> None:
        assert len(StrategyType) == 3


class TestIntelligenceReportType:
    """Tests for IntelligenceReportType enum."""

    def test_all_members_exist(self) -> None:
        assert IntelligenceReportType.FULL.value == "full"
        assert IntelligenceReportType.COMMENTS_ONLY.value == "comments_only"
        assert IntelligenceReportType.PERFORMANCE_ONLY.value == "performance_only"

    def test_member_count(self) -> None:
        assert len(IntelligenceReportType) == 3


class TestOnboardingCategory:
    """Tests for OnboardingCategory enum."""

    def test_all_members_exist(self) -> None:
        assert OnboardingCategory.TOPICS.value == "topics"
        assert OnboardingCategory.AUDIENCE.value == "audience"
        assert OnboardingCategory.TONE.value == "tone"
        assert OnboardingCategory.EXCLUSIONS.value == "exclusions"
        assert OnboardingCategory.SCHEDULE.value == "schedule"
        assert OnboardingCategory.DOCUMENTS.value == "documents"

    def test_member_count(self) -> None:
        assert len(OnboardingCategory) == 6


# ---------------------------------------------------------------------------
# Dataclass tests — ingested data models
# ---------------------------------------------------------------------------


class TestYouTubeChannel:
    """Tests for YouTubeChannel dataclass."""

    def test_defaults(self) -> None:
        """All defaults are applied when no arguments are given."""
        ch = YouTubeChannel()
        assert isinstance(ch.id, UUID)
        assert isinstance(ch.tenant_id, UUID)
        assert ch.channel_youtube_id == ""
        assert ch.channel_name == ""
        assert ch.config == {}
        assert ch.onboarding_complete is False
        assert ch.trust_level == TrustLevel.SUPERVISED.value
        assert ch.trust_stats == {"total": 0, "approved": 0, "rejected": 0}
        assert isinstance(ch.created_at, datetime)
        assert isinstance(ch.updated_at, datetime)

    def test_custom_fields(self) -> None:
        """Constructor accepts all keyword arguments."""
        cid = uuid4()
        tid = uuid4()
        now = datetime(2025, 6, 1, 12, 0, 0)
        ch = YouTubeChannel(
            id=cid,
            tenant_id=tid,
            channel_youtube_id="UC123",
            channel_name="My Channel",
            config={"lang": "en"},
            onboarding_complete=True,
            trust_level=TrustLevel.AUTONOMOUS.value,
            trust_stats={"total": 100, "approved": 90, "rejected": 10},
            created_at=now,
            updated_at=now,
        )
        assert ch.id == cid
        assert ch.tenant_id == tid
        assert ch.channel_youtube_id == "UC123"
        assert ch.channel_name == "My Channel"
        assert ch.config == {"lang": "en"}
        assert ch.onboarding_complete is True
        assert ch.trust_level == 2
        assert ch.trust_stats["total"] == 100
        assert ch.created_at == now

    def test_to_dict_structure(self) -> None:
        """to_dict produces the expected keys and value types."""
        cid = uuid4()
        tid = uuid4()
        now = datetime(2025, 1, 15, 10, 30, 0)
        ch = YouTubeChannel(
            id=cid,
            tenant_id=tid,
            channel_youtube_id="UC456",
            channel_name="Test",
            created_at=now,
            updated_at=now,
        )
        d = ch.to_dict()
        assert d["id"] == str(cid)
        assert d["tenant_id"] == str(tid)
        assert d["channel_youtube_id"] == "UC456"
        assert d["channel_name"] == "Test"
        assert d["config"] == {}
        assert d["onboarding_complete"] is False
        assert d["trust_level"] == 0
        assert d["trust_stats"] == {"total": 0, "approved": 0, "rejected": 0}
        assert d["created_at"] == "2025-01-15T10:30:00"
        assert d["updated_at"] == "2025-01-15T10:30:00"

    def test_to_dict_datetimes_are_isoformat(self) -> None:
        """Datetime values in to_dict can be parsed back via fromisoformat."""
        ch = YouTubeChannel()
        d = ch.to_dict()
        assert isinstance(datetime.fromisoformat(d["created_at"]), datetime)
        assert isinstance(datetime.fromisoformat(d["updated_at"]), datetime)

    def test_to_dict_uuids_are_strings(self) -> None:
        """UUID values in to_dict are plain strings."""
        ch = YouTubeChannel()
        d = ch.to_dict()
        assert isinstance(d["id"], str)
        assert isinstance(d["tenant_id"], str)
        UUID(d["id"])  # should not raise
        UUID(d["tenant_id"])

    def test_two_instances_get_different_ids(self) -> None:
        """Each default-constructed instance gets a unique id."""
        ch1 = YouTubeChannel()
        ch2 = YouTubeChannel()
        assert ch1.id != ch2.id

    def test_mutable_defaults_are_independent(self) -> None:
        """Mutable default fields are not shared between instances."""
        ch1 = YouTubeChannel()
        ch2 = YouTubeChannel()
        ch1.config["key"] = "value"
        assert "key" not in ch2.config
        ch1.trust_stats["total"] = 999
        assert ch2.trust_stats["total"] == 0


class TestYouTubeVideo:
    """Tests for YouTubeVideo dataclass."""

    def test_defaults(self) -> None:
        v = YouTubeVideo()
        assert isinstance(v.id, UUID)
        assert isinstance(v.channel_id, UUID)
        assert v.video_youtube_id == ""
        assert v.title == ""
        assert v.description == ""
        assert v.tags == []
        assert v.stats == {}
        assert v.published_at is None
        assert isinstance(v.ingested_at, datetime)

    def test_custom_fields(self) -> None:
        vid = uuid4()
        cid = uuid4()
        pub = datetime(2025, 3, 1)
        v = YouTubeVideo(
            id=vid,
            channel_id=cid,
            video_youtube_id="dQw4w9WgXcQ",
            title="Test Video",
            description="A description",
            tags=["tag1", "tag2"],
            stats={"views": 1000, "likes": 50},
            published_at=pub,
        )
        assert v.id == vid
        assert v.channel_id == cid
        assert v.video_youtube_id == "dQw4w9WgXcQ"
        assert v.title == "Test Video"
        assert v.tags == ["tag1", "tag2"]
        assert v.stats["views"] == 1000
        assert v.published_at == pub

    def test_to_dict_with_published_at(self) -> None:
        pub = datetime(2025, 5, 20, 8, 0, 0)
        v = YouTubeVideo(published_at=pub)
        d = v.to_dict()
        assert d["published_at"] == "2025-05-20T08:00:00"

    def test_to_dict_without_published_at(self) -> None:
        v = YouTubeVideo()
        d = v.to_dict()
        assert d["published_at"] is None

    def test_to_dict_keys(self) -> None:
        v = YouTubeVideo()
        d = v.to_dict()
        expected_keys = {
            "id",
            "channel_id",
            "video_youtube_id",
            "title",
            "description",
            "tags",
            "stats",
            "published_at",
            "ingested_at",
        }
        assert set(d.keys()) == expected_keys

    def test_mutable_defaults_are_independent(self) -> None:
        v1 = YouTubeVideo()
        v2 = YouTubeVideo()
        v1.tags.append("new_tag")
        assert "new_tag" not in v2.tags
        v1.stats["views"] = 42
        assert "views" not in v2.stats


class TestYouTubeComment:
    """Tests for YouTubeComment dataclass."""

    def test_defaults(self) -> None:
        c = YouTubeComment()
        assert isinstance(c.id, UUID)
        assert isinstance(c.channel_id, UUID)
        assert c.video_id is None
        assert c.comment_youtube_id == ""
        assert c.author == ""
        assert c.text == ""
        assert c.like_count == 0
        assert c.published_at is None
        assert c.parent_comment_id is None
        assert isinstance(c.ingested_at, datetime)
        assert c.sentiment is None
        assert c.category is None
        assert c.topics == []

    def test_custom_fields(self) -> None:
        vid = uuid4()
        c = YouTubeComment(
            video_id=vid,
            comment_youtube_id="Ugx123",
            author="Alice",
            text="Great video!",
            like_count=5,
            published_at=datetime(2025, 4, 1),
            parent_comment_id="Ugx000",
            sentiment="positive",
            category="thank_you",
            topics=["gratitude"],
        )
        assert c.video_id == vid
        assert c.author == "Alice"
        assert c.like_count == 5
        assert c.parent_comment_id == "Ugx000"
        assert c.sentiment == "positive"
        assert c.category == "thank_you"
        assert c.topics == ["gratitude"]

    def test_to_dict_with_optional_none(self) -> None:
        c = YouTubeComment()
        d = c.to_dict()
        assert d["video_id"] is None
        assert d["published_at"] is None
        assert d["parent_comment_id"] is None
        assert d["sentiment"] is None
        assert d["category"] is None

    def test_to_dict_with_optional_set(self) -> None:
        vid = uuid4()
        pub = datetime(2025, 6, 1, 12, 0, 0)
        c = YouTubeComment(
            video_id=vid,
            published_at=pub,
            parent_comment_id="Ugx999",
            sentiment="negative",
            category="complaint",
        )
        d = c.to_dict()
        assert d["video_id"] == str(vid)
        assert d["published_at"] == "2025-06-01T12:00:00"
        assert d["parent_comment_id"] == "Ugx999"
        assert d["sentiment"] == "negative"
        assert d["category"] == "complaint"

    def test_to_dict_keys(self) -> None:
        c = YouTubeComment()
        d = c.to_dict()
        expected_keys = {
            "id",
            "channel_id",
            "video_id",
            "comment_youtube_id",
            "author",
            "text",
            "like_count",
            "published_at",
            "parent_comment_id",
            "ingested_at",
            "sentiment",
            "category",
            "topics",
        }
        assert set(d.keys()) == expected_keys


class TestYouTubeChannelStats:
    """Tests for YouTubeChannelStats dataclass."""

    def test_defaults(self) -> None:
        s = YouTubeChannelStats()
        assert isinstance(s.id, UUID)
        assert isinstance(s.channel_id, UUID)
        assert s.snapshot == {}
        assert isinstance(s.recorded_at, datetime)

    def test_custom_fields(self) -> None:
        snap = {"subscribers": 10000, "total_views": 500000}
        s = YouTubeChannelStats(snapshot=snap)
        assert s.snapshot == snap

    def test_to_dict_structure(self) -> None:
        sid = uuid4()
        cid = uuid4()
        now = datetime(2025, 7, 1, 0, 0, 0)
        s = YouTubeChannelStats(id=sid, channel_id=cid, recorded_at=now)
        d = s.to_dict()
        assert d["id"] == str(sid)
        assert d["channel_id"] == str(cid)
        assert d["snapshot"] == {}
        assert d["recorded_at"] == "2025-07-01T00:00:00"

    def test_mutable_defaults_are_independent(self) -> None:
        s1 = YouTubeChannelStats()
        s2 = YouTubeChannelStats()
        s1.snapshot["key"] = "val"
        assert "key" not in s2.snapshot


class TestYouTubeChannelDocument:
    """Tests for YouTubeChannelDocument dataclass."""

    def test_defaults(self) -> None:
        doc = YouTubeChannelDocument()
        assert isinstance(doc.id, UUID)
        assert isinstance(doc.channel_id, UUID)
        assert doc.title == ""
        assert doc.content == ""
        assert doc.doc_type == ""
        assert isinstance(doc.created_at, datetime)

    def test_custom_fields(self) -> None:
        doc = YouTubeChannelDocument(
            title="Brand Guide",
            content="Our brand voice is friendly.",
            doc_type="brand_guide",
        )
        assert doc.title == "Brand Guide"
        assert doc.content == "Our brand voice is friendly."
        assert doc.doc_type == "brand_guide"

    def test_to_dict_structure(self) -> None:
        did = uuid4()
        cid = uuid4()
        now = datetime(2025, 2, 1, 0, 0, 0)
        doc = YouTubeChannelDocument(
            id=did,
            channel_id=cid,
            title="Guide",
            content="Body text",
            doc_type="content_guidelines",
            created_at=now,
        )
        d = doc.to_dict()
        assert d["id"] == str(did)
        assert d["channel_id"] == str(cid)
        assert d["title"] == "Guide"
        assert d["content"] == "Body text"
        assert d["doc_type"] == "content_guidelines"
        assert d["created_at"] == "2025-02-01T00:00:00"


# ---------------------------------------------------------------------------
# Dataclass tests — skill output models
# ---------------------------------------------------------------------------


class TestIntelligenceReport:
    """Tests for IntelligenceReport dataclass."""

    def test_defaults(self) -> None:
        r = IntelligenceReport()
        assert isinstance(r.id, UUID)
        assert isinstance(r.channel_id, UUID)
        assert r.report_type == IntelligenceReportType.FULL.value
        assert r.report == {}
        assert r.model_used == ""
        assert isinstance(r.generated_at, datetime)

    def test_custom_fields(self) -> None:
        r = IntelligenceReport(
            report_type=IntelligenceReportType.COMMENTS_ONLY.value,
            report={"comments": []},
            model_used="llama3.2:3b",
        )
        assert r.report_type == "comments_only"
        assert r.report == {"comments": []}
        assert r.model_used == "llama3.2:3b"

    def test_to_dict_uses_report_id_key(self) -> None:
        """to_dict maps 'id' to 'report_id' in the output."""
        rid = uuid4()
        r = IntelligenceReport(id=rid)
        d = r.to_dict()
        assert "report_id" in d
        assert d["report_id"] == str(rid)
        assert "id" not in d

    def test_to_dict_keys(self) -> None:
        r = IntelligenceReport()
        d = r.to_dict()
        expected_keys = {
            "report_id",
            "channel_id",
            "report_type",
            "report",
            "model_used",
            "generated_at",
        }
        assert set(d.keys()) == expected_keys


class TestReplyDraft:
    """Tests for ReplyDraft dataclass."""

    def test_defaults(self) -> None:
        rd = ReplyDraft()
        assert isinstance(rd.id, UUID)
        assert isinstance(rd.channel_id, UUID)
        assert rd.comment_id == ""
        assert rd.video_id == ""
        assert rd.original_comment == ""
        assert rd.draft_reply == ""
        assert rd.confidence == 0.0
        assert rd.category == ReplyCategory.FEEDBACK.value
        assert rd.status == ReplyStatus.PENDING.value
        assert rd.auto_approved is False
        assert rd.model_used == ""
        assert isinstance(rd.created_at, datetime)
        assert rd.reviewed_at is None
        assert rd.posted_at is None

    def test_custom_fields(self) -> None:
        reviewed = datetime(2025, 5, 1, 12, 0, 0)
        posted = datetime(2025, 5, 1, 12, 5, 0)
        rd = ReplyDraft(
            comment_id="Ugx111",
            video_id="vid_abc",
            original_comment="Loved it!",
            draft_reply="Thank you!",
            confidence=0.95,
            category=ReplyCategory.THANK_YOU.value,
            status=ReplyStatus.POSTED.value,
            auto_approved=True,
            model_used="gemini-2.0-flash",
            reviewed_at=reviewed,
            posted_at=posted,
        )
        assert rd.comment_id == "Ugx111"
        assert rd.confidence == 0.95
        assert rd.category == "thank_you"
        assert rd.status == "posted"
        assert rd.auto_approved is True
        assert rd.reviewed_at == reviewed
        assert rd.posted_at == posted

    def test_to_dict_uses_reply_id_key(self) -> None:
        """to_dict maps 'id' to 'reply_id' in the output."""
        rid = uuid4()
        rd = ReplyDraft(id=rid)
        d = rd.to_dict()
        assert "reply_id" in d
        assert d["reply_id"] == str(rid)
        assert "id" not in d

    def test_to_dict_optional_datetimes_none(self) -> None:
        rd = ReplyDraft()
        d = rd.to_dict()
        assert d["reviewed_at"] is None
        assert d["posted_at"] is None

    def test_to_dict_optional_datetimes_set(self) -> None:
        reviewed = datetime(2025, 8, 10, 14, 0, 0)
        posted = datetime(2025, 8, 10, 14, 1, 0)
        rd = ReplyDraft(reviewed_at=reviewed, posted_at=posted)
        d = rd.to_dict()
        assert d["reviewed_at"] == "2025-08-10T14:00:00"
        assert d["posted_at"] == "2025-08-10T14:01:00"

    def test_to_dict_keys(self) -> None:
        rd = ReplyDraft()
        d = rd.to_dict()
        expected_keys = {
            "reply_id",
            "channel_id",
            "comment_id",
            "video_id",
            "original_comment",
            "draft_reply",
            "confidence",
            "category",
            "status",
            "auto_approved",
            "model_used",
            "created_at",
            "reviewed_at",
            "posted_at",
        }
        assert set(d.keys()) == expected_keys


class TestTagRecommendation:
    """Tests for TagRecommendation dataclass."""

    def test_defaults(self) -> None:
        tr = TagRecommendation()
        assert isinstance(tr.id, UUID)
        assert isinstance(tr.channel_id, UUID)
        assert tr.video_id == ""
        assert tr.current_tags == []
        assert tr.suggested_tags == []
        assert tr.reason == ""
        assert tr.status == TagRecommendationStatus.PENDING.value
        assert isinstance(tr.created_at, datetime)

    def test_custom_fields(self) -> None:
        tr = TagRecommendation(
            video_id="vid_xyz",
            current_tags=["python", "coding"],
            suggested_tags=["python", "coding", "tutorial"],
            reason="Add tutorial tag for discoverability",
            status=TagRecommendationStatus.APPLIED.value,
        )
        assert tr.video_id == "vid_xyz"
        assert tr.current_tags == ["python", "coding"]
        assert tr.suggested_tags == ["python", "coding", "tutorial"]
        assert tr.reason == "Add tutorial tag for discoverability"
        assert tr.status == "applied"

    def test_to_dict_keys(self) -> None:
        tr = TagRecommendation()
        d = tr.to_dict()
        expected_keys = {
            "id",
            "channel_id",
            "video_id",
            "current_tags",
            "suggested_tags",
            "reason",
            "status",
            "created_at",
        }
        assert set(d.keys()) == expected_keys

    def test_mutable_defaults_are_independent(self) -> None:
        tr1 = TagRecommendation()
        tr2 = TagRecommendation()
        tr1.current_tags.append("test")
        assert "test" not in tr2.current_tags
        tr1.suggested_tags.append("new")
        assert "new" not in tr2.suggested_tags


class TestStrategyDocument:
    """Tests for StrategyDocument dataclass."""

    def test_defaults(self) -> None:
        sd = StrategyDocument()
        assert isinstance(sd.id, UUID)
        assert isinstance(sd.channel_id, UUID)
        assert sd.strategy_type == StrategyType.FULL.value
        assert sd.strategy == {}
        assert sd.model_used == ""
        assert sd.valid_until is None
        assert isinstance(sd.generated_at, datetime)

    def test_custom_fields(self) -> None:
        valid = datetime(2025, 12, 31)
        sd = StrategyDocument(
            strategy_type=StrategyType.GROWTH_ONLY.value,
            strategy={"goals": ["10k subs"]},
            model_used="gemini-2.0-flash",
            valid_until=valid,
        )
        assert sd.strategy_type == "growth_only"
        assert sd.strategy == {"goals": ["10k subs"]}
        assert sd.valid_until == valid

    def test_to_dict_uses_strategy_id_key(self) -> None:
        """to_dict maps 'id' to 'strategy_id' in the output."""
        sid = uuid4()
        sd = StrategyDocument(id=sid)
        d = sd.to_dict()
        assert "strategy_id" in d
        assert d["strategy_id"] == str(sid)
        assert "id" not in d

    def test_to_dict_valid_until_none(self) -> None:
        sd = StrategyDocument()
        d = sd.to_dict()
        assert d["valid_until"] is None

    def test_to_dict_valid_until_set(self) -> None:
        valid = datetime(2025, 12, 31, 23, 59, 59)
        sd = StrategyDocument(valid_until=valid)
        d = sd.to_dict()
        assert d["valid_until"] == "2025-12-31T23:59:59"

    def test_to_dict_keys(self) -> None:
        sd = StrategyDocument()
        d = sd.to_dict()
        expected_keys = {
            "strategy_id",
            "channel_id",
            "strategy_type",
            "strategy",
            "model_used",
            "valid_until",
            "generated_at",
        }
        assert set(d.keys()) == expected_keys


class TestChannelAssumption:
    """Tests for ChannelAssumption dataclass."""

    def test_defaults(self) -> None:
        ca = ChannelAssumption()
        assert isinstance(ca.id, UUID)
        assert isinstance(ca.channel_id, UUID)
        assert ca.category == AssumptionCategory.CONTENT.value
        assert ca.statement == ""
        assert ca.evidence == []
        assert ca.confidence == 0.0
        assert ca.source == AssumptionSource.INFERRED.value
        assert ca.confirmed_at is None
        assert isinstance(ca.last_validated, datetime)
        assert isinstance(ca.next_validation, datetime)

    def test_custom_fields(self) -> None:
        confirmed = datetime(2025, 4, 15, 10, 0, 0)
        ca = ChannelAssumption(
            category=AssumptionCategory.AUDIENCE.value,
            statement="Audience is mostly 18-24",
            evidence=["analytics_report_q1", "survey_2025"],
            confidence=0.85,
            source=AssumptionSource.CONFIRMED.value,
            confirmed_at=confirmed,
        )
        assert ca.category == "audience"
        assert ca.statement == "Audience is mostly 18-24"
        assert len(ca.evidence) == 2
        assert ca.confidence == 0.85
        assert ca.source == "confirmed"
        assert ca.confirmed_at == confirmed

    def test_to_dict_confirmed_at_none(self) -> None:
        ca = ChannelAssumption()
        d = ca.to_dict()
        assert d["confirmed_at"] is None

    def test_to_dict_confirmed_at_set(self) -> None:
        confirmed = datetime(2025, 3, 1, 8, 0, 0)
        ca = ChannelAssumption(confirmed_at=confirmed)
        d = ca.to_dict()
        assert d["confirmed_at"] == "2025-03-01T08:00:00"

    def test_to_dict_keys(self) -> None:
        ca = ChannelAssumption()
        d = ca.to_dict()
        expected_keys = {
            "id",
            "channel_id",
            "category",
            "statement",
            "evidence",
            "confidence",
            "source",
            "confirmed_at",
            "last_validated",
            "next_validation",
        }
        assert set(d.keys()) == expected_keys

    def test_mutable_defaults_are_independent(self) -> None:
        ca1 = ChannelAssumption()
        ca2 = ChannelAssumption()
        ca1.evidence.append("new_evidence")
        assert "new_evidence" not in ca2.evidence


class TestHealthIssue:
    """Tests for HealthIssue dataclass."""

    def test_defaults(self) -> None:
        hi = HealthIssue()
        assert hi.issue_type == ""
        assert hi.severity == HealthIssueSeverity.LOW.value
        assert hi.suggestion == ""

    def test_custom_fields(self) -> None:
        hi = HealthIssue(
            issue_type="missing_tags",
            severity=HealthIssueSeverity.HIGH.value,
            suggestion="Add relevant tags to improve SEO",
        )
        assert hi.issue_type == "missing_tags"
        assert hi.severity == "high"
        assert hi.suggestion == "Add relevant tags to improve SEO"

    def test_to_dict_maps_issue_type_to_type(self) -> None:
        """to_dict maps 'issue_type' field to 'type' key."""
        hi = HealthIssue(issue_type="low_engagement")
        d = hi.to_dict()
        assert "type" in d
        assert d["type"] == "low_engagement"
        assert "issue_type" not in d

    def test_to_dict_keys(self) -> None:
        hi = HealthIssue()
        d = hi.to_dict()
        assert set(d.keys()) == {"type", "severity", "suggestion"}


class TestManagementState:
    """Tests for ManagementState dataclass."""

    def test_defaults(self) -> None:
        ms = ManagementState()
        assert isinstance(ms.channel_id, UUID)
        assert isinstance(ms.updated_at, datetime)
        assert ms.onboarding_complete is False
        assert ms.trust_level == TrustLevel.SUPERVISED.value
        assert ms.trust_label == TrustLevel.SUPERVISED.name
        assert ms.trust_stats == {
            "total": 0,
            "approved": 0,
            "rejected": 0,
            "rate": 0.0,
        }
        assert ms.next_level_at == 50
        assert ms.auto_reply_enabled is False
        assert ms.auto_categories == []
        assert ms.review_categories == []
        assert ms.pending_count == 0
        assert ms.posted_today == 0
        assert ms.health_issues == []

    def test_custom_fields(self) -> None:
        issues = [
            HealthIssue(issue_type="a", severity="high", suggestion="fix a"),
            HealthIssue(issue_type="b", severity="low", suggestion="fix b"),
        ]
        ms = ManagementState(
            onboarding_complete=True,
            trust_level=TrustLevel.GUIDED.value,
            trust_label=TrustLevel.GUIDED.name,
            next_level_at=100,
            auto_reply_enabled=True,
            auto_categories=["thank_you", "faq"],
            review_categories=["complaint"],
            pending_count=3,
            posted_today=12,
            health_issues=issues,
        )
        assert ms.onboarding_complete is True
        assert ms.trust_level == 1
        assert ms.trust_label == "GUIDED"
        assert ms.auto_reply_enabled is True
        assert ms.auto_categories == ["thank_you", "faq"]
        assert ms.pending_count == 3
        assert ms.posted_today == 12
        assert len(ms.health_issues) == 2

    def test_to_dict_nested_structure(self) -> None:
        """to_dict produces nested trust and auto_reply sub-dicts."""
        cid = uuid4()
        now = datetime(2025, 9, 1, 0, 0, 0)
        issues = [HealthIssue(issue_type="stale", severity="medium", suggestion="update")]
        ms = ManagementState(
            channel_id=cid,
            updated_at=now,
            onboarding_complete=True,
            trust_level=TrustLevel.AUTONOMOUS.value,
            trust_label=TrustLevel.AUTONOMOUS.name,
            trust_stats={"total": 200, "approved": 180, "rejected": 20, "rate": 0.9},
            next_level_at=500,
            auto_reply_enabled=True,
            auto_categories=["thank_you"],
            review_categories=["complaint", "spam"],
            pending_count=5,
            posted_today=20,
            health_issues=issues,
        )
        d = ms.to_dict()
        assert d["channel_id"] == str(cid)
        assert d["updated_at"] == "2025-09-01T00:00:00"
        assert d["onboarding_complete"] is True

        # Nested trust block
        assert d["trust"]["level"] == 2
        assert d["trust"]["label"] == "AUTONOMOUS"
        assert d["trust"]["stats"]["rate"] == 0.9
        assert d["trust"]["next_level_at"] == 500

        # Nested auto_reply block
        assert d["auto_reply"]["enabled"] is True
        assert d["auto_reply"]["auto_categories"] == ["thank_you"]
        assert d["auto_reply"]["review_categories"] == ["complaint", "spam"]
        assert d["auto_reply"]["pending_count"] == 5
        assert d["auto_reply"]["posted_today"] == 20

        # Health issues serialized
        assert len(d["health_issues"]) == 1
        assert d["health_issues"][0] == {
            "type": "stale",
            "severity": "medium",
            "suggestion": "update",
        }

    def test_to_dict_empty_health_issues(self) -> None:
        ms = ManagementState()
        d = ms.to_dict()
        assert d["health_issues"] == []

    def test_to_dict_top_level_keys(self) -> None:
        ms = ManagementState()
        d = ms.to_dict()
        expected_keys = {
            "channel_id",
            "updated_at",
            "onboarding_complete",
            "trust",
            "auto_reply",
            "health_issues",
        }
        assert set(d.keys()) == expected_keys

    def test_mutable_defaults_are_independent(self) -> None:
        ms1 = ManagementState()
        ms2 = ManagementState()
        ms1.auto_categories.append("faq")
        assert "faq" not in ms2.auto_categories
        ms1.review_categories.append("spam")
        assert "spam" not in ms2.review_categories
        ms1.health_issues.append(HealthIssue())
        assert len(ms2.health_issues) == 0


class TestOnboardingQuestion:
    """Tests for OnboardingQuestion dataclass."""

    def test_defaults(self) -> None:
        q = OnboardingQuestion()
        assert q.category == ""
        assert q.question == ""
        assert q.hint == ""
        assert q.required is True
        assert q.answered is False

    def test_custom_fields(self) -> None:
        q = OnboardingQuestion(
            category="topics",
            question="What topics do you cover?",
            hint="e.g. programming, cooking",
            required=False,
            answered=True,
        )
        assert q.category == "topics"
        assert q.question == "What topics do you cover?"
        assert q.hint == "e.g. programming, cooking"
        assert q.required is False
        assert q.answered is True

    def test_to_dict_structure(self) -> None:
        q = OnboardingQuestion(
            category="tone",
            question="What tone should replies use?",
            hint="e.g. casual, professional",
        )
        d = q.to_dict()
        assert d == {
            "category": "tone",
            "question": "What tone should replies use?",
            "hint": "e.g. casual, professional",
            "required": True,
            "answered": False,
        }

    def test_to_dict_keys(self) -> None:
        q = OnboardingQuestion()
        d = q.to_dict()
        expected_keys = {"category", "question", "hint", "required", "answered"}
        assert set(d.keys()) == expected_keys


class TestCommentAnalysis:
    """Tests for CommentAnalysis dataclass."""

    def test_defaults(self) -> None:
        ca = CommentAnalysis()
        assert ca.comment_id == ""
        assert ca.sentiment == CommentSentiment.NEUTRAL.value
        assert ca.category == ReplyCategory.FEEDBACK.value
        assert ca.topics == []
        assert ca.is_question is False
        assert ca.entities == []

    def test_custom_fields(self) -> None:
        ca = CommentAnalysis(
            comment_id="Ugx555",
            sentiment=CommentSentiment.POSITIVE.value,
            category=ReplyCategory.QUESTION.value,
            topics=["pricing", "features"],
            is_question=True,
            entities=["ProductX"],
        )
        assert ca.comment_id == "Ugx555"
        assert ca.sentiment == "positive"
        assert ca.category == "question"
        assert ca.topics == ["pricing", "features"]
        assert ca.is_question is True
        assert ca.entities == ["ProductX"]

    def test_to_dict_structure(self) -> None:
        ca = CommentAnalysis(
            comment_id="Ugx777",
            sentiment="negative",
            category="complaint",
            topics=["bugs"],
            is_question=False,
            entities=["AppY"],
        )
        d = ca.to_dict()
        assert d == {
            "comment_id": "Ugx777",
            "sentiment": "negative",
            "category": "complaint",
            "topics": ["bugs"],
            "is_question": False,
            "entities": ["AppY"],
        }

    def test_to_dict_keys(self) -> None:
        ca = CommentAnalysis()
        d = ca.to_dict()
        expected_keys = {
            "comment_id",
            "sentiment",
            "category",
            "topics",
            "is_question",
            "entities",
        }
        assert set(d.keys()) == expected_keys

    def test_mutable_defaults_are_independent(self) -> None:
        ca1 = CommentAnalysis()
        ca2 = CommentAnalysis()
        ca1.topics.append("new_topic")
        assert "new_topic" not in ca2.topics
        ca1.entities.append("entity")
        assert "entity" not in ca2.entities
