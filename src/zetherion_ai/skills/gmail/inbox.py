"""Unified inbox aggregation across multiple Gmail accounts.

Provides a single view of emails from all connected accounts
with deduplication, sorting, and priority scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.client import EmailMessage, GmailClient, GmailClientError

log = get_logger("zetherion_ai.skills.gmail.inbox")

# Priority keywords for scoring
HIGH_PRIORITY_KEYWORDS = frozenset(
    {
        "urgent",
        "asap",
        "immediately",
        "critical",
        "emergency",
        "deadline",
        "important",
        "action required",
    }
)

MEDIUM_PRIORITY_KEYWORDS = frozenset(
    {
        "please",
        "follow up",
        "reminder",
        "update",
        "review",
        "approval",
        "feedback",
        "confirm",
    }
)


@dataclass
class InboxEmail:
    """An email in the unified inbox with account context."""

    message: EmailMessage
    account_email: str
    account_id: int
    priority_score: float = 0.0
    classification: str = "general"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message": self.message.to_dict(),
            "account_email": self.account_email,
            "account_id": self.account_id,
            "priority_score": self.priority_score,
            "classification": self.classification,
        }


@dataclass
class InboxSummary:
    """Summary of the unified inbox."""

    total_emails: int = 0
    unread_count: int = 0
    high_priority: int = 0
    by_account: dict[str, int] = field(default_factory=dict)
    by_classification: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_emails": self.total_emails,
            "unread_count": self.unread_count,
            "high_priority": self.high_priority,
            "by_account": self.by_account,
            "by_classification": self.by_classification,
        }


class UnifiedInbox:
    """Aggregates emails from multiple Gmail accounts into a single view.

    Handles deduplication (same thread across accounts), priority scoring,
    basic classification, and unified sorting.
    """

    def __init__(self) -> None:
        """Initialize the unified inbox."""
        self._emails: list[InboxEmail] = []
        self._seen_ids: set[str] = set()

    def clear(self) -> None:
        """Clear the inbox cache."""
        self._emails.clear()
        self._seen_ids.clear()

    async def fetch_from_account(
        self,
        client: GmailClient,
        account_email: str,
        account_id: int,
        *,
        query: str = "is:inbox",
        max_results: int = 20,
    ) -> list[InboxEmail]:
        """Fetch emails from a single account and add to the inbox.

        Args:
            client: GmailClient bound to the account's access token.
            account_email: The account's email address.
            account_id: The account's DB ID.
            query: Gmail search query.
            max_results: Maximum messages to fetch.

        Returns:
            List of newly added InboxEmails.
        """
        new_emails: list[InboxEmail] = []

        try:
            stubs, _ = await client.list_messages(query=query, max_results=max_results)

            for stub in stubs:
                msg_id = stub.get("id", "")
                # Dedup key: account_id + gmail_id
                dedup_key = f"{account_id}:{msg_id}"
                if dedup_key in self._seen_ids:
                    continue

                try:
                    message = await client.get_message(msg_id)
                    inbox_email = InboxEmail(
                        message=message,
                        account_email=account_email,
                        account_id=account_id,
                    )
                    inbox_email.priority_score = self._score_priority(message)
                    inbox_email.classification = self._classify_email(message)

                    self._emails.append(inbox_email)
                    self._seen_ids.add(dedup_key)
                    new_emails.append(inbox_email)
                except GmailClientError as exc:
                    log.warning(
                        "failed_to_fetch_message",
                        message_id=msg_id,
                        account=account_email,
                        error=str(exc),
                    )

        except GmailClientError as exc:
            log.error(
                "failed_to_list_messages",
                account=account_email,
                error=str(exc),
            )

        log.info(
            "inbox_fetched",
            account=account_email,
            new_count=len(new_emails),
            total=len(self._emails),
        )
        return new_emails

    def get_emails(
        self,
        *,
        unread_only: bool = False,
        account_email: str | None = None,
        classification: str | None = None,
        min_priority: float = 0.0,
        sort_by: str = "date",
        limit: int = 50,
    ) -> list[InboxEmail]:
        """Get emails from the unified inbox with filters.

        Args:
            unread_only: Only return unread emails.
            account_email: Filter by account.
            classification: Filter by classification.
            min_priority: Minimum priority score.
            sort_by: Sort field ("date", "priority").
            limit: Maximum emails to return.

        Returns:
            Filtered and sorted list of InboxEmails.
        """
        filtered = self._emails

        if unread_only:
            filtered = [e for e in filtered if not e.message.is_read]

        if account_email:
            filtered = [e for e in filtered if e.account_email == account_email]

        if classification:
            filtered = [e for e in filtered if e.classification == classification]

        if min_priority > 0:
            filtered = [e for e in filtered if e.priority_score >= min_priority]

        # Sort
        if sort_by == "priority":
            filtered.sort(key=lambda e: e.priority_score, reverse=True)
        else:
            # Default: sort by date (newest first)
            filtered.sort(
                key=lambda e: e.message.received_at or datetime.min,
                reverse=True,
            )

        return filtered[:limit]

    def get_summary(self) -> InboxSummary:
        """Get a summary of the unified inbox."""
        summary = InboxSummary(total_emails=len(self._emails))

        for email_item in self._emails:
            if not email_item.message.is_read:
                summary.unread_count += 1

            if email_item.priority_score >= 0.7:
                summary.high_priority += 1

            acct = email_item.account_email
            summary.by_account[acct] = summary.by_account.get(acct, 0) + 1

            cls = email_item.classification
            summary.by_classification[cls] = summary.by_classification.get(cls, 0) + 1

        return summary

    @property
    def count(self) -> int:
        """Total number of emails in the inbox."""
        return len(self._emails)

    # ------------------------------------------------------------------
    # Scoring and classification
    # ------------------------------------------------------------------

    def _score_priority(self, message: EmailMessage) -> float:
        """Score email priority based on content signals.

        Returns:
            Priority score from 0.0 (low) to 1.0 (high).
        """
        score = 0.3  # Base score

        text = f"{message.subject} {message.snippet}".lower()

        # High priority keywords
        for keyword in HIGH_PRIORITY_KEYWORDS:
            if keyword in text:
                score += 0.3
                break

        # Medium priority keywords
        for keyword in MEDIUM_PRIORITY_KEYWORDS:
            if keyword in text:
                score += 0.15
                break

        # Direct "to" (not CC) is slightly higher priority
        if message.to_emails and not message.cc_emails:
            score += 0.05

        # Unread bonus
        if not message.is_read:
            score += 0.1

        return min(1.0, score)

    def _classify_email(self, message: EmailMessage) -> str:
        """Classify an email into a basic category.

        Returns:
            Classification string.
        """
        text = f"{message.subject} {message.snippet}".lower()

        if any(w in text for w in ["meeting", "invite", "calendar", "rsvp", "agenda"]):
            return "meeting"

        if any(w in text for w in ["invoice", "payment", "receipt", "order", "subscription"]):
            return "financial"

        if any(w in text for w in ["newsletter", "unsubscribe", "digest", "weekly"]):
            return "newsletter"

        if any(w in text for w in ["noreply", "no-reply", "notification", "alert", "automated"]):
            return "automated"

        if any(w in text for w in ["task", "todo", "action item", "assigned", "due"]):
            return "task"

        return "general"
