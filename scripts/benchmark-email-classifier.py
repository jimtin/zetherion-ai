#!/usr/bin/env python3
"""Email classification benchmark for Zetherion AI.

Tests multiple LLM models against a set of real emails fetched from a
connected Gmail account.  Measures JSON schema compliance, cross-model
consensus, category/urgency distributions, contact extraction quality,
latency, and cost.

Usage:
    python scripts/benchmark-email-classifier.py --fetch               # Fetch + benchmark
    python scripts/benchmark-email-classifier.py --dataset emails.json # Pre-fetched
    python scripts/benchmark-email-classifier.py --local-only          # Ollama only
    python scripts/benchmark-email-classifier.py --cloud-only          # Groq only
    python scripts/benchmark-email-classifier.py --models llama3.2:3b  # Specific model
    python scripts/benchmark-email-classifier.py --runs 3              # Multiple runs
    python scripts/benchmark-email-classifier.py --max-emails 2000     # Scale up
"""

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import subprocess  # nosec B404
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Ensure the src package is importable when running as a standalone script.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zetherion_ai.routing.classification import EmailClassification  # noqa: E402
from zetherion_ai.routing.classification_prompt import (  # noqa: E402
    SYSTEM_PROMPT,
    build_classification_prompt,
)

# Max retries for rate-limited cloud API calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry

# Delay between consecutive cloud API calls to respect rate limits
CLOUD_INTER_REQUEST_DELAY = 0.5  # seconds

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"


async def _retry_with_backoff(coro_factory, max_retries=MAX_RETRIES):  # noqa: ANN001
    """Retry an async operation with exponential backoff on transient errors.

    *coro_factory* is a zero-arg callable that returns a new coroutine each
    time.
    """
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            err_str = str(e).lower()
            is_retryable = "429" in err_str or "rate" in err_str or "quota" in err_str
            if is_retryable and attempt < max_retries:
                delay = RETRY_BASE_DELAY * (2**attempt)
                print(f"    [RETRY] Rate limited, waiting {delay:.0f}s (attempt {attempt + 1})")
                await asyncio.sleep(delay)
            else:
                raise


# ---------------------------------------------------------------------------
# Section 1: Config & Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EmailSample:
    """A fetched email for benchmarking."""

    email_id: str
    subject: str
    from_email: str
    to_emails: list[str]
    body_text: str
    received_at: str
    thread_id: str = ""


@dataclass
class ClassificationResult:
    """Result from classifying one email with one model."""

    email_id: str
    subject: str
    model: str
    prompt_version: str
    raw_response: str
    latency_ms: float
    json_valid: bool
    schema_valid: bool
    classification: EmailClassification | None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelPromptResult:
    """Aggregated results for one model+prompt combination."""

    model: str
    prompt_version: str
    results: list[ClassificationResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Section 2: Email Dataset (fetch or load)
# ---------------------------------------------------------------------------


def _parse_gmail_message(raw: dict) -> EmailSample:  # noqa: ANN001
    """Parse a raw Gmail API message response into an EmailSample."""
    headers = {}
    payload = raw.get("payload", {})
    for h in payload.get("headers", []):
        headers[h["name"].lower()] = h["value"]

    # Extract body text
    body_text = ""
    parts = payload.get("parts", [])
    if not parts:
        # Single-part message
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            import base64

            body_text = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
    else:
        # Multi-part: look for text/plain
        for part in parts:
            if part.get("mimeType") == "text/plain":
                body_data = part.get("body", {}).get("data", "")
                if body_data:
                    import base64

                    body_text = base64.urlsafe_b64decode(body_data + "==").decode(
                        "utf-8", errors="replace"
                    )
                    break
        # Fallback: try text/html if no text/plain
        if not body_text:
            for part in parts:
                if part.get("mimeType") == "text/html":
                    body_data = part.get("body", {}).get("data", "")
                    if body_data:
                        import base64

                        body_text = base64.urlsafe_b64decode(body_data + "==").decode(
                            "utf-8", errors="replace"
                        )
                        break

    # Parse recipients
    to_raw = headers.get("to", "")
    to_emails = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

    # Timestamp
    internal_date = raw.get("internalDate", "0")
    try:
        received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC).isoformat()
    except (ValueError, OSError):
        received_at = ""

    return EmailSample(
        email_id=raw.get("id", ""),
        subject=headers.get("subject", ""),
        from_email=headers.get("from", ""),
        to_emails=to_emails,
        body_text=body_text[:6000],
        received_at=received_at,
        thread_id=raw.get("threadId", ""),
    )


async def fetch_email_dataset(
    *,
    max_emails: int = 50,
    gmail_token_path: str = "data/gmail_token.json",
) -> list[EmailSample]:
    """Fetch emails from Gmail for benchmarking.

    Uses stored OAuth credentials.  Requires prior Gmail connection
    through the main application.
    """
    token_path = Path(gmail_token_path)
    if not token_path.exists():
        print(f"ERROR: Gmail token file not found at {token_path}")
        print("Connect Gmail through the main application first, or provide --dataset.")
        sys.exit(1)

    token_data = json.loads(token_path.read_text())
    access_token = token_data.get("access_token", "")
    if not access_token:
        print("ERROR: No access_token found in Gmail token file.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {access_token}"}

    print(f"Fetching up to {max_emails} emails from Gmail...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # List message IDs
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params={"maxResults": max_emails},
        )
        if resp.status_code == 401:
            print("ERROR: Gmail access token expired. Re-authenticate through the main app.")
            sys.exit(1)
        resp.raise_for_status()
        message_stubs = resp.json().get("messages", [])
        print(f"  Found {len(message_stubs)} messages")

        # Fetch full messages
        samples: list[EmailSample] = []
        for i, stub in enumerate(message_stubs):
            if i % 10 == 0 and i > 0:
                print(f"  [{i}/{len(message_stubs)}] Fetching messages...")
            resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{stub['id']}",
                headers=headers,
                params={"format": "full"},
            )
            resp.raise_for_status()
            samples.append(_parse_gmail_message(resp.json()))
            # Respect Gmail rate limits
            await asyncio.sleep(0.1)

    print(f"  Fetched {len(samples)} emails")
    return samples


def load_dataset(path: str) -> list[EmailSample]:
    """Load pre-fetched email dataset from JSON."""
    data = json.loads(Path(path).read_text())
    emails_raw = data.get("emails", data) if isinstance(data, dict) else data
    return [
        EmailSample(
            email_id=item.get("email_id", item.get("gmail_id", "")),
            subject=item.get("subject", ""),
            from_email=item.get("from_email", ""),
            to_emails=item.get("to_emails", []),
            body_text=item.get("body_text", ""),
            received_at=item.get("received_at", ""),
            thread_id=item.get("thread_id", ""),
        )
        for item in emails_raw
    ]


def save_dataset(samples: list[EmailSample], path: str) -> None:
    """Save fetched emails to JSON for reuse."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "metadata": {
            "fetched_at": datetime.now(tz=UTC).isoformat(),
            "count": len(samples),
        },
        "emails": [
            {
                "email_id": s.email_id,
                "subject": s.subject,
                "from_email": s.from_email,
                "to_emails": s.to_emails,
                "body_text": s.body_text[:6000],
                "received_at": s.received_at,
                "thread_id": s.thread_id,
            }
            for s in samples
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  Dataset saved to {path}")


# ---------------------------------------------------------------------------
# Section 3: Prompt Definitions
# ---------------------------------------------------------------------------

# v0: Production prompt (imported from classification_prompt module)
# Additional prompt versions can be added here for A/B testing.

PROMPTS: dict[str, tuple[str, str]] = {}
# Populated dynamically per-email via build_classification_prompt()
PROMPT_VERSIONS = ["v0"]


def _build_prompt_for_email(email: EmailSample, prompt_version: str) -> tuple[str, str]:
    """Build system + user prompt pair for a given email and prompt version."""
    if prompt_version == "v0":
        user_prompt = build_classification_prompt(
            subject=email.subject,
            from_email=email.from_email,
            to_emails=", ".join(email.to_emails),
            body_text=email.body_text,
            user_timezone="UTC",
            current_datetime=datetime.now(tz=UTC).isoformat(),
        )
        return SYSTEM_PROMPT, user_prompt
    # Future prompt versions can be added here
    return SYSTEM_PROMPT, build_classification_prompt(
        subject=email.subject,
        from_email=email.from_email,
        to_emails=", ".join(email.to_emails),
        body_text=email.body_text,
    )


# ---------------------------------------------------------------------------
# Section 4: Model Backends
# ---------------------------------------------------------------------------

LOCAL_MODELS: dict[str, dict] = {
    "llama3.2:3b": {"size_gb": 2.0, "provider": "ollama"},
    "llama3.1:8b": {"size_gb": 4.7, "provider": "ollama"},
}

CLOUD_MODELS: dict[str, dict] = {
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "input_per_m": 0.59,
        "output_per_m": 0.79,
    },
    "llama-3.1-8b-instant": {
        "provider": "groq",
        "input_per_m": 0.05,
        "output_per_m": 0.08,
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "provider": "groq",
        "input_per_m": 0.11,
        "output_per_m": 0.34,
    },
    "meta-llama/llama-4-maverick-17b-128e-instruct": {
        "provider": "groq",
        "input_per_m": 0.20,
        "output_per_m": 0.60,
    },
    "qwen/qwen3-32b": {
        "provider": "groq",
        "input_per_m": 0.29,
        "output_per_m": 0.39,
    },
    "openai/gpt-oss-120b": {
        "provider": "groq",
        "input_per_m": 0.0,
        "output_per_m": 0.0,
    },
    "openai/gpt-oss-20b": {
        "provider": "groq",
        "input_per_m": 0.0,
        "output_per_m": 0.0,
    },
    "moonshotai/kimi-k2-instruct": {
        "provider": "groq",
        "input_per_m": 0.0,
        "output_per_m": 0.0,
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
    # OpenAI
    "gpt-4.1-mini": {
        "provider": "openai",
        "input_per_m": 0.40,
        "output_per_m": 1.60,
    },
    "gpt-4.1": {
        "provider": "openai",
        "input_per_m": 2.00,
        "output_per_m": 8.00,
    },
}


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may contain markdown or extra text."""
    # Try markdown code block first
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()
    # Try raw JSON object
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        return json_match.group(0).strip()
    return text.strip()


def _parse_classification(raw: str) -> tuple[EmailClassification | None, bool, bool]:
    """Parse raw LLM response into EmailClassification.

    Returns ``(classification, json_valid, schema_valid)``.
    """
    json_text = _extract_json(raw)
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None, False, False

    if not isinstance(data, dict):
        return None, True, False

    try:
        classification = EmailClassification.model_validate(data)
        return classification, True, True
    except Exception:
        # JSON was valid but didn't match schema
        return None, True, False


async def classify_ollama(
    email: EmailSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    ollama_url: str,
    timeout: float = 60.0,
) -> ClassificationResult:
    """Classify an email using local Ollama model."""
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    start = time.perf_counter()
    raw = ""
    error = None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": False,
                    "format": "json",
                    "keep_alive": "10m",
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 800,
                    },
                },
            )
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    classification, json_valid, schema_valid = _parse_classification(raw)

    return ClassificationResult(
        email_id=email.email_id,
        subject=email.subject,
        model=model,
        prompt_version="",
        raw_response=raw[:2000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        classification=classification,
        error=error,
    )


async def classify_groq(
    email: EmailSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    timeout: float = 30.0,
) -> ClassificationResult:
    """Classify an email using Groq cloud API (OpenAI-compatible)."""
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
                        "max_tokens": 800,
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
    classification, json_valid, schema_valid = _parse_classification(raw)

    return ClassificationResult(
        email_id=email.email_id,
        subject=email.subject,
        model=model,
        prompt_version="",
        raw_response=raw[:2000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        classification=classification,
        error=error,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


async def classify_openai(
    email: EmailSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    timeout: float = 30.0,
) -> ClassificationResult:
    """Classify an email using OpenAI chat completions API."""
    start = time.perf_counter()
    raw = ""
    error = None
    in_tok = 0
    out_tok = 0

    try:

        async def _call():  # noqa: ANN202
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    OPENAI_API_URL,
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
                        "max_tokens": 800,
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
    classification, json_valid, schema_valid = _parse_classification(raw)

    return ClassificationResult(
        email_id=email.email_id,
        subject=email.subject,
        model=model,
        prompt_version="",
        raw_response=raw[:2000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        classification=classification,
        error=error,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


async def classify_gemini(
    email: EmailSample,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
    timeout: float = 30.0,
) -> ClassificationResult:
    """Classify an email using Google Gemini REST API."""
    start = time.perf_counter()
    raw = ""
    error = None
    in_tok = 0
    out_tok = 0

    try:

        async def _call():  # noqa: ANN202
            url = f"{GEMINI_API_URL}/{model}:generateContent?key={api_key}"
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
    classification, json_valid, schema_valid = _parse_classification(raw)

    return ClassificationResult(
        email_id=email.email_id,
        subject=email.subject,
        model=model,
        prompt_version="",
        raw_response=raw[:2000],
        latency_ms=latency_ms,
        json_valid=json_valid,
        schema_valid=schema_valid,
        classification=classification,
        error=error,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


# ---------------------------------------------------------------------------
# Section 5: Metrics Computation
# ---------------------------------------------------------------------------


def compute_metrics(results: list[ClassificationResult]) -> dict:
    """Compute classification quality metrics for one model+prompt combination."""
    total = len(results)
    if total == 0:
        return {}

    json_valid = sum(1 for r in results if r.json_valid)
    schema_valid = sum(1 for r in results if r.schema_valid)
    latencies = [r.latency_ms for r in results if r.error is None]
    errors = sum(1 for r in results if r.error is not None)

    # Category distribution
    categories = Counter(r.classification.category for r in results if r.classification is not None)

    # Action distribution
    actions = Counter(
        r.classification.action.value for r in results if r.classification is not None
    )

    # Urgency stats
    urgencies = [r.classification.urgency for r in results if r.classification is not None]
    urgency_mean = statistics.mean(urgencies) if urgencies else 0.0
    urgency_high = sum(1 for u in urgencies if u >= 0.7)

    # Sentiment distribution
    sentiments = Counter(
        r.classification.sentiment.value for r in results if r.classification is not None
    )

    # Contact extraction quality
    contacts_with_name = sum(
        1 for r in results if r.classification and r.classification.contact.name
    )
    contacts_with_company = sum(
        1 for r in results if r.classification and r.classification.contact.company
    )
    contacts_with_role = sum(
        1 for r in results if r.classification and r.classification.contact.role
    )

    # Topic richness
    topic_counts = [len(r.classification.topics) for r in results if r.classification is not None]
    avg_topics = statistics.mean(topic_counts) if topic_counts else 0.0

    # Thread detection
    threads_detected = sum(
        1 for r in results if r.classification and r.classification.thread.is_thread
    )

    # Urgency flagged
    urgent_count = sum(1 for r in results if r.classification and r.classification.is_urgent())

    return {
        "total": total,
        "json_compliance": round(json_valid / total, 4),
        "schema_compliance": round(schema_valid / total, 4),
        "error_rate": round(errors / total, 4),
        "mean_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "p95_latency_ms": (
            round(sorted(latencies)[int(len(latencies) * 0.95)], 1)
            if len(latencies) >= 2
            else (round(latencies[0], 1) if latencies else 0)
        ),
        "category_distribution": dict(categories.most_common()),
        "action_distribution": dict(actions.most_common()),
        "sentiment_distribution": dict(sentiments.most_common()),
        "urgency_mean": round(urgency_mean, 3),
        "urgency_high_count": urgency_high,
        "urgent_notification_count": urgent_count,
        "contact_name_rate": round(contacts_with_name / total, 4),
        "contact_company_rate": round(contacts_with_company / total, 4),
        "contact_role_rate": round(contacts_with_role / total, 4),
        "avg_topics_per_email": round(avg_topics, 2),
        "threads_detected": threads_detected,
    }


def compute_consensus_metrics(
    all_results: dict[str, ModelPromptResult],
    emails: list[EmailSample],
) -> dict:
    """Compute cross-model consensus on the same emails.

    For each email, check how many models agree on category and action.
    """
    email_categories: dict[str, list[str]] = defaultdict(list)
    email_actions: dict[str, list[str]] = defaultdict(list)
    email_urgencies: dict[str, list[float]] = defaultdict(list)

    for _key, mpr in all_results.items():
        for result in mpr.results:
            if result.classification is not None:
                email_categories[result.email_id].append(result.classification.category)
                email_actions[result.email_id].append(result.classification.action.value)
                email_urgencies[result.email_id].append(result.classification.urgency)

    category_agreements: list[float] = []
    action_agreements: list[float] = []

    for email_id in email_categories:
        cats = email_categories[email_id]
        if len(cats) >= 2:
            most_common_count = Counter(cats).most_common(1)[0][1]
            category_agreements.append(most_common_count / len(cats))

        acts = email_actions[email_id]
        if len(acts) >= 2:
            most_common_count = Counter(acts).most_common(1)[0][1]
            action_agreements.append(most_common_count / len(acts))

    # Urgency spread: how much do models disagree on urgency per email?
    urgency_spreads: list[float] = []
    for email_id in email_urgencies:
        vals = email_urgencies[email_id]
        if len(vals) >= 2:
            urgency_spreads.append(max(vals) - min(vals))

    return {
        "category_consensus": (
            round(statistics.mean(category_agreements), 4) if category_agreements else 0.0
        ),
        "action_consensus": (
            round(statistics.mean(action_agreements), 4) if action_agreements else 0.0
        ),
        "emails_with_unanimous_category": sum(1 for a in category_agreements if a == 1.0),
        "emails_with_unanimous_action": sum(1 for a in action_agreements if a == 1.0),
        "urgency_mean_spread": (
            round(statistics.mean(urgency_spreads), 3) if urgency_spreads else 0.0
        ),
        "total_emails_compared": len(email_categories),
    }


# ---------------------------------------------------------------------------
# Section 6: Report Generation
# ---------------------------------------------------------------------------


def generate_json_report(
    all_results: dict[str, ModelPromptResult],
    all_metrics: dict[str, dict],
    consensus: dict,
    metadata: dict,
) -> dict:
    """Generate the full JSON report."""
    # Build leaderboard sorted by schema compliance then latency
    leaderboard = []
    for key, metrics in all_metrics.items():
        model, prompt_ver = key.rsplit("_", 1)
        mpr = all_results[key]
        leaderboard.append(
            {
                "model": model,
                "prompt": prompt_ver,
                "schema_compliance": metrics["schema_compliance"],
                "json_compliance": metrics["json_compliance"],
                "mean_latency_ms": metrics["mean_latency_ms"],
                "p95_latency_ms": metrics["p95_latency_ms"],
                "contact_name_rate": metrics["contact_name_rate"],
                "contact_company_rate": metrics["contact_company_rate"],
                "avg_topics": metrics["avg_topics_per_email"],
                "urgency_high_count": metrics["urgency_high_count"],
                "total_input_tokens": mpr.total_input_tokens,
                "total_output_tokens": mpr.total_output_tokens,
            }
        )
    leaderboard.sort(key=lambda x: (-x["schema_compliance"], x["mean_latency_ms"]))

    # Detailed per-combination results
    detailed: dict[str, dict] = {}
    for key, metrics in all_metrics.items():
        mpr = all_results[key]
        detailed[key] = {
            "metrics": metrics,
            "per_email": [
                {
                    "email_id": r.email_id,
                    "subject": r.subject[:100],
                    "category": r.classification.category if r.classification else None,
                    "action": r.classification.action.value if r.classification else None,
                    "urgency": r.classification.urgency if r.classification else None,
                    "confidence": r.classification.confidence if r.classification else None,
                    "contact_name": r.classification.contact.name if r.classification else None,
                    "topics": r.classification.topics if r.classification else [],
                    "summary": r.classification.summary if r.classification else "",
                    "latency_ms": round(r.latency_ms, 1),
                    "json_valid": r.json_valid,
                    "schema_valid": r.schema_valid,
                    "error": r.error,
                }
                for r in mpr.results
            ],
        }

    return {
        "metadata": metadata,
        "summary": {"leaderboard": leaderboard, "consensus": consensus},
        "detailed_results": detailed,
    }


def generate_markdown_report(
    all_metrics: dict[str, dict],
    consensus: dict,
    metadata: dict,
    all_results: dict[str, ModelPromptResult],
) -> str:
    """Generate a human-readable markdown report."""
    lines = [
        "# Email Classification Benchmark Report",
        "",
        f"**Date**: {metadata['timestamp']}",
        f"**Platform**: {metadata.get('platform', 'Unknown')}",
        f"**Emails**: {metadata.get('n_emails', 'N/A')}",
        f"**Runs per combination**: {metadata.get('runs', 'N/A')}",
        f"**Total API calls**: {metadata.get('total_calls', 'N/A')}",
        "",
        "---",
        "",
        "## Overall Leaderboard",
        "",
        "| Rank | Model | Schema % | JSON % | Contact % | Avg Topics "
        "| Mean Latency | P95 Latency | Cost |",
        "|------|-------|----------|--------|-----------|------------|"
        "-------------|-------------|------|",
    ]

    sorted_keys = sorted(
        all_metrics.keys(),
        key=lambda k: (-all_metrics[k]["schema_compliance"], all_metrics[k]["mean_latency_ms"]),
    )

    for rank, key in enumerate(sorted_keys, 1):
        m = all_metrics[key]
        model, _pv = key.rsplit("_", 1)
        mpr = all_results.get(key)
        cost = 0.0
        if mpr:
            cost = estimate_cost(mpr.model, mpr.total_input_tokens, mpr.total_output_tokens)
        lines.append(
            f"| {rank} | {model} "
            f"| {m['schema_compliance']:.0%} | {m['json_compliance']:.0%} "
            f"| {m['contact_name_rate']:.0%} | {m['avg_topics_per_email']:.1f} "
            f"| {m['mean_latency_ms']:.0f}ms | {m['p95_latency_ms']:.0f}ms "
            f"| ${cost:.4f} |"
        )

    # Token usage table
    lines.extend(["", "---", "", "## Token Usage & Cost", ""])
    lines.append("| Model | Input Tokens | Output Tokens | Cost |")
    lines.append("|-------|-------------|--------------|------|")
    for key in sorted_keys:
        model, _pv = key.rsplit("_", 1)
        mpr = all_results.get(key)
        if mpr:
            cost = estimate_cost(mpr.model, mpr.total_input_tokens, mpr.total_output_tokens)
            lines.append(
                f"| {model} | {mpr.total_input_tokens:,} "
                f"| {mpr.total_output_tokens:,} | ${cost:.4f} |"
            )

    # Cross-model consensus
    lines.extend(
        [
            "",
            "---",
            "",
            "## Cross-Model Consensus",
            "",
            f"- **Category consensus**: {consensus.get('category_consensus', 0):.1%}",
            f"- **Action consensus**: {consensus.get('action_consensus', 0):.1%}",
            f"- **Unanimous category**: "
            f"{consensus.get('emails_with_unanimous_category', 0)} / "
            f"{consensus.get('total_emails_compared', 0)} emails",
            f"- **Unanimous action**: "
            f"{consensus.get('emails_with_unanimous_action', 0)} / "
            f"{consensus.get('total_emails_compared', 0)} emails",
            f"- **Urgency mean spread**: {consensus.get('urgency_mean_spread', 0):.3f}",
        ]
    )

    # Category distribution per model
    lines.extend(["", "---", "", "## Category Distribution", ""])
    for key in sorted_keys:
        m = all_metrics[key]
        model, _pv = key.rsplit("_", 1)
        dist = m.get("category_distribution", {})
        top_cats = ", ".join(f"{cat}: {count}" for cat, count in list(dist.items())[:6])
        lines.append(f"**{model}**: {top_cats}")

    # Action distribution per model
    lines.extend(["", "---", "", "## Action Distribution", ""])
    for key in sorted_keys:
        m = all_metrics[key]
        model, _pv = key.rsplit("_", 1)
        dist = m.get("action_distribution", {})
        top_acts = ", ".join(f"{act}: {count}" for act, count in dist.items())
        lines.append(f"**{model}**: {top_acts}")

    # Urgency analysis
    lines.extend(["", "---", "", "## Urgency Analysis", ""])
    lines.append("| Model | Mean Urgency | High Urgency Count | Urgent Notifications |")
    lines.append("|-------|--------------|--------------------|---------------------|")
    for key in sorted_keys:
        m = all_metrics[key]
        model, _pv = key.rsplit("_", 1)
        lines.append(
            f"| {model} | {m['urgency_mean']:.3f} "
            f"| {m['urgency_high_count']} | {m['urgent_notification_count']} |"
        )

    # Contact extraction quality
    lines.extend(["", "---", "", "## Contact Extraction Quality", ""])
    lines.append("| Model | Name % | Company % | Role % |")
    lines.append("|-------|--------|-----------|--------|")
    for key in sorted_keys:
        m = all_metrics[key]
        model, _pv = key.rsplit("_", 1)
        lines.append(
            f"| {model} | {m['contact_name_rate']:.0%} "
            f"| {m['contact_company_rate']:.0%} | {m['contact_role_rate']:.0%} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "*Generated by scripts/benchmark-email-classifier.py*",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 7: Hardware Detection & Cost Calculation
# ---------------------------------------------------------------------------


def detect_hardware_brief() -> dict:
    """Detect basic hardware info for report metadata."""
    info = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
        info["cpu_count"] = psutil.cpu_count(logical=False)
    except ImportError:
        pass
    return info


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a model's token usage."""
    if model in LOCAL_MODELS:
        return 0.0
    info = CLOUD_MODELS.get(model, {})
    input_cost = info.get("input_per_m", 0) * input_tokens / 1_000_000
    output_cost = info.get("output_per_m", 0) * output_tokens / 1_000_000
    return input_cost + output_cost


# ---------------------------------------------------------------------------
# Section 8: Main Orchestration
# ---------------------------------------------------------------------------


async def pull_ollama_model(model: str, ollama_url: str) -> bool:
    """Pull an Ollama model if not already available."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            resp.raise_for_status()
            available = [m["name"] for m in resp.json().get("models", [])]
            if model in available or f"{model}:latest" in available:
                print(f"  [OK] {model} already available")
                return True
    except Exception as e:
        print(f"  [WARN] Could not check models: {e}")

    print(f"  [PULL] Downloading {model}...")
    try:
        result = subprocess.run(  # nosec B603 B607
            ["ollama", "pull", model],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            print(f"  [OK] {model} pulled successfully")
            return True
        print(f"  [FAIL] {model}: exit={result.returncode} {result.stderr[:200]}")
        return False
    except FileNotFoundError:
        print(f"  [FAIL] {model}: ollama CLI not found in PATH")
        return False
    except subprocess.TimeoutExpired:
        print(f"  [FAIL] {model}: pull timed out after 600s")
        return False
    except Exception as e:
        print(f"  [FAIL] {model}: {e}")
        return False


async def warmup_ollama(model: str, ollama_url: str) -> None:
    """Send a warmup request to load model into memory."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "Hello",
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {"num_predict": 5},
                },
            )
            print(f"  [WARM] {model} loaded into memory")
    except Exception as e:
        print(f"  [WARN] Warmup failed for {model}: {e}")


async def run_benchmark(args: argparse.Namespace) -> None:
    """Run the full benchmark."""
    start_time = time.perf_counter()

    # Load API keys from .env
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    groq_key = os.environ.get("GROQ_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    ollama_url = args.ollama_url

    # --- Load or fetch dataset ---
    if args.fetch:
        emails = await fetch_email_dataset(
            max_emails=args.max_emails,
            gmail_token_path=args.gmail_token,
        )
        timestamp_str = datetime.now().strftime("%Y%m%d")
        dataset_path = f"benchmarks/datasets/emails_{timestamp_str}.json"
        save_dataset(emails, dataset_path)
    elif args.dataset:
        emails = load_dataset(args.dataset)
        print(f"Loaded {len(emails)} emails from {args.dataset}")
    else:
        print("ERROR: Provide --fetch to download emails or --dataset <path> to load them.")
        sys.exit(1)

    if not emails:
        print("ERROR: No emails in dataset.")
        sys.exit(1)

    # --- Determine which models to run ---
    models_to_run: dict[str, dict] = {}

    if args.models:
        for m in args.models:
            if m in LOCAL_MODELS:
                models_to_run[m] = LOCAL_MODELS[m]
            elif m in CLOUD_MODELS:
                models_to_run[m] = CLOUD_MODELS[m]
            else:
                print(f"  [WARN] Unknown model: {m}, skipping")
    else:
        if not args.cloud_only:
            models_to_run.update(LOCAL_MODELS)
        if not args.local_only:
            models_to_run.update(CLOUD_MODELS)

    # Filter cloud models by available API keys
    filtered: dict[str, dict] = {}
    for m, info in models_to_run.items():
        provider = info.get("provider", "")
        if provider == "groq" and not groq_key:
            print(f"  [SKIP] {m} — no GROQ_API_KEY")
            continue
        if provider == "openai" and not openai_key:
            print(f"  [SKIP] {m} — no OPENAI_API_KEY")
            continue
        if provider == "gemini" and not gemini_key:
            print(f"  [SKIP] {m} — no GEMINI_API_KEY")
            continue
        filtered[m] = info
    models_to_run = filtered

    if not models_to_run:
        print("ERROR: No models to benchmark. Check API keys and model names.")
        sys.exit(1)

    # Determine prompts
    prompt_versions = args.prompts or PROMPT_VERSIONS
    n_runs = args.runs
    total_calls = len(models_to_run) * len(prompt_versions) * len(emails) * n_runs

    print(f"\n{'=' * 60}")
    print("Email Classification Benchmark")
    print(f"{'=' * 60}")
    print(f"  Models: {list(models_to_run.keys())}")
    print(f"  Prompts: {prompt_versions}")
    print(f"  Emails: {len(emails)}")
    print(f"  Runs: {n_runs}")
    print(f"  Total API calls: {total_calls}")
    print(f"{'=' * 60}\n")

    # Pull and warm up Ollama models
    local_models = {m: i for m, i in models_to_run.items() if i.get("provider") == "ollama"}
    if local_models and not args.skip_pull:
        print("Pulling Ollama models...")
        failed = []
        for model in local_models:
            if not await pull_ollama_model(model, ollama_url):
                print(f"  [WARN] Removing {model} from benchmark (pull failed)")
                failed.append(model)
        for m in failed:
            del models_to_run[m]

    if local_models:
        print("\nWarming up Ollama models...")
        for model in list(local_models.keys()):
            if model in models_to_run:
                await warmup_ollama(model, ollama_url)

    # --- Run benchmark ---
    all_results: dict[str, ModelPromptResult] = {}
    all_metrics: dict[str, dict] = {}
    call_count = 0

    for model, model_info in models_to_run.items():
        provider = model_info.get("provider", "")

        for prompt_ver in prompt_versions:
            key = f"{model}_{prompt_ver}"
            mpr = ModelPromptResult(model=model, prompt_version=prompt_ver)

            print(f"\n--- {model} / {prompt_ver} ---")

            for run_idx in range(n_runs):
                if n_runs > 1:
                    print(f"  Run {run_idx + 1}/{n_runs}")

                for email in emails:
                    call_count += 1
                    if call_count % 10 == 0:
                        print(f"  [{call_count}/{total_calls}] Progress...")

                    system_prompt, user_prompt = _build_prompt_for_email(email, prompt_ver)

                    if provider == "ollama":
                        result = await classify_ollama(
                            email, system_prompt, user_prompt, model, ollama_url
                        )
                    elif provider == "groq":
                        result = await classify_groq(
                            email, system_prompt, user_prompt, model, groq_key
                        )
                    elif provider == "openai":
                        result = await classify_openai(
                            email, system_prompt, user_prompt, model, openai_key
                        )
                    elif provider == "gemini":
                        result = await classify_gemini(
                            email, system_prompt, user_prompt, model, gemini_key
                        )
                    else:
                        continue

                    result.prompt_version = prompt_ver
                    mpr.results.append(result)

                    # Log cloud responses verbosely
                    if provider in ("groq", "openai", "gemini") and result.classification:
                        c = result.classification
                        print(
                            f"    [{call_count}] {email.subject[:50]:<50} "
                            f"cat={c.category:<20} act={c.action.value:<16} "
                            f"urg={c.urgency:.2f}  conf={c.confidence:.2f}"
                        )
                    elif provider in ("groq", "openai", "gemini") and result.error:
                        print(
                            f"    [{call_count}] {email.subject[:50]:<50} "
                            f"ERROR: {result.error[:60]}"
                        )
                    elif provider in ("groq", "openai", "gemini") and not result.schema_valid:
                        print(
                            f"    [{call_count}] {email.subject[:50]:<50} "
                            f"INVALID SCHEMA: {result.raw_response[:80]}"
                        )

                    # Rate limit delay for cloud models
                    if provider != "ollama":
                        await asyncio.sleep(CLOUD_INTER_REQUEST_DELAY)

                    # Token tracking: use actual API counts if available, else estimate
                    if result.input_tokens > 0:
                        mpr.total_input_tokens += result.input_tokens
                        mpr.total_output_tokens += result.output_tokens
                    else:
                        prompt_tokens = len(system_prompt.split()) + len(user_prompt.split())
                        out_tokens = len(result.raw_response.split()) if result.raw_response else 0
                        mpr.total_input_tokens += int(prompt_tokens * 1.3)
                        mpr.total_output_tokens += int(out_tokens * 1.3)

            # Compute metrics
            metrics = compute_metrics(mpr.results)
            all_results[key] = mpr
            all_metrics[key] = metrics

            # Quick summary
            print(
                f"  -> Schema: {metrics.get('schema_compliance', 0):.0%} | "
                f"JSON: {metrics.get('json_compliance', 0):.0%} | "
                f"Latency: {metrics.get('mean_latency_ms', 0):.0f}ms | "
                f"Contact: {metrics.get('contact_name_rate', 0):.0%}"
            )

    # --- Compute consensus ---
    consensus = compute_consensus_metrics(all_results, emails)

    # --- Generate reports ---
    elapsed = time.perf_counter() - start_time
    hw = detect_hardware_brief()

    metadata = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "platform": f"{hw.get('platform', '?')} {hw.get('machine', '?')}",
        "python": hw.get("python", "?"),
        "ram_gb": hw.get("ram_gb"),
        "cpu_count": hw.get("cpu_count"),
        "models": list(models_to_run.keys()),
        "prompts": prompt_versions,
        "n_emails": len(emails),
        "runs": n_runs,
        "total_calls": call_count,
        "elapsed_seconds": round(elapsed, 1),
        "ollama_url": ollama_url,
    }

    # Cost summary
    total_cost = 0.0
    for _key, mpr in all_results.items():
        cost = estimate_cost(mpr.model, mpr.total_input_tokens, mpr.total_output_tokens)
        total_cost += cost
    metadata["estimated_total_cost_usd"] = round(total_cost, 4)

    # Save reports
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"email_benchmark_{timestamp}.json"
    json_report = generate_json_report(all_results, all_metrics, consensus, metadata)
    json_path.write_text(json.dumps(json_report, indent=2, default=str))
    print(f"\nJSON report: {json_path}")

    md_path = output_dir / f"email_benchmark_{timestamp}.md"
    md_report = generate_markdown_report(all_metrics, consensus, metadata, all_results)
    md_path.write_text(md_report)
    print(f"Markdown report: {md_path}")

    # --- Print summary ---
    print(f"\n{'=' * 60}")
    print("BENCHMARK COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  API calls: {call_count}")
    print(f"  Estimated cost: ${total_cost:.4f}")
    print()

    # Per-model cost breakdown
    model_costs: dict[str, dict] = {}
    for key, mpr in all_results.items():
        cost = estimate_cost(mpr.model, mpr.total_input_tokens, mpr.total_output_tokens)
        model_costs[key] = {
            "input_tokens": mpr.total_input_tokens,
            "output_tokens": mpr.total_output_tokens,
            "cost_usd": cost,
        }

    # Leaderboard
    sorted_keys = sorted(
        all_metrics.keys(),
        key=lambda k: (
            -all_metrics[k].get("schema_compliance", 0),
            all_metrics[k].get("mean_latency_ms", 0),
        ),
    )
    print(
        f"{'Rank':<5} {'Model':<40} {'Schema':>8} {'JSON':>6} "
        f"{'Contact':>9} {'Lat':>8} {'Cost':>10}"
    )
    print("-" * 95)
    for rank, key in enumerate(sorted_keys, 1):
        m = all_metrics[key]
        mc = model_costs.get(key, {})
        model, _pv = key.rsplit("_", 1)
        print(
            f"{rank:<5} {model:<40} {m.get('schema_compliance', 0):>7.0%} "
            f"{m.get('json_compliance', 0):>5.0%} "
            f"{m.get('contact_name_rate', 0):>8.0%} "
            f"{m.get('mean_latency_ms', 0):>7.0f}ms"
            f"   ${mc.get('cost_usd', 0):.4f}"
        )

    # Token usage breakdown
    print(f"\n{'=' * 60}")
    print("TOKEN USAGE & COST")
    print(f"{'=' * 60}")
    for key in sorted_keys:
        mc = model_costs.get(key, {})
        model, _pv = key.rsplit("_", 1)
        print(
            f"  {model}: "
            f"{mc.get('input_tokens', 0):,} in / {mc.get('output_tokens', 0):,} out "
            f"= ${mc.get('cost_usd', 0):.4f}"
        )

    # Consensus summary
    print(f"\n{'=' * 60}")
    print("CROSS-MODEL CONSENSUS")
    print(f"{'=' * 60}")
    print(f"  Category agreement: {consensus.get('category_consensus', 0):.1%}")
    print(f"  Action agreement:   {consensus.get('action_consensus', 0):.1%}")
    print(f"  Urgency spread:     {consensus.get('urgency_mean_spread', 0):.3f}")


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Benchmark email classification across models",
    )
    parser.add_argument("--local-only", action="store_true", help="Only test Ollama models")
    parser.add_argument("--cloud-only", action="store_true", help="Only test Groq models")
    parser.add_argument("--models", nargs="+", help="Specific model(s) to test")
    parser.add_argument("--prompts", nargs="+", help="Specific prompt version(s)")
    parser.add_argument("--runs", type=int, default=1, help="Runs per combination (default: 1)")
    parser.add_argument("--skip-pull", action="store_true", help="Skip Ollama model pulls")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
    parser.add_argument(
        "--output-dir",
        default="benchmarks/results",
        help="Output directory for reports",
    )
    parser.add_argument("--dataset", help="Path to pre-fetched email dataset JSON")
    parser.add_argument("--fetch", action="store_true", help="Fetch fresh emails from Gmail")
    parser.add_argument("--max-emails", type=int, default=50, help="Max emails to fetch")
    parser.add_argument(
        "--gmail-token",
        default="data/gmail_token.json",
        help="Path to Gmail OAuth token file",
    )

    args = parser.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
