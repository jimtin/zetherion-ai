"""Tiered extraction engine for the observation pipeline.

Tier 1: Regex/keyword extraction (free, instant)
Tier 2: Ollama local LLM (free, ~1-3s)
Tier 3: InferenceBroker → Claude (~$0.01, ~2-5s)

Tier escalation: Start at Tier 1. If content has signals but extraction
is uncertain (confidence 0.3-0.6), escalate to Tier 2. If Tier 2 is
still uncertain, escalate to Tier 3 only for high-value signals.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.models import (
    TIER_CLOUD,
    TIER_OLLAMA,
    TIER_REGEX,
    ExtractedItem,
    ItemType,
    ObservationEvent,
)

log = get_logger("zetherion_ai.observation.extractors")

# ---------------------------------------------------------------------------
# Confidence thresholds for tier escalation
# ---------------------------------------------------------------------------

ESCALATION_LOW = 0.3  # Below this: no signal, skip
ESCALATION_HIGH = 0.6  # Above this: confident enough, no escalation
MIN_CONTENT_LENGTH_FOR_LLM = 20  # Don't send very short texts to LLMs


# ---------------------------------------------------------------------------
# LLM provider protocol (for dependency injection in tests)
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Protocol for LLM inference providers used by Tier 2 and Tier 3."""

    async def extract(
        self, text: str, *, conversation_history: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Extract structured items from text.

        Returns a list of dicts with keys:
        - item_type: str (from ItemType enum)
        - content: str (human-readable description)
        - confidence: float (0.0-1.0)
        - metadata: dict (type-specific data)
        """
        ...


# ---------------------------------------------------------------------------
# Tier 1: Regex extraction
# ---------------------------------------------------------------------------

# Date patterns
_DATE_PATTERNS = [
    # "by Friday", "by tomorrow", "by next Monday"
    re.compile(
        r"\b(?:by|before|until|due)\s+"
        r"(tomorrow|today|tonight|"
        r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
        r"\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    ),
    # "on March 15", "on 3/15"
    re.compile(
        r"\bon\s+"
        r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}|"
        r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)",
        re.IGNORECASE,
    ),
]

# Task/commitment patterns
_TASK_PATTERNS = [
    # "I'll handle that", "I will do it", "I'll take care of"
    re.compile(
        r"\b(?:i'?ll|i\s+will|i\s+can|i\s+shall)\s+"
        r"(?:handle|do|take\s+care\s+of|finish|complete|send|prepare|write|create|build|fix|"
        r"review|update|check|look\s+into|work\s+on|get\s+back|follow\s+up|set\s+up)",
        re.IGNORECASE,
    ),
    # "TODO:", "TASK:", "ACTION:"
    re.compile(r"\b(?:TODO|TASK|ACTION|FIXME|HACK):\s*(.+)", re.IGNORECASE),
    # "need to", "have to", "must", "should"
    re.compile(
        r"\b(?:i\s+)?(?:need\s+to|have\s+to|must|should)\s+"
        r"(?:handle|do|finish|complete|send|prepare|write|create|build|fix|"
        r"review|update|check|look\s+into|work\s+on|get\s+back|follow\s+up|set\s+up)",
        re.IGNORECASE,
    ),
]

# Meeting patterns
_MEETING_PATTERNS = [
    # "let's meet", "schedule a meeting", "let's schedule"
    re.compile(
        r"\b(?:let'?s\s+(?:meet|schedule|sync|catch\s+up|chat)|"
        r"schedule\s+a\s+(?:meeting|call|sync|chat)|"
        r"meeting\s+(?:at|on|tomorrow|next))",
        re.IGNORECASE,
    ),
]

# Contact patterns (email addresses)
_EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")

# Reminder patterns
_REMINDER_PATTERNS = [
    re.compile(
        r"\b(?:remind\s+me|don'?t\s+forget|remember\s+to|note\s+to\s+self)\b",
        re.IGNORECASE,
    ),
]


def extract_tier1(event: ObservationEvent) -> list[ExtractedItem]:
    """Run Tier 1 regex extraction on an observation event.

    Returns extracted items with confidence scores. Items with confidence
    below ESCALATION_LOW are discarded.
    """
    items: list[ExtractedItem] = []
    text = event.content

    # --- Task/commitment extraction ---
    for pattern in _TASK_PATTERNS:
        match = pattern.search(text)
        if match:
            # Check if there's a date component too
            has_date = any(dp.search(text) for dp in _DATE_PATTERNS)
            confidence = 0.75 if has_date else 0.55

            # Get the task description
            task_text = match.group(0).strip()
            metadata: dict[str, Any] = {"raw_match": task_text}

            # If it's a TODO:/TASK: pattern, extract the content after the colon
            if match.lastindex and match.group(1):
                task_text = match.group(1).strip()
                confidence = 0.85  # Explicit markers are high confidence
                metadata["raw_match"] = task_text

            items.append(
                ExtractedItem(
                    item_type=ItemType.TASK,
                    content=task_text,
                    confidence=confidence,
                    metadata=metadata,
                    source_event=event,
                    extraction_tier=TIER_REGEX,
                )
            )
            break  # One task per message for Tier 1

    # --- Deadline extraction ---
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            date_ref = match.group(1) if match.lastindex else match.group(0)
            items.append(
                ExtractedItem(
                    item_type=ItemType.DEADLINE,
                    content=f"Deadline reference: {date_ref.strip()}",
                    confidence=0.6,
                    metadata={"date_reference": date_ref.strip()},
                    source_event=event,
                    extraction_tier=TIER_REGEX,
                )
            )
            break  # One deadline per message for Tier 1

    # --- Meeting extraction ---
    for pattern in _MEETING_PATTERNS:
        match = pattern.search(text)
        if match:
            items.append(
                ExtractedItem(
                    item_type=ItemType.MEETING,
                    content=f"Meeting reference: {match.group(0).strip()}",
                    confidence=0.55,
                    metadata={"raw_match": match.group(0).strip()},
                    source_event=event,
                    extraction_tier=TIER_REGEX,
                )
            )
            break

    # --- Contact extraction (emails) ---
    for match in _EMAIL_PATTERN.finditer(text):
        email = match.group(0)
        items.append(
            ExtractedItem(
                item_type=ItemType.CONTACT,
                content=f"Email contact: {email}",
                confidence=0.9,
                metadata={"email": email},
                source_event=event,
                extraction_tier=TIER_REGEX,
            )
        )

    # --- Reminder extraction ---
    for pattern in _REMINDER_PATTERNS:
        match = pattern.search(text)
        if match:
            items.append(
                ExtractedItem(
                    item_type=ItemType.REMINDER,
                    content=text.strip(),
                    confidence=0.7,
                    metadata={"raw_match": match.group(0).strip()},
                    source_event=event,
                    extraction_tier=TIER_REGEX,
                )
            )
            break

    return items


# ---------------------------------------------------------------------------
# Tier 2 & 3: LLM-based extraction
# ---------------------------------------------------------------------------


async def extract_tier2(
    event: ObservationEvent,
    provider: LLMProvider,
    *,
    existing_items: list[ExtractedItem] | None = None,
) -> list[ExtractedItem]:
    """Run Tier 2 (Ollama) extraction.

    Uses a local LLM to find implicit commitments, meeting suggestions,
    and relationship signals that regex can't catch.
    """
    if len(event.content) < MIN_CONTENT_LENGTH_FOR_LLM:
        return []

    try:
        raw_items = await provider.extract(
            event.content,
            conversation_history=event.conversation_history or None,
        )
    except Exception as exc:
        log.warning("tier2_extraction_failed", error=str(exc), source_id=event.source_id)
        return []

    items: list[ExtractedItem] = []
    for raw in raw_items:
        try:
            item_type = ItemType(raw.get("item_type", "fact"))
        except ValueError:
            item_type = ItemType.FACT

        confidence = float(raw.get("confidence", 0.5))
        if confidence < ESCALATION_LOW:
            continue

        items.append(
            ExtractedItem(
                item_type=item_type,
                content=raw.get("content", event.content[:100]),
                confidence=confidence,
                metadata=raw.get("metadata", {}),
                source_event=event,
                extraction_tier=TIER_OLLAMA,
            )
        )

    return items


async def extract_tier3(
    event: ObservationEvent,
    provider: LLMProvider,
    *,
    existing_items: list[ExtractedItem] | None = None,
) -> list[ExtractedItem]:
    """Run Tier 3 (Claude) extraction.

    Only used for ambiguous contexts, complex multi-party commitments,
    and nuanced intent where lower tiers are uncertain.
    """
    if len(event.content) < MIN_CONTENT_LENGTH_FOR_LLM:
        return []

    try:
        raw_items = await provider.extract(
            event.content,
            conversation_history=event.conversation_history or None,
        )
    except Exception as exc:
        log.warning("tier3_extraction_failed", error=str(exc), source_id=event.source_id)
        return []

    items: list[ExtractedItem] = []
    for raw in raw_items:
        try:
            item_type = ItemType(raw.get("item_type", "fact"))
        except ValueError:
            item_type = ItemType.FACT

        confidence = float(raw.get("confidence", 0.5))
        if confidence < ESCALATION_LOW:
            continue

        items.append(
            ExtractedItem(
                item_type=item_type,
                content=raw.get("content", event.content[:100]),
                confidence=confidence,
                metadata=raw.get("metadata", {}),
                source_event=event,
                extraction_tier=TIER_CLOUD,
            )
        )

    return items


def needs_escalation(items: list[ExtractedItem]) -> bool:
    """Check if Tier 1 results need escalation to Tier 2/3.

    Returns True if any item has confidence in the uncertain range
    (ESCALATION_LOW to ESCALATION_HIGH).
    """
    return any(ESCALATION_LOW <= item.confidence < ESCALATION_HIGH for item in items)


def merge_extractions(
    tier1: list[ExtractedItem],
    tier2: list[ExtractedItem],
    tier3: list[ExtractedItem] | None = None,
) -> list[ExtractedItem]:
    """Merge results from multiple tiers, preferring higher-tier extractions.

    Higher tiers override lower tiers for the same item type. Items from
    different types are all kept.
    """
    # Group by item_type, keep the highest-tier version
    best: dict[str, ExtractedItem] = {}

    for items in [tier1, tier2, tier3 or []]:
        for item in items:
            key = f"{item.item_type}:{item.content[:50]}"
            existing = best.get(key)
            if existing is None or item.extraction_tier > existing.extraction_tier:
                best[key] = item

    # Also include items that don't overlap
    all_items = list(best.values())

    # Deduplicate by looking for same type + similar content
    seen_types: dict[ItemType, list[ExtractedItem]] = {}
    deduped: list[ExtractedItem] = []

    for item in all_items:
        if item.item_type not in seen_types:
            seen_types[item.item_type] = []

        # Check for duplicates: same type and overlapping content
        is_dup = False
        for existing in seen_types[item.item_type]:
            if (
                item.content[:30] == existing.content[:30]
                and item.extraction_tier <= existing.extraction_tier
            ):
                is_dup = True
                break

        if not is_dup:
            seen_types[item.item_type].append(item)
            deduped.append(item)

    return deduped
