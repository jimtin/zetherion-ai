"""Email digest generation for the Gmail integration.

Produces daily and weekly digest summaries combining email analytics,
calendar events, and pending drafts into formatted reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.analytics import EmailAnalytics, PeriodStats
from zetherion_ai.skills.gmail.conflicts import ConflictDetector
from zetherion_ai.skills.gmail.replies import ReplyDraftStore

log = get_logger("zetherion_ai.skills.gmail.digest")


class DigestType(str):
    """Types of digests."""

    MORNING = "morning"
    EVENING = "evening"
    WEEKLY = "weekly"


@dataclass
class DigestSection:
    """A section in a digest report."""

    title: str
    items: list[str] = field(default_factory=list)
    priority: int = 0  # Higher = more important, shown first

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "items": self.items,
            "priority": self.priority,
        }


@dataclass
class Digest:
    """A formatted digest report."""

    digest_type: str
    generated_at: datetime
    account_email: str
    sections: list[DigestSection] = field(default_factory=list)
    period_stats: PeriodStats | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "digest_type": self.digest_type,
            "generated_at": self.generated_at.isoformat(),
            "account_email": self.account_email,
            "sections": [s.to_dict() for s in self.sections],
            "period_stats": self.period_stats.to_dict() if self.period_stats else None,
        }

    def to_text(self) -> str:
        """Format the digest as a readable text string."""
        lines: list[str] = []
        lines.append(f"--- {self.digest_type.upper()} DIGEST ---")
        lines.append(f"Account: {self.account_email}")
        lines.append(f"Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # Sort sections by priority (descending)
        sorted_sections = sorted(self.sections, key=lambda s: s.priority, reverse=True)

        for section in sorted_sections:
            lines.append(f"## {section.title}")
            if section.items:
                for item in section.items:
                    lines.append(f"  - {item}")
            else:
                lines.append("  (none)")
            lines.append("")

        return "\n".join(lines)


class DigestGenerator:
    """Generates email digest reports.

    Combines data from analytics, pending drafts, and calendar
    to produce formatted daily and weekly summaries.
    """

    def __init__(
        self,
        analytics: EmailAnalytics,
        draft_store: ReplyDraftStore,
        *,
        conflict_detector: ConflictDetector | None = None,
    ) -> None:
        """Initialize the digest generator.

        Args:
            analytics: Email analytics instance.
            draft_store: Reply draft store.
            conflict_detector: Optional calendar conflict detector.
        """
        self._analytics = analytics
        self._draft_store = draft_store
        self._conflict_detector = conflict_detector

    async def generate_morning(
        self,
        account_id: int,
        account_email: str,
    ) -> Digest:
        """Generate a morning briefing digest.

        Includes: unread summary, today's email volume, pending drafts.

        Args:
            account_id: Gmail account ID.
            account_email: Account email address.

        Returns:
            Morning Digest.
        """
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        stats = await self._analytics.get_period_stats(
            account_id,
            period_start=today_start,
            period_end=now,
        )

        digest = Digest(
            digest_type=DigestType.MORNING,
            generated_at=now,
            account_email=account_email,
            period_stats=stats,
        )

        # Unread section
        unread_section = DigestSection(
            title="Unread Emails",
            priority=10,
        )
        unread_section.items.append(f"{stats.unread_count} unread emails")
        if stats.by_classification:
            for cls, count in stats.by_classification.items():
                unread_section.items.append(f"  {cls}: {count}")
        digest.sections.append(unread_section)

        # Today's activity
        activity_section = DigestSection(
            title="Today's Activity",
            priority=5,
        )
        activity_section.items.append(f"{stats.total_received} emails received today")
        digest.sections.append(activity_section)

        # Pending drafts
        pending = await self._draft_store.list_pending(account_id)
        if pending:
            draft_section = DigestSection(
                title="Pending Drafts",
                priority=8,
            )
            draft_section.items.append(f"{len(pending)} drafts awaiting review")
            digest.sections.append(draft_section)

        log.info(
            "morning_digest_generated",
            account=account_email,
            sections=len(digest.sections),
        )

        return digest

    async def generate_evening(
        self,
        account_id: int,
        account_email: str,
    ) -> Digest:
        """Generate an end-of-day summary digest.

        Includes: day's sent count, unresolved threads, draft status.

        Args:
            account_id: Gmail account ID.
            account_email: Account email address.

        Returns:
            Evening Digest.
        """
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        stats = await self._analytics.get_period_stats(
            account_id,
            period_start=today_start,
            period_end=now,
        )

        digest = Digest(
            digest_type=DigestType.EVENING,
            generated_at=now,
            account_email=account_email,
            period_stats=stats,
        )

        # Day summary
        summary_section = DigestSection(
            title="Day Summary",
            priority=10,
        )
        summary_section.items.append(f"{stats.total_received} emails received")
        summary_section.items.append(f"{stats.total_drafts} drafts created")
        summary_section.items.append(f"{stats.unread_count} still unread")
        digest.sections.append(summary_section)

        # Neglected threads
        neglected = await self._analytics.get_neglected_threads(account_id, days_threshold=2)
        if neglected:
            neglect_section = DigestSection(
                title="Needs Attention",
                priority=9,
            )
            for thread in neglected[:5]:
                neglect_section.items.append(
                    f"'{thread['subject']}' from {thread['from_email']} ({thread['days_old']}d ago)"
                )
            digest.sections.append(neglect_section)

        log.info(
            "evening_digest_generated",
            account=account_email,
            sections=len(digest.sections),
        )

        return digest

    async def generate_weekly(
        self,
        account_id: int,
        account_email: str,
    ) -> Digest:
        """Generate a weekly summary digest.

        Includes: volume trends, top contacts, response times,
        neglected threads, classification breakdown.

        Args:
            account_id: Gmail account ID.
            account_email: Account email address.

        Returns:
            Weekly Digest.
        """
        now = datetime.now()
        week_start = now - timedelta(days=7)

        stats = await self._analytics.get_period_stats(
            account_id,
            period_start=week_start,
            period_end=now,
            top_n=10,
        )

        digest = Digest(
            digest_type=DigestType.WEEKLY,
            generated_at=now,
            account_email=account_email,
            period_stats=stats,
        )

        # Volume overview
        volume_section = DigestSection(
            title="Weekly Volume",
            priority=10,
        )
        volume_section.items.append(f"{stats.total_received} emails received this week")
        volume_section.items.append(f"{stats.total_drafts} drafts created")
        digest.sections.append(volume_section)

        # Top senders
        if stats.top_senders:
            senders_section = DigestSection(
                title="Top Contacts",
                priority=7,
            )
            for sender in stats.top_senders[:5]:
                senders_section.items.append(f"{sender.email}: {sender.emails_received} emails")
            digest.sections.append(senders_section)

        # Classification breakdown
        if stats.by_classification:
            cls_section = DigestSection(
                title="Email Categories",
                priority=5,
            )
            for cls, count in sorted(
                stats.by_classification.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                cls_section.items.append(f"{cls}: {count}")
            digest.sections.append(cls_section)

        # Neglected threads
        neglected = await self._analytics.get_neglected_threads(
            account_id, days_threshold=5, limit=5
        )
        if neglected:
            neglect_section = DigestSection(
                title="Neglected Threads",
                priority=8,
            )
            for thread in neglected:
                neglect_section.items.append(
                    f"'{thread['subject']}' from {thread['from_email']} ({thread['days_old']}d ago)"
                )
            digest.sections.append(neglect_section)

        log.info(
            "weekly_digest_generated",
            account=account_email,
            sections=len(digest.sections),
        )

        return digest
