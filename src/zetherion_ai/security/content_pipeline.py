"""Reusable content security pipeline for non-Discord ingress sources."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from zetherion_ai.config import get_settings
from zetherion_ai.discord.security.models import ThreatAction, ThreatVerdict
from zetherion_ai.discord.security.pipeline import SecurityPipeline
from zetherion_ai.discord.security.tier2_ai import SecurityAIAnalyzer
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.security.content_pipeline")


@dataclass
class ContentSecurityResult:
    """Security result with a stable payload hash for audit records."""

    verdict: ThreatVerdict
    payload_hash: str


class ContentSecurityPipeline:
    """Source-agnostic security wrapper around the existing threat pipeline."""

    def __init__(self) -> None:
        settings = get_settings()
        enable_tier2 = bool(settings.security_tier2_enabled)
        analyzer = SecurityAIAnalyzer() if enable_tier2 else None
        self._pipeline = SecurityPipeline(ai_analyzer=analyzer, enable_tier2=enable_tier2)

    async def analyze(
        self,
        content: str,
        *,
        source: str,
        user_id: int,
        context_id: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ContentSecurityResult:
        """Analyze a content payload and return verdict + payload hash."""
        verdict = await self._pipeline.analyze(
            content,
            user_id=user_id,
            channel_id=context_id,
            request_id=f"{source}:{user_id}:{context_id}",
        )
        digest_input = f"{source}|{user_id}|{context_id}|{content[:4000]}".encode()
        payload_hash = hashlib.sha256(digest_input).hexdigest()

        if verdict.action == ThreatAction.BLOCK:
            log.warning(
                "content_blocked",
                source=source,
                user_id=user_id,
                score=verdict.score,
                metadata=metadata or {},
            )

        return ContentSecurityResult(verdict=verdict, payload_hash=payload_hash)

    async def close(self) -> None:
        """Cleanup ai client resources."""
        analyzer = getattr(self._pipeline, "_ai_analyzer", None)
        if analyzer is not None and hasattr(analyzer, "close"):
            await analyzer.close()
