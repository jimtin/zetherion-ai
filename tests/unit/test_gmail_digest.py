"""Unit tests for email digest generation.

Tests the DigestType, DigestSection, Digest dataclasses and the
DigestGenerator async methods (morning, evening, weekly) with mocked
analytics, draft-store, and conflict-detector dependencies.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.gmail.analytics import (
    ContactStats,
    EmailAnalytics,
    PeriodStats,
)
from zetherion_ai.skills.gmail.conflicts import ConflictDetector
from zetherion_ai.skills.gmail.digest import (
    Digest,
    DigestGenerator,
    DigestSection,
    DigestType,
)
from zetherion_ai.skills.gmail.replies import ReplyDraftStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACCOUNT_ID = 1
ACCOUNT_EMAIL = "user@example.com"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_analytics():
    """Mock EmailAnalytics with sensible defaults."""
    analytics = AsyncMock(spec=EmailAnalytics)
    stats = PeriodStats(
        period_start=datetime(2025, 1, 1),
        period_end=datetime(2025, 1, 7),
        total_received=42,
        unread_count=5,
        total_drafts=3,
        by_classification={"meeting": 10, "general": 20},
        top_senders=[
            ContactStats(email="alice@example.com", emails_received=15),
        ],
    )
    analytics.get_period_stats.return_value = stats
    analytics.get_neglected_threads.return_value = []
    return analytics


@pytest.fixture
def mock_draft_store():
    """Mock ReplyDraftStore -- no pending drafts by default."""
    store = AsyncMock(spec=ReplyDraftStore)
    store.list_pending.return_value = []
    return store


@pytest.fixture
def generator(mock_analytics, mock_draft_store):
    """DigestGenerator wired with mocked dependencies."""
    return DigestGenerator(mock_analytics, mock_draft_store)


# ---------------------------------------------------------------------------
# 1. DigestType class attributes
# ---------------------------------------------------------------------------


class TestDigestType:
    def test_morning_value(self):
        assert DigestType.MORNING == "morning"

    def test_evening_value(self):
        assert DigestType.EVENING == "evening"

    def test_weekly_value(self):
        assert DigestType.WEEKLY == "weekly"


# ---------------------------------------------------------------------------
# 2. DigestSection dataclass
# ---------------------------------------------------------------------------


class TestDigestSection:
    def test_defaults(self):
        section = DigestSection(title="Test")
        assert section.title == "Test"
        assert section.items == []
        assert section.priority == 0

    def test_to_dict(self):
        section = DigestSection(title="Inbox", items=["3 unread"], priority=10)
        d = section.to_dict()
        assert d == {
            "title": "Inbox",
            "items": ["3 unread"],
            "priority": 10,
        }


# ---------------------------------------------------------------------------
# 3. Digest dataclass
# ---------------------------------------------------------------------------


class TestDigest:
    def test_to_dict_with_period_stats(self):
        stats = PeriodStats(
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
            total_received=10,
        )
        digest = Digest(
            digest_type="morning",
            generated_at=datetime(2025, 1, 7, 8, 0),
            account_email=ACCOUNT_EMAIL,
            sections=[DigestSection(title="S1", items=["a"], priority=5)],
            period_stats=stats,
        )
        d = digest.to_dict()
        assert d["digest_type"] == "morning"
        assert d["generated_at"] == "2025-01-07T08:00:00"
        assert d["account_email"] == ACCOUNT_EMAIL
        assert len(d["sections"]) == 1
        assert d["sections"][0]["title"] == "S1"
        assert d["period_stats"] is not None
        assert d["period_stats"]["total_received"] == 10

    def test_to_dict_without_period_stats(self):
        digest = Digest(
            digest_type="evening",
            generated_at=datetime(2025, 1, 7, 18, 0),
            account_email=ACCOUNT_EMAIL,
        )
        d = digest.to_dict()
        assert d["period_stats"] is None
        assert d["sections"] == []

    def test_to_text_header_and_formatting(self):
        digest = Digest(
            digest_type="morning",
            generated_at=datetime(2025, 1, 7, 8, 30),
            account_email=ACCOUNT_EMAIL,
            sections=[
                DigestSection(title="Low", items=["item1"], priority=1),
                DigestSection(title="High", items=["item2", "item3"], priority=10),
            ],
        )
        text = digest.to_text()
        # Header
        assert "--- MORNING DIGEST ---" in text
        assert f"Account: {ACCOUNT_EMAIL}" in text
        assert "Generated: 2025-01-07 08:30" in text
        # High-priority section appears before low-priority
        high_pos = text.index("## High")
        low_pos = text.index("## Low")
        assert high_pos < low_pos

    def test_to_text_empty_section_shows_none(self):
        digest = Digest(
            digest_type="weekly",
            generated_at=datetime(2025, 1, 7, 9, 0),
            account_email=ACCOUNT_EMAIL,
            sections=[DigestSection(title="Empty Section", items=[], priority=0)],
        )
        text = digest.to_text()
        assert "(none)" in text


# ---------------------------------------------------------------------------
# 4. DigestGenerator.__init__
# ---------------------------------------------------------------------------


class TestDigestGeneratorInit:
    def test_stores_analytics_and_draft_store(self, mock_analytics, mock_draft_store):
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        assert gen._analytics is mock_analytics
        assert gen._draft_store is mock_draft_store
        assert gen._conflict_detector is None

    def test_optional_conflict_detector(self, mock_analytics, mock_draft_store):
        detector = MagicMock(spec=ConflictDetector)
        gen = DigestGenerator(mock_analytics, mock_draft_store, conflict_detector=detector)
        assert gen._conflict_detector is detector


# ---------------------------------------------------------------------------
# 5. DigestGenerator.generate_morning
# ---------------------------------------------------------------------------


class TestGenerateMorning:
    async def test_returns_morning_type(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        assert digest.digest_type == DigestType.MORNING

    async def test_has_unread_section_with_count(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        unread = _find_section(digest, "Unread Emails")
        assert unread is not None
        assert any("5 unread emails" in i for i in unread.items)

    async def test_has_today_activity_section(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        activity = _find_section(digest, "Today's Activity")
        assert activity is not None
        assert any("42 emails received today" in i for i in activity.items)

    async def test_pending_drafts_section_when_drafts_exist(self, mock_analytics, mock_draft_store):
        mock_draft_store.list_pending.return_value = [
            MagicMock(),
            MagicMock(),
        ]
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        drafts = _find_section(digest, "Pending Drafts")
        assert drafts is not None
        assert any("2 drafts awaiting review" in i for i in drafts.items)

    async def test_no_pending_drafts_section_when_empty(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        drafts = _find_section(digest, "Pending Drafts")
        assert drafts is None

    async def test_classification_breakdown_in_unread_section(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        unread = _find_section(digest, "Unread Emails")
        assert unread is not None
        assert any("meeting: 10" in i for i in unread.items)
        assert any("general: 20" in i for i in unread.items)

    async def test_no_classification_items_when_empty(self, mock_analytics, mock_draft_store):
        stats = mock_analytics.get_period_stats.return_value
        stats.by_classification = {}
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        unread = _find_section(digest, "Unread Emails")
        assert unread is not None
        # Only the count line should be present, no classification entries
        assert len(unread.items) == 1

    async def test_period_stats_attached(self, generator):
        digest = await generator.generate_morning(ACCOUNT_ID, ACCOUNT_EMAIL)
        assert digest.period_stats is not None
        assert digest.period_stats.total_received == 42


# ---------------------------------------------------------------------------
# 6. DigestGenerator.generate_evening
# ---------------------------------------------------------------------------


class TestGenerateEvening:
    async def test_returns_evening_type(self, generator):
        digest = await generator.generate_evening(ACCOUNT_ID, ACCOUNT_EMAIL)
        assert digest.digest_type == DigestType.EVENING

    async def test_day_summary_section_has_all_items(self, generator):
        digest = await generator.generate_evening(ACCOUNT_ID, ACCOUNT_EMAIL)
        summary = _find_section(digest, "Day Summary")
        assert summary is not None
        assert any("42 emails received" in i for i in summary.items)
        assert any("3 drafts created" in i for i in summary.items)
        assert any("5 still unread" in i for i in summary.items)

    async def test_needs_attention_when_neglected_threads(self, mock_analytics, mock_draft_store):
        mock_analytics.get_neglected_threads.return_value = [
            {
                "subject": "Budget review",
                "from_email": "cfo@corp.com",
                "days_old": 3,
            },
        ]
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_evening(ACCOUNT_ID, ACCOUNT_EMAIL)
        needs = _find_section(digest, "Needs Attention")
        assert needs is not None
        assert any("Budget review" in i for i in needs.items)

    async def test_no_needs_attention_when_no_neglected(self, generator):
        digest = await generator.generate_evening(ACCOUNT_ID, ACCOUNT_EMAIL)
        needs = _find_section(digest, "Needs Attention")
        assert needs is None

    async def test_max_five_neglected_threads_shown(self, mock_analytics, mock_draft_store):
        threads = [
            {
                "subject": f"Thread {i}",
                "from_email": f"user{i}@test.com",
                "days_old": i,
            }
            for i in range(8)
        ]
        mock_analytics.get_neglected_threads.return_value = threads
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_evening(ACCOUNT_ID, ACCOUNT_EMAIL)
        needs = _find_section(digest, "Needs Attention")
        assert needs is not None
        assert len(needs.items) == 5


# ---------------------------------------------------------------------------
# 7. DigestGenerator.generate_weekly
# ---------------------------------------------------------------------------


class TestGenerateWeekly:
    async def test_returns_weekly_type(self, generator):
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        assert digest.digest_type == DigestType.WEEKLY

    async def test_has_weekly_volume_section(self, generator):
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        volume = _find_section(digest, "Weekly Volume")
        assert volume is not None
        assert any("42 emails received this week" in i for i in volume.items)
        assert any("3 drafts created" in i for i in volume.items)

    async def test_top_contacts_when_senders_present(self, generator):
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        contacts = _find_section(digest, "Top Contacts")
        assert contacts is not None
        assert any("alice@example.com: 15 emails" in i for i in contacts.items)

    async def test_no_top_contacts_when_no_senders(self, mock_analytics, mock_draft_store):
        stats = mock_analytics.get_period_stats.return_value
        stats.top_senders = []
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        contacts = _find_section(digest, "Top Contacts")
        assert contacts is None

    async def test_email_categories_when_classifications_present(self, generator):
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        cats = _find_section(digest, "Email Categories")
        assert cats is not None
        assert any("general: 20" in i for i in cats.items)
        assert any("meeting: 10" in i for i in cats.items)

    async def test_no_email_categories_when_empty(self, mock_analytics, mock_draft_store):
        stats = mock_analytics.get_period_stats.return_value
        stats.by_classification = {}
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        cats = _find_section(digest, "Email Categories")
        assert cats is None

    async def test_neglected_threads_when_present(self, mock_analytics, mock_draft_store):
        mock_analytics.get_neglected_threads.return_value = [
            {
                "subject": "Old thread",
                "from_email": "bob@test.com",
                "days_old": 6,
            },
        ]
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        neglected = _find_section(digest, "Neglected Threads")
        assert neglected is not None
        assert any("Old thread" in i for i in neglected.items)
        assert any("6d ago" in i for i in neglected.items)

    async def test_no_neglected_threads_when_none(self, generator):
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        neglected = _find_section(digest, "Neglected Threads")
        assert neglected is None

    async def test_email_categories_sorted_descending(self, generator):
        """Categories should be sorted by count descending."""
        digest = await generator.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        cats = _find_section(digest, "Email Categories")
        assert cats is not None
        # "general: 20" should come before "meeting: 10"
        general_idx = next(i for i, item in enumerate(cats.items) if "general" in item)
        meeting_idx = next(i for i, item in enumerate(cats.items) if "meeting" in item)
        assert general_idx < meeting_idx

    async def test_top_contacts_limited_to_five(self, mock_analytics, mock_draft_store):
        stats = mock_analytics.get_period_stats.return_value
        stats.top_senders = [
            ContactStats(email=f"sender{i}@test.com", emails_received=100 - i) for i in range(8)
        ]
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        digest = await gen.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        contacts = _find_section(digest, "Top Contacts")
        assert contacts is not None
        assert len(contacts.items) == 5

    async def test_calls_get_period_stats_with_top_n_10(self, mock_analytics, mock_draft_store):
        gen = DigestGenerator(mock_analytics, mock_draft_store)
        await gen.generate_weekly(ACCOUNT_ID, ACCOUNT_EMAIL)
        call_kwargs = mock_analytics.get_period_stats.call_args
        assert call_kwargs.kwargs.get("top_n") == 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_section(digest: Digest, title: str) -> DigestSection | None:
    """Return the first section matching *title*, or None."""
    for section in digest.sections:
        if section.title == title:
            return section
    return None
