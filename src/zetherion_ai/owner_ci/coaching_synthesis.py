"""Additive model-backed coaching synthesis grounded in deterministic evidence."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci.models import AgentCoachingFeedback, SynthesizedCoachingGuidance

log = get_logger("zetherion_ai.owner_ci.coaching_synthesis")


class CoachingSynthesizer:
    """Generate additive coaching summaries without changing deterministic findings."""

    def __init__(self, *, inference: InferenceBroker) -> None:
        self._inference = inference

    async def synthesize_many(
        self,
        coaching: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        limited = coaching[: max(1, settings.coaching_max_items_per_response)]
        synthesized = [await self.synthesize_feedback(item) for item in limited]
        if len(coaching) <= len(limited):
            return synthesized
        return [*synthesized, *coaching[len(limited) :]]

    async def synthesize_feedback(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        feedback = AgentCoachingFeedback.model_validate(payload)
        settings = get_settings()
        if not settings.coaching_synthesis_enabled:
            return self._with_guidance(
                feedback,
                self._fallback_guidance(
                    feedback,
                    status="skipped",
                    explanation="Synthesized coaching is disabled for this runtime.",
                ),
            )

        try:
            result = await asyncio.wait_for(
                self._inference.infer(
                    prompt=self._build_prompt(feedback),
                    task_type=TaskType.COACHING_SYNTHESIS,
                    system_prompt=self._system_prompt(),
                    max_tokens=settings.coaching_max_tokens,
                    temperature=settings.coaching_temperature,
                    forced_provider=Provider.GROQ,
                    forced_model=settings.coaching_model,
                    allow_forced_fallback=False,
                ),
                timeout=settings.coaching_timeout_seconds,
            )
            guidance = self._validate_guidance(feedback, self._extract_json(result.content))
            guidance.provider = result.provider.value
            guidance.model = result.model
            guidance.generated_at = datetime.now(UTC).isoformat()
            return self._with_guidance(feedback, guidance)
        except Exception as exc:
            log.warning(
                "coaching_synthesis_fallback",
                feedback_id=feedback.feedback_id,
                error=str(exc),
            )
            return self._with_guidance(
                feedback,
                self._fallback_guidance(
                    feedback,
                    status="fallback",
                    explanation=(
                        "Deterministic coaching is preserved because the synthesized overlay "
                        "was unavailable or invalid."
                    ),
                ),
            )

    def _with_guidance(
        self,
        feedback: AgentCoachingFeedback,
        guidance: SynthesizedCoachingGuidance,
    ) -> dict[str, Any]:
        enriched = feedback.model_copy(update={"synthesized_guidance": guidance})
        return enriched.model_dump(mode="json", exclude_none=True)

    def _fallback_guidance(
        self,
        feedback: AgentCoachingFeedback,
        *,
        status: str,
        explanation: str,
    ) -> SynthesizedCoachingGuidance:
        return SynthesizedCoachingGuidance(
            status=status,
            summary=feedback.summary,
            explanation=explanation,
            prioritized_actions=[
                recommendation.title
                for recommendation in feedback.recommendations[:3]
                if recommendation.title
            ],
            agents_md_delta=(
                feedback.recommendations[0].agents_md_update
                if feedback.recommendations
                else None
            ),
            cited_finding_ids=[finding.finding_id for finding in feedback.findings[:5]],
            cited_rule_codes=[
                violation.rule_code for violation in feedback.rule_violations[:5]
            ],
            cited_evidence_ref_ids=[
                reference.evidence_ref_id for reference in feedback.evidence_references[:8]
            ],
            provider="groq",
            model=get_settings().coaching_model,
            generated_at=datetime.now(UTC).isoformat(),
        )

    def _system_prompt(self) -> str:
        return (
            "You are generating additive coaching guidance for downstream coding agents. "
            "Do not invent new findings, new rule codes, or new evidence. "
            "Return JSON only with keys: status, summary, explanation, prioritized_actions, "
            "agents_md_delta, cited_finding_ids, cited_rule_codes, cited_evidence_ref_ids. "
            "Set status to synthesized. Keep prioritized_actions concise and concrete."
        )

    def _build_prompt(self, feedback: AgentCoachingFeedback) -> str:
        return json.dumps(
            {
                "feedback": feedback.model_dump(mode="json", exclude_none=True),
                "instructions": {
                    "allowed_status": "synthesized",
                    "must_cite_existing_ids_only": True,
                    "must_not_change_blocking_or_gate_outcomes": True,
                    "must_not_add_new_findings": True,
                },
            },
            separators=(",", ":"),
        )

    def _extract_json(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if "\n" in stripped:
                stripped = stripped.split("\n", 1)[1]
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Synthesized coaching did not return a JSON object")
        return json.loads(stripped[start : end + 1])

    def _validate_guidance(
        self,
        feedback: AgentCoachingFeedback,
        payload: dict[str, Any],
    ) -> SynthesizedCoachingGuidance:
        guidance = SynthesizedCoachingGuidance.model_validate(payload)
        finding_ids = {finding.finding_id for finding in feedback.findings}
        rule_codes = {violation.rule_code for violation in feedback.rule_violations}
        evidence_ids = {reference.evidence_ref_id for reference in feedback.evidence_references}

        if guidance.status != "synthesized":
            raise ValueError("Synthesized coaching returned an invalid status")
        if not guidance.cited_finding_ids and not guidance.cited_rule_codes:
            raise ValueError("Synthesized coaching must cite findings or rule codes")
        if any(item not in finding_ids for item in guidance.cited_finding_ids):
            raise ValueError("Synthesized coaching cited an unknown finding id")
        if any(item not in rule_codes for item in guidance.cited_rule_codes):
            raise ValueError("Synthesized coaching cited an unknown rule code")
        if any(item not in evidence_ids for item in guidance.cited_evidence_ref_ids):
            raise ValueError("Synthesized coaching cited an unknown evidence id")

        return guidance
