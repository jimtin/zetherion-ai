"""Tests for AI-powered email reply generation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.skills.gmail.client import EmailMessage
from zetherion_ai.skills.gmail.replies import (
    TRUST_CEILINGS,
    DraftStatus,
    ReplyClassifier,
    ReplyDraft,
    ReplyDraftStore,
    ReplyGenerator,
    ReplyType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool with async context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.fixture
def mock_broker():
    """Mock InferenceBroker returning a standard reply."""
    broker = AsyncMock()
    result = MagicMock()
    result.content = "Thank you for your email. I'll review and get back to you shortly."
    broker.infer.return_value = result
    return broker


@pytest.fixture
def classifier():
    """Create a ReplyClassifier instance."""
    return ReplyClassifier()


def _make_email(
    subject: str = "",
    snippet: str = "",
    body_text: str = "",
    from_email: str = "sender@example.com",
) -> EmailMessage:
    """Helper to create an EmailMessage with minimal fields."""
    return EmailMessage(
        gmail_id="msg-001",
        thread_id="thread-001",
        subject=subject,
        from_email=from_email,
        snippet=snippet,
        body_text=body_text,
    )


# ---------------------------------------------------------------------------
# 1. ReplyType enum
# ---------------------------------------------------------------------------


class TestReplyType:
    def test_is_str_enum(self):
        assert issubclass(ReplyType, StrEnum)

    def test_all_values_exist(self):
        expected = {
            "acknowledgment",
            "meeting_confirm",
            "meeting_decline",
            "info_request",
            "task_update",
            "negotiation",
            "sensitive",
            "general",
        }
        actual = {member.value for member in ReplyType}
        assert actual == expected

    def test_string_behaviour(self):
        assert str(ReplyType.ACKNOWLEDGMENT) == "acknowledgment"
        assert ReplyType.SENSITIVE == "sensitive"


# ---------------------------------------------------------------------------
# 2. DraftStatus enum
# ---------------------------------------------------------------------------


class TestDraftStatus:
    def test_is_str_enum(self):
        assert issubclass(DraftStatus, StrEnum)

    def test_all_values_exist(self):
        expected = {"pending", "approved", "edited", "rejected", "sent"}
        actual = {member.value for member in DraftStatus}
        assert actual == expected

    def test_string_behaviour(self):
        assert str(DraftStatus.PENDING) == "pending"
        assert DraftStatus.SENT == "sent"


# ---------------------------------------------------------------------------
# 3. TRUST_CEILINGS
# ---------------------------------------------------------------------------


class TestTrustCeilings:
    def test_all_reply_types_have_ceilings(self):
        for rt in ReplyType:
            assert rt in TRUST_CEILINGS, f"Missing ceiling for {rt}"

    def test_values_between_zero_and_one(self):
        for rt, ceiling in TRUST_CEILINGS.items():
            assert 0.0 < ceiling <= 1.0, f"Ceiling out of range for {rt}: {ceiling}"

    def test_acknowledgment_highest(self):
        assert TRUST_CEILINGS[ReplyType.ACKNOWLEDGMENT] == 0.95

    def test_sensitive_lowest(self):
        assert TRUST_CEILINGS[ReplyType.SENSITIVE] == 0.30


# ---------------------------------------------------------------------------
# 4. ReplyDraft dataclass
# ---------------------------------------------------------------------------


class TestReplyDraft:
    def test_default_values(self):
        draft = ReplyDraft(
            email_id=1,
            account_id=2,
            draft_text="Hello",
            reply_type=ReplyType.GENERAL,
            confidence=0.5,
        )
        assert draft.status == DraftStatus.PENDING
        assert draft.draft_id is None
        assert draft.sent_at is None
        assert draft.created_at is None

    def test_to_dict_all_fields_set(self):
        now = datetime(2025, 6, 15, 12, 0, 0)
        created = datetime(2025, 6, 15, 11, 0, 0)
        draft = ReplyDraft(
            email_id=10,
            account_id=20,
            draft_text="Some text",
            reply_type=ReplyType.MEETING_CONFIRM,
            confidence=0.85,
            status=DraftStatus.SENT,
            draft_id=99,
            sent_at=now,
            created_at=created,
        )
        d = draft.to_dict()
        assert d["draft_id"] == 99
        assert d["email_id"] == 10
        assert d["account_id"] == 20
        assert d["draft_text"] == "Some text"
        assert d["reply_type"] == "meeting_confirm"
        assert d["confidence"] == 0.85
        assert d["status"] == "sent"
        assert d["sent_at"] == now.isoformat()
        assert d["created_at"] == created.isoformat()

    def test_to_dict_none_datetimes(self):
        draft = ReplyDraft(
            email_id=1,
            account_id=2,
            draft_text="Hi",
            reply_type=ReplyType.GENERAL,
            confidence=0.4,
        )
        d = draft.to_dict()
        assert d["sent_at"] is None
        assert d["created_at"] is None
        assert d["draft_id"] is None


# ---------------------------------------------------------------------------
# 5. ReplyClassifier.classify
# ---------------------------------------------------------------------------


class TestReplyClassifierClassify:
    def test_sensitive_email(self, classifier):
        msg = _make_email(subject="Confidential: HR matter")
        assert classifier.classify(msg) == ReplyType.SENSITIVE

    def test_sensitive_body(self, classifier):
        msg = _make_email(body_text="This is about your salary review")
        assert classifier.classify(msg) == ReplyType.SENSITIVE

    def test_negotiation_email(self, classifier):
        msg = _make_email(subject="New proposal for Q3")
        assert classifier.classify(msg) == ReplyType.NEGOTIATION

    def test_meeting_confirm(self, classifier):
        msg = _make_email(subject="Meeting invitation for Monday")
        assert classifier.classify(msg) == ReplyType.MEETING_CONFIRM

    def test_meeting_decline(self, classifier):
        msg = _make_email(
            subject="Meeting invitation",
            body_text="I cannot attend due to a conflict",
        )
        assert classifier.classify(msg) == ReplyType.MEETING_DECLINE

    def test_info_request(self, classifier):
        msg = _make_email(subject="Quick question about the project")
        assert classifier.classify(msg) == ReplyType.INFO_REQUEST

    def test_task_update(self, classifier):
        msg = _make_email(subject="Status update on action item")
        assert classifier.classify(msg) == ReplyType.TASK_UPDATE

    def test_acknowledgment(self, classifier):
        msg = _make_email(body_text="Thanks for letting me know")
        assert classifier.classify(msg) == ReplyType.ACKNOWLEDGMENT

    def test_general_fallback(self, classifier):
        msg = _make_email(subject="Hello!", body_text="How are you doing?")
        assert classifier.classify(msg) == ReplyType.GENERAL

    def test_priority_sensitive_over_negotiation(self, classifier):
        """When both sensitive and negotiation keywords present, sensitive wins."""
        msg = _make_email(
            subject="Confidential contract proposal",
            body_text="This is a private deal",
        )
        assert classifier.classify(msg) == ReplyType.SENSITIVE

    def test_priority_negotiation_over_meeting(self, classifier):
        """When both negotiation and meeting keywords present, negotiation wins."""
        msg = _make_email(subject="Proposal for meeting schedule terms")
        assert classifier.classify(msg) == ReplyType.NEGOTIATION

    def test_priority_meeting_over_info_request(self, classifier):
        """Meeting keywords take priority over info request."""
        msg = _make_email(subject="Can you attend the meeting? question")
        assert classifier.classify(msg) == ReplyType.MEETING_CONFIRM

    def test_snippet_used_for_classification(self, classifier):
        """Snippet content is included in classification text."""
        msg = _make_email(snippet="This is confidential information")
        assert classifier.classify(msg) == ReplyType.SENSITIVE


# ---------------------------------------------------------------------------
# 6. ReplyClassifier._matches
# ---------------------------------------------------------------------------


class TestReplyClassifierMatches:
    def test_matches_keyword_present(self, classifier):
        assert classifier._matches("thank you for the email", frozenset({"thank"})) is True

    def test_matches_keyword_absent(self, classifier):
        assert classifier._matches("hello world", frozenset({"thank", "thanks"})) is False

    def test_matches_multi_word_keyword(self, classifier):
        text = "please confirm receipt of the document"
        assert classifier._matches(text, frozenset({"confirm receipt"})) is True

    def test_matches_empty_keywords(self, classifier):
        assert classifier._matches("any text here", frozenset()) is False

    def test_matches_empty_text(self, classifier):
        assert classifier._matches("", frozenset({"hello"})) is False


# ---------------------------------------------------------------------------
# 7. ReplyGenerator.__init__
# ---------------------------------------------------------------------------


class TestReplyGeneratorInit:
    def test_stores_broker(self, mock_broker):
        gen = ReplyGenerator(mock_broker)
        assert gen._broker is mock_broker


# ---------------------------------------------------------------------------
# 8. ReplyGenerator.generate
# ---------------------------------------------------------------------------


class TestReplyGeneratorGenerate:
    @pytest.fixture
    def generator(self, mock_broker):
        return ReplyGenerator(mock_broker)

    async def test_generate_calls_broker_with_correct_params(self, generator, mock_broker):
        msg = _make_email(
            subject="Hello",
            from_email="alice@example.com",
            body_text="Can you help me with the report?",
        )
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            draft = await generator.generate(msg, ReplyType.INFO_REQUEST)

        mock_broker.infer.assert_called_once()
        call_kwargs = mock_broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == "conversation"
        assert call_kwargs.kwargs["temperature"] == 0.5
        assert "system_prompt" in call_kwargs.kwargs
        assert "info_request" in call_kwargs.kwargs["system_prompt"]
        assert isinstance(draft, ReplyDraft)
        assert draft.reply_type == ReplyType.INFO_REQUEST

    async def test_generate_with_additional_context(self, generator, mock_broker):
        msg = _make_email(subject="Update", body_text="Please update me.")
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            await generator.generate(
                msg,
                ReplyType.TASK_UPDATE,
                additional_context="Priority is high",
            )

        call_kwargs = mock_broker.infer.call_args
        assert "Additional context: Priority is high" in call_kwargs.kwargs["prompt"]

    async def test_generate_without_body_falls_back_to_snippet(self, generator, mock_broker):
        msg = _make_email(subject="Hi", snippet="Just a quick note", body_text="")
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            await generator.generate(msg, ReplyType.GENERAL)

        call_kwargs = mock_broker.infer.call_args
        assert "Just a quick note" in call_kwargs.kwargs["prompt"]

    async def test_generate_without_body_or_snippet(self, generator, mock_broker):
        msg = _make_email(subject="Empty", body_text="", snippet="")
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            await generator.generate(msg, ReplyType.GENERAL)

        call_kwargs = mock_broker.infer.call_args
        assert "(no body)" in call_kwargs.kwargs["prompt"]

    async def test_generate_truncates_long_body(self, generator, mock_broker):
        long_body = "x" * 5000
        msg = _make_email(subject="Long", body_text=long_body)
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            await generator.generate(msg, ReplyType.GENERAL)

        call_kwargs = mock_broker.infer.call_args
        prompt = call_kwargs.kwargs["prompt"]
        # The body in the prompt should be at most 2000 chars, not 5000
        assert "x" * 5000 not in prompt
        assert "x" * 2000 in prompt

    async def test_generate_custom_user_name_and_style(self, generator, mock_broker):
        msg = _make_email(subject="Test", body_text="content")
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            await generator.generate(
                msg,
                ReplyType.ACKNOWLEDGMENT,
                user_name="Alice",
                communication_style="formal",
            )

        call_kwargs = mock_broker.infer.call_args
        system = call_kwargs.kwargs["system_prompt"]
        assert "Alice" in system
        assert "formal" in system

    async def test_generate_returns_reply_draft(self, generator, mock_broker):
        msg = _make_email(subject="Test", body_text="Test body text")
        mock_tt = MagicMock()
        mock_tt.CONVERSATION = "conversation"
        with patch.dict("sys.modules", {"zetherion_ai.agent.providers": mock_tt}):
            mock_tt.TaskType = mock_tt
            draft = await generator.generate(msg, ReplyType.GENERAL)

        assert isinstance(draft, ReplyDraft)
        assert draft.email_id == 0
        assert draft.account_id == 0
        assert draft.draft_text == mock_broker.infer.return_value.content
        assert draft.status == DraftStatus.PENDING


# ---------------------------------------------------------------------------
# 9. ReplyGenerator._score_confidence
# ---------------------------------------------------------------------------


class TestScoreConfidence:
    @pytest.fixture
    def generator(self, mock_broker):
        return ReplyGenerator(mock_broker)

    def test_short_content_gets_penalty(self, generator):
        """Content < 20 chars gets a penalty of 0.1."""
        score = generator._score_confidence(ReplyType.GENERAL, "Hi")
        ceiling = TRUST_CEILINGS[ReplyType.GENERAL]
        # base = ceiling * 0.7, no >10 bonus (len=2), no 50-500 bonus, minus 0.1
        expected = max(0.0, ceiling * 0.7 - 0.1)
        assert score == pytest.approx(expected, abs=1e-9)

    def test_medium_content_gets_full_bonus(self, generator):
        """Content between 50-500 chars gets both >10 and 50-500 bonuses."""
        content = "A" * 100  # 100 chars: >10 and in [50, 500]
        score = generator._score_confidence(ReplyType.ACKNOWLEDGMENT, content)
        ceiling = TRUST_CEILINGS[ReplyType.ACKNOWLEDGMENT]
        expected = ceiling * 0.7 + ceiling * 0.15 + ceiling * 0.10
        assert score == pytest.approx(min(ceiling, expected), abs=1e-9)

    def test_long_content_gets_partial_bonus(self, generator):
        """Content > 500 chars gets >10 bonus but not 50-500 bonus."""
        content = "B" * 600
        score = generator._score_confidence(ReplyType.MEETING_CONFIRM, content)
        ceiling = TRUST_CEILINGS[ReplyType.MEETING_CONFIRM]
        expected = ceiling * 0.7 + ceiling * 0.15
        assert score == pytest.approx(min(ceiling, expected), abs=1e-9)

    def test_content_10_chars_no_length_bonus(self, generator):
        """Content exactly 10 chars does NOT get >10 bonus (it requires > 10)."""
        content = "A" * 10
        score = generator._score_confidence(ReplyType.GENERAL, content)
        ceiling = TRUST_CEILINGS[ReplyType.GENERAL]
        # base = ceiling * 0.7, no >10 bonus (len == 10), no 50-500 bonus, penalty for <20
        expected = max(0.0, ceiling * 0.7 - 0.1)
        assert score == pytest.approx(expected, abs=1e-9)

    def test_content_11_chars_gets_length_bonus(self, generator):
        """Content with 11 chars gets >10 bonus but not 50-500, and <20 penalty."""
        content = "A" * 11
        score = generator._score_confidence(ReplyType.GENERAL, content)
        ceiling = TRUST_CEILINGS[ReplyType.GENERAL]
        # base = ceiling * 0.7 + ceiling * 0.15, penalty for <20
        expected = max(0.0, ceiling * 0.7 + ceiling * 0.15 - 0.1)
        assert score == pytest.approx(expected, abs=1e-9)

    def test_score_clamped_to_ceiling(self, generator):
        """Score cannot exceed the ceiling for the reply type."""
        content = "A" * 100
        score = generator._score_confidence(ReplyType.ACKNOWLEDGMENT, content)
        ceiling = TRUST_CEILINGS[ReplyType.ACKNOWLEDGMENT]
        assert score <= ceiling

    def test_score_minimum_zero(self, generator):
        """Score cannot go below 0.0 even with penalties."""
        # Use sensitive (ceiling 0.30) with short content to test lower bound
        score = generator._score_confidence(ReplyType.SENSITIVE, "Hi")
        assert score >= 0.0

    def test_empty_content(self, generator):
        """Empty content gets penalty and no bonuses."""
        score = generator._score_confidence(ReplyType.GENERAL, "")
        ceiling = TRUST_CEILINGS[ReplyType.GENERAL]
        expected = max(0.0, ceiling * 0.7 - 0.1)
        assert score == pytest.approx(expected, abs=1e-9)

    def test_content_exactly_50_chars(self, generator):
        """Content exactly 50 chars gets both >10 and 50-500 bonuses."""
        content = "C" * 50
        score = generator._score_confidence(ReplyType.TASK_UPDATE, content)
        ceiling = TRUST_CEILINGS[ReplyType.TASK_UPDATE]
        expected = ceiling * 0.7 + ceiling * 0.15 + ceiling * 0.10
        assert score == pytest.approx(min(ceiling, expected), abs=1e-9)

    def test_content_exactly_500_chars(self, generator):
        """Content exactly 500 chars gets both >10 and 50-500 bonuses."""
        content = "D" * 500
        score = generator._score_confidence(ReplyType.INFO_REQUEST, content)
        ceiling = TRUST_CEILINGS[ReplyType.INFO_REQUEST]
        expected = ceiling * 0.7 + ceiling * 0.15 + ceiling * 0.10
        assert score == pytest.approx(min(ceiling, expected), abs=1e-9)

    def test_content_501_chars_no_medium_bonus(self, generator):
        """Content with 501 chars does NOT get 50-500 bonus."""
        content = "E" * 501
        score = generator._score_confidence(ReplyType.INFO_REQUEST, content)
        ceiling = TRUST_CEILINGS[ReplyType.INFO_REQUEST]
        expected = ceiling * 0.7 + ceiling * 0.15
        assert score == pytest.approx(min(ceiling, expected), abs=1e-9)


# ---------------------------------------------------------------------------
# 10. ReplyDraftStore.save_draft
# ---------------------------------------------------------------------------


class TestReplyDraftStoreSave:
    async def test_save_draft_returns_id(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {"id": 42}
        store = ReplyDraftStore(pool)
        draft = ReplyDraft(
            email_id=1,
            account_id=2,
            draft_text="Reply text",
            reply_type=ReplyType.GENERAL,
            confidence=0.5,
        )
        result = await store.save_draft(draft)
        assert result == 42
        conn.fetchrow.assert_called_once()
        call_args = conn.fetchrow.call_args
        # Verify SQL contains INSERT and the positional arguments
        assert "INSERT" in call_args.args[0]
        assert call_args.args[1] == 1  # email_id
        assert call_args.args[2] == 2  # account_id
        assert call_args.args[3] == "Reply text"
        assert call_args.args[4] == "general"
        assert call_args.args[5] == 0.5
        assert call_args.args[6] == "pending"


# ---------------------------------------------------------------------------
# 11. ReplyDraftStore.get_draft
# ---------------------------------------------------------------------------


class TestReplyDraftStoreGet:
    async def test_get_draft_found(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = {
            "id": 10,
            "email_id": 1,
            "account_id": 2,
            "draft_text": "Hello",
            "reply_type": "general",
            "confidence": 0.6,
            "status": "pending",
            "sent_at": None,
            "created_at": None,
        }
        store = ReplyDraftStore(pool)
        draft = await store.get_draft(10)
        assert draft is not None
        assert draft.draft_id == 10
        assert draft.email_id == 1
        assert draft.reply_type == ReplyType.GENERAL
        assert draft.status == DraftStatus.PENDING

    async def test_get_draft_not_found(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchrow.return_value = None
        store = ReplyDraftStore(pool)
        draft = await store.get_draft(999)
        assert draft is None


# ---------------------------------------------------------------------------
# 12. ReplyDraftStore.list_pending
# ---------------------------------------------------------------------------


class TestReplyDraftStoreListPending:
    async def test_list_pending_returns_drafts(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = [
            {
                "id": 1,
                "email_id": 10,
                "account_id": 5,
                "draft_text": "Reply 1",
                "reply_type": "acknowledgment",
                "confidence": 0.9,
                "status": "pending",
                "sent_at": None,
                "created_at": None,
            },
            {
                "id": 2,
                "email_id": 11,
                "account_id": 5,
                "draft_text": "Reply 2",
                "reply_type": "meeting_confirm",
                "confidence": 0.85,
                "status": "pending",
                "sent_at": None,
                "created_at": None,
            },
        ]
        store = ReplyDraftStore(pool)
        drafts = await store.list_pending(5)
        assert len(drafts) == 2
        assert drafts[0].draft_id == 1
        assert drafts[1].draft_id == 2

    async def test_list_pending_empty(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = []
        store = ReplyDraftStore(pool)
        drafts = await store.list_pending(99)
        assert drafts == []

    async def test_list_pending_custom_limit(self, mock_pool):
        pool, conn = mock_pool
        conn.fetch.return_value = []
        store = ReplyDraftStore(pool)
        await store.list_pending(5, limit=50)
        call_args = conn.fetch.call_args
        assert call_args.args[2] == 50  # limit parameter


# ---------------------------------------------------------------------------
# 13. ReplyDraftStore.update_status
# ---------------------------------------------------------------------------


class TestReplyDraftStoreUpdateStatus:
    async def test_update_status_without_sent_at_returns_true(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 1"
        store = ReplyDraftStore(pool)
        result = await store.update_status(1, DraftStatus.APPROVED)
        assert result is True
        call_args = conn.execute.call_args
        assert "status" in call_args.args[0].lower()
        assert call_args.args[1] == "approved"

    async def test_update_status_with_sent_at(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 1"
        store = ReplyDraftStore(pool)
        now = datetime(2025, 6, 15, 12, 0, 0)
        result = await store.update_status(1, DraftStatus.SENT, sent_at=now)
        assert result is True
        call_args = conn.execute.call_args
        assert "sent_at" in call_args.args[0].lower()
        assert call_args.args[1] == "sent"
        assert call_args.args[2] == now

    async def test_update_status_not_found_returns_false(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        store = ReplyDraftStore(pool)
        result = await store.update_status(999, DraftStatus.APPROVED)
        assert result is False


# ---------------------------------------------------------------------------
# 14. ReplyDraftStore.delete_draft
# ---------------------------------------------------------------------------


class TestReplyDraftStoreDelete:
    async def test_delete_returns_true(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "DELETE 1"
        store = ReplyDraftStore(pool)
        result = await store.delete_draft(1)
        assert result is True

    async def test_delete_not_found_returns_false(self, mock_pool):
        pool, conn = mock_pool
        conn.execute.return_value = "DELETE 0"
        store = ReplyDraftStore(pool)
        result = await store.delete_draft(999)
        assert result is False


# ---------------------------------------------------------------------------
# 15. ReplyDraftStore._row_to_draft
# ---------------------------------------------------------------------------


class TestRowToDraft:
    def test_row_to_draft_mapping(self, mock_pool):
        pool, _ = mock_pool
        store = ReplyDraftStore(pool)
        now = datetime(2025, 6, 15, 12, 0, 0)
        created = datetime(2025, 6, 15, 11, 0, 0)
        row = {
            "id": 5,
            "email_id": 100,
            "account_id": 200,
            "draft_text": "Draft content here",
            "reply_type": "negotiation",
            "confidence": 0.45,
            "status": "edited",
            "sent_at": now,
            "created_at": created,
        }
        draft = store._row_to_draft(row)
        assert draft.draft_id == 5
        assert draft.email_id == 100
        assert draft.account_id == 200
        assert draft.draft_text == "Draft content here"
        assert draft.reply_type == ReplyType.NEGOTIATION
        assert draft.confidence == 0.45
        assert draft.status == DraftStatus.EDITED
        assert draft.sent_at == now
        assert draft.created_at == created

    def test_row_to_draft_none_optional_fields(self, mock_pool):
        pool, _ = mock_pool
        store = ReplyDraftStore(pool)
        row = {
            "id": 1,
            "email_id": 10,
            "account_id": 20,
            "draft_text": "Text",
            "reply_type": "general",
            "confidence": 0.5,
            "status": "pending",
        }
        # dict.get returns None for missing keys
        draft = store._row_to_draft(row)
        assert draft.sent_at is None
        assert draft.created_at is None
