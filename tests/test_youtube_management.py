"""Tests for YouTube Management Skill.

Covers metadata, lifecycle, intent dispatch, reply generation pipeline,
management state building, onboarding configuration, reply review actions,
tag recommendations, channel health audit, and heartbeat behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillRequest,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.youtube.management import (
    INTENT_HANDLERS,
    YouTubeManagementSkill,
    _parse_json,
    _resolve_channel_id,
    _serialise,
)
from zetherion_ai.skills.youtube.models import (
    ManagementState,
    ReplyCategory,
    ReplyStatus,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeInferenceResult:
    """Minimal stand-in for InferenceResult used in mock returns."""

    content: str
    provider: Provider = Provider.GEMINI
    task_type: TaskType = TaskType.SUMMARIZATION
    model: str = "gemini-2.0-flash"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0


def _make_channel(
    channel_id: UUID | None = None,
    *,
    onboarding_complete: bool = True,
    trust_level: int = TrustLevel.SUPERVISED.value,
    trust_stats: dict[str, int] | None = None,
    config: dict[str, Any] | None = None,
    channel_name: str = "Test Channel",
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    """Build a synthetic channel row."""
    return {
        "id": channel_id or uuid4(),
        "tenant_id": tenant_id or uuid4(),
        "channel_youtube_id": "UC_test",
        "channel_name": channel_name,
        "config": config or {},
        "onboarding_complete": onboarding_complete,
        "trust_level": trust_level,
        "trust_stats": trust_stats or {"total": 0, "approved": 0, "rejected": 0},
        "updated_at": datetime(2026, 2, 1, 12, 0, 0),
    }


def _make_comment(
    *,
    comment_youtube_id: str = "yt_cmt_1",
    category: str = ReplyCategory.FEEDBACK.value,
    sentiment: str = "positive",
    text: str = "Great video!",
    video_id: UUID | None = None,
    author: str = "TestUser",
) -> dict[str, Any]:
    return {
        "comment_youtube_id": comment_youtube_id,
        "category": category,
        "sentiment": sentiment,
        "text": text,
        "video_id": video_id,
        "video_youtube_id": "vid_yt_1",
        "author": author,
    }


def _make_request(
    intent: str = "",
    context: dict[str, Any] | None = None,
) -> SkillRequest:
    return SkillRequest(
        intent=intent,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage() -> AsyncMock:
    """Return a fully-stubbed AsyncMock for YouTubeStorage."""
    s = AsyncMock()
    # Provide sensible defaults so callers only override what they need.
    s.get_channel = AsyncMock(return_value=None)
    s.get_channels_due_for_analysis = AsyncMock(return_value=[])
    s.get_comments = AsyncMock(return_value=[])
    s.get_reply_drafts = AsyncMock(return_value=[])
    s.get_videos = AsyncMock(return_value=[])
    s.get_documents = AsyncMock(return_value=[])
    s.get_latest_stats = AsyncMock(return_value=None)
    s.get_latest_strategy = AsyncMock(return_value=None)
    s.get_tag_recommendations = AsyncMock(return_value=[])
    s.get_video_by_youtube_id = AsyncMock(return_value=None)
    s.get_assumptions = AsyncMock(return_value=[])
    s.save_reply_draft = AsyncMock(side_effect=lambda d: d)
    s.save_tag_recommendation = AsyncMock(side_effect=lambda d: d)
    s.save_assumption = AsyncMock(side_effect=lambda d: d)
    s.update_channel = AsyncMock()
    s.update_reply_status = AsyncMock(side_effect=lambda rid, status: {"id": rid, "status": status})
    s.count_replies_today = AsyncMock(return_value=0)
    return s


@pytest.fixture()
def broker() -> AsyncMock:
    """Return a stubbed InferenceBroker."""
    b = AsyncMock()
    b.infer = AsyncMock(
        return_value=FakeInferenceResult(content="Mock reply text")
    )
    return b


@pytest.fixture()
def skill(storage: AsyncMock, broker: AsyncMock) -> YouTubeManagementSkill:
    """Return an initialized YouTubeManagementSkill with mocked deps."""
    s = YouTubeManagementSkill(memory=None, storage=storage, broker=broker)
    return s


@pytest.fixture()
def initialized_skill(
    storage: AsyncMock, broker: AsyncMock
) -> YouTubeManagementSkill:
    """Return a skill that has already been initialized (status=READY)."""
    s = YouTubeManagementSkill(memory=None, storage=storage, broker=broker)
    # Manually set internal state as if initialize() succeeded.
    s._assumptions = MagicMock()
    s._assumptions.add_confirmed = AsyncMock(return_value={})
    s._assumptions.get_missing_categories = AsyncMock(return_value=[])
    s._assumptions.get_high_confidence = AsyncMock(return_value=[])
    s._set_status(SkillStatus.READY)
    return s


# ===================================================================
# Metadata
# ===================================================================


class TestMetadata:
    """Verify skill metadata is declared correctly."""

    def test_name(self, skill: YouTubeManagementSkill) -> None:
        assert skill.metadata.name == "youtube_management"

    def test_version(self, skill: YouTubeManagementSkill) -> None:
        assert skill.metadata.version == "1.0.0"

    def test_description_non_empty(self, skill: YouTubeManagementSkill) -> None:
        assert len(skill.metadata.description) > 0

    def test_permissions(self, skill: YouTubeManagementSkill) -> None:
        perms = skill.metadata.permissions
        assert Permission.READ_PROFILE in perms
        assert Permission.WRITE_MEMORIES in perms
        assert Permission.SEND_MESSAGES in perms

    def test_collections(self, skill: YouTubeManagementSkill) -> None:
        assert "yt_comments" in skill.metadata.collections

    def test_intents_match_handler_map(self, skill: YouTubeManagementSkill) -> None:
        assert set(skill.metadata.intents) == set(INTENT_HANDLERS.keys())


# ===================================================================
# initialize()
# ===================================================================


class TestInitialize:
    """Tests for the initialize lifecycle method."""

    @pytest.mark.asyncio
    async def test_success(self, storage: AsyncMock, broker: AsyncMock) -> None:
        skill = YouTubeManagementSkill(memory=None, storage=storage, broker=broker)
        result = await skill.initialize()
        assert result is True
        assert skill.status == SkillStatus.READY
        assert skill._assumptions is not None

    @pytest.mark.asyncio
    async def test_fails_without_storage(self, broker: AsyncMock) -> None:
        skill = YouTubeManagementSkill(memory=None, storage=None, broker=broker)
        result = await skill.initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR

    @pytest.mark.asyncio
    async def test_fails_without_broker(self, storage: AsyncMock) -> None:
        skill = YouTubeManagementSkill(memory=None, storage=storage, broker=None)
        result = await skill.initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR


# ===================================================================
# handle() dispatch
# ===================================================================


class TestHandleDispatch:
    """Tests for the top-level handle() intent dispatcher."""

    @pytest.mark.asyncio
    async def test_unknown_intent(self, initialized_skill: YouTubeManagementSkill) -> None:
        req = _make_request(intent="totally_unknown")
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Unknown intent" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_known_intent_dispatches(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """yt_get_management_state should reach _handle_get_state."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel
        storage.get_reply_drafts.return_value = []
        storage.count_replies_today.return_value = 5

        req = _make_request(
            intent="yt_get_management_state",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "Management state" in resp.message

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """If a handler raises, handle() should return an error response."""
        storage.get_channel.side_effect = RuntimeError("DB down")
        channel_id = uuid4()
        req = _make_request(
            intent="yt_get_management_state",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Management error" in (resp.error or "")


# ===================================================================
# _handle_get_state / get_management_state
# ===================================================================


class TestGetManagementState:
    """Tests for building the ManagementState."""

    @pytest.mark.asyncio
    async def test_returns_none_when_channel_missing(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        storage.get_channel.return_value = None
        state = await initialized_skill.get_management_state(uuid4())
        assert state is None

    @pytest.mark.asyncio
    async def test_builds_state_correctly(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_level=TrustLevel.GUIDED.value,
            trust_stats={"total": 60, "approved": 58, "rejected": 2},
            config={"auto_reply": True},
        )
        storage.get_channel.return_value = channel
        storage.get_reply_drafts.return_value = [{"id": uuid4()}, {"id": uuid4()}]
        storage.count_replies_today.return_value = 7

        state = await initialized_skill.get_management_state(channel_id)

        assert state is not None
        assert isinstance(state, ManagementState)
        assert state.channel_id == channel_id
        assert state.trust_level == TrustLevel.GUIDED.value
        assert state.trust_label == "GUIDED"
        assert state.onboarding_complete is True
        assert state.auto_reply_enabled is True
        assert state.pending_count == 2
        assert state.posted_today == 7
        # GUIDED auto-approves thank_you and faq
        assert "thank_you" in state.auto_categories
        assert "faq" in state.auto_categories

    @pytest.mark.asyncio
    async def test_state_to_dict(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel
        storage.get_reply_drafts.return_value = []
        storage.count_replies_today.return_value = 0

        state = await initialized_skill.get_management_state(channel_id)
        assert state is not None
        d = state.to_dict()
        assert "trust" in d
        assert "auto_reply" in d
        assert str(channel_id) == d["channel_id"]


# ===================================================================
# _handle_configure
# ===================================================================


class TestHandleConfigure:
    """Tests for onboarding configuration flow."""

    @pytest.mark.asyncio
    async def test_stores_answers_as_assumptions(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, onboarding_complete=False)
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_configure_management",
            context={
                "channel_id": str(channel_id),
                "answers": {"topics": "tech reviews", "tone": "casual"},
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True

        # Two answers -> two add_confirmed calls
        assert initialized_skill._assumptions.add_confirmed.call_count == 2

    @pytest.mark.asyncio
    async def test_marks_onboarding_complete_when_no_missing(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, onboarding_complete=False)
        storage.get_channel.return_value = channel
        initialized_skill._assumptions.get_missing_categories.return_value = []

        req = _make_request(
            intent="yt_configure_management",
            context={
                "channel_id": str(channel_id),
                "answers": {"topics": "cooking"},
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert resp.data["onboarding_complete"] is True
        storage.update_channel.assert_any_await(channel_id, onboarding_complete=True)

    @pytest.mark.asyncio
    async def test_returns_missing_categories(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, onboarding_complete=False)
        storage.get_channel.return_value = channel
        missing = ["audience", "schedule"]
        initialized_skill._assumptions.get_missing_categories.return_value = missing

        # Broker returns follow-up questions as JSON
        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps([
                {"category": "audience", "question": "Who watches your videos?"},
                {"category": "schedule", "question": "How often do you upload?"},
            ])
        )

        req = _make_request(
            intent="yt_configure_management",
            context={
                "channel_id": str(channel_id),
                "answers": {"topics": "gaming"},
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert resp.data["onboarding_complete"] is False
        assert "audience" in resp.data["missing_categories"]

    @pytest.mark.asyncio
    async def test_returns_initial_questions_when_no_answers(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, onboarding_complete=False)
        storage.get_channel.return_value = channel
        initialized_skill._assumptions.get_missing_categories.return_value = []

        req = _make_request(
            intent="yt_configure_management",
            context={"channel_id": str(channel_id), "answers": {}},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert len(resp.data["initial_questions"]) == 5  # 5 default questions

    @pytest.mark.asyncio
    async def test_applies_config_updates(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, config={"auto_reply": False})
        storage.get_channel.return_value = channel
        initialized_skill._assumptions.get_missing_categories.return_value = []

        req = _make_request(
            intent="yt_configure_management",
            context={
                "channel_id": str(channel_id),
                "answers": {},
                "config": {"auto_reply": True},
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        storage.update_channel.assert_any_await(
            channel_id, config={"auto_reply": True}
        )

    @pytest.mark.asyncio
    async def test_missing_channel_returns_error(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        storage.get_channel.return_value = None
        req = _make_request(
            intent="yt_configure_management",
            context={"channel_id": str(uuid4()), "answers": {}},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False


# ===================================================================
# _handle_review_replies
# ===================================================================


class TestHandleReviewReplies:
    """Tests for approve/reject/posted actions and trust model updates."""

    @pytest.mark.asyncio
    async def test_list_pending_when_no_action(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        storage.get_reply_drafts.return_value = [
            {"id": uuid4(), "status": "pending"},
            {"id": uuid4(), "status": "pending"},
        ]

        req = _make_request(
            intent="yt_review_replies",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "2 reply draft(s)" in resp.message

    @pytest.mark.asyncio
    async def test_approve_updates_status_and_trust(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_level=TrustLevel.SUPERVISED.value,
            trust_stats={"total": 10, "approved": 9, "rejected": 1},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "approve",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert ReplyStatus.APPROVED.value in resp.message

        # Should have persisted trust changes
        storage.update_channel.assert_awaited_once()
        call_kwargs = storage.update_channel.call_args
        assert call_kwargs.args[0] == channel_id
        assert "trust_level" in call_kwargs.kwargs
        assert "trust_stats" in call_kwargs.kwargs

    @pytest.mark.asyncio
    async def test_reject_updates_status_and_trust(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_level=TrustLevel.SUPERVISED.value,
            trust_stats={"total": 5, "approved": 4, "rejected": 1},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "reject",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert ReplyStatus.REJECTED.value in resp.message
        storage.update_channel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_posted_does_not_persist_trust(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "posted",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert ReplyStatus.POSTED.value in resp.message
        # "posted" does not call update_channel for trust
        storage.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "explode",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Unknown action" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_invalid_reply_id_returns_error(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "approve",
                "reply_id": "not-a-uuid",
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Invalid reply_id" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_trust_promotion_on_approval(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """After 50 approvals with <5% rejection, trust should promote from SUPERVISED to GUIDED."""
        channel_id = uuid4()
        reply_id = uuid4()
        # 49 approved, 1 rejected, total=50 -- next approval triggers promotion check
        channel = _make_channel(
            channel_id,
            trust_level=TrustLevel.SUPERVISED.value,
            trust_stats={"total": 49, "approved": 48, "rejected": 1},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "approve",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        # Trust data should reflect GUIDED after promotion
        assert resp.data["trust"]["level"] == TrustLevel.GUIDED.value
        assert resp.data["trust"]["label"] == "GUIDED"

    @pytest.mark.asyncio
    async def test_trust_demotion_on_rejection_at_autonomous(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """Rejection at AUTONOMOUS level should demote to GUIDED."""
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_level=TrustLevel.AUTONOMOUS.value,
            trust_stats={"total": 200, "approved": 195, "rejected": 5},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "reject",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert resp.data["trust"]["level"] == TrustLevel.GUIDED.value


# ===================================================================
# generate_reply_drafts
# ===================================================================


class TestGenerateReplyDrafts:
    """Tests for the reply generation pipeline."""

    @pytest.mark.asyncio
    async def test_skips_already_replied_comments(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="cmt_1", sentiment="positive"),
        ]
        storage.get_reply_drafts.return_value = [
            {"comment_id": "cmt_1"},
        ]

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 0
        broker.infer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_spam_comments(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(category=ReplyCategory.SPAM.value),
        ]
        storage.get_reply_drafts.return_value = []

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 0
        broker.infer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_unanalyzed_comments(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(sentiment=None),  # no analysis
        ]
        storage.get_reply_drafts.return_value = []

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 0

    @pytest.mark.asyncio
    async def test_generates_reply_for_eligible_comment(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="cmt_new"),
        ]
        storage.get_reply_drafts.return_value = []

        broker.infer.return_value = FakeInferenceResult(content="Thank you!")

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 1
        broker.infer.assert_awaited_once()
        storage.save_reply_draft.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complaint_uses_conversation_task_type(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """Complaints should be routed to Claude (CONVERSATION task type)."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.COMPLAINT.value,
                comment_youtube_id="cmt_complaint",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="We are sorry.")

        await initialized_skill.generate_reply_drafts(channel_id, channel)

        call_kwargs = broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == TaskType.CONVERSATION

    @pytest.mark.asyncio
    async def test_question_uses_summarization_task_type(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """Questions should be routed to Gemini (SUMMARIZATION task type)."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.QUESTION.value,
                comment_youtube_id="cmt_question",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Here's the answer.")

        await initialized_skill.generate_reply_drafts(channel_id, channel)

        call_kwargs = broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == TaskType.SUMMARIZATION

    @pytest.mark.asyncio
    async def test_feedback_uses_summarization_task_type(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """Feedback should also route to Gemini (SUMMARIZATION)."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.FEEDBACK.value,
                comment_youtube_id="cmt_fb",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Thanks!")

        await initialized_skill.generate_reply_drafts(channel_id, channel)

        call_kwargs = broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == TaskType.SUMMARIZATION

    @pytest.mark.asyncio
    async def test_thank_you_uses_simple_qa_task_type(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """thank_you category should route to Ollama (SIMPLE_QA)."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.THANK_YOU.value,
                comment_youtube_id="cmt_ty",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="You're welcome!")

        await initialized_skill.generate_reply_drafts(channel_id, channel)

        call_kwargs = broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == TaskType.SIMPLE_QA

    @pytest.mark.asyncio
    async def test_trust_auto_approve_at_guided(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """At GUIDED level, thank_you and faq should be auto-approved."""
        channel_id = uuid4()
        channel = _make_channel(
            channel_id, trust_level=TrustLevel.GUIDED.value
        )
        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.THANK_YOU.value,
                comment_youtube_id="cmt_auto",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Thanks!")

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 1
        saved_call = storage.save_reply_draft.call_args[0][0]
        assert saved_call["auto_approved"] is True
        assert saved_call["status"] == ReplyStatus.APPROVED.value

    @pytest.mark.asyncio
    async def test_trust_pending_at_supervised(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """At SUPERVISED level, nothing should be auto-approved."""
        channel_id = uuid4()
        channel = _make_channel(
            channel_id, trust_level=TrustLevel.SUPERVISED.value
        )
        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.THANK_YOU.value,
                comment_youtube_id="cmt_pending",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Thanks!")

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 1
        saved_call = storage.save_reply_draft.call_args[0][0]
        assert saved_call["auto_approved"] is False
        assert saved_call["status"] == ReplyStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_inference_error_skips_comment(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """If broker.infer raises, the comment should be silently skipped."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="cmt_err"),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.side_effect = RuntimeError("LLM timeout")

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 0

    @pytest.mark.asyncio
    async def test_video_title_lookup(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """When comment has a video_id, the pipeline should look up the title."""
        channel_id = uuid4()
        video_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="cmt_vtitle", video_id=video_id),
        ]
        storage.get_reply_drafts.return_value = []
        storage.get_videos.return_value = [
            {"id": video_id, "title": "My Awesome Video"},
        ]
        broker.infer.return_value = FakeInferenceResult(content="Nice video!")

        drafts = await initialized_skill.generate_reply_drafts(channel_id, channel)
        assert len(drafts) == 1
        # Verify the prompt included the video title
        call_kwargs = broker.infer.call_args
        assert "My Awesome Video" in call_kwargs.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_simple_qa_confidence_higher(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """SIMPLE_QA (thank_you) should have 0.85 confidence, others 0.7."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.THANK_YOU.value,
                comment_youtube_id="cmt_conf_high",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Thanks!")

        await initialized_skill.generate_reply_drafts(channel_id, channel)
        saved = storage.save_reply_draft.call_args[0][0]
        assert saved["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_non_simple_qa_confidence_lower(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)

        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.COMPLAINT.value,
                comment_youtube_id="cmt_conf_low",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Sorry.")

        await initialized_skill.generate_reply_drafts(channel_id, channel)
        saved = storage.save_reply_draft.call_args[0][0]
        assert saved["confidence"] == 0.7


# ===================================================================
# _generate_tag_recommendation
# ===================================================================


class TestGenerateTagRecommendation:
    """Tests for tag SEO analysis pipeline."""

    @pytest.mark.asyncio
    async def test_returns_none_when_video_not_found(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        storage.get_video_by_youtube_id.return_value = None
        result = await initialized_skill._generate_tag_recommendation(uuid4(), "vid_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_generates_tag_recommendation(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        video = {
            "title": "Python Tips",
            "description": "Top 10 Python tips",
            "tags": ["python", "coding"],
        }
        storage.get_video_by_youtube_id.return_value = video
        storage.get_latest_strategy.return_value = None

        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps({
                "suggested_tags": ["python tips", "programming", "tutorial"],
                "reason": "Better SEO coverage",
            })
        )

        result = await initialized_skill._generate_tag_recommendation(channel_id, "vid_py")
        assert result is not None
        storage.save_tag_recommendation.assert_awaited_once()
        saved = storage.save_tag_recommendation.call_args[0][0]
        assert saved["video_id"] == "vid_py"
        assert "python tips" in saved["suggested_tags"]

    @pytest.mark.asyncio
    async def test_uses_strategy_keyword_targets(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        video = {"title": "Rust Intro", "description": "Learn Rust", "tags": []}
        storage.get_video_by_youtube_id.return_value = video
        storage.get_latest_strategy.return_value = {
            "strategy": {"seo": {"tag_strategy": "focus on beginner keywords"}}
        }

        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps({"suggested_tags": ["rust"], "reason": "ok"})
        )

        await initialized_skill._generate_tag_recommendation(channel_id, "vid_r")
        call_kwargs = broker.infer.call_args
        assert "focus on beginner keywords" in call_kwargs.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_uses_assumptions_for_topics(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        video = {"title": "Go Lang", "description": "Go tutorial", "tags": []}
        storage.get_video_by_youtube_id.return_value = video
        storage.get_latest_strategy.return_value = None
        initialized_skill._assumptions.get_high_confidence.return_value = [
            {"category": "topic", "statement": "Systems programming"},
        ]

        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps({"suggested_tags": ["go"], "reason": "ok"})
        )

        await initialized_skill._generate_tag_recommendation(channel_id, "vid_go")
        call_kwargs = broker.infer.call_args
        assert "Systems programming" in call_kwargs.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_inference_error_returns_none(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        video = {"title": "x", "description": "y", "tags": []}
        storage.get_video_by_youtube_id.return_value = video
        storage.get_latest_strategy.return_value = None
        broker.infer.side_effect = RuntimeError("boom")

        result = await initialized_skill._generate_tag_recommendation(channel_id, "vid_err")
        assert result is None


# ===================================================================
# _run_health_audit
# ===================================================================


class TestRunHealthAudit:
    """Tests for channel health audit."""

    @pytest.mark.asyncio
    async def test_returns_issues_list(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id, config={"description": "My channel"})

        storage.get_latest_stats.return_value = {
            "snapshot": {"subscriberCount": 1000}
        }
        storage.get_videos.return_value = [{"id": uuid4(), "title": "v1"}]
        storage.get_documents.return_value = []

        issues = [
            {"type": "missing_playlists", "severity": "medium", "suggestion": "Create playlists"},
        ]
        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps({"issues": issues})
        )

        result = await initialized_skill._run_health_audit(channel_id, channel)
        assert len(result) == 1
        assert result[0]["type"] == "missing_playlists"

    @pytest.mark.asyncio
    async def test_returns_list_directly_from_llm(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_latest_stats.return_value = None
        storage.get_videos.return_value = []
        storage.get_documents.return_value = []

        # LLM returns a list directly instead of {"issues": [...]}
        raw_list = [{"type": "no_videos", "severity": "high", "suggestion": "Upload videos"}]
        broker.infer.return_value = FakeInferenceResult(content=json.dumps(raw_list))

        result = await initialized_skill._run_health_audit(channel_id, channel)
        assert len(result) == 1
        assert result[0]["type"] == "no_videos"

    @pytest.mark.asyncio
    async def test_inference_error_returns_empty(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_latest_stats.return_value = None
        storage.get_videos.return_value = []
        storage.get_documents.return_value = []
        broker.infer.side_effect = RuntimeError("LLM error")

        result = await initialized_skill._run_health_audit(channel_id, channel)
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_json_returns_empty(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_latest_stats.return_value = None
        storage.get_videos.return_value = []
        storage.get_documents.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="{}")

        result = await initialized_skill._run_health_audit(channel_id, channel)
        assert result == []

    @pytest.mark.asyncio
    async def test_health_audit_handler(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """The yt_channel_health intent handler should work end-to-end."""
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel
        storage.get_latest_stats.return_value = None
        storage.get_videos.return_value = []
        storage.get_documents.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="[]")

        req = _make_request(
            intent="yt_channel_health",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "0 issue(s)" in resp.message


# ===================================================================
# on_heartbeat
# ===================================================================


class TestOnHeartbeat:
    """Tests for the heartbeat cycle."""

    @pytest.mark.asyncio
    async def test_returns_empty_without_storage(self) -> None:
        skill = YouTubeManagementSkill(memory=None, storage=None, broker=None)
        actions = await skill.on_heartbeat(["user1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_skips_channels_without_onboarding(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        storage.get_channels_due_for_analysis.return_value = [
            _make_channel(onboarding_complete=False),
        ]
        actions = await initialized_skill.on_heartbeat(["user1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_generates_actions_for_ready_channels(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        tenant_id = uuid4()
        channel = _make_channel(
            channel_id, onboarding_complete=True, tenant_id=tenant_id
        )
        storage.get_channels_due_for_analysis.return_value = [channel]

        # Make generate_reply_drafts produce one draft
        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="hb_cmt"),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Reply text")

        actions = await initialized_skill.on_heartbeat(["user1"])
        assert len(actions) == 1
        action = actions[0]
        assert isinstance(action, HeartbeatAction)
        assert action.action_type == "reply_drafts_generated"
        assert action.data["total"] == 1
        assert action.skill_name == "youtube_management"

    @pytest.mark.asyncio
    async def test_heartbeat_handles_per_channel_error(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """If one channel fails, others should still be processed."""
        good_id = uuid4()
        bad_id = uuid4()
        good_channel = _make_channel(good_id, onboarding_complete=True)
        bad_channel = _make_channel(bad_id, onboarding_complete=True)

        storage.get_channels_due_for_analysis.return_value = [bad_channel, good_channel]

        call_count = 0

        async def side_effect(cid: UUID, ch: dict) -> list:
            nonlocal call_count
            call_count += 1
            if cid == bad_id:
                raise RuntimeError("DB error")
            return []

        initialized_skill.generate_reply_drafts = side_effect  # type: ignore[assignment]
        actions = await initialized_skill.on_heartbeat(["user1"])
        # The bad channel should have been caught, the good channel processed
        assert call_count == 2
        assert isinstance(actions, list)

    @pytest.mark.asyncio
    async def test_heartbeat_no_drafts_no_action(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """If generate_reply_drafts returns empty, no HeartbeatAction is emitted."""
        channel_id = uuid4()
        channel = _make_channel(channel_id, onboarding_complete=True)
        storage.get_channels_due_for_analysis.return_value = [channel]
        storage.get_comments.return_value = []
        storage.get_reply_drafts.return_value = []

        actions = await initialized_skill.on_heartbeat(["user1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_counts_auto_approved(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        """HeartbeatAction data should correctly count auto_approved vs pending_review."""
        channel_id = uuid4()
        channel = _make_channel(
            channel_id,
            onboarding_complete=True,
            trust_level=TrustLevel.GUIDED.value,
        )
        storage.get_channels_due_for_analysis.return_value = [channel]

        # Two comments: one auto-approvable (thank_you at GUIDED), one not (complaint)
        storage.get_comments.return_value = [
            _make_comment(
                category=ReplyCategory.THANK_YOU.value,
                comment_youtube_id="hb_ty",
            ),
            _make_comment(
                category=ReplyCategory.COMPLAINT.value,
                comment_youtube_id="hb_comp",
            ),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="reply")

        actions = await initialized_skill.on_heartbeat(["u1"])
        assert len(actions) == 1
        data = actions[0].data
        assert data["total"] == 2
        assert data["auto_approved"] == 1
        assert data["pending_review"] == 1


# ===================================================================
# get_system_prompt_fragment
# ===================================================================


class TestGetSystemPromptFragment:
    def test_returns_fragment_when_ready(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        fragment = initialized_skill.get_system_prompt_fragment("user1")
        assert fragment is not None
        assert "YouTube Management" in fragment

    def test_returns_none_when_not_ready(self, skill: YouTubeManagementSkill) -> None:
        # skill.status is UNINITIALIZED by default
        fragment = skill.get_system_prompt_fragment("user1")
        assert fragment is None


# ===================================================================
# Module-level helpers
# ===================================================================


class TestResolveChannelId:
    """Tests for _resolve_channel_id helper."""

    def test_valid_uuid(self) -> None:
        uid = uuid4()
        req = _make_request(context={"channel_id": str(uid)})
        assert _resolve_channel_id(req) == uid

    def test_missing_channel_id(self) -> None:
        req = _make_request(context={})
        assert _resolve_channel_id(req) is None

    def test_invalid_uuid(self) -> None:
        req = _make_request(context={"channel_id": "not-a-uuid"})
        assert _resolve_channel_id(req) is None


class TestSerialise:
    """Tests for _serialise helper."""

    def test_none_returns_empty(self) -> None:
        assert _serialise(None) == {}

    def test_converts_datetime(self) -> None:
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = _serialise({"ts": dt, "name": "test"})
        assert result["ts"] == "2026-01-01T12:00:00"
        assert result["name"] == "test"

    def test_converts_uuid(self) -> None:
        uid = uuid4()
        result = _serialise({"id": uid})
        assert result["id"] == str(uid)

    def test_passes_plain_values(self) -> None:
        result = _serialise({"count": 42, "flag": True})
        assert result == {"count": 42, "flag": True}


class TestParseJson:
    """Tests for _parse_json helper."""

    def test_plain_json(self) -> None:
        raw = '{"key": "value"}'
        assert _parse_json(raw) == {"key": "value"}

    def test_json_in_markdown_fence(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        assert _parse_json(raw) == {"key": "value"}

    def test_invalid_json_returns_empty(self) -> None:
        raw = "this is not json"
        assert _parse_json(raw) == {}

    def test_json_list(self) -> None:
        raw = '[{"a": 1}, {"a": 2}]'
        result = _parse_json(raw)
        assert isinstance(result, list)
        assert len(result) == 2


# ===================================================================
# _handle_manage (generate replies via intent handler)
# ===================================================================


class TestHandleManage:
    """Tests for the yt_manage_channel intent."""

    @pytest.mark.asyncio
    async def test_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        req = _make_request(intent="yt_manage_channel", context={})
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_channel_not_found(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        storage.get_channel.return_value = None
        req = _make_request(
            intent="yt_manage_channel",
            context={"channel_id": str(uuid4())},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Channel not found" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_returns_draft_count(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        channel = _make_channel(channel_id)
        storage.get_channel.return_value = channel
        storage.get_comments.return_value = [
            _make_comment(comment_youtube_id="m1"),
        ]
        storage.get_reply_drafts.return_value = []
        broker.infer.return_value = FakeInferenceResult(content="Reply!")

        req = _make_request(
            intent="yt_manage_channel",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "1 reply draft(s)" in resp.message


# ===================================================================
# _handle_tag_recommendations (intent handler)
# ===================================================================


class TestHandleTagRecommendations:
    """Tests for the yt_get_tag_recommendations intent handler."""

    @pytest.mark.asyncio
    async def test_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        req = _make_request(intent="yt_get_tag_recommendations", context={})
        resp = await initialized_skill.handle(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_returns_existing_recommendations(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        storage.get_tag_recommendations.return_value = [
            {"video_id": "v1", "suggested_tags": ["a", "b"]},
        ]

        req = _make_request(
            intent="yt_get_tag_recommendations",
            context={"channel_id": str(channel_id)},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "1 tag recommendation(s)" in resp.message

    @pytest.mark.asyncio
    async def test_generates_for_specific_video(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock, broker: AsyncMock
    ) -> None:
        channel_id = uuid4()
        video = {"title": "Test", "description": "d", "tags": []}
        storage.get_video_by_youtube_id.return_value = video
        storage.get_latest_strategy.return_value = None
        broker.infer.return_value = FakeInferenceResult(
            content=json.dumps({"suggested_tags": ["test"], "reason": "ok"})
        )

        req = _make_request(
            intent="yt_get_tag_recommendations",
            context={"channel_id": str(channel_id), "video_id": "vid_x"},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        assert "Tag recommendation generated" in resp.message


# ===================================================================
# Trust model integration in review flow
# ===================================================================


class TestTrustModelIntegration:
    """Verify TrustModel interacts correctly with review actions."""

    @pytest.mark.asyncio
    async def test_approve_increments_stats(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_stats={"total": 0, "approved": 0, "rejected": 0},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "approve",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        # Trust stats after one approval
        trust_data = resp.data["trust"]
        assert trust_data["stats"]["total"] == 1
        assert trust_data["stats"]["approved"] == 1

    @pytest.mark.asyncio
    async def test_reject_increments_stats(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        channel_id = uuid4()
        reply_id = uuid4()
        channel = _make_channel(
            channel_id,
            trust_stats={"total": 0, "approved": 0, "rejected": 0},
        )
        storage.get_channel.return_value = channel

        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "reject",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is True
        trust_data = resp.data["trust"]
        assert trust_data["stats"]["total"] == 1
        assert trust_data["stats"]["rejected"] == 1


# ===================================================================
# Handler not implemented (line 164)
# ===================================================================


class TestHandlerNotImplemented:
    """Tests for the 'handler not implemented' branch in handle()."""

    @pytest.mark.asyncio
    async def test_handle_handler_not_implemented(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        """handle() should return error when getattr returns None for a valid intent."""
        req = _make_request(
            intent="yt_manage_channel",
            context={"channel_id": str(uuid4())},
        )
        with patch.object(YouTubeManagementSkill, "_handle_manage", new=None):
            resp = await initialized_skill.handle(req)

        assert resp.success is False
        assert "Handler not implemented" in (resp.error or "")


# ===================================================================
# Missing channel_id errors in various handlers (lines 201, 205, 220,
# 278, 304, 372, 376)
# ===================================================================


class TestMissingChannelIdErrors:
    """Tests for missing channel_id / channel not found in various handlers."""

    @pytest.mark.asyncio
    async def test_get_state_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        """_handle_get_state returns error when channel_id missing (line 201)."""
        req = _make_request(intent="yt_get_management_state", context={})
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_get_state_channel_not_found(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """_handle_get_state returns error when channel not found (line 205)."""
        storage.get_channel.return_value = None
        req = _make_request(
            intent="yt_get_management_state",
            context={"channel_id": str(uuid4())},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Channel not found" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_configure_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        """_handle_configure returns error when channel_id missing (line 220)."""
        req = _make_request(
            intent="yt_configure_management",
            context={"answers": {}},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_review_replies_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        """_handle_review_replies returns error when channel_id missing (line 278)."""
        req = _make_request(intent="yt_review_replies", context={})
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_review_replies_channel_not_found(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """_handle_review_replies returns error when channel not found (line 304)."""
        channel_id = uuid4()
        reply_id = uuid4()
        storage.get_channel.return_value = None
        req = _make_request(
            intent="yt_review_replies",
            context={
                "channel_id": str(channel_id),
                "action": "approve",
                "reply_id": str(reply_id),
            },
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Channel not found" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_channel_health_missing_channel_id(
        self, initialized_skill: YouTubeManagementSkill
    ) -> None:
        """_handle_channel_health returns error when channel_id missing (line 372)."""
        req = _make_request(intent="yt_channel_health", context={})
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_channel_health_channel_not_found(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """_handle_channel_health returns error when channel not found (line 376)."""
        storage.get_channel.return_value = None
        req = _make_request(
            intent="yt_channel_health",
            context={"channel_id": str(uuid4())},
        )
        resp = await initialized_skill.handle(req)
        assert resp.success is False
        assert "Channel not found" in (resp.error or "")


# ===================================================================
# _generate_followup_questions failure (lines 655-661)
# ===================================================================


class TestGenerateFollowupQuestionsFailure:
    """Tests for _generate_followup_questions exception and empty return."""

    @pytest.mark.asyncio
    async def test_followup_exception_returns_empty(
        self, initialized_skill: YouTubeManagementSkill, broker: AsyncMock
    ) -> None:
        """Should return empty list when broker raises (lines 655-657)."""
        broker.infer.side_effect = RuntimeError("LLM down")

        result = await initialized_skill._generate_followup_questions(
            answers_so_far={"topics": "gaming"},
            missing_categories=["audience"],
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_followup_non_list_returns_empty(
        self, initialized_skill: YouTubeManagementSkill, broker: AsyncMock
    ) -> None:
        """Should return empty list when parsed result is not a list (line 661)."""
        broker.infer.return_value = FakeInferenceResult(
            content='{"not": "a list"}'
        )

        result = await initialized_skill._generate_followup_questions(
            answers_so_far={"topics": "cooking"},
            missing_categories=["schedule"],
        )

        assert result == []


# ===================================================================
# on_heartbeat outer exception handler (lines 709-710)
# ===================================================================


class TestHeartbeatOuterException:
    """Test for the outer exception handler in on_heartbeat."""

    @pytest.mark.asyncio
    async def test_heartbeat_outer_exception_returns_empty(
        self, initialized_skill: YouTubeManagementSkill, storage: AsyncMock
    ) -> None:
        """Should return empty list when get_channels_due_for_analysis raises (lines 709-710)."""
        storage.get_channels_due_for_analysis.side_effect = RuntimeError(
            "Database connection failed"
        )

        actions = await initialized_skill.on_heartbeat(["user1"])

        assert actions == []
