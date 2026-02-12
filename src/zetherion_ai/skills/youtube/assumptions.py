"""Assumption tracking and validation for YouTube skills.

Assumptions are shared across Intelligence, Management, and Strategy
skills.  They are created during onboarding (confirmed), inferred from
data analysis, and periodically re-validated via the heartbeat cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.youtube.models import AssumptionCategory, AssumptionSource

if TYPE_CHECKING:
    from zetherion_ai.skills.youtube.storage import YouTubeStorage

log = get_logger("zetherion_ai.skills.youtube.assumptions")

# Default re-validation interval for inferred assumptions
_DEFAULT_VALIDATION_DAYS = 7
# Confirmed assumptions re-validate less frequently
_CONFIRMED_VALIDATION_DAYS = 30


class AssumptionTracker:
    """Manages the lifecycle of channel assumptions."""

    def __init__(self, storage: YouTubeStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def add_confirmed(
        self,
        channel_id: UUID,
        category: str,
        statement: str,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a user-confirmed assumption (from onboarding answers)."""
        now = datetime.utcnow()
        return await self._storage.save_assumption(
            {
                "channel_id": channel_id,
                "category": category,
                "statement": statement,
                "evidence": evidence or [],
                "confidence": 1.0,
                "source": AssumptionSource.CONFIRMED.value,
                "confirmed_at": now.isoformat(),
                "next_validation": (
                    now + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
                ).isoformat(),
            }
        )

    async def add_inferred(
        self,
        channel_id: UUID,
        category: str,
        statement: str,
        evidence: list[str] | None = None,
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        """Add a data-inferred assumption (from Intelligence analysis)."""
        now = datetime.utcnow()
        return await self._storage.save_assumption(
            {
                "channel_id": channel_id,
                "category": category,
                "statement": statement,
                "evidence": evidence or [],
                "confidence": confidence,
                "source": AssumptionSource.INFERRED.value,
                "confirmed_at": None,
                "next_validation": (
                    now + timedelta(days=_DEFAULT_VALIDATION_DAYS)
                ).isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_all(
        self, channel_id: UUID, *, active_only: bool = True
    ) -> list[dict[str, Any]]:
        """Return all assumptions for a channel.

        If *active_only*, excludes invalidated ones.
        """
        rows = await self._storage.get_assumptions(channel_id)
        if active_only:
            rows = [
                r
                for r in rows
                if r.get("source") != AssumptionSource.INVALIDATED.value
            ]
        return rows

    async def get_confirmed(self, channel_id: UUID) -> list[dict[str, Any]]:
        return await self._storage.get_assumptions(
            channel_id, source=AssumptionSource.CONFIRMED.value
        )

    async def get_high_confidence(
        self, channel_id: UUID, threshold: float = 0.7
    ) -> list[dict[str, Any]]:
        """Return confirmed + high-confidence inferred assumptions."""
        all_assumptions = await self.get_all(channel_id)
        return [
            a
            for a in all_assumptions
            if a.get("source") == AssumptionSource.CONFIRMED.value
            or (a.get("confidence", 0) >= threshold)
        ]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def confirm(self, assumption_id: UUID) -> dict[str, Any] | None:
        """Mark an inferred assumption as confirmed by the user."""
        now = datetime.utcnow()
        return await self._storage.update_assumption(
            assumption_id,
            source=AssumptionSource.CONFIRMED.value,
            confidence=1.0,
            confirmed_at=now.isoformat(),
            next_validation=(
                now + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
            ).isoformat(),
        )

    async def invalidate(
        self, assumption_id: UUID, reason: str = ""
    ) -> dict[str, Any] | None:
        """Mark an assumption as invalidated."""
        updates: dict[str, Any] = {
            "source": AssumptionSource.INVALIDATED.value,
            "confidence": 0.0,
        }
        if reason:
            # Append reason to evidence list
            existing = await self._storage.get_assumption(assumption_id)
            if existing:
                evidence = list(existing.get("evidence") or [])
                evidence.append(f"Invalidated: {reason}")
                updates["evidence"] = evidence
        return await self._storage.update_assumption(assumption_id, **updates)

    async def mark_needs_review(
        self, assumption_id: UUID
    ) -> dict[str, Any] | None:
        return await self._storage.update_assumption(
            assumption_id,
            source=AssumptionSource.NEEDS_REVIEW.value,
        )

    async def refresh_validation(
        self, assumption_id: UUID, new_confidence: float
    ) -> dict[str, Any] | None:
        """Update confidence and push next_validation forward."""
        now = datetime.utcnow()
        interval = _CONFIRMED_VALIDATION_DAYS if new_confidence >= 0.9 else _DEFAULT_VALIDATION_DAYS
        return await self._storage.update_assumption(
            assumption_id,
            confidence=new_confidence,
            last_validated=now.isoformat(),
            next_validation=(now + timedelta(days=interval)).isoformat(),
        )

    # ------------------------------------------------------------------
    # Heartbeat: find stale assumptions
    # ------------------------------------------------------------------

    async def get_stale(self) -> list[dict[str, Any]]:
        """Return all assumptions past their next_validation date."""
        return await self._storage.get_stale_assumptions()

    # ------------------------------------------------------------------
    # Helpers for onboarding
    # ------------------------------------------------------------------

    async def has_category(self, channel_id: UUID, category: str) -> bool:
        """Check if a confirmed assumption exists for a given category."""
        confirmed = await self.get_confirmed(channel_id)
        return any(a.get("category") == category for a in confirmed)

    async def get_missing_categories(
        self, channel_id: UUID
    ) -> list[str]:
        """Return onboarding categories that still need confirmed assumptions."""
        required = {c.value for c in AssumptionCategory if c != AssumptionCategory.PERFORMANCE}
        confirmed = await self.get_confirmed(channel_id)
        covered = {a["category"] for a in confirmed}
        return sorted(required - covered)
