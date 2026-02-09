"""Data models for the observation pipeline.

Defines ObservationEvent (input) and ExtractedItem (output) used by all
source adapters, extractors, and dispatchers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ItemType(StrEnum):
    """Types of items the pipeline can extract."""

    TASK = "task"
    DEADLINE = "deadline"
    COMMITMENT = "commitment"
    CONTACT = "contact"
    FACT = "fact"
    MEETING = "meeting"
    REMINDER = "reminder"
    ACTION_ITEM = "action_item"


class ExtractionTier(int):
    """Extraction method tier.

    Tier 1: Regex/keywords (free, instant)
    Tier 2: Ollama local LLM (free, ~1-3s)
    Tier 3: InferenceBroker → Claude (~$0.01, ~2-5s)
    """


TIER_REGEX = 1
TIER_OLLAMA = 2
TIER_CLOUD = 3


@dataclass
class ObservationEvent:
    """Universal input event from any source adapter.

    Every source (Discord, Gmail, Calendar, Slack, etc.) converts its
    native events into this format before feeding the pipeline.
    """

    source: str  # 'discord', 'gmail', 'calendar', 'slack'
    source_id: str  # message ID, email ID, etc.
    user_id: int  # Discord user ID (bot owner)
    author: str  # who wrote/sent this
    author_is_owner: bool  # is this the bot's owner speaking?
    content: str  # the text to analyze
    timestamp: datetime = field(default_factory=datetime.now)
    context: dict[str, Any] = field(default_factory=dict)
    conversation_history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.source:
            raise ValueError("source must not be empty")
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        if not self.content:
            raise ValueError("content must not be empty")


@dataclass
class ExtractedItem:
    """An item extracted from an observation event.

    Produced by extractors and consumed by the dispatcher to route
    to action targets (TaskManager, Calendar, PersonalModel, etc.).
    """

    item_type: ItemType
    content: str  # human-readable description
    confidence: float  # 0.0-1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    source_event: ObservationEvent | None = None
    extraction_tier: int = TIER_REGEX

    def __post_init__(self) -> None:
        """Validate fields."""
        if not self.content:
            raise ValueError("content must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")
        if self.extraction_tier not in (TIER_REGEX, TIER_OLLAMA, TIER_CLOUD):
            raise ValueError(f"extraction_tier must be 1, 2, or 3, got {self.extraction_tier}")
