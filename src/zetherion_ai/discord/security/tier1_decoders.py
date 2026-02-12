"""Tier 1 payload decoders: detect and decode obfuscated content.

Attempts to decode base64, hex, and URL-encoded payloads, then re-runs
the Tier 1 regex patterns against the decoded content.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import unquote

from zetherion_ai.discord.security.models import ThreatCategory, ThreatSignal

# At least 20 chars of base64 alphabet with optional padding
_BASE64_PATTERN = re.compile(r"(?:[A-Za-z0-9+/]{4}){5,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")

# Long hex strings (>= 20 hex chars)
_HEX_PATTERN = re.compile(r"(?:0x)?([0-9a-fA-F]{20,})")

# Three or more consecutive percent-encoded characters
_URL_ENCODED_PATTERN = re.compile(r"(?:%[0-9a-fA-F]{2}){3,}")


def decode_and_check(content: str) -> list[ThreatSignal]:
    """Attempt to decode encoded payloads and re-scan their content.

    Returns :class:`ThreatSignal` instances for any detections. Even if no
    inner patterns match, the presence of decoded content that looks like
    readable text is flagged at a low score.
    """
    signals: list[ThreatSignal] = []
    decoded_texts: list[tuple[str, str, str]] = []  # (encoding, decoded, preview)

    # Base64
    for match in _BASE64_PATTERN.finditer(content):
        try:
            decoded = base64.b64decode(match.group(0)).decode("utf-8", errors="ignore")
            if len(decoded) > 5 and _is_mostly_printable(decoded):
                decoded_texts.append(("base64", decoded, match.group(0)[:30]))
        except Exception:
            pass  # nosec B110

    # Hex
    for match in _HEX_PATTERN.finditer(content):
        try:
            decoded = bytes.fromhex(match.group(1)).decode("utf-8", errors="ignore")
            if len(decoded) > 5 and _is_mostly_printable(decoded):
                decoded_texts.append(("hex", decoded, match.group(0)[:30]))
        except Exception:
            pass  # nosec B110

    # URL-encoded
    for match in _URL_ENCODED_PATTERN.finditer(content):
        decoded = unquote(match.group(0))
        if decoded != match.group(0):
            decoded_texts.append(("url_encoded", decoded, match.group(0)[:30]))

    # Re-run Tier 1 regex on each decoded payload
    for encoding, decoded_text, original_preview in decoded_texts:
        from zetherion_ai.discord.security.tier1_regex import check_all_patterns

        inner_signals = check_all_patterns(decoded_text)
        if inner_signals:
            for sig in inner_signals:
                sig.score = min(1.0, sig.score + 0.2)  # Boost for encoded payload
                sig.metadata["encoding"] = encoding
                sig.metadata["original_preview"] = original_preview
                sig.category = ThreatCategory.ENCODED_PAYLOAD
            signals.extend(inner_signals)
        else:
            # Flag presence of encoded readable content even without pattern matches
            signals.append(
                ThreatSignal(
                    category=ThreatCategory.ENCODED_PAYLOAD,
                    pattern_name=f"{encoding}_detected",
                    matched_text=original_preview,
                    score=0.3,
                    metadata={
                        "encoding": encoding,
                        "decoded_preview": decoded_text[:100],
                    },
                )
            )

    return signals


def _is_mostly_printable(text: str) -> bool:
    """Return True if most characters in *text* are printable ASCII."""
    if not text:
        return False
    printable = sum(1 for c in text if c.isprintable())
    return printable / len(text) > 0.7
