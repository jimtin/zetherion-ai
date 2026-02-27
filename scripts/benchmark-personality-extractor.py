#!/usr/bin/env python3
"""Personality extraction benchmark for Zetherion AI.

Tests multiple LLM models against synthetic conversation threads to measure
how accurately they extract personality signals, writing style, and
relationship dynamics.  Supports concurrent execution across providers.

Usage:
    python scripts/benchmark-personality-extractor.py \
        --dataset benchmarks/datasets/personality_conversations.json
    python scripts/benchmark-personality-extractor.py \
        --contacts sarah_chen tom_wilson emily_brooks mum ahmed_khan
    python scripts/benchmark-personality-extractor.py \
        --models llama-3.3-70b-versatile gemini-2.5-flash
"""

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Ensure the src package is importable when running as a standalone script.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zetherion_ai.routing.personality import PersonalitySignal  # noqa: E402
from zetherion_ai.routing.personality_prompt import (  # noqa: E402
    SYSTEM_PROMPT,
    build_personality_prompt,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
CLOUD_INTER_REQUEST_DELAY = 0.3  # seconds between calls within a model

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Gemini concurrency cap (shared across models within the provider)
GEMINI_SEMAPHORE_LIMIT = 5  # max concurrent Gemini requests


async def _retry_with_backoff(coro_factory, max_retries=MAX_RETRIES):  # noqa: ANN001
    """Retry an async operation with exponential backoff on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            err_str = str(e).lower()
            is_retryable = "429" in err_str or "rate" in err_str or "quota" in err_str
            if is_retryable and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2**attempt)
                print(f"    [RETRY] Rate limited, waiting {delay:.0f}s " f"(attempt {attempt + 1})")
                await asyncio.sleep(delay)
            else:
                raise


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

CLOUD_MODELS: dict[str, dict] = {
    # Groq
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "input_per_m": 0.59,
        "output_per_m": 0.79,
    },
    # Google Gemini
    "gemini-2.5-flash": {
        "provider": "gemini",
        "input_per_m": 0.15,
        "output_per_m": 0.60,
    },
    "gemini-3-flash-preview": {
        "provider": "gemini",
        "input_per_m": 0.50,
        "output_per_m": 3.00,
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MessageSample:
    """One message from the dataset."""

    message_id: str
    thread_id: str
    thread_position: int
    persona_key: str
    subject: str
    from_email: str
    to_emails: list[str]
    body_text: str
    author_is_owner: bool
    expected_personality: dict


@dataclass
class ExtractionResult:
    """Result of one personality extraction attempt."""

    message_id: str
    persona_key: str
    model: str
    raw_response: str = ""
    latency_ms: float = 0.0
    json_valid: bool = False
    schema_valid: bool = False
    signal: PersonalitySignal | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# JSON extraction & parsing
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may contain markdown."""
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        return json_match.group(0).strip()
    return text.strip()


_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "formality": {
        "informal": "casual",
        "very_informal": "very_casual",
        "extremely_formal": "very_formal",
        "colloquial": "very_casual",
        "conversational": "casual",
        "professional": "formal",
    },
    "emotional_tone": {
        "friendly": "warm",
        "affectionate": "warm",
        "positive": "warm",
        "cold": "reserved",
        "detached": "reserved",
        "excited": "enthusiastic",
        "passionate": "enthusiastic",
        "flat": "neutral",
        "matter_of_fact": "neutral",
        "matter-of-fact": "neutral",
    },
    "primary_trait": {
        "concise": "terse",
        "brief": "terse",
        "wordy": "verbose",
        "expressive": "emotional",
        "sentimental": "emotional",
        "blunt": "direct",
        "straightforward": "direct",
        "tactful": "diplomatic",
        "careful": "diplomatic",
        "methodical": "analytical",
        "logical": "analytical",
    },
    "secondary_trait": {
        "concise": "terse",
        "brief": "terse",
        "wordy": "verbose",
        "expressive": "emotional",
        "sentimental": "emotional",
        "blunt": "direct",
        "straightforward": "direct",
        "tactful": "diplomatic",
        "careful": "diplomatic",
        "methodical": "analytical",
        "logical": "analytical",
    },
    "power_dynamic": {
        "equal": "peer",
        "colleague": "peer",
        "manager": "superior",
        "boss": "superior",
        "report": "subordinate",
        "employee": "subordinate",
        "customer": "client",
        "provider": "vendor",
        "supplier": "vendor",
        "family": "peer",
        "friend": "peer",
        "parent": "superior",
    },
    "vocabulary_level": {
        "basic": "simple",
        "everyday": "simple",
        "normal": "standard",
        "professional": "standard",
        "business": "standard",
        "specialised": "technical",
        "specialized": "technical",
        "domain_specific": "technical",
        "domain-specific": "technical",
        "scholarly": "academic",
        "formal_academic": "academic",
    },
    "avg_sentence_length": {
        "brief": "short",
        "concise": "short",
        "moderate": "medium",
        "average": "medium",
        "lengthy": "long",
        "verbose": "long",
        "extended": "long",
    },
}


def _normalise_enums(data: dict) -> dict:
    """Normalise LLM-generated enum values using alias mappings.

    Mutates and returns the dict.  Handles nested sub-objects
    (writing_style, communication, relationship).
    """
    # Top-level enum fields
    for field in ("formality", "emotional_tone", "power_dynamic"):
        if field in data and isinstance(data[field], str):
            val = data[field].strip().lower().replace(" ", "_")
            data[field] = _ENUM_ALIASES.get(field, {}).get(val, val)

    # Nested writing_style
    ws = data.get("writing_style")
    if isinstance(ws, dict):
        for field in ("formality", "vocabulary_level", "avg_sentence_length"):
            if field in ws and isinstance(ws[field], str):
                val = ws[field].strip().lower().replace(" ", "_")
                ws[field] = _ENUM_ALIASES.get(field, {}).get(val, val)

    # Nested communication
    comm = data.get("communication")
    if isinstance(comm, dict):
        for field in ("primary_trait", "secondary_trait", "emotional_tone"):
            if field in comm and isinstance(comm[field], str):
                val = comm[field].strip().lower().replace(" ", "_")
                comm[field] = _ENUM_ALIASES.get(field, {}).get(val, val)

    # Nested relationship
    rel = data.get("relationship")
    if isinstance(rel, dict):
        for field in ("power_dynamic",):
            if field in rel and isinstance(rel[field], str):
                val = rel[field].strip().lower().replace(" ", "_")
                rel[field] = _ENUM_ALIASES.get(field, {}).get(val, val)

    return data


def _parse_signal(raw: str) -> tuple[PersonalitySignal | None, bool, bool]:
    """Parse raw LLM response into PersonalitySignal.

    Returns (signal, json_valid, schema_valid).
    """
    json_text = _extract_json(raw)
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None, False, False

    if not isinstance(data, dict):
        return None, True, False

    # Normalise creative enum values before Pydantic validation
    _normalise_enums(data)

    try:
        signal = PersonalitySignal.model_validate(data)
        return signal, True, True
    except Exception:
        return None, True, False


# ---------------------------------------------------------------------------
# Provider functions
# ---------------------------------------------------------------------------


async def extract_groq(
    msg: MessageSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    timeout: float = 30.0,
) -> ExtractionResult:
    """Extract personality using Groq cloud API."""
    start = time.perf_counter()
    raw = ""
    error = None
    in_tok = 0
    out_tok = 0

    try:

        async def _call():  # noqa: ANN202
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1200,
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                return resp.json()

        data = await _retry_with_backoff(_call)
        raw = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    signal, json_valid, schema_valid = _parse_signal(raw)

    return ExtractionResult(
        message_id=msg.message_id,
        persona_key=msg.persona_key,
        model=model,
        raw_response=raw[:3000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        signal=signal,
        error=error,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


async def extract_gemini(
    msg: MessageSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    semaphore: asyncio.Semaphore,
    timeout: float = 30.0,
) -> ExtractionResult:
    """Extract personality using Google Gemini REST API."""
    start = time.perf_counter()
    raw = ""
    error = None
    in_tok = 0
    out_tok = 0

    try:

        async def _call():  # noqa: ANN202
            async with semaphore:
                url = f"{GEMINI_API_URL}/{model}:generateContent" f"?key={api_key}"
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json={
                            "systemInstruction": {"parts": [{"text": system_prompt}]},
                            "contents": [{"parts": [{"text": user_prompt}]}],
                            "generationConfig": {
                                "temperature": 0.1,
                                "maxOutputTokens": 2048,
                                "responseMimeType": "application/json",
                                "thinkingConfig": {"thinkingBudget": 0},
                            },
                        },
                    )
                    resp.raise_for_status()
                    return resp.json()

        data = await _retry_with_backoff(_call)
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        usage = data.get("usageMetadata", {})
        in_tok = usage.get("promptTokenCount", 0)
        out_tok = usage.get("candidatesTokenCount", 0)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    signal, json_valid, schema_valid = _parse_signal(raw)

    return ExtractionResult(
        message_id=msg.message_id,
        persona_key=msg.persona_key,
        model=model,
        raw_response=raw[:3000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        signal=signal,
        error=error,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


# ---------------------------------------------------------------------------
# Accuracy scoring
# ---------------------------------------------------------------------------

# Formality ordering for ±1 matching
FORMALITY_ORDER = [
    "very_formal",
    "formal",
    "semi_formal",
    "casual",
    "very_casual",
]


def _formality_match(predicted: str, expected: str) -> bool:
    """Check if formality is exact or ±1 level."""
    if predicted == expected:
        return True
    try:
        pi = FORMALITY_ORDER.index(predicted)
        ei = FORMALITY_ORDER.index(expected)
        return abs(pi - ei) <= 1
    except ValueError:
        return False


def _bool_match(predicted: bool, expected: bool) -> bool:
    return predicted == expected


def _string_match(predicted: str, expected: str) -> bool:
    """Normalised string comparison."""
    return predicted.strip().lower() == expected.strip().lower()


def _enum_match(predicted: str, expected: str | None) -> bool:
    """Exact enum value match (None == None is a match)."""
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    return predicted.strip().lower() == expected.strip().lower()


def _float_error(predicted: float, expected: float) -> float:
    """Absolute error for float comparisons."""
    return abs(predicted - expected)


def score_extraction(
    signal: PersonalitySignal,
    expected: dict,
) -> dict:
    """Score a single extraction against ground truth.

    Returns a dict of metric_name -> (correct: bool or error: float).
    """
    scores: dict[str, dict] = {}

    # Writing style
    ws = expected.get("writing_style", {})
    if ws:
        scores["formality"] = {
            "correct": _formality_match(
                signal.writing_style.formality.value,
                ws.get("formality", ""),
            ),
            "predicted": signal.writing_style.formality.value,
            "expected": ws.get("formality", ""),
        }
        scores["uses_greeting"] = {
            "correct": _bool_match(
                signal.writing_style.uses_greeting,
                ws.get("uses_greeting", True),
            ),
            "predicted": signal.writing_style.uses_greeting,
            "expected": ws.get("uses_greeting", True),
        }
        scores["uses_signoff"] = {
            "correct": _bool_match(
                signal.writing_style.uses_signoff,
                ws.get("uses_signoff", True),
            ),
            "predicted": signal.writing_style.uses_signoff,
            "expected": ws.get("uses_signoff", True),
        }
        scores["uses_emoji"] = {
            "correct": _bool_match(
                signal.writing_style.uses_emoji,
                ws.get("uses_emoji", False),
            ),
            "predicted": signal.writing_style.uses_emoji,
            "expected": ws.get("uses_emoji", False),
        }
        scores["vocabulary_level"] = {
            "correct": _enum_match(
                signal.writing_style.vocabulary_level.value,
                ws.get("vocabulary_level", "standard"),
            ),
            "predicted": signal.writing_style.vocabulary_level.value,
            "expected": ws.get("vocabulary_level", "standard"),
        }
        scores["avg_sentence_length"] = {
            "correct": _enum_match(
                signal.writing_style.avg_sentence_length.value,
                ws.get("avg_sentence_length", "medium"),
            ),
            "predicted": signal.writing_style.avg_sentence_length.value,
            "expected": ws.get("avg_sentence_length", "medium"),
        }

    # Communication profile
    comm = expected.get("communication", {})
    if comm:
        scores["primary_trait"] = {
            "correct": _enum_match(
                signal.communication.primary_trait.value,
                comm.get("primary_trait", ""),
            ),
            "predicted": signal.communication.primary_trait.value,
            "expected": comm.get("primary_trait", ""),
        }
        scores["emotional_tone"] = {
            "correct": _enum_match(
                signal.communication.emotional_tone.value,
                comm.get("emotional_tone", ""),
            ),
            "predicted": signal.communication.emotional_tone.value,
            "expected": comm.get("emotional_tone", ""),
        }
        scores["assertiveness_error"] = {
            "error": _float_error(
                signal.communication.assertiveness,
                comm.get("assertiveness", 0.5),
            ),
            "predicted": signal.communication.assertiveness,
            "expected": comm.get("assertiveness", 0.5),
        }

    # Relationship dynamics
    rel = expected.get("relationship", {})
    if rel:
        scores["power_dynamic"] = {
            "correct": _enum_match(
                signal.relationship.power_dynamic.value,
                rel.get("power_dynamic", "peer"),
            ),
            "predicted": signal.relationship.power_dynamic.value,
            "expected": rel.get("power_dynamic", "peer"),
        }
        scores["familiarity_error"] = {
            "error": _float_error(
                signal.relationship.familiarity,
                rel.get("familiarity", 0.5),
            ),
            "predicted": signal.relationship.familiarity,
            "expected": rel.get("familiarity", 0.5),
        }

    return scores


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


def compute_accuracy(
    all_scores: list[dict],
    messages: list[MessageSample],
    results: list[ExtractionResult],
) -> dict:
    """Compute aggregate accuracy metrics."""
    if not all_scores:
        return {}

    # Boolean metrics (exact/fuzzy match)
    bool_metrics = [
        "formality",
        "uses_greeting",
        "uses_signoff",
        "uses_emoji",
        "vocabulary_level",
        "avg_sentence_length",
        "primary_trait",
        "emotional_tone",
        "power_dynamic",
    ]

    # Float metrics (MAE)
    float_metrics = ["assertiveness_error", "familiarity_error"]

    # Overall accuracy per boolean metric
    metric_accuracy: dict[str, dict] = {}
    for metric in bool_metrics:
        correct = sum(1 for s in all_scores if metric in s and s[metric]["correct"])
        total = sum(1 for s in all_scores if metric in s)
        metric_accuracy[metric] = {
            "correct": correct,
            "total": total,
            "accuracy": correct / total if total > 0 else 0.0,
        }

    # MAE per float metric
    for metric in float_metrics:
        errors = [s[metric]["error"] for s in all_scores if metric in s]
        metric_accuracy[metric] = {
            "mae": statistics.mean(errors) if errors else 0.0,
            "count": len(errors),
        }

    # Overall composite accuracy (average of boolean metrics)
    accuracies = [v["accuracy"] for k, v in metric_accuracy.items() if "accuracy" in v]
    overall = statistics.mean(accuracies) if accuracies else 0.0

    # Split by owner vs contact
    owner_scores = [s for s, m in zip(all_scores, messages, strict=False) if m.author_is_owner]
    contact_scores = [
        s for s, m in zip(all_scores, messages, strict=False) if not m.author_is_owner
    ]

    def _bool_accuracy(scores: list[dict]) -> float:
        if not scores:
            return 0.0
        correct = sum(1 for s in scores for m in bool_metrics if m in s and s[m]["correct"])
        total = sum(1 for s in scores for m in bool_metrics if m in s)
        return correct / total if total > 0 else 0.0

    # Per-persona consistency (std dev of trait predictions)
    persona_traits: dict[str, list[str]] = defaultdict(list)
    for s, m in zip(all_scores, messages, strict=False):
        if "primary_trait" in s:
            persona_traits[m.persona_key].append(s["primary_trait"]["predicted"])

    consistency_scores = []
    for _persona_key, traits in persona_traits.items():
        if len(traits) >= 2:
            most_common = Counter(traits).most_common(1)[0][1]
            consistency = most_common / len(traits)
            consistency_scores.append(consistency)

    # Token usage and cost
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)

    return {
        "overall_accuracy": overall,
        "owner_accuracy": _bool_accuracy(owner_scores),
        "contact_accuracy": _bool_accuracy(contact_scores),
        "per_metric": metric_accuracy,
        "persona_consistency": (statistics.mean(consistency_scores) if consistency_scores else 0.0),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }


# ---------------------------------------------------------------------------
# Provider worker coroutines
# ---------------------------------------------------------------------------


async def _run_model(
    model: str,
    provider: str,
    messages: list[MessageSample],
    api_key: str,
    owner_email: str,
    gemini_semaphore: asyncio.Semaphore | None = None,
) -> list[ExtractionResult]:
    """Run extraction for one model against all messages sequentially."""
    results: list[ExtractionResult] = []

    for i, msg in enumerate(messages):
        user_prompt = build_personality_prompt(
            subject=msg.subject,
            from_email=msg.from_email,
            to_emails=", ".join(msg.to_emails),
            body_text=msg.body_text,
            author_is_owner=msg.author_is_owner,
            owner_email=owner_email,
        )

        if provider == "groq":
            result = await extract_groq(msg, SYSTEM_PROMPT, user_prompt, model, api_key)
        elif provider == "gemini":
            assert gemini_semaphore is not None
            result = await extract_gemini(
                msg,
                SYSTEM_PROMPT,
                user_prompt,
                model,
                api_key,
                gemini_semaphore,
            )
        else:
            continue

        results.append(result)

        # Log progress
        status = "OK" if result.schema_valid else "FAIL"
        role = "owner" if msg.author_is_owner else msg.persona_key
        print(
            f"    [{model}] {i + 1}/{len(messages)} "
            f"{role:20s} {status} {result.latency_ms:.0f}ms"
        )

        # Inter-request delay
        if i < len(messages) - 1:
            await asyncio.sleep(CLOUD_INTER_REQUEST_DELAY)

    return results


async def _run_provider_group(
    provider: str,
    models: list[str],
    messages: list[MessageSample],
    api_key: str,
    owner_email: str,
) -> dict[str, list[ExtractionResult]]:
    """Run all models for one provider. Models within a provider run
    sequentially (to preserve ordering), but providers run concurrently.
    """
    all_results: dict[str, list[ExtractionResult]] = {}

    gemini_semaphore = asyncio.Semaphore(GEMINI_SEMAPHORE_LIMIT) if provider == "gemini" else None

    for model in models:
        print(f"\n  [{provider.upper()}] Starting {model}...")
        results = await _run_model(
            model,
            provider,
            messages,
            api_key,
            owner_email,
            gemini_semaphore,
        )
        all_results[model] = results

    return all_results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    model_metrics: dict[str, dict],
    model_results: dict[str, list[ExtractionResult]],
    messages: list[MessageSample],
    dataset_meta: dict,
) -> str:
    """Generate a markdown report."""
    lines = [
        "# Personality Extraction Benchmark Report",
        "",
        f"**Date**: {datetime.now(tz=UTC).isoformat()}",
        f"**Platform**: {platform.system()} {platform.machine()}",
        f"**Messages**: {len(messages)}",
        f"**Personas**: {dataset_meta.get('personas', '?')}",
        (f"**Total API calls**: " f"{sum(len(r) for r in model_results.values())}"),
        "",
        "---",
        "",
        "## Overall Leaderboard",
        "",
        (
            "| Rank | Model | Overall % | Owner % | Contact % "
            "| Consistency | Mean Latency | Cost |"
        ),
        (
            "|------|-------|-----------|---------|----------- "
            "|-------------|-------------|------|"
        ),
    ]

    # Sort models by overall accuracy descending
    ranked = sorted(
        model_metrics.items(),
        key=lambda x: x[1].get("overall_accuracy", 0),
        reverse=True,
    )

    for rank, (model, metrics) in enumerate(ranked, 1):
        results = model_results[model]
        latencies = [r.latency_ms for r in results if r.error is None]
        mean_lat = statistics.mean(latencies) if latencies else 0.0

        model_info = CLOUD_MODELS.get(model, {})
        in_cost = (
            metrics.get("total_input_tokens", 0) * model_info.get("input_per_m", 0) / 1_000_000
        )
        out_cost = (
            metrics.get("total_output_tokens", 0) * model_info.get("output_per_m", 0) / 1_000_000
        )
        total_cost = in_cost + out_cost

        lines.append(
            f"| {rank} | {model} "
            f"| {metrics.get('overall_accuracy', 0) * 100:.1f}% "
            f"| {metrics.get('owner_accuracy', 0) * 100:.1f}% "
            f"| {metrics.get('contact_accuracy', 0) * 100:.1f}% "
            f"| {metrics.get('persona_consistency', 0) * 100:.1f}% "
            f"| {mean_lat:.0f}ms "
            f"| ${total_cost:.4f} |"
        )

    lines += ["", "---", "", "## Per-Metric Accuracy", ""]
    lines.append("| Metric | " + " | ".join(m for m, _ in ranked) + " |")
    lines.append("|--------|" + "|".join("-----" for _ in ranked) + "|")

    bool_metrics = [
        "formality",
        "uses_greeting",
        "uses_signoff",
        "uses_emoji",
        "vocabulary_level",
        "avg_sentence_length",
        "primary_trait",
        "emotional_tone",
        "power_dynamic",
    ]
    float_metrics = ["assertiveness_error", "familiarity_error"]

    for metric in bool_metrics:
        row = f"| {metric} |"
        for _model, metrics in ranked:
            pm = metrics.get("per_metric", {}).get(metric, {})
            acc = pm.get("accuracy", 0)
            row += f" {acc * 100:.1f}% |"
        lines.append(row)

    for metric in float_metrics:
        row = f"| {metric} (MAE) |"
        for _model, metrics in ranked:
            pm = metrics.get("per_metric", {}).get(metric, {})
            mae = pm.get("mae", 0)
            row += f" {mae:.3f} |"
        lines.append(row)

    # Token usage
    lines += ["", "---", "", "## Token Usage & Cost", ""]
    lines.append("| Model | Input Tokens | Output Tokens | Cost |")
    lines.append("|-------|-------------|--------------|------|")
    for model, metrics in ranked:
        in_t = metrics.get("total_input_tokens", 0)
        out_t = metrics.get("total_output_tokens", 0)
        model_info = CLOUD_MODELS.get(model, {})
        cost = (
            in_t * model_info.get("input_per_m", 0) / 1_000_000
            + out_t * model_info.get("output_per_m", 0) / 1_000_000
        )
        lines.append(f"| {model} | {in_t:,} | {out_t:,} | ${cost:.4f} |")

    # Schema compliance
    lines += ["", "---", "", "## Schema Compliance", ""]
    lines.append("| Model | JSON Valid | Schema Valid |")
    lines.append("|-------|-----------|-------------|")
    for model, _ in ranked:
        results = model_results[model]
        jv = sum(1 for r in results if r.json_valid)
        sv = sum(1 for r in results if r.schema_valid)
        total = len(results)
        lines.append(
            f"| {model} | {jv}/{total} ({jv / total * 100:.0f}%) "
            f"| {sv}/{total} ({sv / total * 100:.0f}%) |"
        )

    lines += [
        "",
        "---",
        "",
        "*Generated by scripts/benchmark-personality-extractor.py*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------


async def run_benchmark(args: argparse.Namespace) -> None:
    """Run the personality extraction benchmark."""
    # Load dataset
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}")
        sys.exit(1)

    with dataset_path.open() as f:
        dataset = json.load(f)

    raw_messages = dataset["messages"]
    metadata = dataset["metadata"]
    owner_email = dataset.get("owner_persona", {}).get("email", "james@zetherion.com")

    # Filter by contacts if specified
    if args.contacts:
        contact_set = set(args.contacts)
        raw_messages = [
            m for m in raw_messages if m["persona_key"] in contact_set or m["author_is_owner"]
        ]
        # Only keep owner messages that are part of threads with
        # selected contacts
        selected_threads = {m["thread_id"] for m in raw_messages if m["persona_key"] in contact_set}
        raw_messages = [m for m in raw_messages if m["thread_id"] in selected_threads]
        print(f"Filtered to {len(raw_messages)} messages " f"from contacts: {args.contacts}")

    # Apply max-messages limit
    if args.max_messages and args.max_messages < len(raw_messages):
        raw_messages = raw_messages[: args.max_messages]

    # Convert to MessageSample objects
    messages = [
        MessageSample(
            message_id=m["message_id"],
            thread_id=m["thread_id"],
            thread_position=m["thread_position"],
            persona_key=m["persona_key"],
            subject=m["subject"],
            from_email=m["from_email"],
            to_emails=m["to_emails"],
            body_text=m["body_text"],
            author_is_owner=m["author_is_owner"],
            expected_personality=m["expected_personality"],
        )
        for m in raw_messages
    ]

    print(f"Loaded {len(messages)} messages from {dataset_path}")

    # Determine which models to run
    if args.models:
        selected = {m for m in args.models if m in CLOUD_MODELS}
    else:
        selected = set(CLOUD_MODELS.keys())

    # Load API keys from .env
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    groq_key = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    # Group models by provider
    provider_models: dict[str, list[str]] = defaultdict(list)
    for model in selected:
        info = CLOUD_MODELS[model]
        provider = info["provider"]
        if provider == "groq" and not groq_key:
            print(f"  [SKIP] {model} — no GROQ_API_KEY")
            continue
        if provider == "gemini" and not gemini_key:
            print(f"  [SKIP] {model} — no GEMINI_API_KEY")
            continue
        provider_models[provider].append(model)

    if not provider_models:
        print("No models available to test. Check API keys.")
        sys.exit(1)

    print(f"\nModels to test: {dict(provider_models)}")
    print(f"Messages per model: {len(messages)}")

    # Run providers concurrently
    api_keys = {"groq": groq_key, "gemini": gemini_key}

    tasks = []
    for provider, models in provider_models.items():
        tasks.append(
            _run_provider_group(
                provider,
                models,
                messages,
                api_keys[provider],
                owner_email,
            )
        )

    print("\nRunning providers concurrently...")
    start_time = time.perf_counter()
    provider_results = await asyncio.gather(*tasks)
    wall_time = time.perf_counter() - start_time

    # Merge results
    all_model_results: dict[str, list[ExtractionResult]] = {}
    for pr in provider_results:
        all_model_results.update(pr)

    print(f"\nAll models complete in {wall_time:.1f}s")

    # Score each model
    model_metrics: dict[str, dict] = {}
    for model, results in all_model_results.items():
        # Align results with messages for scoring
        valid_pairs = [
            (r, m)
            for r, m in zip(results, messages, strict=False)
            if r.schema_valid and r.signal is not None
        ]

        scores = [score_extraction(r.signal, m.expected_personality) for r, m in valid_pairs]
        valid_messages = [m for _, m in valid_pairs]

        metrics = compute_accuracy(scores, valid_messages, results)
        model_metrics[model] = metrics

        print(
            f"\n  {model}: "
            f"{metrics.get('overall_accuracy', 0) * 100:.1f}% overall, "
            f"{metrics.get('owner_accuracy', 0) * 100:.1f}% owner, "
            f"{metrics.get('contact_accuracy', 0) * 100:.1f}% contact, "
            f"{metrics.get('persona_consistency', 0) * 100:.1f}% consistency"
        )

    # Generate reports
    report = generate_report(
        model_metrics,
        all_model_results,
        messages,
        metadata,
    )

    # Save results
    results_dir = Path("benchmarks/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    md_path = results_dir / f"personality_benchmark_{timestamp}.md"
    json_path = results_dir / f"personality_benchmark_{timestamp}.json"

    md_path.write_text(report)
    print(f"\nMarkdown report: {md_path}")

    # Save detailed JSON results
    json_data = {
        "metadata": {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "platform": f"{platform.system()} {platform.machine()}",
            "messages": len(messages),
            "wall_time_seconds": wall_time,
            "dataset": str(dataset_path),
        },
        "model_metrics": model_metrics,
        "detailed_results": {
            model: [
                {
                    "message_id": r.message_id,
                    "persona_key": r.persona_key,
                    "latency_ms": r.latency_ms,
                    "json_valid": r.json_valid,
                    "schema_valid": r.schema_valid,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "signal": (r.signal.to_dict() if r.signal else None),
                    "error": r.error,
                }
                for r in results
            ]
            for model, results in all_model_results.items()
        },
    }
    json_path.write_text(json.dumps(json_data, indent=2, default=str))
    print(f"JSON results: {json_path}")

    print(f"\n{'=' * 60}")
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Personality extraction benchmark")
    parser.add_argument(
        "--dataset",
        default="benchmarks/datasets/personality_conversations.json",
        help="Path to personality conversation dataset",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        help="Specific models to test",
    )
    parser.add_argument(
        "--contacts",
        nargs="+",
        help="Filter to specific contact persona keys",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="Maximum messages to process per model",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per model (not yet implemented)",
    )
    args = parser.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
