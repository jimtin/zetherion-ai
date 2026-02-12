"""Forensic logging for security events.

All WARNING+ events automatically land in the rotating error log file
(``logs/zetherion_ai_error.log``).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from zetherion_ai.discord.security.models import ThreatVerdict
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.discord.security.forensics")


def log_security_event(
    *,
    user_id: int,
    channel_id: int,
    content: str,
    verdict: ThreatVerdict,
    request_id: str,
) -> None:
    """Log a detailed forensic record for a security event."""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    log.warning(
        "security_event",
        event_type="threat_detected",
        request_id=request_id,
        user_id=user_id,
        channel_id=channel_id,
        timestamp=datetime.now(UTC).isoformat(),
        action=verdict.action.value,
        threat_score=round(verdict.score, 4),
        tier_reached=verdict.tier_reached,
        signal_count=len(verdict.signals),
        signals=[
            {
                "category": s.category.value,
                "pattern": s.pattern_name,
                "score": round(s.score, 3),
                "matched": s.matched_text[:100],
            }
            for s in verdict.signals
        ],
        content_hash=content_hash,
        content_length=len(content),
        content_preview=content[:200],
        processing_ms=round(verdict.processing_time_ms, 2),
        ai_reasoning=verdict.ai_reasoning[:300] if verdict.ai_reasoning else None,
    )
