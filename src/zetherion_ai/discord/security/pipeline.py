"""Two-tier security analysis pipeline.

Tier 1: Regex patterns + heuristics + payload decoding (~0ms)
Tier 2: Ollama AI analysis (~1-2s, runs on all messages by default)

No role-based bypasses. All users — including the owner — go through the full
pipeline with identical thresholds. Bypass requires the explicit config flag
``security_bypass_enabled`` (default ``False``).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from zetherion_ai.config import get_dynamic
from zetherion_ai.discord.security.forensics import log_security_event
from zetherion_ai.discord.security.models import ThreatAction, ThreatSignal, ThreatVerdict
from zetherion_ai.discord.security.tier1_decoders import decode_and_check
from zetherion_ai.discord.security.tier1_regex import check_all_patterns, check_heuristics
from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.discord.security.tier2_ai import SecurityAIAnalyzer

log = get_logger("zetherion_ai.discord.security.pipeline")

# Default thresholds — can be overridden via config
_DEFAULT_FLAG_THRESHOLD = 0.3
_DEFAULT_BLOCK_THRESHOLD = 0.6

# Short-circuit: obvious attacks are blocked immediately without Tier 2
_TIER1_SHORT_CIRCUIT = 0.9


class SecurityPipeline:
    """Orchestrate Tier 1 + Tier 2 security analysis.

    All users are checked identically (no owner/admin bypass).
    """

    def __init__(
        self,
        *,
        ai_analyzer: SecurityAIAnalyzer | None = None,
        enable_tier2: bool = True,
    ) -> None:
        self._ai_analyzer = ai_analyzer
        self._enable_tier2 = enable_tier2

    async def analyze(
        self,
        content: str,
        *,
        user_id: int,
        channel_id: int,
        request_id: str = "",
    ) -> ThreatVerdict:
        """Analyse a message through the full security pipeline.

        Args:
            content: Raw message text.
            user_id: Discord user ID (for logging only, no bypass).
            channel_id: Discord channel ID (for logging).
            request_id: Correlation ID for logs.

        Returns:
            A :class:`ThreatVerdict` with the action to take.
        """
        start = time.perf_counter()

        # Check if bypass is explicitly enabled (logged as security degradation)
        bypass_enabled = get_dynamic("security", "bypass_enabled", False)
        if bypass_enabled:
            log.warning("security_bypass_active", user_id=user_id)
            return ThreatVerdict(
                action=ThreatAction.ALLOW,
                score=0.0,
                tier_reached=0,
                processing_time_ms=(time.perf_counter() - start) * 1000,
            )

        # Read thresholds from dynamic config
        flag_threshold = get_dynamic("security", "flag_threshold", _DEFAULT_FLAG_THRESHOLD)
        block_threshold = get_dynamic("security", "block_threshold", _DEFAULT_BLOCK_THRESHOLD)

        # ---- Tier 1: Regex + Heuristics + Payload decoding ----
        signals: list[ThreatSignal] = []
        signals.extend(check_all_patterns(content))
        signals.extend(check_heuristics(content))
        signals.extend(decode_and_check(content))

        tier1_score = _aggregate_score(signals)
        tier_reached = 1
        ai_reasoning = ""

        # Short-circuit for obvious attacks
        if tier1_score >= _TIER1_SHORT_CIRCUIT:
            elapsed = (time.perf_counter() - start) * 1000
            verdict = ThreatVerdict(
                action=ThreatAction.BLOCK,
                score=tier1_score,
                signals=signals,
                tier_reached=1,
                processing_time_ms=elapsed,
            )
            log_security_event(
                user_id=user_id,
                channel_id=channel_id,
                content=content,
                verdict=verdict,
                request_id=request_id,
            )
            return verdict

        # ---- Tier 2: AI Analysis (runs on ALL messages by default) ----
        if self._enable_tier2 and self._ai_analyzer is not None:
            tier_reached = 2
            ai_signal = await self._ai_analyzer.analyze(content, signals)
            if ai_signal is not None:
                # If AI says false positive, halve its own score
                if ai_signal.metadata.get("false_positive_likely"):
                    ai_signal.score *= 0.5
                signals.append(ai_signal)
                ai_reasoning = ai_signal.metadata.get("ai_reasoning", "")

        # ---- Final scoring and action ----
        final_score = _aggregate_score(signals)

        if final_score >= block_threshold:
            action = ThreatAction.BLOCK
        elif final_score >= flag_threshold:
            action = ThreatAction.FLAG
        else:
            action = ThreatAction.ALLOW

        elapsed = (time.perf_counter() - start) * 1000

        verdict = ThreatVerdict(
            action=action,
            score=final_score,
            signals=signals,
            tier_reached=tier_reached,
            ai_reasoning=ai_reasoning,
            processing_time_ms=elapsed,
        )

        # Log non-ALLOW verdicts
        if action != ThreatAction.ALLOW:
            log_security_event(
                user_id=user_id,
                channel_id=channel_id,
                content=content,
                verdict=verdict,
                request_id=request_id,
            )

        return verdict


def _aggregate_score(signals: list[ThreatSignal]) -> float:
    """Aggregate signals into a single threat score.

    Uses max signal score + exponentially diminishing contributions from
    secondary signals.  This prevents many low-scoring signals from adding
    up to a false block.
    """
    if not signals:
        return 0.0
    scores = sorted((s.score for s in signals), reverse=True)
    total = scores[0]
    for i, score in enumerate(scores[1:], 1):
        total += score * (0.3**i)
    return min(1.0, total)
