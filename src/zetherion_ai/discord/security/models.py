"""Data models for the security analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ThreatCategory(StrEnum):
    """Categories of detected threats."""

    PROMPT_INJECTION = "prompt_injection"
    ENCODED_PAYLOAD = "encoded_payload"
    COMMAND_INJECTION = "command_injection"
    UNICODE_OBFUSCATION = "unicode_obfuscation"
    SOCIAL_ENGINEERING = "social_engineering"
    SUSPICIOUS_URL = "suspicious_url"
    EXCESSIVE_SPECIAL_CHARS = "excessive_special_chars"
    CONTROL_CHARACTERS = "control_characters"
    DATA_EXFILTRATION = "data_exfiltration"
    TOKEN_SMUGGLING = "token_smuggling"  # nosec B105
    CLEAN = "clean"


class ThreatAction(StrEnum):
    """Action to take based on analysis."""

    ALLOW = "allow"
    ESCALATE = "escalate"  # Send to Tier 2
    FLAG = "flag"  # Allow but log
    BLOCK = "block"  # Reject message


@dataclass
class ThreatSignal:
    """A single signal detected by a check."""

    category: ThreatCategory
    pattern_name: str
    matched_text: str
    score: float  # 0.0 - 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThreatVerdict:
    """The final verdict for a message."""

    action: ThreatAction
    score: float  # Aggregate threat score
    signals: list[ThreatSignal] = field(default_factory=list)
    tier_reached: int = 1
    ai_reasoning: str = ""
    processing_time_ms: float = 0.0
