"""Tests for the unified Gmail inbox module."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.skills.gmail.client import EmailMessage, GmailClientError
from zetherion_ai.skills.gmail.inbox import (
    InboxEmail,
    InboxSummary,
    UnifiedInbox,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UNSET: list[str] = object()  # type: ignore[assignment]


def _make_email(
    gmail_id: str = "msg1",
    thread_id: str = "thread1",
    subject: str = "Test",
    from_email: str = "sender@test.com",
    is_read: bool = False,
    snippet: str = "",
    received_at: datetime | None = None,
    to_emails: list[str] | object = _UNSET,
    cc_emails: list[str] | object = _UNSET,
) -> EmailMessage:
    return EmailMessage(
        gmail_id=gmail_id,
        thread_id=thread_id,
        subject=subject,
        from_email=from_email,
        to_emails=["user@test.com"] if to_emails is _UNSET else to_emails,
        cc_emails=[] if cc_emails is _UNSET else cc_emails,
        body_text="body text",
        is_read=is_read,
        snippet=snippet,
        received_at=received_at or datetime(2025, 1, 1),
    )


def _make_mock_client(
    stubs: list[dict[str, str]] | None = None,
    messages: list[EmailMessage] | None = None,
    list_error: bool = False,
    get_error_ids: set[str] | None = None,
) -> AsyncMock:
    """Create a mocked GmailClient.

    Args:
        stubs: Message stub dicts returned by list_messages.
        messages: EmailMessage instances returned by get_message (in order).
        list_error: If True, list_messages raises GmailClientError.
        get_error_ids: Set of message IDs for which get_message should raise.
    """
    client = AsyncMock()

    if list_error:
        client.list_messages = AsyncMock(side_effect=GmailClientError("list failed"))
    else:
        client.list_messages = AsyncMock(return_value=(stubs or [], None))

    if messages is not None:
        msg_map = {m.gmail_id: m for m in messages}

        async def _get_message(msg_id: str) -> EmailMessage:
            if get_error_ids and msg_id in get_error_ids:
                raise GmailClientError(f"get failed for {msg_id}")
            return msg_map[msg_id]

        client.get_message = AsyncMock(side_effect=_get_message)
    else:
        client.get_message = AsyncMock()

    return client


# ---------------------------------------------------------------------------
# 1. InboxEmail tests
# ---------------------------------------------------------------------------


class TestInboxEmail:
    """Tests for the InboxEmail dataclass."""

    def test_to_dict_output(self):
        """to_dict should contain all expected fields with correct values."""
        msg = _make_email()
        ie = InboxEmail(
            message=msg,
            account_email="acct@test.com",
            account_id=42,
            priority_score=0.75,
            classification="meeting",
        )
        d = ie.to_dict()
        assert d["account_email"] == "acct@test.com"
        assert d["account_id"] == 42
        assert d["priority_score"] == 0.75
        assert d["classification"] == "meeting"
        assert d["message"]["gmail_id"] == "msg1"

    def test_default_values(self):
        """Default priority_score and classification should be set."""
        ie = InboxEmail(
            message=_make_email(),
            account_email="x@test.com",
            account_id=1,
        )
        assert ie.priority_score == 0.0
        assert ie.classification == "general"


# ---------------------------------------------------------------------------
# 2. InboxSummary tests
# ---------------------------------------------------------------------------


class TestInboxSummary:
    """Tests for the InboxSummary dataclass."""

    def test_to_dict_output(self):
        """to_dict should contain all expected summary fields."""
        s = InboxSummary(
            total_emails=10,
            unread_count=3,
            high_priority=2,
            by_account={"a@test.com": 5, "b@test.com": 5},
            by_classification={"meeting": 4, "general": 6},
        )
        d = s.to_dict()
        assert d["total_emails"] == 10
        assert d["unread_count"] == 3
        assert d["high_priority"] == 2
        assert d["by_account"] == {"a@test.com": 5, "b@test.com": 5}
        assert d["by_classification"] == {"meeting": 4, "general": 6}

    def test_default_empty_values(self):
        """Default InboxSummary should have zeroes and empty dicts."""
        s = InboxSummary()
        assert s.total_emails == 0
        assert s.unread_count == 0
        assert s.high_priority == 0
        assert s.by_account == {}
        assert s.by_classification == {}


# ---------------------------------------------------------------------------
# 3. UnifiedInbox initialization
# ---------------------------------------------------------------------------


class TestUnifiedInboxInit:
    """Tests for UnifiedInbox initialization."""

    def test_empty_inbox(self):
        """A new inbox should have no emails and no seen IDs."""
        inbox = UnifiedInbox()
        assert inbox._emails == []
        assert inbox._seen_ids == set()

    def test_count_property(self):
        """count should reflect the number of emails."""
        inbox = UnifiedInbox()
        assert inbox.count == 0
        inbox._emails.append(
            InboxEmail(
                message=_make_email(),
                account_email="a@test.com",
                account_id=1,
            )
        )
        assert inbox.count == 1


# ---------------------------------------------------------------------------
# 4. fetch_from_account tests
# ---------------------------------------------------------------------------


class TestFetchFromAccount:
    """Tests for UnifiedInbox.fetch_from_account."""

    @pytest.mark.asyncio
    async def test_fetches_and_adds_emails(self):
        """Emails fetched from the client should be added to the inbox."""
        msg = _make_email(gmail_id="m1")
        client = _make_mock_client(
            stubs=[{"id": "m1"}],
            messages=[msg],
        )
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert len(result) == 1
        assert result[0].message.gmail_id == "m1"
        assert inbox.count == 1

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """The same message should not be added twice."""
        msg = _make_email(gmail_id="m1")
        client = _make_mock_client(
            stubs=[{"id": "m1"}],
            messages=[msg],
        )
        inbox = UnifiedInbox()
        await inbox.fetch_from_account(client, "user@test.com", 1)
        result2 = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert len(result2) == 0
        assert inbox.count == 1

    @pytest.mark.asyncio
    async def test_list_messages_error_handled(self):
        """GmailClientError on list_messages should be caught; returns empty."""
        client = _make_mock_client(list_error=True)
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert result == []
        assert inbox.count == 0

    @pytest.mark.asyncio
    async def test_get_message_error_continues(self):
        """GmailClientError on a single get_message should skip that message."""
        msg2 = _make_email(gmail_id="m2")
        client = _make_mock_client(
            stubs=[{"id": "m1"}, {"id": "m2"}],
            messages=[msg2],
            get_error_ids={"m1"},
        )
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert len(result) == 1
        assert result[0].message.gmail_id == "m2"
        assert inbox.count == 1

    @pytest.mark.asyncio
    async def test_scores_priority(self):
        """Fetched emails should have a priority score set."""
        msg = _make_email(gmail_id="m1", subject="URGENT action required")
        client = _make_mock_client(
            stubs=[{"id": "m1"}],
            messages=[msg],
        )
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert result[0].priority_score > 0.0

    @pytest.mark.asyncio
    async def test_classifies_emails(self):
        """Fetched emails should have a classification set."""
        msg = _make_email(gmail_id="m1", subject="Meeting invite tomorrow")
        client = _make_mock_client(
            stubs=[{"id": "m1"}],
            messages=[msg],
        )
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        assert result[0].classification == "meeting"

    @pytest.mark.asyncio
    async def test_stub_without_id_key_skipped(self):
        """A stub without an 'id' key should use empty string, deduplicated."""
        msg = _make_email(gmail_id="")
        client = _make_mock_client(
            stubs=[{}],
            messages=[msg],
        )
        inbox = UnifiedInbox()
        result = await inbox.fetch_from_account(client, "user@test.com", 1)

        # Should still attempt to fetch the message with id=""
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 5. get_emails tests
# ---------------------------------------------------------------------------


class TestGetEmails:
    """Tests for UnifiedInbox.get_emails."""

    def _populated_inbox(self) -> UnifiedInbox:
        """Build an inbox with a variety of emails for filtering tests."""
        inbox = UnifiedInbox()
        inbox._emails = [
            InboxEmail(
                message=_make_email(
                    gmail_id="a1",
                    is_read=False,
                    received_at=datetime(2025, 1, 3),
                ),
                account_email="alice@test.com",
                account_id=1,
                priority_score=0.8,
                classification="meeting",
            ),
            InboxEmail(
                message=_make_email(
                    gmail_id="a2",
                    is_read=True,
                    received_at=datetime(2025, 1, 1),
                ),
                account_email="alice@test.com",
                account_id=1,
                priority_score=0.3,
                classification="general",
            ),
            InboxEmail(
                message=_make_email(
                    gmail_id="b1",
                    is_read=False,
                    received_at=datetime(2025, 1, 2),
                ),
                account_email="bob@test.com",
                account_id=2,
                priority_score=0.5,
                classification="newsletter",
            ),
        ]
        return inbox

    def test_returns_all_emails(self):
        """Without filters, all emails should be returned."""
        inbox = self._populated_inbox()
        result = inbox.get_emails()
        assert len(result) == 3

    def test_unread_only_filter(self):
        """unread_only=True should exclude read emails."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(unread_only=True)
        assert len(result) == 2
        assert all(not e.message.is_read for e in result)

    def test_account_email_filter(self):
        """account_email should limit results to one account."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(account_email="bob@test.com")
        assert len(result) == 1
        assert result[0].account_email == "bob@test.com"

    def test_classification_filter(self):
        """classification filter should only return matching type."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(classification="meeting")
        assert len(result) == 1
        assert result[0].classification == "meeting"

    def test_min_priority_filter(self):
        """min_priority should exclude low-priority emails."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(min_priority=0.6)
        assert len(result) == 1
        assert result[0].priority_score >= 0.6

    def test_sort_by_date_default(self):
        """Default sort should order by received_at descending."""
        inbox = self._populated_inbox()
        result = inbox.get_emails()
        dates = [e.message.received_at for e in result]
        assert dates == sorted(dates, reverse=True)

    def test_sort_by_priority(self):
        """sort_by='priority' should order by priority_score descending."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(sort_by="priority")
        scores = [e.priority_score for e in result]
        assert scores == sorted(scores, reverse=True)

    def test_limit_parameter(self):
        """limit should cap the number of returned emails."""
        inbox = self._populated_inbox()
        result = inbox.get_emails(limit=2)
        assert len(result) == 2

    def test_sort_by_date_with_none_received_at(self):
        """Emails with received_at=None should sort using datetime.min."""
        inbox = UnifiedInbox()
        inbox._emails = [
            InboxEmail(
                message=_make_email(gmail_id="n1", received_at=None),
                account_email="x@test.com",
                account_id=1,
            ),
            InboxEmail(
                message=_make_email(
                    gmail_id="n2",
                    received_at=datetime(2025, 6, 1),
                ),
                account_email="x@test.com",
                account_id=1,
            ),
        ]
        # Manually set received_at to None for n1
        inbox._emails[0].message.received_at = None

        result = inbox.get_emails()
        # The email with a real date should come first (newest first)
        assert result[0].message.gmail_id == "n2"
        assert result[1].message.gmail_id == "n1"


# ---------------------------------------------------------------------------
# 6. get_summary tests
# ---------------------------------------------------------------------------


class TestGetSummary:
    """Tests for UnifiedInbox.get_summary."""

    def test_empty_inbox_summary(self):
        """Summary of an empty inbox should be all zeroes."""
        inbox = UnifiedInbox()
        summary = inbox.get_summary()
        assert summary.total_emails == 0
        assert summary.unread_count == 0
        assert summary.high_priority == 0
        assert summary.by_account == {}
        assert summary.by_classification == {}

    def test_summary_with_multiple_accounts(self):
        """Summary should count emails per account."""
        inbox = UnifiedInbox()
        inbox._emails = [
            InboxEmail(
                message=_make_email(is_read=True),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.2,
                classification="general",
            ),
            InboxEmail(
                message=_make_email(gmail_id="m2", is_read=True),
                account_email="b@test.com",
                account_id=2,
                priority_score=0.3,
                classification="general",
            ),
        ]
        summary = inbox.get_summary()
        assert summary.by_account == {"a@test.com": 1, "b@test.com": 1}
        assert summary.total_emails == 2

    def test_counts_unread_and_high_priority(self):
        """Summary should correctly count unread and high priority emails."""
        inbox = UnifiedInbox()
        inbox._emails = [
            InboxEmail(
                message=_make_email(is_read=False),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.9,
                classification="meeting",
            ),
            InboxEmail(
                message=_make_email(gmail_id="m2", is_read=True),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.3,
                classification="general",
            ),
            InboxEmail(
                message=_make_email(gmail_id="m3", is_read=False),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.7,
                classification="task",
            ),
        ]
        summary = inbox.get_summary()
        assert summary.unread_count == 2
        assert summary.high_priority == 2  # scores >= 0.7

    def test_by_classification_breakdown(self):
        """Summary should tally emails by classification."""
        inbox = UnifiedInbox()
        inbox._emails = [
            InboxEmail(
                message=_make_email(is_read=True),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.1,
                classification="meeting",
            ),
            InboxEmail(
                message=_make_email(gmail_id="m2", is_read=True),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.1,
                classification="meeting",
            ),
            InboxEmail(
                message=_make_email(gmail_id="m3", is_read=True),
                account_email="a@test.com",
                account_id=1,
                priority_score=0.1,
                classification="newsletter",
            ),
        ]
        summary = inbox.get_summary()
        assert summary.by_classification == {"meeting": 2, "newsletter": 1}


# ---------------------------------------------------------------------------
# 7. _score_priority tests
# ---------------------------------------------------------------------------


class TestScorePriority:
    """Tests for UnifiedInbox._score_priority."""

    def test_base_score_generic_email(self):
        """A generic read email should have the base score + direct-to bonus."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="Hello there",
            snippet="Just saying hi",
            is_read=True,
            to_emails=["user@test.com"],
            cc_emails=["other@test.com"],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 only (read, has CC, no keywords)
        assert score == pytest.approx(0.3)

    def test_high_priority_keywords_boost(self):
        """High priority keywords should add 0.3 to the score."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="URGENT: server down",
            is_read=True,
            to_emails=[],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 + high keyword 0.3 = 0.6
        assert score == pytest.approx(0.6)

    def test_medium_priority_keywords(self):
        """Medium priority keywords should add 0.15 to the score."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="Please review this",
            is_read=True,
            to_emails=[],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 + medium keyword 0.15 = 0.45
        assert score == pytest.approx(0.45)

    def test_unread_bonus(self):
        """Unread emails should receive +0.1 bonus."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="Nothing special",
            is_read=False,
            to_emails=[],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 + unread 0.1 = 0.4
        assert score == pytest.approx(0.4)

    def test_direct_to_no_cc_bonus(self):
        """Emails sent directly (to_emails present, no CC) get +0.05."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="Nothing special",
            is_read=True,
            to_emails=["user@test.com"],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 + direct-to 0.05 = 0.35
        assert score == pytest.approx(0.35)

    def test_score_caps_at_one(self):
        """Score should never exceed 1.0 even with all bonuses."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="URGENT please review immediately",
            snippet="action required deadline asap reminder",
            is_read=False,
            to_emails=["user@test.com"],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        assert score <= 1.0

    def test_high_and_medium_combined(self):
        """Both high and medium keywords should contribute."""
        inbox = UnifiedInbox()
        msg = _make_email(
            subject="URGENT please confirm",
            is_read=True,
            to_emails=[],
            cc_emails=[],
        )
        score = inbox._score_priority(msg)
        # Base 0.3 + high 0.3 + medium 0.15 = 0.75
        assert score == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 8. _classify_email tests
# ---------------------------------------------------------------------------


class TestClassifyEmail:
    """Tests for UnifiedInbox._classify_email."""

    def test_meeting_classification(self):
        """Emails with meeting keywords should be classified as meeting."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Meeting invite for Friday")
        assert inbox._classify_email(msg) == "meeting"

    def test_financial_classification(self):
        """Emails with financial keywords should be classified as financial."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Your invoice #12345")
        assert inbox._classify_email(msg) == "financial"

    def test_newsletter_classification(self):
        """Emails with newsletter keywords should be classified as newsletter."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Weekly newsletter digest")
        assert inbox._classify_email(msg) == "newsletter"

    def test_automated_classification(self):
        """Emails with automated keywords should be classified as automated."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Notification alert from system")
        assert inbox._classify_email(msg) == "automated"

    def test_task_classification(self):
        """Emails with task keywords should be classified as task."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="New task assigned to you")
        assert inbox._classify_email(msg) == "task"

    def test_general_default(self):
        """Emails without matching keywords should be classified as general."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Hey, how are you doing?")
        assert inbox._classify_email(msg) == "general"

    def test_classification_uses_snippet(self):
        """Classification should also check the snippet, not just subject."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Hello", snippet="Here is your receipt")
        assert inbox._classify_email(msg) == "financial"

    def test_classification_priority_order(self):
        """Meeting should take precedence over financial if both present."""
        inbox = UnifiedInbox()
        msg = _make_email(subject="Meeting about invoice payment")
        assert inbox._classify_email(msg) == "meeting"


# ---------------------------------------------------------------------------
# 9. clear() tests
# ---------------------------------------------------------------------------


class TestClear:
    """Tests for UnifiedInbox.clear."""

    def test_clear_removes_all_data(self):
        """clear() should empty both _emails and _seen_ids."""
        inbox = UnifiedInbox()
        inbox._emails.append(
            InboxEmail(
                message=_make_email(),
                account_email="a@test.com",
                account_id=1,
            )
        )
        inbox._seen_ids.add("1:msg1")

        inbox.clear()

        assert inbox._emails == []
        assert inbox._seen_ids == set()
        assert inbox.count == 0
