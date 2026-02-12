"""Scaling trust model for YouTube comment auto-replies.

Trust levels control which reply categories are auto-approved vs. requiring
human review.  The model promotes/demotes based on historical approval rates.
"""

from __future__ import annotations

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.youtube.models import ReplyCategory, TrustLevel

log = get_logger("zetherion_ai.skills.youtube.trust")

# Categories considered "routine" at each trust level.
# At a given level, categories in the set are auto-approved.
_AUTO_CATEGORIES: dict[int, set[str]] = {
    TrustLevel.SUPERVISED.value: set(),  # nothing auto-approved
    TrustLevel.GUIDED.value: {
        ReplyCategory.THANK_YOU.value,
        ReplyCategory.FAQ.value,
    },
    TrustLevel.AUTONOMOUS.value: {
        ReplyCategory.THANK_YOU.value,
        ReplyCategory.FAQ.value,
        ReplyCategory.QUESTION.value,
        ReplyCategory.FEEDBACK.value,
    },
    TrustLevel.FULL_AUTO.value: {
        ReplyCategory.THANK_YOU.value,
        ReplyCategory.FAQ.value,
        ReplyCategory.QUESTION.value,
        ReplyCategory.FEEDBACK.value,
        ReplyCategory.COMPLAINT.value,
    },
}

# Promotion thresholds: (min_total, max_rejection_rate)
_PROMOTION_RULES: dict[int, tuple[int, float]] = {
    TrustLevel.SUPERVISED.value: (50, 0.05),  # 50 replies, <5% rejected → GUIDED
    TrustLevel.GUIDED.value: (200, 0.03),  # 200 replies, <3% rejected → AUTONOMOUS
    # AUTONOMOUS → FULL_AUTO is manual only
}

# Demotion: rejection at AUTONOMOUS → back to GUIDED
_DEMOTION_FROM = TrustLevel.AUTONOMOUS.value
_DEMOTION_TO = TrustLevel.GUIDED.value


class TrustModel:
    """Per-channel trust model for auto-reply behaviour."""

    def __init__(
        self,
        level: int = TrustLevel.SUPERVISED.value,
        stats: dict[str, int] | None = None,
    ) -> None:
        self._level = level
        self._stats = stats or {"total": 0, "approved": 0, "rejected": 0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def level(self) -> int:
        return self._level

    @property
    def label(self) -> str:
        return TrustLevel(self._level).name

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def approval_rate(self) -> float:
        total = self._stats["total"]
        if total == 0:
            return 0.0
        return self._stats["approved"] / total

    @property
    def rejection_rate(self) -> float:
        total = self._stats["total"]
        if total == 0:
            return 0.0
        return self._stats["rejected"] / total

    @property
    def auto_categories(self) -> set[str]:
        """Categories that are auto-approved at the current trust level."""
        return set(_AUTO_CATEGORIES.get(self._level, set()))

    @property
    def review_categories(self) -> set[str]:
        """Categories that still require human review."""
        all_cats = {c.value for c in ReplyCategory if c != ReplyCategory.SPAM}
        return all_cats - self.auto_categories

    @property
    def next_level_at(self) -> int | None:
        """Total replies needed to be eligible for the next level, or None."""
        rule = _PROMOTION_RULES.get(self._level)
        return rule[0] if rule else None

    def should_auto_approve(self, category: str) -> bool:
        """Return True if *category* is auto-approved at the current level."""
        return category in self.auto_categories

    # ------------------------------------------------------------------
    # State changes
    # ------------------------------------------------------------------

    def record_approval(self) -> None:
        """Record a human-approved reply."""
        self._stats["total"] += 1
        self._stats["approved"] += 1
        self._try_promote()

    def record_rejection(self) -> None:
        """Record a human-rejected reply."""
        self._stats["total"] += 1
        self._stats["rejected"] += 1
        self._try_demote()

    def set_level(self, level: int) -> None:
        """Manually set the trust level (user override)."""
        if 0 <= level <= TrustLevel.FULL_AUTO.value:
            old = self._level
            self._level = level
            log.info(
                "trust_level_manual_set",
                old=old,
                new=level,
                label=TrustLevel(level).name,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _try_promote(self) -> None:
        rule = _PROMOTION_RULES.get(self._level)
        if rule is None:
            return
        min_total, max_rejection = rule
        if self._stats["total"] >= min_total and self.rejection_rate < max_rejection:
            old = self._level
            self._level += 1
            log.info(
                "trust_level_promoted",
                old=old,
                new=self._level,
                label=TrustLevel(self._level).name,
                stats=self._stats,
            )

    def _try_demote(self) -> None:
        if self._level == _DEMOTION_FROM:
            old = self._level
            self._level = _DEMOTION_TO
            log.info(
                "trust_level_demoted",
                old=old,
                new=self._level,
                label=TrustLevel(self._level).name,
                reason="rejection_at_autonomous",
                stats=self._stats,
            )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self._level,
            "label": self.label,
            "stats": self._stats,
            "approval_rate": round(self.approval_rate, 4),
            "rejection_rate": round(self.rejection_rate, 4),
            "next_level_at": self.next_level_at,
        }

    @classmethod
    def from_channel(cls, channel: dict[str, object]) -> TrustModel:
        """Construct a TrustModel from a youtube_channels row."""
        return cls(
            level=int(channel.get("trust_level", 0)),  # type: ignore[call-overload]
            stats=channel.get("trust_stats") or {"total": 0, "approved": 0, "rejected": 0},  # type: ignore[arg-type]
        )
