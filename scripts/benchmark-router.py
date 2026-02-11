#!/usr/bin/env python3
"""Router classification benchmark for Zetherion AI.

Tests multiple LLM models and prompt variations against a curated set of
50 messages with ground-truth intent labels. Measures accuracy, latency,
JSON compliance, consistency, confidence calibration, and cost.

Usage:
    python scripts/benchmark-router.py                      # Full benchmark
    python scripts/benchmark-router.py --local-only          # Ollama models only
    python scripts/benchmark-router.py --cloud-only          # Cloud models only
    python scripts/benchmark-router.py --models llama3.2:1b  # Specific model(s)
    python scripts/benchmark-router.py --prompts v0 v1       # Specific prompts
    python scripts/benchmark-router.py --runs 1              # Quick single-run
    python scripts/benchmark-router.py --skip-pull           # Skip Ollama pulls
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
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

# Max retries for rate-limited cloud API calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry

# Delay between consecutive cloud API calls to respect rate limits
CLOUD_INTER_REQUEST_DELAY = 0.5  # seconds


async def _retry_with_backoff(coro_factory, max_retries=MAX_RETRIES):
    """Retry an async operation with exponential backoff on transient errors.

    coro_factory is a zero-arg callable that returns a new coroutine each time.
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

VALID_INTENTS = [
    "simple_query",
    "complex_task",
    "memory_store",
    "memory_recall",
    "system_command",
    "task_management",
    "calendar_query",
    "profile_query",
    "personal_model",
    "email_management",
]

# V2 merged intents and their mapping back to original intents
V2_INTENT_MAP = {
    "greeting_or_simple": "simple_query",
    "complex_task": "complex_task",
    "memory": "memory_store",  # Disambiguated by sub_intent or keywords
    "task_or_calendar": "task_management",  # Disambiguated by sub_intent or keywords
    "profile_or_personal": "profile_query",  # Disambiguated by sub_intent or keywords
    "email": "email_management",
}

V2_VALID_INTENTS = list(V2_INTENT_MAP.keys())


@dataclass
class TestMessage:
    """A test message with ground-truth label."""

    text: str
    expected_intent: str
    acceptable_alts: list[str] = field(default_factory=list)
    category: str = ""


@dataclass
class ClassificationResult:
    """Result from a single classification attempt."""

    message: str
    expected_intent: str
    predicted_intent: str | None
    confidence: float
    reasoning: str
    latency_ms: float
    json_valid: bool
    intent_valid: bool
    raw_response: str
    error: str | None = None


@dataclass
class ModelPromptResult:
    """Aggregated results for one model+prompt combination."""

    model: str
    prompt_version: str
    results: list[ClassificationResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Section 2: Test Corpus (50 messages)
# ---------------------------------------------------------------------------

TEST_CORPUS: list[TestMessage] = [
    # SIMPLE_QUERY (8)
    TestMessage("Hi", "simple_query", category="simple_query"),
    TestMessage("Are you there?", "simple_query", category="simple_query"),
    TestMessage("Hey", "simple_query", category="simple_query"),
    TestMessage("What's up?", "simple_query", category="simple_query"),
    TestMessage("Thanks!", "simple_query", category="simple_query"),
    TestMessage("What's 2+2?", "simple_query", category="simple_query"),
    TestMessage("Good morning", "simple_query", category="simple_query"),
    TestMessage("How are you doing?", "simple_query", category="simple_query"),
    # COMPLEX_TASK (6)
    TestMessage(
        "Write a Python script to sort a list of dictionaries by value",
        "complex_task",
        category="complex_task",
    ),
    TestMessage(
        "Explain how neural networks work in detail",
        "complex_task",
        category="complex_task",
    ),
    TestMessage(
        "Help me debug this error: TypeError: 'NoneType' object is not subscriptable",
        "complex_task",
        category="complex_task",
    ),
    TestMessage(
        "Write a short story about a robot learning to love",
        "complex_task",
        category="complex_task",
    ),
    TestMessage(
        "Compare and contrast REST and GraphQL APIs with examples",
        "complex_task",
        category="complex_task",
    ),
    TestMessage(
        "Refactor this function to use async/await",
        "complex_task",
        category="complex_task",
    ),
    # MEMORY_STORE (5)
    TestMessage(
        "Remember that I prefer dark mode",
        "memory_store",
        category="memory_store",
    ),
    TestMessage(
        "My birthday is March 15",
        "memory_store",
        acceptable_alts=["profile_query", "personal_model"],
        category="memory_store",
    ),
    TestMessage(
        "Note that I'm allergic to peanuts",
        "memory_store",
        category="memory_store",
    ),
    TestMessage(
        "I just started a new job at Google",
        "memory_store",
        acceptable_alts=["personal_model"],
        category="memory_store",
    ),
    TestMessage(
        "Keep in mind that I work night shifts",
        "memory_store",
        category="memory_store",
    ),
    # MEMORY_RECALL (5)
    TestMessage(
        "What's my favorite color?",
        "memory_recall",
        acceptable_alts=["profile_query"],
        category="memory_recall",
    ),
    TestMessage(
        "What did we talk about yesterday?",
        "memory_recall",
        category="memory_recall",
    ),
    TestMessage(
        "What's my birthday?",
        "memory_recall",
        acceptable_alts=["profile_query"],
        category="memory_recall",
    ),
    TestMessage(
        "Do you remember what I told you about my project?",
        "memory_recall",
        category="memory_recall",
    ),
    TestMessage(
        "What are my preferences?",
        "memory_recall",
        acceptable_alts=["profile_query"],
        category="memory_recall",
    ),
    # SYSTEM_COMMAND (4)
    TestMessage("Help", "system_command", category="system_command"),
    TestMessage(
        "What can you do?",
        "system_command",
        acceptable_alts=["simple_query"],
        category="system_command",
    ),
    TestMessage("List commands", "system_command", category="system_command"),
    TestMessage("Settings", "system_command", category="system_command"),
    # TASK_MANAGEMENT (5)
    TestMessage(
        "Add a task to buy groceries",
        "task_management",
        category="task_management",
    ),
    TestMessage("What are my tasks?", "task_management", category="task_management"),
    TestMessage(
        "Mark the report task as done",
        "task_management",
        category="task_management",
    ),
    TestMessage(
        "Create a todo for tomorrow: call dentist",
        "task_management",
        category="task_management",
    ),
    TestMessage(
        "Show my overdue tasks",
        "task_management",
        category="task_management",
    ),
    # CALENDAR_QUERY (5)
    TestMessage(
        "What's on my calendar today?",
        "calendar_query",
        category="calendar_query",
    ),
    TestMessage("Am I free at 3pm?", "calendar_query", category="calendar_query"),
    TestMessage(
        "Schedule a meeting for Friday at 2pm",
        "calendar_query",
        acceptable_alts=["task_management"],
        category="calendar_query",
    ),
    TestMessage(
        "What events do I have this week?",
        "calendar_query",
        category="calendar_query",
    ),
    TestMessage(
        "Set my work hours to 9-5",
        "calendar_query",
        acceptable_alts=["profile_query"],
        category="calendar_query",
    ),
    # PROFILE_QUERY (4)
    TestMessage(
        "Show my profile",
        "profile_query",
        acceptable_alts=["personal_model"],
        category="profile_query",
    ),
    TestMessage(
        "Update my timezone to EST",
        "profile_query",
        acceptable_alts=["personal_model"],
        category="profile_query",
    ),
    TestMessage(
        "Export my data",
        "profile_query",
        acceptable_alts=["personal_model"],
        category="profile_query",
    ),
    TestMessage(
        "What's your confidence in my preferences?",
        "profile_query",
        acceptable_alts=["personal_model"],
        category="profile_query",
    ),
    # PERSONAL_MODEL (4)
    TestMessage("Show my contacts", "personal_model", category="personal_model"),
    TestMessage(
        "Who are my important contacts?",
        "personal_model",
        category="personal_model",
    ),
    TestMessage(
        "Forget that I like coffee",
        "personal_model",
        acceptable_alts=["memory_store", "profile_query"],
        category="personal_model",
    ),
    TestMessage(
        "What have you learned about me?",
        "personal_model",
        acceptable_alts=["profile_query", "memory_recall"],
        category="personal_model",
    ),
    # EMAIL_MANAGEMENT (4)
    TestMessage("Check my emails", "email_management", category="email_management"),
    TestMessage("Any urgent emails?", "email_management", category="email_management"),
    TestMessage(
        "Give me a morning digest",
        "email_management",
        category="email_management",
    ),
    TestMessage(
        "Search emails from Alice about the project",
        "email_management",
        category="email_management",
    ),
]

assert len(TEST_CORPUS) == 50, f"Expected 50 messages, got {len(TEST_CORPUS)}"

# ---------------------------------------------------------------------------
# Section 3: Prompt Definitions
# ---------------------------------------------------------------------------

# V0: Current production prompt (imported verbatim)
PROMPT_V0 = """You are a message router. Classify the user's message into one of these intents:

1. SIMPLE_QUERY - Greetings, quick factual questions, simple requests
   Examples: "Hi", "What's 2+2?", "What day is it?", "Thanks!"

2. COMPLEX_TASK - Code generation, detailed analysis, creative writing, multi-step tasks
   Examples: "Write a Python script to...", "Explain how transformers work in detail", \
"Help me debug this code..."

3. MEMORY_STORE - User explicitly wants you to remember something
   Examples: "Remember that I prefer dark mode", "My birthday is March 15", "Note that..."

4. MEMORY_RECALL - User asking about previously stored personal information or past conversations
   Examples: "What's my favorite color?", "What did we talk about yesterday?", \
"What do you know about me?", "What's my birthday?", "What are my preferences?", "Where do I live?"

5. SYSTEM_COMMAND - Bot commands, settings, help requests
   Examples: "Help", "What can you do?", "List commands", "Settings"

6. TASK_MANAGEMENT - Creating, listing, updating, or completing tasks and todos
   Examples: "Add a task to buy groceries", "What are my tasks?", "Mark the report task as done", \
"Create a todo for tomorrow", "Show my overdue tasks", "Delete the shopping task"

7. CALENDAR_QUERY - Schedule, events, availability, and calendar-related queries
   Examples: "What's on my calendar today?", "Am I free at 3pm?", "Schedule a meeting for Friday", \
"What events do I have this week?", "Set my work hours to 9-5"

8. PROFILE_QUERY - Viewing, updating, or managing what the bot knows about the user
   Examples: "What do you know about me?", "Update my timezone to EST", "Forget my location", \
"Show my profile", "Export my data", "What's your confidence in my preferences?"

9. PERSONAL_MODEL - Deep personal understanding queries, contact management, and policy control
   Examples: "Show my contacts", "Who are my important contacts?", "My timezone is PST", \
"Forget that I like coffee", "Show my policies", "Export my personal data", \
"What have you learned about me?"

10. EMAIL_MANAGEMENT - Email checking, reading, drafts, digests, Gmail management
   Examples: "Check my emails", "Any urgent emails?", "Show unread emails", \
"Review my drafts", "Give me a morning digest", "Weekly email summary", \
"Search emails from Alice", "Gmail status", "How many emails today?"

Respond with ONLY a JSON object:
{"intent": "INTENT_NAME", "confidence": 0.0-1.0, "reasoning": "brief reason"}"""

# V1: Prioritized with negative examples
PROMPT_V1 = """You are a message router. Classify the user's message into exactly one intent.

PRIORITY RULES (check in order):
1. If the message is a greeting, small talk, thanks, or simple factual question -> SIMPLE_QUERY
2. If the message mentions emails, inbox, digest, or Gmail -> EMAIL_MANAGEMENT
3. If the message mentions tasks, todos, or completing/adding items -> TASK_MANAGEMENT
4. If the message mentions calendar, schedule, events, or availability -> CALENDAR_QUERY
5. If it says "remember", "note that", "keep in mind", or shares personal facts -> MEMORY_STORE
6. If it asks "what's my...", "do you remember...", or recalls past info -> MEMORY_RECALL
7. If it mentions contacts, personal data export, or "what have you learned" -> PERSONAL_MODEL
8. If the message asks about profile, timezone, or preferences management -> PROFILE_QUERY
9. If the message says "help", "commands", "settings", or asks what you can do -> SYSTEM_COMMAND
10. If none of the above, or it requires code/analysis/creative work -> COMPLEX_TASK

IMPORTANT DISAMBIGUATIONS:
- "Are you there?" is SIMPLE_QUERY (it's a greeting/check-in), NOT memory_store
- "How are you?" is SIMPLE_QUERY (it's small talk), NOT memory_recall
- "Thanks!" is SIMPLE_QUERY (it's gratitude), NOT any other intent
- "Hi" / "Hey" / "Hello" are ALWAYS SIMPLE_QUERY
- Short messages (< 5 words) without explicit intent keywords are usually SIMPLE_QUERY
- "Help" alone is SYSTEM_COMMAND, but "Help me debug..." is COMPLEX_TASK

INTENTS:
- SIMPLE_QUERY: Greetings, thanks, quick factual questions, casual check-ins
- COMPLEX_TASK: Code, analysis, creative writing, debugging, multi-step work
- MEMORY_STORE: User explicitly asking to remember/note something
- MEMORY_RECALL: User asking about past conversations or stored personal info
- SYSTEM_COMMAND: Bot commands, settings, help menu
- TASK_MANAGEMENT: Creating, listing, updating tasks/todos
- CALENDAR_QUERY: Schedule, events, availability
- PROFILE_QUERY: Profile viewing/updating, timezone, preferences
- PERSONAL_MODEL: Contacts, deep personal understanding, data policies
- EMAIL_MANAGEMENT: Emails, inbox, digests, Gmail

Respond with ONLY a JSON object:
{"intent": "INTENT_NAME", "confidence": 0.0-1.0, "reasoning": "brief reason"}"""

# V2: Reduced to 6 intents
PROMPT_V2 = """You are a message router. Classify the user's message into one of 6 categories.

CATEGORIES:
1. GREETING_OR_SIMPLE - Greetings, thanks, small talk, simple factual questions, help/commands
   YES: "Hi", "Thanks!", "Are you there?", "What's 2+2?", "Help", "What can you do?"
   NO: "Write a script", "Remember that...", "Check my emails"

2. COMPLEX_TASK - Code generation, detailed analysis, creative writing, debugging, multi-step work
   YES: "Write a Python script", "Explain neural networks in detail", "Debug this error"
   NO: "Hi", "Check my emails", "What's my birthday?"

3. MEMORY - Storing new information OR recalling previously stored information
   YES: "Remember I prefer dark mode", "What's my favorite color?", "What did we discuss?"
   NO: "Hi", "Show my contacts", "Check my emails"

4. TASK_OR_CALENDAR - Tasks, todos, schedules, events, availability
   YES: "Add a task", "What's on my calendar?", "Am I free at 3pm?", "Show overdue tasks"
   NO: "Check my emails", "Show my profile", "Hi"

5. PROFILE_OR_PERSONAL - Profile, preferences, contacts, personal data, timezone
   YES: "Show my profile", "Update timezone", "Show my contacts", "Export my data"
   NO: "Check my emails", "What's 2+2?", "Write a script"

6. EMAIL - Email checking, reading, drafts, digests, Gmail
   YES: "Check my emails", "Any urgent emails?", "Morning digest"
   NO: "What are my tasks?", "Show my profile", "Hi"

Also include a "sub_intent" field to help disambiguate:
- For MEMORY: "store" or "recall"
- For TASK_OR_CALENDAR: "task" or "calendar"
- For PROFILE_OR_PERSONAL: "profile" or "personal"
- For others: leave empty ""

Respond with ONLY a JSON object:
{"intent": "CATEGORY_NAME", "sub_intent": "", "confidence": 0.0-1.0, "reasoning": "brief reason"}"""

# V3: Chain-of-thought step-by-step
PROMPT_V3 = """You are a message router. You must classify the user's message step by step.

Follow these steps IN ORDER. Stop at the first match:

Step 1: Is this a greeting, thanks, small talk, or simple factual question?
  Check: Is it under 5 words? Is it "hi", "hey", "hello", "thanks", "good morning"?
  Check: Is it a check-in like "are you there?" or "how are you?"
  Check: Is it a simple factual question like "what's 2+2?"
  If YES to any -> intent = SIMPLE_QUERY

Step 2: Is this about emails, inbox, or digests?
  Check: Does it mention "email", "inbox", "digest", "gmail", "unread"?
  If YES -> intent = EMAIL_MANAGEMENT

Step 3: Is this about tasks or todos?
  Check: Does it mention "task", "todo", "overdue", "mark as done"?
  If YES -> intent = TASK_MANAGEMENT

Step 4: Is this about calendar, schedule, or events?
  Check: Does it mention "calendar", "schedule", "meeting", "event", "free at"?
  If YES -> intent = CALENDAR_QUERY

Step 5: Is the user asking you to remember or note something?
  Check: Does it say "remember", "note that", "keep in mind"?
  Check: Is the user sharing a personal fact for you to store?
  If YES -> intent = MEMORY_STORE

Step 6: Is the user asking about past information or what you know?
  Check: Does it say "what's my", "do you remember", "what did we talk about"?
  Check: Is the user asking to recall previously stored information?
  If YES -> intent = MEMORY_RECALL

Step 7: Is this about contacts, personal data management, or policies?
  Check: Does it mention "contacts", "learned about me", "personal data"?
  If YES -> intent = PERSONAL_MODEL

Step 8: Is this about profile, timezone, or preferences?
  Check: Does it mention "profile", "timezone", "preferences", "export data"?
  If YES -> intent = PROFILE_QUERY

Step 9: Is this asking for help, commands, or settings?
  Check: Is it just "help", "commands", "settings", "what can you do"?
  If YES -> intent = SYSTEM_COMMAND

Step 10: If none of the above matched -> intent = COMPLEX_TASK

Now classify. Show your step-by-step reasoning, then provide the result.

Respond with ONLY a JSON object:
{"intent": "INTENT_NAME", "confidence": 0.0-1.0, "reasoning": "Step X matched: ..."}"""

PROMPTS = {
    "v0": PROMPT_V0,
    "v1": PROMPT_V1,
    "v2": PROMPT_V2,
    "v3": PROMPT_V3,
}

# ---------------------------------------------------------------------------
# Section 4: Model Backends
# ---------------------------------------------------------------------------

# Model definitions
LOCAL_MODELS = {
    "llama3.2:1b": {"size_gb": 1.3, "provider": "ollama"},
    "llama3.2:3b": {"size_gb": 2.0, "provider": "ollama"},
    "gemma2:2b": {"size_gb": 1.6, "provider": "ollama"},
    "phi3:mini": {"size_gb": 2.3, "provider": "ollama"},
}

CLOUD_MODELS = {
    "gemini-2.0-flash": {"provider": "gemini", "input_per_m": 0.075, "output_per_m": 0.30},
    "gpt-4o-mini": {"provider": "openai", "input_per_m": 0.15, "output_per_m": 0.60},
    "claude-haiku-4-5-20251001": {
        "provider": "anthropic",
        "input_per_m": 1.00,
        "output_per_m": 5.00,
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


def _parse_classification(
    raw: str, prompt_version: str
) -> tuple[str | None, float, str, bool, bool]:
    """Parse a classification response into (intent, confidence, reasoning, json_ok, intent_ok).

    Returns normalized intent in lowercase.
    """
    json_text = _extract_json(raw)
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None, 0.0, "", False, False

    intent_raw = data.get("intent", "")
    if not isinstance(intent_raw, str) or not intent_raw:
        return None, 0.0, data.get("reasoning", ""), True, False

    intent = intent_raw.lower()
    confidence = float(data.get("confidence", 0.8))
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(data.get("reasoning", ""))

    # For V2, map merged intents back to original
    if prompt_version == "v2":
        sub_intent = str(data.get("sub_intent", "")).lower()
        if intent in V2_VALID_INTENTS:
            intent = _map_v2_intent(intent, sub_intent, reasoning)
            return intent, confidence, reasoning, True, intent in VALID_INTENTS
        return None, confidence, reasoning, True, False

    return intent, confidence, reasoning, True, intent in VALID_INTENTS


def _map_v2_intent(intent: str, sub_intent: str, reasoning: str) -> str:
    """Map V2 merged intent back to original 10-way intent."""
    if intent == "greeting_or_simple":
        # Check if it's system_command via keywords in reasoning
        lower_reasoning = reasoning.lower()
        if any(w in lower_reasoning for w in ["help", "command", "setting"]):
            return "system_command"
        return "simple_query"

    if intent == "memory":
        if sub_intent == "recall":
            return "memory_recall"
        return "memory_store"

    if intent == "task_or_calendar":
        if sub_intent == "calendar":
            return "calendar_query"
        return "task_management"

    if intent == "profile_or_personal":
        if sub_intent == "personal":
            return "personal_model"
        return "profile_query"

    if intent == "email":
        return "email_management"

    if intent == "complex_task":
        return "complex_task"

    # Fallback: try direct mapping
    return V2_INTENT_MAP.get(intent, intent)


async def classify_ollama(
    message: str,
    prompt: str,
    prompt_version: str,
    model: str,
    ollama_url: str,
    timeout: float = 30.0,
) -> ClassificationResult:
    """Classify a message using Ollama."""
    full_prompt = f"{prompt}\n\nUser message: {message}"
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
                        "num_predict": 150 if prompt_version != "v3" else 300,
                    },
                },
            )
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    intent, conf, reasoning, json_ok, intent_ok = _parse_classification(raw, prompt_version)

    return ClassificationResult(
        message=message,
        expected_intent="",  # Filled by caller
        predicted_intent=intent,
        confidence=conf,
        reasoning=reasoning,
        latency_ms=latency_ms,
        json_valid=json_ok,
        intent_valid=intent_ok,
        raw_response=raw[:500],
        error=error,
    )


async def classify_gemini(
    message: str,
    prompt: str,
    prompt_version: str,
    model: str,
    api_key: str,
) -> ClassificationResult:
    """Classify a message using Gemini."""
    from google import genai  # type: ignore[attr-defined]

    client = genai.Client(api_key=api_key)
    full_prompt = f"{prompt}\n\nUser message: {message}"
    start = time.perf_counter()
    raw = ""
    error = None

    try:

        async def _call():
            return await asyncio.to_thread(
                lambda: client.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config={
                        "temperature": 0.1,
                        "max_output_tokens": 150 if prompt_version != "v3" else 300,
                    },
                )
            )

        response = await _retry_with_backoff(_call)
        raw = (response.text or "").strip()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    intent, conf, reasoning, json_ok, intent_ok = _parse_classification(raw, prompt_version)

    return ClassificationResult(
        message=message,
        expected_intent="",
        predicted_intent=intent,
        confidence=conf,
        reasoning=reasoning,
        latency_ms=latency_ms,
        json_valid=json_ok,
        intent_valid=intent_ok,
        raw_response=raw[:500],
        error=error,
    )


async def classify_openai(
    message: str,
    prompt: str,
    prompt_version: str,
    model: str,
    api_key: str,
) -> ClassificationResult:
    """Classify a message using OpenAI."""
    import openai

    client = openai.OpenAI(api_key=api_key)
    start = time.perf_counter()
    raw = ""
    error = None

    try:

        async def _call():
            return await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": message},
                    ],
                    temperature=0.1,
                    max_tokens=150 if prompt_version != "v3" else 300,
                    response_format={"type": "json_object"},
                )
            )

        response = await _retry_with_backoff(_call)
        raw = (response.choices[0].message.content or "").strip()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    intent, conf, reasoning, json_ok, intent_ok = _parse_classification(raw, prompt_version)

    return ClassificationResult(
        message=message,
        expected_intent="",
        predicted_intent=intent,
        confidence=conf,
        reasoning=reasoning,
        latency_ms=latency_ms,
        json_valid=json_ok,
        intent_valid=intent_ok,
        raw_response=raw[:500],
        error=error,
    )


async def classify_anthropic(
    message: str,
    prompt: str,
    prompt_version: str,
    model: str,
    api_key: str,
) -> ClassificationResult:
    """Classify a message using Anthropic Claude."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    start = time.perf_counter()
    raw = ""
    error = None

    try:

        async def _call():
            return await asyncio.to_thread(
                lambda: client.messages.create(
                    model=model,
                    max_tokens=150 if prompt_version != "v3" else 300,
                    temperature=0.1,
                    system=prompt,
                    messages=[{"role": "user", "content": message}],
                )
            )

        response = await _retry_with_backoff(_call)
        raw = response.content[0].text.strip() if response.content else ""
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - start) * 1000
    intent, conf, reasoning, json_ok, intent_ok = _parse_classification(raw, prompt_version)

    return ClassificationResult(
        message=message,
        expected_intent="",
        predicted_intent=intent,
        confidence=conf,
        reasoning=reasoning,
        latency_ms=latency_ms,
        json_valid=json_ok,
        intent_valid=intent_ok,
        raw_response=raw[:500],
        error=error,
    )


# ---------------------------------------------------------------------------
# Section 5: Metrics Computation
# ---------------------------------------------------------------------------


def compute_metrics(results: list[ClassificationResult], corpus: list[TestMessage]) -> dict:
    """Compute all metrics for a set of classification results.

    Expects results in the same order as corpus (repeated for multiple runs).
    """
    n_messages = len(corpus)
    n_runs = len(results) // n_messages if results else 0

    # Build lookup for acceptable alts
    alt_lookup = {msg.text: msg.acceptable_alts for msg in corpus}

    # Per-message aggregation across runs
    strict_correct = 0
    weighted_correct = 0.0
    total = 0
    json_valid_count = 0
    intent_valid_count = 0
    latencies = []
    confidences_correct = []
    confidences_wrong = []
    overconfident_wrong = 0

    # For confusion matrix and per-intent metrics
    per_intent_tp: dict[str, int] = defaultdict(int)
    per_intent_fp: dict[str, int] = defaultdict(int)
    per_intent_fn: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # For consistency: group by message text
    message_predictions: dict[str, list[str | None]] = defaultdict(list)

    for r in results:
        total += 1
        if r.json_valid:
            json_valid_count += 1
        if r.intent_valid:
            intent_valid_count += 1
        if r.error is None:
            latencies.append(r.latency_ms)

        expected = r.expected_intent
        predicted = r.predicted_intent
        message_predictions[r.message].append(predicted)

        if predicted and expected:
            confusion[expected][predicted] += 1

        if predicted == expected:
            strict_correct += 1
            weighted_correct += 1.0
            confidences_correct.append(r.confidence)
            if expected:
                per_intent_tp[expected] += 1
        elif predicted in alt_lookup.get(r.message, []):
            weighted_correct += 0.5
            confidences_correct.append(r.confidence)
            # Count as partial — don't penalize in confusion matrix
        else:
            confidences_wrong.append(r.confidence)
            if r.confidence > 0.8:
                overconfident_wrong += 1
            if expected:
                per_intent_fn[expected] += 1
            if predicted:
                per_intent_fp[predicted] += 1

    # Aggregate metrics
    strict_accuracy = strict_correct / total if total else 0
    weighted_accuracy = weighted_correct / total if total else 0
    json_compliance = json_valid_count / total if total else 0
    intent_compliance = intent_valid_count / total if total else 0

    # Latency stats
    mean_latency = statistics.mean(latencies) if latencies else 0
    p95_latency = (
        sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 2 else mean_latency
    )

    # Per-intent F1
    per_intent_f1 = {}
    all_intents = set(per_intent_tp) | set(per_intent_fp) | set(per_intent_fn)
    for intent in sorted(all_intents):
        tp = per_intent_tp[intent]
        fp = per_intent_fp[intent]
        fn = per_intent_fn[intent]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        per_intent_f1[intent] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    macro_f1 = statistics.mean(v["f1"] for v in per_intent_f1.values()) if per_intent_f1 else 0

    # Consistency: for each message, what fraction of runs agree?
    consistency_scores = []
    for _msg_text, preds in message_predictions.items():
        if len(preds) > 1:
            # Most common prediction
            from collections import Counter

            most_common_count = Counter(preds).most_common(1)[0][1]
            consistency_scores.append(most_common_count / len(preds))
    mean_consistency = statistics.mean(consistency_scores) if consistency_scores else 1.0

    # ECE (Expected Calibration Error) — 5 bins
    ece = _compute_ece(results, corpus)

    # Overconfidence rate
    total_wrong = len(confidences_wrong)
    overconfidence_rate = overconfident_wrong / total_wrong if total_wrong > 0 else 0

    return {
        "strict_accuracy": round(strict_accuracy, 4),
        "weighted_accuracy": round(weighted_accuracy, 4),
        "json_compliance": round(json_compliance, 4),
        "intent_compliance": round(intent_compliance, 4),
        "mean_latency_ms": round(mean_latency, 1),
        "p95_latency_ms": round(p95_latency, 1),
        "macro_f1": round(macro_f1, 4),
        "per_intent_f1": per_intent_f1,
        "consistency": round(mean_consistency, 4),
        "ece": round(ece, 4),
        "overconfidence_rate": round(overconfidence_rate, 4),
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "total_predictions": total,
        "n_runs": n_runs,
    }


def _compute_ece(results: list[ClassificationResult], corpus: list[TestMessage]) -> float:
    """Compute Expected Calibration Error with 5 bins."""
    alt_lookup = {msg.text: msg.acceptable_alts for msg in corpus}
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(5)]

    for r in results:
        correct = r.predicted_intent == r.expected_intent or r.predicted_intent in alt_lookup.get(
            r.message, []
        )
        bin_idx = min(int(r.confidence * 5), 4)
        bins[bin_idx].append((r.confidence, correct))

    ece = 0.0
    total = sum(len(b) for b in bins)
    if total == 0:
        return 0.0

    for b in bins:
        if not b:
            continue
        avg_conf = statistics.mean(c for c, _ in b)
        avg_acc = statistics.mean(1.0 if correct else 0.0 for _, correct in b)
        ece += (len(b) / total) * abs(avg_conf - avg_acc)

    return ece


# ---------------------------------------------------------------------------
# Section 6: Report Generation
# ---------------------------------------------------------------------------


def generate_json_report(
    all_results: dict[str, ModelPromptResult],
    all_metrics: dict[str, dict],
    metadata: dict,
) -> dict:
    """Generate the full JSON report."""
    report = {
        "metadata": metadata,
        "summary": {},
        "detailed_results": {},
    }

    # Build summary leaderboard
    leaderboard = []
    for key, metrics in all_metrics.items():
        model, prompt_ver = key.rsplit("_", 1)
        mpr = all_results[key]
        leaderboard.append(
            {
                "model": model,
                "prompt": prompt_ver,
                "strict_accuracy": metrics["strict_accuracy"],
                "weighted_accuracy": metrics["weighted_accuracy"],
                "macro_f1": metrics["macro_f1"],
                "json_compliance": metrics["json_compliance"],
                "consistency": metrics["consistency"],
                "mean_latency_ms": metrics["mean_latency_ms"],
                "p95_latency_ms": metrics["p95_latency_ms"],
                "ece": metrics["ece"],
                "overconfidence_rate": metrics["overconfidence_rate"],
                "total_input_tokens": mpr.total_input_tokens,
                "total_output_tokens": mpr.total_output_tokens,
            }
        )

    leaderboard.sort(key=lambda x: x["weighted_accuracy"], reverse=True)
    report["summary"]["leaderboard"] = leaderboard

    # Detailed per-combination results
    for key, metrics in all_metrics.items():
        mpr = all_results[key]
        report["detailed_results"][key] = {
            "metrics": metrics,
            "per_message": [
                {
                    "message": r.message,
                    "expected": r.expected_intent,
                    "predicted": r.predicted_intent,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                    "latency_ms": round(r.latency_ms, 1),
                    "json_valid": r.json_valid,
                    "intent_valid": r.intent_valid,
                    "error": r.error,
                }
                for r in mpr.results
            ],
        }

    return report


def generate_markdown_report(
    all_metrics: dict[str, dict],
    metadata: dict,
    all_results: dict[str, ModelPromptResult],
) -> str:
    """Generate a human-readable markdown report."""
    lines = [
        "# Router Classification Benchmark Report",
        "",
        f"**Date**: {metadata['timestamp']}",
        f"**Platform**: {metadata.get('platform', 'Unknown')}",
        f"**Runs per combination**: {metadata.get('runs', 'N/A')}",
        f"**Total API calls**: {metadata.get('total_calls', 'N/A')}",
        "",
        "---",
        "",
        "## Overall Leaderboard",
        "",
        "| Rank | Model | Prompt | Weighted Acc | Strict Acc | Macro F1 "
        "| JSON % | Consistency | Mean Latency | P95 Latency |",
        "|------|-------|--------|-------------|-----------|----------|"
        "--------|-------------|-------------|-------------|",
    ]

    # Sort by weighted accuracy
    sorted_keys = sorted(
        all_metrics.keys(), key=lambda k: all_metrics[k]["weighted_accuracy"], reverse=True
    )

    for rank, key in enumerate(sorted_keys, 1):
        m = all_metrics[key]
        model, prompt_ver = key.rsplit("_", 1)
        lines.append(
            f"| {rank} | {model} | {prompt_ver} "
            f"| {m['weighted_accuracy']:.1%} | {m['strict_accuracy']:.1%} "
            f"| {m['macro_f1']:.3f} | {m['json_compliance']:.0%} "
            f"| {m['consistency']:.1%} | {m['mean_latency_ms']:.0f}ms "
            f"| {m['p95_latency_ms']:.0f}ms |"
        )

    # The Bug Case section
    lines.extend(
        [
            "",
            "---",
            "",
            '## The Bug Case: "Are you there?"',
            "",
            "| Model | Prompt | Predicted | Confidence | Correct? |",
            "|-------|--------|-----------|------------|----------|",
        ]
    )

    for key in sorted_keys:
        mpr = all_results[key]
        model, prompt_ver = key.rsplit("_", 1)
        for r in mpr.results:
            if r.message == "Are you there?":
                correct = "YES" if r.predicted_intent == "simple_query" else "NO"
                lines.append(
                    f"| {model} | {prompt_ver} | {r.predicted_intent} "
                    f"| {r.confidence:.2f} | {correct} |"
                )
                break  # Only show first run per combination

    # Local vs Cloud comparison
    lines.extend(["", "---", "", "## Local vs Cloud Comparison", ""])

    local_accs = []
    cloud_accs = []
    for key in sorted_keys:
        m = all_metrics[key]
        model, _ = key.rsplit("_", 1)
        if model in LOCAL_MODELS:
            local_accs.append(m["weighted_accuracy"])
        elif model in CLOUD_MODELS:
            cloud_accs.append(m["weighted_accuracy"])

    if local_accs:
        lines.append(f"- **Best local**: {max(local_accs):.1%} weighted accuracy")
        lines.append(f"- **Mean local**: {statistics.mean(local_accs):.1%} weighted accuracy")
    if cloud_accs:
        lines.append(f"- **Best cloud**: {max(cloud_accs):.1%} weighted accuracy")
        lines.append(f"- **Mean cloud**: {statistics.mean(cloud_accs):.1%} weighted accuracy")
    if local_accs and cloud_accs:
        gap = max(cloud_accs) - max(local_accs)
        lines.append(f"- **Gap (best cloud - best local)**: {gap:+.1%}")

    # Prompt impact
    lines.extend(["", "---", "", "## Prompt Impact", ""])
    prompt_accs: dict[str, list[float]] = defaultdict(list)
    for key in sorted_keys:
        m = all_metrics[key]
        _, prompt_ver = key.rsplit("_", 1)
        prompt_accs[prompt_ver].append(m["weighted_accuracy"])

    lines.append("| Prompt | Mean Weighted Acc | Best | Worst |")
    lines.append("|--------|-------------------|------|-------|")
    for pv in sorted(prompt_accs.keys()):
        accs = prompt_accs[pv]
        lines.append(f"| {pv} | {statistics.mean(accs):.1%} | {max(accs):.1%} | {min(accs):.1%} |")

    # Per-intent breakdown for best combination
    if sorted_keys:
        best_key = sorted_keys[0]
        best_metrics = all_metrics[best_key]
        lines.extend(
            [
                "",
                "---",
                "",
                f"## Per-Intent Breakdown (Best: {best_key})",
                "",
                "| Intent | Precision | Recall | F1 | TP | FP | FN |",
                "|--------|-----------|--------|-----|----|----|-----|",
            ]
        )
        for intent, stats in sorted(best_metrics["per_intent_f1"].items()):
            lines.append(
                f"| {intent} | {stats['precision']:.3f} | {stats['recall']:.3f} "
                f"| {stats['f1']:.3f} | {stats['tp']} | {stats['fp']} | {stats['fn']} |"
            )

    # Confidence calibration
    lines.extend(["", "---", "", "## Confidence Calibration", ""])
    lines.append("| Model | Prompt | ECE | Overconfidence Rate |")
    lines.append("|-------|--------|-----|---------------------|")
    for key in sorted_keys:
        m = all_metrics[key]
        model, prompt_ver = key.rsplit("_", 1)
        lines.append(
            f"| {model} | {prompt_ver} | {m['ece']:.4f} | {m['overconfidence_rate']:.1%} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "*Generated by scripts/benchmark-router.py*",
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
            # Check both exact match and with :latest suffix
            if model in available or f"{model}:latest" in available:
                print(f"  [OK] {model} already available")
                return True
            # Also check without tag (e.g. "llama3.2:1b" matches "llama3.2:1b")
            model_base = model.split(":")[0] if ":" in model else model
            for avail in available:
                if avail.startswith(model_base) and (
                    avail == model or avail.startswith(f"{model}-")
                ):
                    print(f"  [OK] {model} available as {avail}")
                    return True
    except Exception as e:
        print(f"  [WARN] Could not check models: {e}")

    print(f"  [PULL] Downloading {model}...")
    try:
        # Use stdin=DEVNULL and don't capture output — let ollama write progress
        # to stderr directly. Only check return code.
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
        else:
            # Check if it actually succeeded despite non-zero exit
            # (ollama sometimes returns 1 with terminal control chars)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{ollama_url}/api/tags")
                    available = [m["name"] for m in resp.json().get("models", [])]
                    if model in available:
                        print(f"  [OK] {model} pulled (exit code {result.returncode} ignored)")
                        return True
            except Exception:
                pass
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
        pass  # Keys may be in environment already

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    ollama_url = args.ollama_url

    # Determine which models to run
    models_to_run: dict[str, dict] = {}

    if args.models:
        # Specific models requested
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
    filtered = {}
    for m, info in models_to_run.items():
        provider = info.get("provider", "")
        if provider == "gemini" and not gemini_key:
            print(f"  [SKIP] {m} — no GEMINI_API_KEY")
            continue
        if provider == "openai" and not openai_key:
            print(f"  [SKIP] {m} — no OPENAI_API_KEY")
            continue
        if provider == "anthropic" and not anthropic_key:
            print(f"  [SKIP] {m} — no ANTHROPIC_API_KEY")
            continue
        filtered[m] = info
    models_to_run = filtered

    if not models_to_run:
        print("ERROR: No models to benchmark. Check API keys and model names.")
        sys.exit(1)

    # Determine prompts
    prompt_versions = args.prompts or list(PROMPTS.keys())

    n_runs = args.runs
    total_calls = len(models_to_run) * len(prompt_versions) * len(TEST_CORPUS) * n_runs
    print(f"\n{'=' * 60}")
    print("Router Classification Benchmark")
    print(f"{'=' * 60}")
    print(f"  Models: {list(models_to_run.keys())}")
    print(f"  Prompts: {prompt_versions}")
    print(f"  Messages: {len(TEST_CORPUS)}")
    print(f"  Runs: {n_runs}")
    print(f"  Total API calls: {total_calls}")
    print(f"{'=' * 60}\n")

    # Pull and warm up Ollama models
    local_models_to_run = {m: i for m, i in models_to_run.items() if i.get("provider") == "ollama"}
    if local_models_to_run and not args.skip_pull:
        print("Pulling Ollama models...")
        for model in local_models_to_run:
            if not await pull_ollama_model(model, ollama_url):
                print(f"  [WARN] Removing {model} from benchmark (pull failed)")
                del models_to_run[model]

    if local_models_to_run:
        print("\nWarming up Ollama models...")
        for model in list(local_models_to_run.keys()):
            if model in models_to_run:
                await warmup_ollama(model, ollama_url)

    # Run benchmark
    all_results: dict[str, ModelPromptResult] = {}
    all_metrics: dict[str, dict] = {}
    call_count = 0

    for model, model_info in models_to_run.items():
        provider = model_info.get("provider", "")

        for prompt_ver in prompt_versions:
            key = f"{model}_{prompt_ver}"
            prompt_text = PROMPTS[prompt_ver]
            mpr = ModelPromptResult(model=model, prompt_version=prompt_ver)

            print(f"\n--- {model} / {prompt_ver} ---")

            for run_idx in range(n_runs):
                if n_runs > 1:
                    print(f"  Run {run_idx + 1}/{n_runs}")

                for msg in TEST_CORPUS:
                    call_count += 1
                    if call_count % 25 == 0:
                        print(f"  [{call_count}/{total_calls}] Progress...")

                    if provider == "ollama":
                        result = await classify_ollama(
                            msg.text, prompt_text, prompt_ver, model, ollama_url
                        )
                    elif provider == "gemini":
                        result = await classify_gemini(
                            msg.text, prompt_text, prompt_ver, model, gemini_key
                        )
                    elif provider == "openai":
                        result = await classify_openai(
                            msg.text, prompt_text, prompt_ver, model, openai_key
                        )
                    elif provider == "anthropic":
                        result = await classify_anthropic(
                            msg.text, prompt_text, prompt_ver, model, anthropic_key
                        )
                    else:
                        continue

                    result.expected_intent = msg.expected_intent
                    mpr.results.append(result)

                    # Rate limit delay for cloud models
                    if provider != "ollama":
                        await asyncio.sleep(CLOUD_INTER_REQUEST_DELAY)

                    # Rough token estimation for cost tracking
                    prompt_tokens = len(prompt_text.split()) + len(msg.text.split())
                    output_tokens = len(result.raw_response.split()) if result.raw_response else 0
                    mpr.total_input_tokens += int(prompt_tokens * 1.3)  # ~1.3 tokens per word
                    mpr.total_output_tokens += int(output_tokens * 1.3)

            # Compute metrics for this combination
            metrics = compute_metrics(mpr.results, TEST_CORPUS)
            all_results[key] = mpr
            all_metrics[key] = metrics

            # Quick summary
            print(
                f"  -> Accuracy: {metrics['strict_accuracy']:.1%} strict, "
                f"{metrics['weighted_accuracy']:.1%} weighted | "
                f"Latency: {metrics['mean_latency_ms']:.0f}ms | "
                f"JSON: {metrics['json_compliance']:.0%}"
            )

    # Generate reports
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
        "n_messages": len(TEST_CORPUS),
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

    json_path = output_dir / f"benchmark_{timestamp}.json"
    json_report = generate_json_report(all_results, all_metrics, metadata)
    json_path.write_text(json.dumps(json_report, indent=2, default=str))
    print(f"\nJSON report: {json_path}")

    md_path = output_dir / f"benchmark_{timestamp}.md"
    md_report = generate_markdown_report(all_metrics, metadata, all_results)
    md_path.write_text(md_report)
    print(f"Markdown report: {md_path}")

    # Print summary
    print(f"\n{'=' * 60}")
    print("BENCHMARK COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  API calls: {call_count}")
    print(f"  Estimated cost: ${total_cost:.4f}")
    print()

    # Print leaderboard
    sorted_keys = sorted(
        all_metrics.keys(), key=lambda k: all_metrics[k]["weighted_accuracy"], reverse=True
    )
    print(
        f"{'Rank':<5} {'Model':<30} {'Prompt':<8} {'W.Acc':>7} {'S.Acc':>7} "
        f"{'F1':>7} {'Lat':>8} {'JSON':>6}"
    )
    print("-" * 85)
    for rank, key in enumerate(sorted_keys, 1):
        m = all_metrics[key]
        model, pv = key.rsplit("_", 1)
        print(
            f"{rank:<5} {model:<30} {pv:<8} {m['weighted_accuracy']:>6.1%} "
            f"{m['strict_accuracy']:>6.1%} {m['macro_f1']:>6.3f} "
            f"{m['mean_latency_ms']:>7.0f}ms {m['json_compliance']:>5.0%}"
        )

    # The Bug Case highlight
    print(f"\n{'=' * 60}")
    print('THE BUG CASE: "Are you there?"')
    print(f"{'=' * 60}")
    for key in sorted_keys:
        mpr = all_results[key]
        model, pv = key.rsplit("_", 1)
        for r in mpr.results:
            if r.message == "Are you there?":
                icon = "OK" if r.predicted_intent == "simple_query" else "FAIL"
                print(f"  [{icon}] {model}/{pv}: {r.predicted_intent} ({r.confidence:.2f})")
                break


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Benchmark router classification across models and prompts"
    )
    parser.add_argument("--local-only", action="store_true", help="Only test Ollama models")
    parser.add_argument("--cloud-only", action="store_true", help="Only test cloud models")
    parser.add_argument("--models", nargs="+", help="Specific model(s) to test")
    parser.add_argument(
        "--prompts", nargs="+", choices=list(PROMPTS.keys()), help="Specific prompts"
    )
    parser.add_argument("--runs", type=int, default=3, help="Runs per combination (default: 3)")
    parser.add_argument("--skip-pull", action="store_true", help="Skip Ollama model pulls")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
    parser.add_argument(
        "--output-dir",
        default="benchmarks/results",
        help="Output directory for reports",
    )

    args = parser.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
