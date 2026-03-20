"""Unit tests for additive coaching synthesis."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.agent.inference import InferenceResult
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.owner_ci.coaching_synthesis import CoachingSynthesizer


def _feedback_payload() -> dict[str, object]:
    return {
        "feedback_id": "coach-1",
        "scope": "repo",
        "status": "open",
        "blocking": True,
        "summary": "Coverage and validation are failing repeatedly.",
        "findings": [
            {
                "finding_id": "finding-1",
                "coaching_kind": "diagnostic",
                "rule_code": "coverage_gate_failed",
                "summary": "Coverage gate failed in repeated runs.",
                "remediation": "Raise coverage or narrow the changed surface.",
                "blocking": True,
                "recurrence_count": 3,
                "evidence_ref_ids": ["evidence-1"],
                "metadata": {},
            }
        ],
        "recommendations": [
            {
                "title": "Document the coverage rule",
                "instructions": ["Update AGENTS.md with the required local coverage check."],
                "agents_md_update": "Add coverage verification before CI submission.",
                "patch_guidance": {},
            }
        ],
        "rule_violations": [
            {
                "rule_code": "coverage_gate_failed",
                "summary": "Coverage gate failed in CI.",
                "blocking": True,
                "evidence_ref_ids": ["evidence-1"],
                "metadata": {},
            }
        ],
        "evidence_references": [
            {
                "evidence_ref_id": "evidence-1",
                "provider": "github",
                "service": "actions",
                "query": "coverage report",
                "metadata": {},
            }
        ],
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_coaching_synthesizer_adds_grounded_guidance() -> None:
    inference = AsyncMock()
    inference.infer.return_value = InferenceResult(
        content=json.dumps(
            {
                "status": "synthesized",
                "summary": "Fix the recurring coverage failure before asking for another run.",
                "explanation": "The same blocking coverage issue has already recurred several times.",
                "prioritized_actions": [
                    "Run coverage locally before submitting CI.",
                    "Update AGENTS.md with the coverage gate."
                ],
                "agents_md_delta": "Add the mandatory local coverage check to AGENTS.md.",
                "cited_finding_ids": ["finding-1"],
                "cited_rule_codes": ["coverage_gate_failed"],
                "cited_evidence_ref_ids": ["evidence-1"],
            }
        ),
        provider=Provider.GROQ,
        task_type=TaskType.COACHING_SYNTHESIS,
        model="openai/gpt-oss-120b",
    )
    synthesizer = CoachingSynthesizer(inference=inference)

    result = await synthesizer.synthesize_feedback(_feedback_payload())

    assert result["synthesized_guidance"]["status"] == "synthesized"
    assert result["synthesized_guidance"]["provider"] == "groq"
    assert result["synthesized_guidance"]["model"] == "openai/gpt-oss-120b"
    assert result["synthesized_guidance"]["cited_finding_ids"] == ["finding-1"]


@pytest.mark.asyncio
async def test_coaching_synthesizer_falls_back_on_invalid_citations() -> None:
    inference = AsyncMock()
    inference.infer.return_value = InferenceResult(
        content=json.dumps(
            {
                "status": "synthesized",
                "summary": "Invalid coaching",
                "explanation": "This should not validate.",
                "prioritized_actions": ["Do something"],
                "agents_md_delta": "Broken",
                "cited_finding_ids": ["finding-missing"],
                "cited_rule_codes": ["coverage_gate_failed"],
                "cited_evidence_ref_ids": ["evidence-1"],
            }
        ),
        provider=Provider.GROQ,
        task_type=TaskType.COACHING_SYNTHESIS,
        model="openai/gpt-oss-120b",
    )
    synthesizer = CoachingSynthesizer(inference=inference)

    result = await synthesizer.synthesize_feedback(_feedback_payload())

    assert result["synthesized_guidance"]["status"] == "fallback"
    assert result["synthesized_guidance"]["summary"] == (
        "Coverage and validation are failing repeatedly."
    )
