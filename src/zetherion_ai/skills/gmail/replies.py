"""AI-powered email reply generation.

Uses the InferenceBroker to draft replies, with confidence scoring
and category classification. Integrates with the DecisionContext
for personalized, context-aware responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.client import EmailMessage

log = get_logger("zetherion_ai.skills.gmail.replies")


class ReplyType(StrEnum):
    """Categories of email replies with different trust ceilings."""

    ACKNOWLEDGMENT = "acknowledgment"
    MEETING_CONFIRM = "meeting_confirm"
    MEETING_DECLINE = "meeting_decline"
    INFO_REQUEST = "info_request"
    TASK_UPDATE = "task_update"
    NEGOTIATION = "negotiation"
    SENSITIVE = "sensitive"
    GENERAL = "general"


# Maximum trust ceiling per reply type (higher = more automatable)
TRUST_CEILINGS: dict[ReplyType, float] = {
    ReplyType.ACKNOWLEDGMENT: 0.95,
    ReplyType.MEETING_CONFIRM: 0.90,
    ReplyType.MEETING_DECLINE: 0.80,
    ReplyType.INFO_REQUEST: 0.75,
    ReplyType.TASK_UPDATE: 0.70,
    ReplyType.NEGOTIATION: 0.50,
    ReplyType.SENSITIVE: 0.30,
    ReplyType.GENERAL: 0.60,
}


class DraftStatus(StrEnum):
    """Status of a reply draft."""

    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    SENT = "sent"


@dataclass
class ReplyDraft:
    """A generated reply draft awaiting review or sending."""

    email_id: int
    account_id: int
    draft_text: str
    reply_type: ReplyType
    confidence: float
    status: DraftStatus = DraftStatus.PENDING
    draft_id: int | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "draft_id": self.draft_id,
            "email_id": self.email_id,
            "account_id": self.account_id,
            "draft_text": self.draft_text,
            "reply_type": self.reply_type.value,
            "confidence": self.confidence,
            "status": self.status.value,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReplyClassifier:
    """Classifies emails into reply types based on content analysis."""

    # Keyword groups for classification
    _ACKNOWLEDGMENT_KEYWORDS = frozenset(
        {
            "thank",
            "thanks",
            "received",
            "noted",
            "got it",
            "acknowledged",
            "confirm receipt",
        }
    )
    _MEETING_KEYWORDS = frozenset(
        {
            "meeting",
            "invite",
            "calendar",
            "schedule",
            "rsvp",
            "attend",
            "join",
            "conference",
            "call",
        }
    )
    _DECLINE_SIGNALS = frozenset(
        {
            "cannot",
            "can't",
            "unable",
            "conflict",
            "reschedule",
            "decline",
            "regret",
        }
    )
    _INFO_REQUEST_KEYWORDS = frozenset(
        {
            "question",
            "asking",
            "could you",
            "can you",
            "please provide",
            "need information",
            "clarify",
            "wondering",
            "inquiry",
        }
    )
    _TASK_KEYWORDS = frozenset(
        {
            "task",
            "todo",
            "action item",
            "assigned",
            "due",
            "complete",
            "status update",
            "progress",
        }
    )
    _NEGOTIATION_KEYWORDS = frozenset(
        {
            "proposal",
            "offer",
            "negotiate",
            "counter",
            "terms",
            "contract",
            "pricing",
            "deal",
            "bid",
        }
    )
    _SENSITIVE_KEYWORDS = frozenset(
        {
            "confidential",
            "private",
            "personal",
            "salary",
            "termination",
            "legal",
            "complaint",
            "grievance",
            "harassment",
            "disciplinary",
        }
    )

    def classify(self, message: EmailMessage) -> ReplyType:
        """Classify an email into a reply type.

        Args:
            message: The email to classify.

        Returns:
            The detected ReplyType.
        """
        text = f"{message.subject} {message.snippet} {message.body_text}".lower()

        # Check from most to least specific
        if self._matches(text, self._SENSITIVE_KEYWORDS):
            return ReplyType.SENSITIVE

        if self._matches(text, self._NEGOTIATION_KEYWORDS):
            return ReplyType.NEGOTIATION

        if self._matches(text, self._MEETING_KEYWORDS):
            if self._matches(text, self._DECLINE_SIGNALS):
                return ReplyType.MEETING_DECLINE
            return ReplyType.MEETING_CONFIRM

        if self._matches(text, self._INFO_REQUEST_KEYWORDS):
            return ReplyType.INFO_REQUEST

        if self._matches(text, self._TASK_KEYWORDS):
            return ReplyType.TASK_UPDATE

        if self._matches(text, self._ACKNOWLEDGMENT_KEYWORDS):
            return ReplyType.ACKNOWLEDGMENT

        return ReplyType.GENERAL

    def _matches(self, text: str, keywords: frozenset[str]) -> bool:
        """Check if any keyword appears in the text."""
        return any(kw in text for kw in keywords)


class ReplyGenerator:
    """Generates reply drafts using the InferenceBroker.

    Combines email content, conversation thread context, and the user's
    DecisionContext to produce personalized reply drafts.
    """

    # System prompt template for reply generation
    SYSTEM_PROMPT = (
        "You are drafting an email reply on behalf of {user_name}. "
        "Match their communication style: {style}. "
        "Keep the reply concise and professional unless the context "
        "suggests otherwise. Do not include a subject line. "
        "Reply type: {reply_type}."
    )

    # User prompt template
    USER_PROMPT = (
        "Original email from {from_email}:\n"
        "Subject: {subject}\n"
        "---\n{body}\n---\n\n"
        "Draft a {reply_type} reply."
    )

    def __init__(self, inference_broker: Any) -> None:
        """Initialize the reply generator.

        Args:
            inference_broker: InferenceBroker instance for LLM calls.
        """
        self._broker = inference_broker

    async def generate(
        self,
        message: EmailMessage,
        reply_type: ReplyType,
        *,
        user_name: str = "the user",
        communication_style: str = "balanced",
        additional_context: str = "",
    ) -> ReplyDraft:
        """Generate a reply draft for an email.

        Args:
            message: The email to reply to.
            reply_type: Type of reply to generate.
            user_name: The user's display name.
            communication_style: The user's preferred style.
            additional_context: Extra context for the LLM.

        Returns:
            A ReplyDraft with generated text and confidence.
        """
        system = self.SYSTEM_PROMPT.format(
            user_name=user_name,
            style=communication_style,
            reply_type=reply_type.value,
        )

        body = message.body_text or message.snippet or "(no body)"
        user_prompt = self.USER_PROMPT.format(
            from_email=message.from_email,
            subject=message.subject,
            body=body[:2000],  # Truncate long emails
            reply_type=reply_type.value,
        )

        if additional_context:
            user_prompt += f"\n\nAdditional context: {additional_context}"

        # Use InferenceBroker to generate the draft
        # Import here to avoid circular imports at module level
        from zetherion_ai.agent.providers import TaskType

        result = await self._broker.infer(
            prompt=user_prompt,
            task_type=TaskType.CONVERSATION,
            system_prompt=system,
            temperature=0.5,
        )

        # Score confidence based on reply type and generation quality
        confidence = self._score_confidence(reply_type, result.content)

        draft = ReplyDraft(
            email_id=0,  # Caller sets this
            account_id=0,  # Caller sets this
            draft_text=result.content,
            reply_type=reply_type,
            confidence=confidence,
        )

        log.info(
            "reply_generated",
            reply_type=reply_type.value,
            confidence=confidence,
            length=len(result.content),
        )

        return draft

    def _score_confidence(self, reply_type: ReplyType, content: str) -> float:
        """Score the confidence of a generated reply.

        Combines reply type ceiling with content quality signals.
        """
        ceiling = TRUST_CEILINGS.get(reply_type, 0.5)

        # Base confidence starts at 70% of ceiling
        confidence = ceiling * 0.7

        # Bonus for non-empty content
        if len(content) > 10:
            confidence += ceiling * 0.15

        # Bonus for reasonable length (not too short, not too long)
        if 50 <= len(content) <= 500:
            confidence += ceiling * 0.10

        # Penalty for very short responses
        if len(content) < 20:
            confidence -= 0.1

        return max(0.0, min(ceiling, confidence))


class ReplyDraftStore:
    """PostgreSQL storage for reply drafts."""

    def __init__(self, pool: Any) -> None:
        """Initialize the draft store.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def save_draft(self, draft: ReplyDraft) -> int:
        """Save a reply draft and return its ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gmail_drafts (email_id, account_id, draft_text,
                                          reply_type, confidence, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                draft.email_id,
                draft.account_id,
                draft.draft_text,
                draft.reply_type.value,
                draft.confidence,
                draft.status.value,
            )
            draft_id: int = row["id"]
            log.debug("draft_saved", draft_id=draft_id)
            return draft_id

    async def get_draft(self, draft_id: int) -> ReplyDraft | None:
        """Get a draft by ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM gmail_drafts WHERE id = $1", draft_id)
            if not row:
                return None
            return self._row_to_draft(row)

    async def list_pending(self, account_id: int, *, limit: int = 20) -> list[ReplyDraft]:
        """List pending drafts for an account."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM gmail_drafts
                WHERE account_id = $1 AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT $2
                """,
                account_id,
                limit,
            )
            return [self._row_to_draft(r) for r in rows]

    async def update_status(
        self,
        draft_id: int,
        status: DraftStatus,
        *,
        sent_at: datetime | None = None,
    ) -> bool:
        """Update the status of a draft."""
        async with self._pool.acquire() as conn:
            if sent_at:
                result = await conn.execute(
                    """
                    UPDATE gmail_drafts SET status = $1, sent_at = $2
                    WHERE id = $3
                    """,
                    status.value,
                    sent_at,
                    draft_id,
                )
            else:
                result = await conn.execute(
                    "UPDATE gmail_drafts SET status = $1 WHERE id = $2",
                    status.value,
                    draft_id,
                )
            updated: bool = result.split()[-1] != "0"
            return updated

    async def delete_draft(self, draft_id: int) -> bool:
        """Delete a draft."""
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM gmail_drafts WHERE id = $1", draft_id)
            deleted: bool = result.split()[-1] != "0"
            return deleted

    def _row_to_draft(self, row: Any) -> ReplyDraft:
        """Convert a database row to a ReplyDraft."""
        return ReplyDraft(
            draft_id=row["id"],
            email_id=row["email_id"],
            account_id=row["account_id"],
            draft_text=row["draft_text"],
            reply_type=ReplyType(row["reply_type"]),
            confidence=row["confidence"],
            status=DraftStatus(row["status"]),
            sent_at=row.get("sent_at"),
            created_at=row.get("created_at"),
        )
