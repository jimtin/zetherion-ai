"""Per-contact and per-type trust scoring for email replies.

Tracks trust at two granularities:
1. Per reply type — global trust for each category of reply.
2. Per contact — trust for replies to specific senders.

Trust evolves based on user feedback (approve/edit/reject) and
determines whether a reply can be auto-sent or needs review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.replies import TRUST_CEILINGS, ReplyType

log = get_logger("zetherion_ai.skills.gmail.trust")

# ---------------------------------------------------------------------------
# Trust evolution constants
# ---------------------------------------------------------------------------

APPROVAL_DELTA = 0.05
MINOR_EDIT_DELTA = -0.02
MAJOR_EDIT_DELTA = -0.10
REJECTION_DELTA = -0.20
TRUST_FLOOR = 0.0
GLOBAL_CAP = 0.95


@dataclass
class TrustScore:
    """Trust score with metadata."""

    score: float
    approvals: int = 0
    rejections: int = 0
    edits: int = 0
    total_interactions: int = 0

    @property
    def approval_rate(self) -> float:
        """Fraction of interactions that were approvals."""
        if self.total_interactions == 0:
            return 0.0
        return self.approvals / self.total_interactions

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "score": round(self.score, 4),
            "approvals": self.approvals,
            "rejections": self.rejections,
            "edits": self.edits,
            "total_interactions": self.total_interactions,
            "approval_rate": round(self.approval_rate, 4),
        }


class TrustManager:
    """Manages trust scores for email reply automation.

    Maintains two trust dimensions:
    - **Type trust**: How much we trust auto-sending each reply type.
    - **Contact trust**: How much we trust auto-sending to a specific contact.

    The effective trust for a reply is:
        min(type_trust, contact_trust, reply_type_ceiling)
    """

    def __init__(self, pool: Any) -> None:
        """Initialize the trust manager.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def get_effective_trust(
        self,
        user_id: int,
        contact_email: str,
        reply_type: ReplyType,
    ) -> float:
        """Get the effective trust score for sending a reply.

        The effective trust is the minimum of:
        - The per-type trust score
        - The per-contact trust score
        - The reply type's ceiling

        Args:
            user_id: The user.
            contact_email: The recipient.
            reply_type: Type of reply.

        Returns:
            Effective trust score (0.0 to ceiling).
        """
        type_trust = await self.get_type_trust(user_id, reply_type)
        contact_trust = await self.get_contact_trust(user_id, contact_email)
        ceiling = TRUST_CEILINGS.get(reply_type, 0.5)

        effective = min(type_trust.score, contact_trust.score, ceiling)

        log.debug(
            "effective_trust_computed",
            user_id=user_id,
            contact=contact_email,
            reply_type=reply_type.value,
            type_trust=type_trust.score,
            contact_trust=contact_trust.score,
            ceiling=ceiling,
            effective=effective,
        )

        return effective

    async def should_auto_send(
        self,
        user_id: int,
        contact_email: str,
        reply_type: ReplyType,
        confidence: float,
        *,
        auto_threshold: float = 0.85,
    ) -> bool:
        """Determine if a reply should be auto-sent.

        A reply is auto-sent if its effective trust meets the threshold
        AND the draft confidence meets the threshold.

        Args:
            user_id: The user.
            contact_email: The recipient.
            reply_type: Type of reply.
            confidence: Draft confidence score.
            auto_threshold: Minimum trust/confidence for auto-send.

        Returns:
            True if the reply should be auto-sent.
        """
        effective_trust = await self.get_effective_trust(user_id, contact_email, reply_type)
        return effective_trust >= auto_threshold and confidence >= auto_threshold

    async def record_feedback(
        self,
        user_id: int,
        contact_email: str,
        reply_type: ReplyType,
        outcome: str,
    ) -> tuple[float, float]:
        """Record user feedback and update both trust scores.

        Args:
            user_id: The user.
            contact_email: The contact.
            reply_type: Type of reply.
            outcome: One of 'approved', 'minor_edit', 'major_edit', 'rejected'.

        Returns:
            Tuple of (new_type_trust, new_contact_trust).
        """
        delta = _outcome_delta(outcome)

        new_type = await self._update_type_trust(user_id, reply_type, delta, outcome)
        new_contact = await self._update_contact_trust(user_id, contact_email, delta, outcome)

        log.info(
            "trust_feedback_recorded",
            user_id=user_id,
            contact=contact_email,
            reply_type=reply_type.value,
            outcome=outcome,
            delta=delta,
            new_type_trust=new_type,
            new_contact_trust=new_contact,
        )

        return new_type, new_contact

    async def get_type_trust(self, user_id: int, reply_type: ReplyType) -> TrustScore:
        """Get trust score for a reply type."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT score, approvals, rejections, edits, total_interactions
                FROM gmail_type_trust
                WHERE user_id = $1 AND reply_type = $2
                """,
                user_id,
                reply_type.value,
            )
            if not row:
                return TrustScore(score=0.0)
            return TrustScore(
                score=row["score"],
                approvals=row["approvals"],
                rejections=row["rejections"],
                edits=row["edits"],
                total_interactions=row["total_interactions"],
            )

    async def get_contact_trust(self, user_id: int, contact_email: str) -> TrustScore:
        """Get trust score for a contact."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT score, approvals, rejections, edits, total_interactions
                FROM gmail_contact_trust
                WHERE user_id = $1 AND contact_email = $2
                """,
                user_id,
                contact_email,
            )
            if not row:
                return TrustScore(score=0.0)
            return TrustScore(
                score=row["score"],
                approvals=row["approvals"],
                rejections=row["rejections"],
                edits=row["edits"],
                total_interactions=row["total_interactions"],
            )

    async def list_type_trusts(self, user_id: int) -> dict[str, TrustScore]:
        """List all type trust scores for a user."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT reply_type, score, approvals, rejections,
                       edits, total_interactions
                FROM gmail_type_trust
                WHERE user_id = $1
                ORDER BY reply_type
                """,
                user_id,
            )
            return {
                row["reply_type"]: TrustScore(
                    score=row["score"],
                    approvals=row["approvals"],
                    rejections=row["rejections"],
                    edits=row["edits"],
                    total_interactions=row["total_interactions"],
                )
                for row in rows
            }

    async def list_contact_trusts(self, user_id: int, *, limit: int = 20) -> dict[str, TrustScore]:
        """List contact trust scores for a user."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT contact_email, score, approvals, rejections,
                       edits, total_interactions
                FROM gmail_contact_trust
                WHERE user_id = $1
                ORDER BY total_interactions DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            return {
                row["contact_email"]: TrustScore(
                    score=row["score"],
                    approvals=row["approvals"],
                    rejections=row["rejections"],
                    edits=row["edits"],
                    total_interactions=row["total_interactions"],
                )
                for row in rows
            }

    async def reset_type_trust(self, user_id: int, reply_type: ReplyType) -> bool:
        """Reset trust for a reply type to 0."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE gmail_type_trust
                SET score = 0.0, approvals = 0, rejections = 0,
                    edits = 0, total_interactions = 0
                WHERE user_id = $1 AND reply_type = $2
                """,
                user_id,
                reply_type.value,
            )
            reset: bool = result.split()[-1] != "0"
            return reset

    async def reset_contact_trust(self, user_id: int, contact_email: str) -> bool:
        """Reset trust for a contact to 0."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE gmail_contact_trust
                SET score = 0.0, approvals = 0, rejections = 0,
                    edits = 0, total_interactions = 0
                WHERE user_id = $1 AND contact_email = $2
                """,
                user_id,
                contact_email,
            )
            reset: bool = result.split()[-1] != "0"
            return reset

    # ------------------------------------------------------------------
    # Internal update methods
    # ------------------------------------------------------------------

    async def _update_type_trust(
        self,
        user_id: int,
        reply_type: ReplyType,
        delta: float,
        outcome: str,
    ) -> float:
        """Update type trust and return new score."""
        ceiling = TRUST_CEILINGS.get(reply_type, GLOBAL_CAP)
        cap = min(ceiling, GLOBAL_CAP)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gmail_type_trust
                    (user_id, reply_type, score, approvals, rejections,
                     edits, total_interactions)
                VALUES ($1, $2, GREATEST(0.0, LEAST($3, $4)), $5, $6, $7, 1)
                ON CONFLICT (user_id, reply_type) DO UPDATE SET
                    score = GREATEST(0.0, LEAST(
                        gmail_type_trust.score + $3, $4
                    )),
                    approvals = gmail_type_trust.approvals + $5,
                    rejections = gmail_type_trust.rejections + $6,
                    edits = gmail_type_trust.edits + $7,
                    total_interactions = gmail_type_trust.total_interactions + 1
                RETURNING score
                """,
                user_id,
                reply_type.value,
                delta,
                cap,
                1 if outcome == "approved" else 0,
                1 if outcome == "rejected" else 0,
                1 if outcome in ("minor_edit", "major_edit") else 0,
            )
            new_score: float = row["score"]
            return new_score

    async def _update_contact_trust(
        self,
        user_id: int,
        contact_email: str,
        delta: float,
        outcome: str,
    ) -> float:
        """Update contact trust and return new score."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gmail_contact_trust
                    (user_id, contact_email, score, approvals, rejections,
                     edits, total_interactions)
                VALUES ($1, $2, GREATEST(0.0, LEAST($3, $4)), $5, $6, $7, 1)
                ON CONFLICT (user_id, contact_email) DO UPDATE SET
                    score = GREATEST(0.0, LEAST(
                        gmail_contact_trust.score + $3, $4
                    )),
                    approvals = gmail_contact_trust.approvals + $5,
                    rejections = gmail_contact_trust.rejections + $6,
                    edits = gmail_contact_trust.edits + $7,
                    total_interactions = gmail_contact_trust.total_interactions + 1
                RETURNING score
                """,
                user_id,
                contact_email,
                delta,
                GLOBAL_CAP,
                1 if outcome == "approved" else 0,
                1 if outcome == "rejected" else 0,
                1 if outcome in ("minor_edit", "major_edit") else 0,
            )
            new_score: float = row["score"]
            return new_score


def _outcome_delta(outcome: str) -> float:
    """Map an outcome string to its trust delta."""
    deltas = {
        "approved": APPROVAL_DELTA,
        "minor_edit": MINOR_EDIT_DELTA,
        "major_edit": MAJOR_EDIT_DELTA,
        "rejected": REJECTION_DELTA,
    }
    if outcome not in deltas:
        raise ValueError(f"Unknown outcome: {outcome!r}")
    return deltas[outcome]
