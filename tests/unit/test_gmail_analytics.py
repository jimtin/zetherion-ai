"""Tests for Gmail email analytics module."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.gmail.analytics import (
    ContactStats,
    EmailAnalytics,
    PeriodStats,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg connection pool and its connection.

    Returns a (pool, conn) tuple so tests can configure conn.fetchrow /
    conn.fetch return values.
    """
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.fixture
def analytics(mock_pool):
    """Create an EmailAnalytics instance backed by the mock pool."""
    pool, _conn = mock_pool
    return EmailAnalytics(pool)


# ===================================================================
# 1. ContactStats dataclass tests
# ===================================================================


class TestContactStats:
    """Tests for the ContactStats dataclass."""

    def test_defaults(self):
        cs = ContactStats(email="alice@example.com")
        assert cs.email == "alice@example.com"
        assert cs.emails_received == 0
        assert cs.emails_sent == 0
        assert cs.avg_response_time_hours == 0.0
        assert cs.last_interaction is None
        assert cs.relationship_score == 0.0

    def test_to_dict_without_last_interaction(self):
        cs = ContactStats(email="bob@example.com")
        d = cs.to_dict()
        assert d == {
            "email": "bob@example.com",
            "emails_received": 0,
            "emails_sent": 0,
            "avg_response_time_hours": 0.0,
            "last_interaction": None,
            "relationship_score": 0.0,
        }

    def test_to_dict_with_last_interaction(self):
        dt = datetime(2025, 6, 15, 12, 30, 0)
        cs = ContactStats(
            email="carol@example.com",
            emails_received=10,
            emails_sent=5,
            avg_response_time_hours=2.567,
            last_interaction=dt,
            relationship_score=0.78912,
        )
        d = cs.to_dict()
        assert d["email"] == "carol@example.com"
        assert d["emails_received"] == 10
        assert d["emails_sent"] == 5
        assert d["avg_response_time_hours"] == 2.57  # rounded to 2 decimals
        assert d["last_interaction"] == dt.isoformat()
        assert d["relationship_score"] == 0.7891  # rounded to 4 decimals


# ===================================================================
# 2. PeriodStats dataclass tests
# ===================================================================


class TestPeriodStats:
    """Tests for the PeriodStats dataclass."""

    def test_defaults(self):
        start = datetime(2025, 1, 1)
        end = datetime(2025, 1, 7)
        ps = PeriodStats(period_start=start, period_end=end)
        assert ps.total_received == 0
        assert ps.total_sent == 0
        assert ps.total_drafts == 0
        assert ps.avg_response_time_hours == 0.0
        assert ps.top_senders == []
        assert ps.unread_count == 0
        assert ps.by_classification == {}

    def test_to_dict_with_top_senders_and_classification(self):
        start = datetime(2025, 3, 1)
        end = datetime(2025, 3, 7)
        sender = ContactStats(email="s@example.com", emails_received=5)
        ps = PeriodStats(
            period_start=start,
            period_end=end,
            total_received=20,
            total_sent=10,
            total_drafts=3,
            avg_response_time_hours=1.234,
            top_senders=[sender],
            unread_count=4,
            by_classification={"important": 5, "spam": 2},
        )
        d = ps.to_dict()
        assert d["period_start"] == start.isoformat()
        assert d["period_end"] == end.isoformat()
        assert d["total_received"] == 20
        assert d["total_sent"] == 10
        assert d["total_drafts"] == 3
        assert d["avg_response_time_hours"] == 1.23
        assert len(d["top_senders"]) == 1
        assert d["top_senders"][0]["email"] == "s@example.com"
        assert d["unread_count"] == 4
        assert d["by_classification"] == {"important": 5, "spam": 2}

    def test_to_dict_empty_senders_and_classification(self):
        start = datetime(2025, 5, 1)
        end = datetime(2025, 5, 8)
        ps = PeriodStats(period_start=start, period_end=end)
        d = ps.to_dict()
        assert d["top_senders"] == []
        assert d["by_classification"] == {}


# ===================================================================
# 3. get_period_stats tests
# ===================================================================


class TestGetPeriodStats:
    """Tests for EmailAnalytics.get_period_stats."""

    async def test_default_dates(self, mock_pool):
        """When no dates are provided, defaults to last 7 days."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value={"cnt": 0})
        conn.fetch = AsyncMock(return_value=[])

        analytics = EmailAnalytics(pool)
        before = datetime.now()
        stats = await analytics.get_period_stats(account_id=1)
        after = datetime.now()

        # period_end should be approximately now
        assert before <= stats.period_end <= after
        # period_start should be approximately 7 days ago
        expected_start = before - timedelta(days=7)
        assert abs((stats.period_start - expected_start).total_seconds()) < 2

    async def test_custom_dates(self, mock_pool):
        """Explicit start/end dates are used when provided."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value={"cnt": 0})
        conn.fetch = AsyncMock(return_value=[])

        analytics = EmailAnalytics(pool)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 1, 31)
        stats = await analytics.get_period_stats(account_id=1, period_start=start, period_end=end)
        assert stats.period_start == start
        assert stats.period_end == end

    async def test_with_data(self, mock_pool):
        """All fields are populated from DB rows."""
        pool, conn = mock_pool

        # Simulate fetchrow calls in order: total_received, unread, drafts
        fetchrow_returns = iter(
            [
                {"cnt": 42},  # total_received
                {"cnt": 10},  # unread_count
                {"cnt": 3},  # total_drafts
            ]
        )
        conn.fetchrow = AsyncMock(side_effect=lambda *a, **kw: next(fetchrow_returns))

        # Simulate fetch calls in order: by_classification, top_senders
        fetch_returns = iter(
            [
                [  # by_classification
                    {"classification": "important", "cnt": 15},
                    {"classification": "spam", "cnt": 5},
                ],
                [  # top_senders
                    {"from_email": "alice@test.com", "cnt": 20},
                    {"from_email": "bob@test.com", "cnt": 12},
                ],
            ]
        )
        conn.fetch = AsyncMock(side_effect=lambda *a, **kw: next(fetch_returns))

        analytics = EmailAnalytics(pool)
        start = datetime(2025, 2, 1)
        end = datetime(2025, 2, 7)
        stats = await analytics.get_period_stats(
            account_id=1, period_start=start, period_end=end, top_n=2
        )

        assert stats.total_received == 42
        assert stats.unread_count == 10
        assert stats.total_drafts == 3
        assert stats.by_classification == {"important": 15, "spam": 5}
        assert len(stats.top_senders) == 2
        assert stats.top_senders[0].email == "alice@test.com"
        assert stats.top_senders[0].emails_received == 20
        assert stats.top_senders[1].email == "bob@test.com"

    async def test_empty_data_none_rows(self, mock_pool):
        """When fetchrow returns None, counts default to 0."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_period_stats(
            account_id=1,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )
        assert stats.total_received == 0
        assert stats.unread_count == 0
        assert stats.total_drafts == 0
        assert stats.top_senders == []
        assert stats.by_classification == {}

    async def test_classification_filters_null_values(self, mock_pool):
        """Null classification rows are excluded from by_classification."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value={"cnt": 0})

        fetch_returns = iter(
            [
                [  # by_classification — includes a None classification
                    {"classification": "important", "cnt": 5},
                    {"classification": None, "cnt": 3},
                    {"classification": "promotions", "cnt": 2},
                ],
                [],  # top_senders (empty)
            ]
        )
        conn.fetch = AsyncMock(side_effect=lambda *a, **kw: next(fetch_returns))

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_period_stats(
            account_id=1,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )
        assert stats.by_classification == {"important": 5, "promotions": 2}
        assert None not in stats.by_classification

    async def test_top_senders_filters_null_from_email(self, mock_pool):
        """Rows with null from_email are excluded from top_senders."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value={"cnt": 0})

        fetch_returns = iter(
            [
                [],  # by_classification
                [  # top_senders — includes a None from_email
                    {"from_email": "valid@test.com", "cnt": 10},
                    {"from_email": None, "cnt": 5},
                ],
            ]
        )
        conn.fetch = AsyncMock(side_effect=lambda *a, **kw: next(fetch_returns))

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_period_stats(
            account_id=1,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )
        assert len(stats.top_senders) == 1
        assert stats.top_senders[0].email == "valid@test.com"


# ===================================================================
# 4. get_contact_stats tests
# ===================================================================


class TestGetContactStats:
    """Tests for EmailAnalytics.get_contact_stats."""

    async def test_with_data(self, mock_pool):
        """Returns populated ContactStats when DB has matching rows."""
        pool, conn = mock_pool
        last_at = datetime(2025, 6, 1, 14, 0, 0)
        conn.fetchrow = AsyncMock(return_value={"cnt": 25, "last_at": last_at})

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_contact_stats(1, "alice@test.com")

        assert stats.email == "alice@test.com"
        assert stats.emails_received == 25
        assert stats.last_interaction == last_at
        # Relationship score should be > 0 since there's volume and recency
        assert stats.relationship_score > 0

    async def test_with_empty_row(self, mock_pool):
        """Row exists but with zero count and no last_at."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value={"cnt": 0, "last_at": None})

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_contact_stats(1, "nobody@test.com")

        assert stats.email == "nobody@test.com"
        assert stats.emails_received == 0
        assert stats.last_interaction is None
        assert stats.relationship_score == 0.0

    async def test_row_is_none(self, mock_pool):
        """When fetchrow returns None, stats keep defaults."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value=None)

        analytics = EmailAnalytics(pool)
        stats = await analytics.get_contact_stats(1, "ghost@test.com")

        assert stats.email == "ghost@test.com"
        assert stats.emails_received == 0
        assert stats.last_interaction is None
        assert stats.relationship_score == 0.0


# ===================================================================
# 5. get_neglected_threads tests
# ===================================================================


class TestGetNeglectedThreads:
    """Tests for EmailAnalytics.get_neglected_threads."""

    async def test_with_threads(self, mock_pool):
        """Returns formatted thread dicts with days_old computed."""
        pool, conn = mock_pool
        old_date = datetime.now() - timedelta(days=5)
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "thread_id": "t1",
                    "subject": "Urgent request",
                    "from_email": "boss@work.com",
                    "received_at": old_date,
                },
            ]
        )

        analytics = EmailAnalytics(pool)
        threads = await analytics.get_neglected_threads(1, days_threshold=3, limit=5)

        assert len(threads) == 1
        assert threads[0]["thread_id"] == "t1"
        assert threads[0]["subject"] == "Urgent request"
        assert threads[0]["from_email"] == "boss@work.com"
        assert threads[0]["received_at"] == old_date.isoformat()
        assert threads[0]["days_old"] >= 4  # at least 4 full days

    async def test_empty_result(self, mock_pool):
        """Returns empty list when no neglected threads exist."""
        pool, conn = mock_pool
        conn.fetch = AsyncMock(return_value=[])

        analytics = EmailAnalytics(pool)
        threads = await analytics.get_neglected_threads(1)
        assert threads == []

    async def test_with_none_received_at(self, mock_pool):
        """Handles rows where received_at is None."""
        pool, conn = mock_pool
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "thread_id": "t2",
                    "subject": "No date",
                    "from_email": "unknown@test.com",
                    "received_at": None,
                },
            ]
        )

        analytics = EmailAnalytics(pool)
        threads = await analytics.get_neglected_threads(1)

        assert len(threads) == 1
        assert threads[0]["received_at"] is None
        assert threads[0]["days_old"] == 0


# ===================================================================
# 6. _compute_relationship_score tests
# ===================================================================


class TestComputeRelationshipScore:
    """Tests for EmailAnalytics._compute_relationship_score."""

    def _make_analytics(self):
        """Create an analytics instance with a dummy pool."""
        return EmailAnalytics(pool=MagicMock())

    def test_zero_interactions(self):
        """No emails, no interaction, no response time -> score 0."""
        analytics = self._make_analytics()
        stats = ContactStats(email="zero@test.com")
        score = analytics._compute_relationship_score(stats)
        assert score == 0.0

    def test_recent_interaction_within_1_day(self):
        """Interaction within 1 day adds 0.3 recency bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="recent@test.com",
            last_interaction=datetime.now() - timedelta(hours=12),
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.3

    def test_interaction_within_7_days(self):
        """Interaction within 7 days adds 0.2 recency bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="week@test.com",
            last_interaction=datetime.now() - timedelta(days=3),
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.2

    def test_interaction_within_30_days(self):
        """Interaction within 30 days adds 0.1 recency bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="month@test.com",
            last_interaction=datetime.now() - timedelta(days=15),
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.1

    def test_interaction_beyond_30_days(self):
        """Interaction older than 30 days adds no recency bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="old@test.com",
            last_interaction=datetime.now() - timedelta(days=60),
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.0

    def test_fast_response_time_lte_1_hour(self):
        """Response time <= 1 hour adds 0.2 response bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="fast@test.com",
            avg_response_time_hours=0.5,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.2

    def test_medium_response_time_lte_4_hours(self):
        """Response time <= 4 hours adds 0.1 response bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="medium@test.com",
            avg_response_time_hours=3.0,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.1

    def test_slow_response_time_gt_4_hours(self):
        """Response time > 4 hours adds no response bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="slow@test.com",
            avg_response_time_hours=10.0,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.0

    def test_no_response_time(self):
        """avg_response_time_hours == 0 adds no response bonus."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="none@test.com",
            avg_response_time_hours=0.0,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.0

    def test_high_volume_log_scale(self):
        """High volume uses logarithmic scaling, capped at 0.4."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="active@test.com",
            emails_received=500,
            emails_sent=500,
        )
        score = analytics._compute_relationship_score(stats)
        expected_volume = min(0.4, math.log1p(1000) / 10)
        assert abs(score - expected_volume) < 1e-6

    def test_moderate_volume(self):
        """Moderate volume gets partial volume score."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="mod@test.com",
            emails_received=5,
            emails_sent=0,
        )
        score = analytics._compute_relationship_score(stats)
        expected = min(0.4, math.log1p(5) / 10)
        assert abs(score - expected) < 1e-6

    def test_score_capped_at_1(self):
        """Combined score never exceeds 1.0."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="max@test.com",
            emails_received=10000,
            emails_sent=10000,
            avg_response_time_hours=0.5,
            last_interaction=datetime.now() - timedelta(hours=1),
        )
        score = analytics._compute_relationship_score(stats)
        assert score <= 1.0
        # With volume (0.4) + recency (0.3) + response (0.2) = 0.9, should be 0.9
        expected_volume = min(0.4, math.log1p(20000) / 10)
        expected_total = min(1.0, expected_volume + 0.3 + 0.2)
        assert abs(score - expected_total) < 1e-6

    def test_all_factors_combined(self):
        """Volume + recency + response combined correctly."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="combo@test.com",
            emails_received=10,
            emails_sent=10,
            avg_response_time_hours=2.5,  # <= 4 -> +0.1
            last_interaction=datetime.now() - timedelta(days=5),  # <= 7 -> +0.2
        )
        score = analytics._compute_relationship_score(stats)
        expected_volume = min(0.4, math.log1p(20) / 10)
        expected = expected_volume + 0.2 + 0.1
        assert abs(score - expected) < 1e-6

    def test_response_time_exactly_1_hour(self):
        """Response time exactly 1 hour gets the fast bonus (0.2)."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="exact1@test.com",
            avg_response_time_hours=1.0,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.2

    def test_response_time_exactly_4_hours(self):
        """Response time exactly 4 hours gets the medium bonus (0.1)."""
        analytics = self._make_analytics()
        stats = ContactStats(
            email="exact4@test.com",
            avg_response_time_hours=4.0,
        )
        score = analytics._compute_relationship_score(stats)
        assert score == 0.1
