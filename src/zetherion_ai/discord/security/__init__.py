"""Discord security package — tiered threat analysis pipeline.

Public API
----------
- :class:`SecurityPipeline` — orchestrate Tier 1 + Tier 2 analysis
- :class:`ThreatVerdict`, :class:`ThreatAction` — result types
- :class:`RateLimiter` — per-user sliding-window rate limiter
- :func:`detect_prompt_injection` — backward-compatible boolean wrapper
"""

from zetherion_ai.discord.security.models import ThreatAction, ThreatVerdict
from zetherion_ai.discord.security.pipeline import SecurityPipeline, _aggregate_score
from zetherion_ai.discord.security.rate_limiter import RateLimiter, RateLimitState
from zetherion_ai.discord.security.tier1_regex import check_all_patterns, check_heuristics
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.discord.security")

__all__ = [
    "RateLimiter",
    "RateLimitState",
    "SecurityPipeline",
    "ThreatAction",
    "ThreatVerdict",
    "detect_prompt_injection",
]


def detect_prompt_injection(content: str) -> bool:
    """Backward-compatible wrapper.

    Returns ``True`` if the Tier 1 aggregate score exceeds the flag
    threshold (0.6).  This preserves the existing call-site contract in
    ``bot.py`` and test suites while the codebase migrates to the full
    :class:`SecurityPipeline`.
    """
    signals = check_all_patterns(content) + check_heuristics(content)
    score = _aggregate_score(signals)
    return score >= 0.6
