"""Tests for YouTubeIntelligenceSkill."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.youtube.intelligence import (
    INTENT_HANDLERS,
    YouTubeIntelligenceSkill,
    _serialise_report,
)
from zetherion_ai.skills.youtube.models import (
    CommentAnalysis,
    IntelligenceReportType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHANNEL_ID = uuid4()
TENANT_ID = uuid4()


def _make_storage() -> AsyncMock:
    """Create a mock YouTubeStorage with sensible defaults."""
    storage = AsyncMock()
    storage.initialize = AsyncMock()
    storage.get_channel = AsyncMock(return_value={"channel_name": "TestChannel", "id": CHANNEL_ID})
    storage.get_latest_report = AsyncMock(return_value=None)
    storage.get_report_history = AsyncMock(return_value=[])
    storage.get_unanalyzed_comments = AsyncMock(return_value=[])
    storage.get_videos = AsyncMock(return_value=[])
    storage.get_comments = AsyncMock(return_value=[])
    storage.get_latest_stats = AsyncMock(return_value=None)
    storage.save_report = AsyncMock(return_value={"id": uuid4(), "report": {}})
    storage.update_channel = AsyncMock()
    storage.update_comment_analysis = AsyncMock()
    storage.get_channels_due_for_analysis = AsyncMock(return_value=[])
    storage.save_assumption = AsyncMock(return_value={})
    storage.get_assumptions = AsyncMock(return_value=[])
    return storage


def _make_broker() -> AsyncMock:
    """Create a mock InferenceBroker."""
    broker = AsyncMock()
    broker.infer = AsyncMock(return_value=MagicMock(content='{"key": "value"}'))
    return broker


def _make_skill(
    storage: AsyncMock | None = None,
    broker: AsyncMock | None = None,
) -> YouTubeIntelligenceSkill:
    """Create a YouTubeIntelligenceSkill with mocked dependencies."""
    s = storage if storage is not None else _make_storage()
    b = broker if broker is not None else _make_broker()
    skill = YouTubeIntelligenceSkill(memory=None, storage=s, broker=b)
    return skill


async def _init_skill(
    storage: AsyncMock | None = None,
    broker: AsyncMock | None = None,
) -> YouTubeIntelligenceSkill:
    """Create and initialize a skill, returning it in READY state."""
    skill = _make_skill(storage=storage, broker=broker)
    result = await skill.initialize()
    assert result is True
    assert skill.status == SkillStatus.READY
    return skill


def _make_request(
    intent: str = "yt_analyze_channel",
    channel_id: UUID | None = None,
    context: dict | None = None,
) -> SkillRequest:
    """Create a SkillRequest with sensible defaults."""
    ctx = context or {}
    if channel_id is not None:
        ctx["channel_id"] = str(channel_id)
    return SkillRequest(
        user_id="test-user",
        intent=intent,
        message="test message",
        context=ctx,
    )


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for the metadata property."""

    def test_metadata_name(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert skill.metadata.name == "youtube_intelligence"

    def test_metadata_intents(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert set(skill.metadata.intents) == {
            "yt_analyze_channel",
            "yt_get_intelligence",
            "yt_intelligence_history",
        }

    def test_metadata_permissions(self) -> None:
        skill = YouTubeIntelligenceSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_PROFILE in perms
        assert Permission.WRITE_MEMORIES in perms

    def test_metadata_version(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert skill.metadata.version == "1.0.0"

    def test_metadata_description(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert len(skill.metadata.description) > 0

    def test_metadata_collections(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert "yt_comments" in skill.metadata.collections

    def test_intent_handlers_match_metadata_intents(self) -> None:
        """INTENT_HANDLERS keys should match metadata.intents exactly."""
        skill = YouTubeIntelligenceSkill()
        assert set(INTENT_HANDLERS.keys()) == set(skill.metadata.intents)


# ---------------------------------------------------------------------------
# 2. initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for the initialize() async method."""

    @pytest.mark.asyncio
    async def test_initialize_success(self) -> None:
        """initialize() should succeed with storage and broker provided."""
        storage = _make_storage()
        broker = _make_broker()
        skill = YouTubeIntelligenceSkill(storage=storage, broker=broker)

        result = await skill.initialize()

        assert result is True
        assert skill.status == SkillStatus.READY
        storage.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_no_storage(self) -> None:
        """initialize() should fail when no storage is provided."""
        skill = YouTubeIntelligenceSkill(storage=None, broker=_make_broker())

        result = await skill.initialize()

        assert result is False
        assert skill.status == SkillStatus.ERROR
        assert skill.error == "No storage provided"

    @pytest.mark.asyncio
    async def test_initialize_no_broker(self) -> None:
        """initialize() should fail when no broker is provided."""
        skill = YouTubeIntelligenceSkill(storage=_make_storage(), broker=None)

        result = await skill.initialize()

        assert result is False
        assert skill.status == SkillStatus.ERROR
        assert skill.error == "No inference broker"

    @pytest.mark.asyncio
    async def test_initialize_creates_assumption_tracker(self) -> None:
        """initialize() should create an AssumptionTracker instance."""
        skill = _make_skill()
        await skill.initialize()
        assert skill._assumptions is not None


# ---------------------------------------------------------------------------
# 3. handle() dispatch
# ---------------------------------------------------------------------------


class TestHandleDispatch:
    """Tests for the handle() method's intent dispatch logic."""

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self) -> None:
        """handle() should return error for unknown intent."""
        skill = await _init_skill()
        request = _make_request(intent="yt_unknown_intent", channel_id=CHANNEL_ID)

        response = await skill.handle(request)

        assert response.success is False
        assert "Unknown intent" in response.error
        assert "yt_unknown_intent" in response.error

    @pytest.mark.asyncio
    async def test_handle_dispatches_to_analyze(self) -> None:
        """handle() should route yt_analyze_channel to _handle_analyze."""
        skill = await _init_skill()
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        with patch.object(skill, "_handle_analyze", new_callable=AsyncMock) as mock_handler:
            mock_handler.return_value = SkillResponse(request_id=request.id, success=True)
            response = await skill.handle(request)

        mock_handler.assert_awaited_once_with(request)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_handle_dispatches_to_get_intelligence(self) -> None:
        """handle() should route yt_get_intelligence to _handle_get_intelligence."""
        skill = await _init_skill()
        request = _make_request(intent="yt_get_intelligence", channel_id=CHANNEL_ID)

        with patch.object(
            skill,
            "_handle_get_intelligence",
            new_callable=AsyncMock,
        ) as mock_handler:
            mock_handler.return_value = SkillResponse(request_id=request.id, success=True)
            response = await skill.handle(request)

        mock_handler.assert_awaited_once_with(request)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_handle_dispatches_to_intelligence_history(self) -> None:
        """handle() should route yt_intelligence_history to _handle_intelligence_history."""
        skill = await _init_skill()
        request = _make_request(intent="yt_intelligence_history", channel_id=CHANNEL_ID)

        with patch.object(
            skill,
            "_handle_intelligence_history",
            new_callable=AsyncMock,
        ) as mock_handler:
            mock_handler.return_value = SkillResponse(request_id=request.id, success=True)
            response = await skill.handle(request)

        mock_handler.assert_awaited_once_with(request)
        assert response.success is True

    @pytest.mark.asyncio
    async def test_handle_catches_handler_exception(self) -> None:
        """handle() should return error response when handler raises an exception."""
        skill = await _init_skill()
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        with patch.object(skill, "_handle_analyze", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = RuntimeError("Analysis exploded")
            response = await skill.handle(request)

        assert response.success is False
        assert "Intelligence error" in response.error
        assert "Analysis exploded" in response.error


# ---------------------------------------------------------------------------
# 4. _handle_analyze
# ---------------------------------------------------------------------------


class TestHandleAnalyze:
    """Tests for _handle_analyze intent handler."""

    @pytest.mark.asyncio
    async def test_analyze_requires_channel_id(self) -> None:
        """_handle_analyze should return error when channel_id is missing."""
        skill = await _init_skill()
        request = _make_request(intent="yt_analyze_channel")  # no channel_id

        response = await skill.handle(request)

        assert response.success is False
        assert "channel_id required" in response.error

    @pytest.mark.asyncio
    async def test_analyze_channel_not_found(self) -> None:
        """_handle_analyze should return error when channel doesn't exist."""
        storage = _make_storage()
        storage.get_channel.return_value = None
        skill = await _init_skill(storage=storage)
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        response = await skill.handle(request)

        assert response.success is False
        assert "Channel not found" in response.error

    @pytest.mark.asyncio
    async def test_analyze_triggers_full_pipeline(self) -> None:
        """_handle_analyze should call run_analysis and return the report."""
        skill = await _init_skill()
        fake_report = {"report_id": "abc", "overview": {}}
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_report
            response = await skill.handle(request)

        assert response.success is True
        assert response.message == "Intelligence report generated."
        assert response.data == fake_report

    @pytest.mark.asyncio
    async def test_analyze_no_new_data(self) -> None:
        """_handle_analyze should handle None from run_analysis (no new data)."""
        skill = await _init_skill()
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            response = await skill.handle(request)

        assert response.success is True
        assert "No new data" in response.message
        assert response.data == {}


# ---------------------------------------------------------------------------
# 5. _handle_get_intelligence
# ---------------------------------------------------------------------------


class TestHandleGetIntelligence:
    """Tests for _handle_get_intelligence intent handler."""

    @pytest.mark.asyncio
    async def test_get_intelligence_returns_latest_report(self) -> None:
        """Should return the latest report when available."""
        storage = _make_storage()
        report_row = {
            "id": uuid4(),
            "report": {"overview": {"channel_name": "Test"}},
            "generated_at": datetime(2026, 1, 15, 12, 0, 0),
        }
        storage.get_latest_report.return_value = report_row
        skill = await _init_skill(storage=storage)
        request = _make_request(intent="yt_get_intelligence", channel_id=CHANNEL_ID)

        response = await skill.handle(request)

        assert response.success is True
        assert response.message == "Latest intelligence report."
        # Should have serialised the datetime and UUID
        assert "2026-01-15" in response.data["generated_at"]

    @pytest.mark.asyncio
    async def test_get_intelligence_no_report(self) -> None:
        """Should return empty data when no report exists."""
        storage = _make_storage()
        storage.get_latest_report.return_value = None
        skill = await _init_skill(storage=storage)
        request = _make_request(intent="yt_get_intelligence", channel_id=CHANNEL_ID)

        response = await skill.handle(request)

        assert response.success is True
        assert "No intelligence report available" in response.message
        assert response.data == {}

    @pytest.mark.asyncio
    async def test_get_intelligence_requires_channel_id(self) -> None:
        """Should return error when channel_id is missing."""
        skill = await _init_skill()
        request = _make_request(intent="yt_get_intelligence")

        response = await skill.handle(request)

        assert response.success is False
        assert "channel_id required" in response.error


# ---------------------------------------------------------------------------
# 6. _handle_intelligence_history
# ---------------------------------------------------------------------------


class TestHandleIntelligenceHistory:
    """Tests for _handle_intelligence_history intent handler."""

    @pytest.mark.asyncio
    async def test_intelligence_history_returns_reports(self) -> None:
        """Should return serialised historical reports."""
        storage = _make_storage()
        rows = [
            {"id": uuid4(), "report": {"v": 1}, "generated_at": datetime(2026, 1, 10)},
            {"id": uuid4(), "report": {"v": 2}, "generated_at": datetime(2026, 1, 11)},
        ]
        storage.get_report_history.return_value = rows
        skill = await _init_skill(storage=storage)
        request = _make_request(
            intent="yt_intelligence_history",
            channel_id=CHANNEL_ID,
            context={"channel_id": str(CHANNEL_ID), "limit": 5},
        )

        response = await skill.handle(request)

        assert response.success is True
        assert "2 report(s) found" in response.message
        assert len(response.data["reports"]) == 2
        storage.get_report_history.assert_awaited_once_with(CHANNEL_ID, limit=5)

    @pytest.mark.asyncio
    async def test_intelligence_history_default_limit(self) -> None:
        """Should default to limit=10 when not specified."""
        storage = _make_storage()
        storage.get_report_history.return_value = []
        skill = await _init_skill(storage=storage)
        request = _make_request(intent="yt_intelligence_history", channel_id=CHANNEL_ID)

        await skill.handle(request)

        storage.get_report_history.assert_awaited_once_with(CHANNEL_ID, limit=10)

    @pytest.mark.asyncio
    async def test_intelligence_history_empty(self) -> None:
        """Should return empty list when no reports exist."""
        storage = _make_storage()
        storage.get_report_history.return_value = []
        skill = await _init_skill(storage=storage)
        request = _make_request(intent="yt_intelligence_history", channel_id=CHANNEL_ID)

        response = await skill.handle(request)

        assert response.success is True
        assert "0 report(s) found" in response.message
        assert response.data["reports"] == []


# ---------------------------------------------------------------------------
# 7. _classify_comments
# ---------------------------------------------------------------------------


class TestClassifyComments:
    """Tests for _classify_comments batch processing."""

    @pytest.mark.asyncio
    async def test_classify_no_unanalyzed(self) -> None:
        """Should return empty list when no unanalyzed comments exist."""
        storage = _make_storage()
        storage.get_unanalyzed_comments.return_value = []
        skill = await _init_skill(storage=storage)

        result = await skill._classify_comments(CHANNEL_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_classify_single_batch(self) -> None:
        """Should classify a batch of comments via broker and persist results."""
        storage = _make_storage()
        broker = _make_broker()
        comments = [
            {"id": uuid4(), "text": "Great video!"},
            {"id": uuid4(), "text": "This was terrible."},
        ]
        storage.get_unanalyzed_comments.return_value = comments

        broker_response = json.dumps(
            [
                {
                    "comment_id": str(comments[0]["id"]),
                    "sentiment": "positive",
                    "category": "feedback",
                    "topics": ["quality"],
                    "is_question": False,
                    "entities": [],
                },
                {
                    "comment_id": str(comments[1]["id"]),
                    "sentiment": "negative",
                    "category": "complaint",
                    "topics": ["content"],
                    "is_question": False,
                    "entities": [],
                },
            ]
        )
        broker.infer.return_value = MagicMock(content=broker_response)

        skill = await _init_skill(storage=storage, broker=broker)
        result = await skill._classify_comments(CHANNEL_ID)

        assert len(result) == 2
        assert result[0].sentiment == "positive"
        assert result[1].sentiment == "negative"
        assert result[1].category == "complaint"
        # Should persist each analysis
        assert storage.update_comment_analysis.await_count == 2

    @pytest.mark.asyncio
    async def test_classify_multiple_batches(self) -> None:
        """Should process comments in batches of _COMMENT_BATCH_SIZE."""
        storage = _make_storage()
        broker = _make_broker()

        # Create 25 comments (batch size is 20, so 2 batches)
        comments = [{"id": uuid4(), "text": f"Comment {i}"} for i in range(25)]
        storage.get_unanalyzed_comments.return_value = comments

        # Each broker call returns a simple valid array
        def make_response(batch):
            items = [
                {
                    "comment_id": str(c["id"]),
                    "sentiment": "neutral",
                    "category": "feedback",
                    "topics": [],
                }
                for c in batch
            ]
            return MagicMock(content=json.dumps(items))

        broker.infer.side_effect = [
            make_response(comments[:20]),
            make_response(comments[20:]),
        ]

        skill = await _init_skill(storage=storage, broker=broker)
        result = await skill._classify_comments(CHANNEL_ID)

        assert len(result) == 25
        assert broker.infer.await_count == 2

    @pytest.mark.asyncio
    async def test_classify_broker_failure_uses_fallback(self) -> None:
        """Should use fallback analyses when broker raises an exception."""
        storage = _make_storage()
        broker = _make_broker()
        comments = [{"id": uuid4(), "text": "Broken comment"}]
        storage.get_unanalyzed_comments.return_value = comments
        broker.infer.side_effect = RuntimeError("LLM down")

        skill = await _init_skill(storage=storage, broker=broker)
        result = await skill._classify_comments(CHANNEL_ID)

        assert len(result) == 1
        assert result[0].sentiment == "neutral"
        assert result[0].category == "feedback"

    @pytest.mark.asyncio
    async def test_classify_persist_failure_does_not_crash(self) -> None:
        """Should continue even if persisting a comment analysis fails."""
        storage = _make_storage()
        broker = _make_broker()
        comments = [{"id": uuid4(), "text": "Some comment"}]
        storage.get_unanalyzed_comments.return_value = comments
        analysis_item = {
            "comment_id": str(comments[0]["id"]),
            "sentiment": "positive",
            "category": "feedback",
            "topics": [],
        }
        broker.infer.return_value = MagicMock(content=json.dumps([analysis_item]))
        storage.update_comment_analysis.side_effect = RuntimeError("DB error")

        skill = await _init_skill(storage=storage, broker=broker)
        result = await skill._classify_comments(CHANNEL_ID)

        # Should still return the analysis even though persist failed
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 8. _compute_performance
# ---------------------------------------------------------------------------


class TestComputePerformance:
    """Tests for _compute_performance video ranking."""

    @pytest.mark.asyncio
    async def test_no_videos(self) -> None:
        """Should return empty list when there are no videos."""
        storage = _make_storage()
        storage.get_videos.return_value = []
        skill = await _init_skill(storage=storage)

        result = await skill._compute_performance(CHANNEL_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_engagement_ranking(self) -> None:
        """Should rank videos by engagement rate (likes + comments / views)."""
        storage = _make_storage()
        videos = [
            {
                "video_youtube_id": "vid1",
                "title": "Low Engagement",
                "stats": {"viewCount": 1000, "likeCount": 10, "commentCount": 5},
                "published_at": datetime(2026, 1, 1),
                "tags": ["tag1"],
            },
            {
                "video_youtube_id": "vid2",
                "title": "High Engagement",
                "stats": {"viewCount": 1000, "likeCount": 100, "commentCount": 50},
                "published_at": datetime(2026, 1, 2),
                "tags": ["tag2"],
            },
        ]
        storage.get_videos.return_value = videos
        skill = await _init_skill(storage=storage)

        result = await skill._compute_performance(CHANNEL_ID)

        assert len(result) == 2
        # vid2 should be first (higher engagement)
        assert result[0]["video_id"] == "vid2"
        assert result[0]["engagement_rate"] == round((100 + 50) / 1000, 5)
        assert result[1]["video_id"] == "vid1"

    @pytest.mark.asyncio
    async def test_engagement_handles_zero_views(self) -> None:
        """Should handle zero views without division errors (uses max(views, 1))."""
        storage = _make_storage()
        videos = [
            {
                "video_youtube_id": "vid_zero",
                "title": "Zero Views",
                "stats": {"viewCount": 0, "likeCount": 5, "commentCount": 2},
                "published_at": None,
                "tags": [],
            },
        ]
        storage.get_videos.return_value = videos
        skill = await _init_skill(storage=storage)

        result = await skill._compute_performance(CHANNEL_ID)

        assert len(result) == 1
        assert result[0]["engagement_rate"] == round((5 + 2) / 1, 5)
        assert result[0]["published_at"] is None

    @pytest.mark.asyncio
    async def test_engagement_handles_missing_stats(self) -> None:
        """Should handle videos with no stats dict."""
        storage = _make_storage()
        videos = [
            {
                "video_youtube_id": "vid_no_stats",
                "title": "No Stats",
                "stats": None,
                "published_at": None,
                "tags": [],
            },
        ]
        storage.get_videos.return_value = videos
        skill = await _init_skill(storage=storage)

        result = await skill._compute_performance(CHANNEL_ID)

        assert len(result) == 1
        assert result[0]["views"] == 0
        assert result[0]["likes"] == 0
        assert result[0]["comments"] == 0

    @pytest.mark.asyncio
    async def test_engagement_alternate_stat_keys(self) -> None:
        """Should handle alternate stat keys (views instead of viewCount)."""
        storage = _make_storage()
        videos = [
            {
                "video_youtube_id": "vid_alt",
                "title": "Alt Keys",
                "stats": {"views": 500, "likes": 25, "comments": 10},
                "published_at": datetime(2026, 1, 5),
                "tags": [],
            },
        ]
        storage.get_videos.return_value = videos
        skill = await _init_skill(storage=storage)

        result = await skill._compute_performance(CHANNEL_ID)

        assert result[0]["views"] == 500
        assert result[0]["likes"] == 25
        assert result[0]["comments"] == 10


# ---------------------------------------------------------------------------
# 9. _parse_batch_response
# ---------------------------------------------------------------------------


class TestParseBatchResponse:
    """Tests for _parse_batch_response JSON parsing."""

    def test_parse_valid_json_array(self) -> None:
        """Should parse a valid JSON array into CommentAnalysis objects."""
        batch = [{"id": "c1", "text": "hello"}]
        raw = json.dumps(
            [
                {
                    "comment_id": "c1",
                    "sentiment": "positive",
                    "category": "feedback",
                    "topics": ["AI"],
                    "is_question": False,
                    "entities": ["GPT"],
                }
            ]
        )

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 1
        assert isinstance(result[0], CommentAnalysis)
        assert result[0].comment_id == "c1"
        assert result[0].sentiment == "positive"
        assert result[0].topics == ["AI"]
        assert result[0].entities == ["GPT"]

    def test_parse_json_with_markdown_fences(self) -> None:
        """Should strip markdown ```json fences before parsing."""
        batch = [{"id": "c2", "text": "test"}]
        inner = json.dumps(
            [
                {
                    "comment_id": "c2",
                    "sentiment": "neutral",
                    "category": "question",
                    "topics": [],
                }
            ]
        )
        raw = f"```json\n{inner}\n```"

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 1
        assert result[0].comment_id == "c2"
        assert result[0].sentiment == "neutral"

    def test_parse_json_with_bare_backtick_fences(self) -> None:
        """Should strip bare ``` fences too."""
        batch = [{"id": "c3", "text": "test"}]
        inner = json.dumps(
            [
                {
                    "comment_id": "c3",
                    "sentiment": "positive",
                    "category": "feedback",
                    "topics": [],
                }
            ]
        )
        raw = f"```\n{inner}\n```"

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 1
        assert result[0].comment_id == "c3"

    def test_parse_invalid_json_uses_fallback(self) -> None:
        """Should fallback to neutral/feedback when JSON is invalid."""
        batch = [
            {"id": uuid4(), "text": "comment A"},
            {"id": uuid4(), "text": "comment B"},
        ]
        raw = "this is not JSON at all"

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 2
        assert all(r.sentiment == "neutral" for r in result)
        assert all(r.category == "feedback" for r in result)

    def test_parse_single_object_wrapped_in_list(self) -> None:
        """Should handle a single JSON object (not an array) by wrapping it."""
        batch = [{"id": "c4", "text": "only one"}]
        raw = json.dumps(
            {
                "comment_id": "c4",
                "sentiment": "negative",
                "category": "complaint",
                "topics": ["bugs"],
            }
        )

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 1
        assert result[0].sentiment == "negative"
        assert result[0].category == "complaint"

    def test_parse_truncates_topics_to_three(self) -> None:
        """Should keep only the first 3 topics."""
        batch = [{"id": "c5", "text": "verbose"}]
        raw = json.dumps(
            [
                {
                    "comment_id": "c5",
                    "sentiment": "neutral",
                    "category": "feedback",
                    "topics": ["a", "b", "c", "d", "e"],
                }
            ]
        )

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result[0].topics) == 3
        assert result[0].topics == ["a", "b", "c"]

    def test_parse_defaults_for_missing_fields(self) -> None:
        """Should provide sensible defaults for missing fields."""
        batch = [{"id": "c6", "text": "sparse"}]
        raw = json.dumps([{}])

        result = YouTubeIntelligenceSkill._parse_batch_response(raw, batch)

        assert len(result) == 1
        assert result[0].comment_id == ""
        assert result[0].sentiment == "neutral"
        assert result[0].category == "feedback"
        assert result[0].topics == []
        assert result[0].is_question is False
        assert result[0].entities == []


# ---------------------------------------------------------------------------
# 10. _fallback_report
# ---------------------------------------------------------------------------


class TestFallbackReport:
    """Tests for _fallback_report minimal report structure."""

    def test_fallback_report_structure(self) -> None:
        """Should build a complete report with all expected sections."""
        comment_summary = {
            "total": 100,
            "sentiments": {"positive": 60, "neutral": 30, "negative": 10},
            "top_topics": [("python", 20), ("AI", 15)],
            "questions": ["How do I use this?", "Is it free?"],
            "complaints": ["Too short", "Bad audio"],
        }
        video_performance = [
            {"title": "Vid 1", "engagement_rate": 0.05},
            {"title": "Vid 2", "engagement_rate": 0.03},
            {"title": "Vid 3", "engagement_rate": 0.02},
            {"title": "Vid 4", "engagement_rate": 0.01},
            {"title": "Vid 5", "engagement_rate": 0.005},
            {"title": "Vid 6", "engagement_rate": 0.001},
        ]
        channel_stats = {"subscriberCount": 5000, "viewCount": 100000, "videoCount": 50}

        report = YouTubeIntelligenceSkill._fallback_report(
            "TestChannel",
            comment_summary,
            video_performance,
            channel_stats,
        )

        # Top-level keys
        assert "overview" in report
        assert "content_performance" in report
        assert "audience" in report
        assert "recommendations" in report
        assert report["_fallback"] is True

        # Overview
        assert report["overview"]["channel_name"] == "TestChannel"
        assert report["overview"]["subscriber_count"] == 5000
        assert report["overview"]["growth_trend"] == "stable"

        # Content performance
        assert len(report["content_performance"]["top_performing"]) == 5
        assert len(report["content_performance"]["underperforming"]) == 3

        # Audience
        assert report["audience"]["sentiment"]["positive"] == 0.6
        assert report["audience"]["sentiment"]["neutral"] == 0.3
        assert report["audience"]["sentiment"]["negative"] == 0.1

        # Questions and complaints truncated
        assert len(report["audience"]["unanswered_questions"]) == 2
        assert len(report["audience"]["complaints"]) == 2

    def test_fallback_report_zero_comments(self) -> None:
        """Should handle zero total comments without division error."""
        comment_summary = {
            "total": 0,
            "sentiments": {},
            "top_topics": [],
            "questions": [],
            "complaints": [],
        }

        report = YouTubeIntelligenceSkill._fallback_report(
            "EmptyChannel",
            comment_summary,
            [],
            {},
        )

        assert report["audience"]["sentiment"]["positive"] == 0.0
        assert report["audience"]["sentiment"]["neutral"] == 0.0
        assert report["audience"]["sentiment"]["negative"] == 0.0
        assert report["content_performance"]["top_performing"] == []
        assert report["content_performance"]["underperforming"] == []

    def test_fallback_report_few_videos(self) -> None:
        """Should not include underperforming when <= 5 videos."""
        videos = [{"title": f"Vid {i}", "engagement_rate": 0.01} for i in range(3)]
        comment_summary = {
            "total": 10,
            "sentiments": {},
            "top_topics": [],
            "questions": [],
            "complaints": [],
        }

        report = YouTubeIntelligenceSkill._fallback_report(
            "SmallChannel",
            comment_summary,
            videos,
            {},
        )

        assert len(report["content_performance"]["top_performing"]) == 3
        assert report["content_performance"]["underperforming"] == []


# ---------------------------------------------------------------------------
# 11. on_heartbeat
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """Tests for the on_heartbeat() method."""

    @pytest.mark.asyncio
    async def test_heartbeat_no_storage(self) -> None:
        """Should return empty list when storage is None."""
        skill = YouTubeIntelligenceSkill(storage=None, broker=None)

        actions = await skill.on_heartbeat(["user1"])

        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_no_channels_due(self) -> None:
        """Should return empty list when no channels are due for analysis."""
        storage = _make_storage()
        storage.get_channels_due_for_analysis.return_value = []
        skill = await _init_skill(storage=storage)

        actions = await skill.on_heartbeat(["user1"])

        assert actions == []
        storage.get_channels_due_for_analysis.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_heartbeat_triggers_analysis(self) -> None:
        """Should trigger analysis for each due channel and return actions."""
        storage = _make_storage()
        channel_data = {
            "id": CHANNEL_ID,
            "channel_name": "DueChannel",
            "tenant_id": TENANT_ID,
        }
        storage.get_channels_due_for_analysis.return_value = [channel_data]
        skill = await _init_skill(storage=storage)

        fake_report = {"report_id": "rpt-123"}
        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_report
            actions = await skill.on_heartbeat(["user1"])

        assert len(actions) == 1
        action = actions[0]
        assert isinstance(action, HeartbeatAction)
        assert action.skill_name == "youtube_intelligence"
        assert action.action_type == "intelligence_report_generated"
        assert action.user_id == str(TENANT_ID)
        assert action.data["channel_id"] == str(CHANNEL_ID)
        assert action.data["report_id"] == "rpt-123"
        assert action.priority == 5

    @pytest.mark.asyncio
    async def test_heartbeat_skips_channel_with_no_report(self) -> None:
        """Should not create action when run_analysis returns None (no data)."""
        storage = _make_storage()
        channel_data = {"id": CHANNEL_ID, "channel_name": "NoData", "tenant_id": TENANT_ID}
        storage.get_channels_due_for_analysis.return_value = [channel_data]
        skill = await _init_skill(storage=storage)

        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            actions = await skill.on_heartbeat(["user1"])

        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_handles_analysis_failure(self) -> None:
        """Should not crash when run_analysis raises an exception."""
        storage = _make_storage()
        channel_data = {"id": CHANNEL_ID, "channel_name": "Broken", "tenant_id": TENANT_ID}
        storage.get_channels_due_for_analysis.return_value = [channel_data]
        skill = await _init_skill(storage=storage)

        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("Pipeline failed")
            actions = await skill.on_heartbeat(["user1"])

        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_handles_due_query_failure(self) -> None:
        """Should not crash when get_channels_due_for_analysis raises."""
        storage = _make_storage()
        storage.get_channels_due_for_analysis.side_effect = RuntimeError("DB exploded")
        skill = await _init_skill(storage=storage)

        actions = await skill.on_heartbeat(["user1"])

        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_channels(self) -> None:
        """Should process multiple channels and return actions for each successful one."""
        storage = _make_storage()
        ch1_id, ch2_id = uuid4(), uuid4()
        channels = [
            {"id": ch1_id, "channel_name": "Ch1", "tenant_id": uuid4()},
            {"id": ch2_id, "channel_name": "Ch2", "tenant_id": uuid4()},
        ]
        storage.get_channels_due_for_analysis.return_value = channels
        skill = await _init_skill(storage=storage)

        with patch.object(skill, "run_analysis", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [
                {"report_id": "rpt-1"},
                {"report_id": "rpt-2"},
            ]
            actions = await skill.on_heartbeat(["user1"])

        assert len(actions) == 2
        assert actions[0].data["channel_name"] == "Ch1"
        assert actions[1].data["channel_name"] == "Ch2"


# ---------------------------------------------------------------------------
# 12. run_analysis pipeline
# ---------------------------------------------------------------------------


class TestRunAnalysis:
    """Tests for the full run_analysis pipeline."""

    @pytest.mark.asyncio
    async def test_run_analysis_no_data(self) -> None:
        """Should return None when there are no comments and no videos."""
        storage = _make_storage()
        storage.get_comments.return_value = []
        storage.get_videos.return_value = []
        storage.get_unanalyzed_comments.return_value = []
        skill = await _init_skill(storage=storage)

        result = await skill.run_analysis(CHANNEL_ID, {"channel_name": "Empty"})

        assert result is None

    @pytest.mark.asyncio
    async def test_run_analysis_full_pipeline(self) -> None:
        """Should execute all pipeline steps and persist the report."""
        storage = _make_storage()
        broker = _make_broker()

        # Set up some comments so total > 0
        storage.get_comments.return_value = [
            {"sentiment": "positive", "category": "feedback", "topics": ["AI"], "text": "Great!"},
        ]
        storage.get_unanalyzed_comments.return_value = []
        storage.get_videos.return_value = []
        storage.get_latest_stats.return_value = {"snapshot": {"subscriberCount": 1000}}
        saved_report = {
            "id": uuid4(),
            "report": {"overview": {}},
            "generated_at": datetime.utcnow(),
        }
        storage.save_report.return_value = saved_report

        # Broker returns valid synthesis JSON
        synthesis_json = json.dumps(
            {
                "overview": {"channel_name": "Test"},
                "content_performance": {"top_performing": []},
                "audience": {"sentiment": {"positive": 0.8, "neutral": 0.1, "negative": 0.1}},
                "recommendations": [],
            }
        )
        broker.infer.return_value = MagicMock(content=synthesis_json)

        skill = await _init_skill(storage=storage, broker=broker)

        result = await skill.run_analysis(CHANNEL_ID, {"channel_name": "TestChannel"})

        assert result is not None
        storage.save_report.assert_awaited_once()
        storage.update_channel.assert_awaited_once()
        # Report type should be FULL
        call_args = storage.save_report.call_args
        assert call_args.kwargs["report_type"] == IntelligenceReportType.FULL.value


# ---------------------------------------------------------------------------
# 13. _parse_synthesis
# ---------------------------------------------------------------------------


class TestParseSynthesis:
    """Tests for _parse_synthesis static method."""

    def test_parse_valid_json(self) -> None:
        report = YouTubeIntelligenceSkill._parse_synthesis('{"overview": {"name": "ch"}}')
        assert report == {"overview": {"name": "ch"}}

    def test_parse_json_with_fences(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        report = YouTubeIntelligenceSkill._parse_synthesis(raw)
        assert report == {"key": "value"}

    def test_parse_invalid_json_returns_raw(self) -> None:
        raw = "This is not JSON"
        report = YouTubeIntelligenceSkill._parse_synthesis(raw)
        assert report == {"raw_response": raw}


# ---------------------------------------------------------------------------
# 14. get_system_prompt_fragment
# ---------------------------------------------------------------------------


class TestGetSystemPromptFragment:
    """Tests for get_system_prompt_fragment method."""

    @pytest.mark.asyncio
    async def test_returns_fragment_when_ready(self) -> None:
        skill = await _init_skill()

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is not None
        assert "YouTube Intelligence" in fragment
        assert "Ready" in fragment

    def test_returns_none_when_not_ready(self) -> None:
        skill = YouTubeIntelligenceSkill()
        assert skill.status == SkillStatus.UNINITIALIZED

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is None


# ---------------------------------------------------------------------------
# 15. _resolve_channel_id
# ---------------------------------------------------------------------------


class TestResolveChannelId:
    """Tests for _resolve_channel_id static helper."""

    def test_valid_uuid(self) -> None:
        cid = uuid4()
        request = _make_request(channel_id=cid)
        result = YouTubeIntelligenceSkill._resolve_channel_id(request)
        assert result == cid

    def test_missing_channel_id(self) -> None:
        request = _make_request()
        result = YouTubeIntelligenceSkill._resolve_channel_id(request)
        assert result is None

    def test_invalid_uuid_string(self) -> None:
        request = _make_request(context={"channel_id": "not-a-uuid"})
        result = YouTubeIntelligenceSkill._resolve_channel_id(request)
        assert result is None


# ---------------------------------------------------------------------------
# 16. _serialise_report
# ---------------------------------------------------------------------------


class TestSerialiseReport:
    """Tests for the module-level _serialise_report helper."""

    def test_serialises_datetime(self) -> None:
        dt = datetime(2026, 1, 15, 10, 30, 0)
        row = {"generated_at": dt, "report": {"key": "value"}}

        result = _serialise_report(row)

        assert result["generated_at"] == "2026-01-15T10:30:00"
        assert result["report"] == {"key": "value"}

    def test_serialises_uuid(self) -> None:
        uid = uuid4()
        row = {"id": uid, "name": "test"}

        result = _serialise_report(row)

        assert result["id"] == str(uid)
        assert result["name"] == "test"

    def test_passthrough_plain_values(self) -> None:
        row = {"count": 42, "flag": True, "label": "hello"}

        result = _serialise_report(row)

        assert result == row


# ---------------------------------------------------------------------------
# 17. _fallback_analyses
# ---------------------------------------------------------------------------


class TestFallbackAnalyses:
    """Tests for _fallback_analyses static method."""

    def test_fallback_for_batch(self) -> None:
        batch = [
            {"id": uuid4(), "text": "A"},
            {"id": uuid4(), "text": "B"},
        ]

        result = YouTubeIntelligenceSkill._fallback_analyses(batch)

        assert len(result) == 2
        for i, analysis in enumerate(result):
            assert isinstance(analysis, CommentAnalysis)
            assert analysis.comment_id == str(batch[i]["id"])
            assert analysis.sentiment == "neutral"
            assert analysis.category == "feedback"
            assert analysis.topics == []


# ---------------------------------------------------------------------------
# 18. handle() — handler not implemented (line 132)
# ---------------------------------------------------------------------------


class TestHandlerNotImplemented:
    """Tests for the 'handler not implemented' branch in handle()."""

    @pytest.mark.asyncio
    async def test_handle_handler_not_implemented(self) -> None:
        """handle() should return error when getattr returns None for a valid intent."""
        skill = await _init_skill()
        request = _make_request(intent="yt_analyze_channel", channel_id=CHANNEL_ID)

        # Patch the handler method to None so getattr returns None
        with patch.object(YouTubeIntelligenceSkill, "_handle_analyze", new=None):
            response = await skill.handle(request)

        assert response.success is False
        assert "Handler not implemented" in response.error


# ---------------------------------------------------------------------------
# 19. _handle_intelligence_history — channel_id required (line 200)
# ---------------------------------------------------------------------------


class TestIntelligenceHistoryChannelIdRequired:
    """Test that _handle_intelligence_history returns error when channel_id missing."""

    @pytest.mark.asyncio
    async def test_intelligence_history_requires_channel_id(self) -> None:
        """Should return error when channel_id is missing."""
        skill = await _init_skill()
        request = _make_request(intent="yt_intelligence_history")

        response = await skill.handle(request)

        assert response.success is False
        assert "channel_id required" in response.error


# ---------------------------------------------------------------------------
# 20. _aggregate_comments — populated branches (lines 440-449)
# ---------------------------------------------------------------------------


class TestAggregateCommentsPopulated:
    """Tests for _aggregate_comments when comments have sentiment, category, topics."""

    @pytest.mark.asyncio
    async def test_aggregate_with_populated_comments(self) -> None:
        """Should correctly count sentiments, categories, topics, questions, complaints."""
        storage = _make_storage()
        comments = [
            {
                "sentiment": "positive",
                "category": "feedback",
                "topics": ["AI", "python"],
                "text": "Great video about AI!",
            },
            {
                "sentiment": "negative",
                "category": "complaint",
                "topics": ["audio"],
                "text": "The audio quality is terrible and needs fixing",
            },
            {
                "sentiment": "neutral",
                "category": "question",
                "topics": ["tutorial"],
                "text": "How do I install this library?",
            },
            {
                "sentiment": "positive",
                "category": "question",
                "topics": ["setup"],
                "text": "Where can I download the code?",
            },
        ]
        storage.get_comments.return_value = comments
        skill = await _init_skill(storage=storage)

        result = await skill._aggregate_comments(CHANNEL_ID)

        assert result["total"] == 4
        assert result["sentiments"]["positive"] == 2
        assert result["sentiments"]["negative"] == 1
        assert result["sentiments"]["neutral"] == 1
        assert result["categories"]["feedback"] == 1
        assert result["categories"]["complaint"] == 1
        assert result["categories"]["question"] == 2
        # Topics counted
        topics_dict = dict(result["top_topics"])
        assert "AI" in topics_dict
        assert "python" in topics_dict
        assert "audio" in topics_dict
        assert "tutorial" in topics_dict
        # Questions extracted
        assert len(result["questions"]) == 2
        assert any("install" in q for q in result["questions"])
        # Complaints extracted
        assert len(result["complaints"]) == 1
        assert any("audio" in c for c in result["complaints"])

    @pytest.mark.asyncio
    async def test_aggregate_skips_empty_fields(self) -> None:
        """Should skip comments with empty/None sentiment, category, topics."""
        storage = _make_storage()
        comments = [
            {
                "sentiment": None,
                "category": None,
                "topics": None,
                "text": "Just a comment",
            },
            {
                "sentiment": "",
                "category": "",
                "topics": [],
                "text": "Another comment",
            },
        ]
        storage.get_comments.return_value = comments
        skill = await _init_skill(storage=storage)

        result = await skill._aggregate_comments(CHANNEL_ID)

        assert result["total"] == 2
        assert result["sentiments"] == {}
        assert result["categories"] == {}
        assert result["top_topics"] == []
        assert result["questions"] == []
        assert result["complaints"] == []


# ---------------------------------------------------------------------------
# 21. _synthesise — exception fallback (lines 511-514)
# ---------------------------------------------------------------------------


class TestSynthesiseExceptionFallback:
    """Tests for _synthesise when broker raises an exception."""

    @pytest.mark.asyncio
    async def test_synthesise_exception_returns_fallback_report(self) -> None:
        """Should return a fallback report when broker.infer raises."""
        storage = _make_storage()
        broker = _make_broker()
        broker.infer.side_effect = RuntimeError("LLM service unavailable")
        skill = await _init_skill(storage=storage, broker=broker)

        result = await skill._synthesise(
            channel_name="TestChannel",
            comment_summary={
                "total": 10,
                "sentiments": {"positive": 7, "neutral": 2, "negative": 1},
                "top_topics": [("python", 5)],
                "questions": ["How to start?"],
                "complaints": ["Too short"],
            },
            video_performance=[
                {"title": "Vid 1", "engagement_rate": 0.05},
            ],
            channel_stats={"subscriberCount": 1000},
            assumptions=[],
        )

        assert result["_fallback"] is True
        assert "overview" in result
        assert result["overview"]["channel_name"] == "TestChannel"


# ---------------------------------------------------------------------------
# 22. _infer_assumptions — top_performing and top_requests branches
#     (lines 595-596, 621-623)
# ---------------------------------------------------------------------------


class TestInferAssumptionsBranches:
    """Tests for _infer_assumptions with top_performing and top_requests."""

    @pytest.mark.asyncio
    async def test_infer_top_performing_content(self) -> None:
        """Should infer performance assumption when report has top_performing."""
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        report = {
            "content_performance": {
                "top_performing": [
                    {"title": "Best Video", "engagement_rate": 0.15},
                    {"title": "Second Best", "engagement_rate": 0.10},
                ],
            },
            "audience": {},
        }

        await skill._infer_assumptions(CHANNEL_ID, report)

        calls = storage.save_assumption.call_args_list
        statements = [c.args[0]["statement"] for c in calls]
        assert any("Best Video" in s for s in statements)
        assert any(c.args[0]["category"] == "performance" for c in calls)

    @pytest.mark.asyncio
    async def test_infer_top_requests_with_topic(self) -> None:
        """Should infer topic assumption when report has top_requests with a topic."""
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        report = {
            "content_performance": {},
            "audience": {
                "sentiment": {"positive": 0.7, "neutral": 0.2, "negative": 0.1},
                "top_requests": [
                    {"topic": "Python tutorials", "mentions": 15},
                    {"topic": "AI projects", "mentions": 8},
                ],
            },
        }

        await skill._infer_assumptions(CHANNEL_ID, report)

        calls = storage.save_assumption.call_args_list
        statements = [c.args[0]["statement"] for c in calls]
        categories = [c.args[0]["category"] for c in calls]
        # Should have audience sentiment assumption + topic assumption
        assert "audience" in categories
        assert "topic" in categories
        assert any("Python tutorials" in s for s in statements)

    @pytest.mark.asyncio
    async def test_infer_top_requests_empty_topic_skipped(self) -> None:
        """Should NOT infer topic assumption when top_requests topic is empty."""
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        report = {
            "content_performance": {},
            "audience": {
                "sentiment": {"positive": 0.5, "neutral": 0.5},
                "top_requests": [
                    {"topic": "", "mentions": 5},  # empty topic
                ],
            },
        }

        await skill._infer_assumptions(CHANNEL_ID, report)

        calls = storage.save_assumption.call_args_list
        categories = [c.args[0]["category"] for c in calls]
        # Should have audience assumption but NOT topic
        assert "audience" in categories
        assert "topic" not in categories
