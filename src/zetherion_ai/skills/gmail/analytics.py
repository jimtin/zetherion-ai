"""Email analytics for the Gmail integration.

Tracks response times, email volumes, relationship scoring,
and generates trend data for weekly digests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.gmail.analytics")


@dataclass
class ContactStats:
    """Statistics for a single contact."""

    email: str
    emails_received: int = 0
    emails_sent: int = 0
    avg_response_time_hours: float = 0.0
    last_interaction: datetime | None = None
    relationship_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "email": self.email,
            "emails_received": self.emails_received,
            "emails_sent": self.emails_sent,
            "avg_response_time_hours": round(self.avg_response_time_hours, 2),
            "last_interaction": (
                self.last_interaction.isoformat() if self.last_interaction else None
            ),
            "relationship_score": round(self.relationship_score, 4),
        }


@dataclass
class PeriodStats:
    """Aggregated stats for a time period."""

    period_start: datetime
    period_end: datetime
    total_received: int = 0
    total_sent: int = 0
    total_drafts: int = 0
    avg_response_time_hours: float = 0.0
    top_senders: list[ContactStats] = field(default_factory=list)
    unread_count: int = 0
    by_classification: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_received": self.total_received,
            "total_sent": self.total_sent,
            "total_drafts": self.total_drafts,
            "avg_response_time_hours": round(self.avg_response_time_hours, 2),
            "top_senders": [s.to_dict() for s in self.top_senders],
            "unread_count": self.unread_count,
            "by_classification": self.by_classification,
        }


class EmailAnalytics:
    """Computes email analytics from the Gmail database.

    Queries the gmail_emails table to generate response time metrics,
    volume trends, and per-contact relationship scores.
    """

    def __init__(self, pool: Any) -> None:
        """Initialize with an asyncpg connection pool."""
        self._pool = pool

    async def get_period_stats(
        self,
        account_id: int,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        top_n: int = 5,
    ) -> PeriodStats:
        """Get aggregated stats for a time period.

        Args:
            account_id: The Gmail account ID.
            period_start: Start of period (default: 7 days ago).
            period_end: End of period (default: now).
            top_n: Number of top senders to include.

        Returns:
            PeriodStats for the time range.
        """
        now = datetime.now()
        if period_end is None:
            period_end = now
        if period_start is None:
            period_start = now - timedelta(days=7)

        stats = PeriodStats(period_start=period_start, period_end=period_end)

        async with self._pool.acquire() as conn:
            # Total received
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt FROM gmail_emails
                WHERE account_id = $1
                  AND received_at >= $2 AND received_at <= $3
                """,
                account_id,
                period_start,
                period_end,
            )
            stats.total_received = row["cnt"] if row else 0

            # Unread count
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt FROM gmail_emails
                WHERE account_id = $1
                  AND received_at >= $2 AND received_at <= $3
                  AND NOT is_read
                """,
                account_id,
                period_start,
                period_end,
            )
            stats.unread_count = row["cnt"] if row else 0

            # By classification
            rows = await conn.fetch(
                """
                SELECT classification, COUNT(*) as cnt FROM gmail_emails
                WHERE account_id = $1
                  AND received_at >= $2 AND received_at <= $3
                GROUP BY classification
                """,
                account_id,
                period_start,
                period_end,
            )
            stats.by_classification = {
                r["classification"]: r["cnt"] for r in rows if r["classification"]
            }

            # Top senders
            sender_rows = await conn.fetch(
                """
                SELECT from_email, COUNT(*) as cnt
                FROM gmail_emails
                WHERE account_id = $1
                  AND received_at >= $2 AND received_at <= $3
                GROUP BY from_email
                ORDER BY cnt DESC
                LIMIT $4
                """,
                account_id,
                period_start,
                period_end,
                top_n,
            )
            stats.top_senders = [
                ContactStats(
                    email=r["from_email"],
                    emails_received=r["cnt"],
                )
                for r in sender_rows
                if r["from_email"]
            ]

            # Draft count
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt FROM gmail_drafts
                WHERE account_id = $1
                  AND created_at >= $2 AND created_at <= $3
                """,
                account_id,
                period_start,
                period_end,
            )
            stats.total_drafts = row["cnt"] if row else 0

        log.info(
            "period_stats_computed",
            account_id=account_id,
            received=stats.total_received,
            unread=stats.unread_count,
        )

        return stats

    async def get_contact_stats(
        self,
        account_id: int,
        contact_email: str,
    ) -> ContactStats:
        """Get detailed stats for a specific contact.

        Args:
            account_id: The Gmail account ID.
            contact_email: The contact's email address.

        Returns:
            ContactStats for the contact.
        """
        stats = ContactStats(email=contact_email)

        async with self._pool.acquire() as conn:
            # Emails received from this contact
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as cnt, MAX(received_at) as last_at
                FROM gmail_emails
                WHERE account_id = $1 AND from_email = $2
                """,
                account_id,
                contact_email,
            )
            if row:
                stats.emails_received = row["cnt"]
                stats.last_interaction = row["last_at"]

        stats.relationship_score = self._compute_relationship_score(stats)

        log.debug(
            "contact_stats_computed",
            contact=contact_email,
            received=stats.emails_received,
            score=stats.relationship_score,
        )

        return stats

    async def get_neglected_threads(
        self,
        account_id: int,
        *,
        days_threshold: int = 3,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find email threads that haven't been responded to.

        Args:
            account_id: The Gmail account ID.
            days_threshold: Number of days without response.
            limit: Maximum threads to return.

        Returns:
            List of neglected thread info dicts.
        """
        cutoff = datetime.now() - timedelta(days=days_threshold)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT thread_id, subject, from_email, received_at
                FROM gmail_emails
                WHERE account_id = $1
                  AND received_at <= $2
                  AND NOT is_read
                ORDER BY received_at ASC
                LIMIT $3
                """,
                account_id,
                cutoff,
                limit,
            )
            return [
                {
                    "thread_id": r["thread_id"],
                    "subject": r["subject"],
                    "from_email": r["from_email"],
                    "received_at": (r["received_at"].isoformat() if r["received_at"] else None),
                    "days_old": (
                        (datetime.now() - r["received_at"]).days if r["received_at"] else 0
                    ),
                }
                for r in rows
            ]

    def _compute_relationship_score(self, stats: ContactStats) -> float:
        """Compute a relationship score based on interaction patterns.

        Score is 0.0-1.0, higher means more important contact.
        """
        score = 0.0

        # Volume factor (logarithmic scale)
        total = stats.emails_received + stats.emails_sent
        if total > 0:
            import math

            score += min(0.4, math.log1p(total) / 10)

        # Recency factor
        if stats.last_interaction:
            days_ago = (datetime.now() - stats.last_interaction).days
            if days_ago <= 1:
                score += 0.3
            elif days_ago <= 7:
                score += 0.2
            elif days_ago <= 30:
                score += 0.1

        # Response time factor (faster = higher score)
        if stats.avg_response_time_hours > 0:
            if stats.avg_response_time_hours <= 1:
                score += 0.2
            elif stats.avg_response_time_hours <= 4:
                score += 0.1

        return min(1.0, score)
