"""Tier 1 security checks: regex patterns and heuristics.

All checks are synchronous and run in ~0ms. Each returns a list of
:class:`ThreatSignal` instances with per-pattern scores.
"""

from __future__ import annotations

import re
import unicodedata

from zetherion_ai.discord.security.models import ThreatCategory, ThreatSignal

# ---------------------------------------------------------------------------
# Pattern tuples: (compiled_regex, score, pattern_name)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    # --- Original patterns (from security.py) ---
    (
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions?|commands?|prompts?)",
            re.IGNORECASE,
        ),
        0.85,
        "ignore_previous",
    ),
    (
        re.compile(
            r"\bdisregard\s+(?:your|all|the)\s+(?:instructions?|commands?|rules?)", re.IGNORECASE
        ),
        0.85,
        "disregard_rules",
    ),
    (
        re.compile(
            r"\bforget\s+(?:your|all|the)\s+(?:instructions?|commands?|rules?|prompts?)",
            re.IGNORECASE,
        ),
        0.85,
        "forget_instructions",
    ),
    (
        re.compile(
            r"\boverride\s+(?:your|all|the|system)\s+(?:instructions?|commands?|settings?)",
            re.IGNORECASE,
        ),
        0.80,
        "override_instructions",
    ),
    (re.compile(r"\byou\s+are\s+now\s+(?:a|an|in)", re.IGNORECASE), 0.75, "you_are_now"),
    (
        re.compile(
            r"\bact\s+as\s+(?:if|though|my|the|a\s+different|an?\s+unrestricted)\b", re.IGNORECASE
        ),
        0.70,
        "act_as",
    ),
    (re.compile(r"\bpretend\s+(?:you\s+are|to\s+be|that)", re.IGNORECASE), 0.65, "pretend"),
    (
        re.compile(r"\bnew\s+(?:instructions?|commands?|rules?)[\s:]+", re.IGNORECASE),
        0.80,
        "new_instructions",
    ),
    (
        re.compile(r"\bsystem\s+(?:prompt|message|instruction)[\s:]+", re.IGNORECASE),
        0.75,
        "system_prompt_ref",
    ),
    (re.compile(r"\bjailbreak(?:ing)?", re.IGNORECASE), 0.90, "jailbreak"),
    (re.compile(r"\bdan\s+mode", re.IGNORECASE), 0.90, "dan_mode"),
    (
        re.compile(r"\b(?:enable|activate)\s+developer\s+mode", re.IGNORECASE),
        0.85,
        "developer_mode_enable",
    ),
    (
        re.compile(r"\bdeveloper\s+mode\s+(?:enable|on|activated?)", re.IGNORECASE),
        0.85,
        "developer_mode_on",
    ),
    (re.compile(r"\brole[\s:]?\s*system", re.IGNORECASE), 0.80, "role_system"),
    (re.compile(r"\bbegin\s+new\s+(?:task|role|persona)", re.IGNORECASE), 0.75, "begin_new_role"),
    (
        re.compile(r"\bignor(?:e|ing)\s+(?:all\s+)?(?:safeguards?|safety)", re.IGNORECASE),
        0.85,
        "ignore_safeguards",
    ),
    (
        re.compile(r"\bdisable\s+(?:all\s+)?(?:filters?|safety|restrictions?)", re.IGNORECASE),
        0.85,
        "disable_filters",
    ),
    (
        re.compile(r"\bbypass\s+(?:all\s+)?(?:filters?|safety|restrictions?)", re.IGNORECASE),
        0.85,
        "bypass_restrictions",
    ),
    # --- New prompt injection patterns ---
    (
        re.compile(r"\bfrom\s+now\s+on\b.*\b(?:always|never|only)\b", re.IGNORECASE),
        0.70,
        "multi_turn_manipulation",
    ),
    (
        re.compile(r"\b(?:end|close)\s+(?:of\s+)?(?:system|context|conversation)\b", re.IGNORECASE),
        0.60,
        "context_poisoning",
    ),
    (
        re.compile(
            r"\bthe\s+(?:ai|bot|assistant)\s+(?:should|must|will|needs?\s+to)\b", re.IGNORECASE
        ),
        0.40,
        "indirect_instruction",
    ),
    (
        re.compile(r"(?:assistant|model|AI)\s*:\s*(?:Sure|Of course|I'?ll|Yes|OK)", re.IGNORECASE),
        0.75,
        "completion_attack",
    ),
    (
        re.compile(r"```(?:system|instruction|hidden)[\s\S]*?```", re.IGNORECASE),
        0.70,
        "format_injection",
    ),
]

_COMMAND_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(
            r"(?:^|\s)(?:sudo|chmod|chown|rm\s+-rf|wget|curl\s+-[oO]|nc\s|netcat)\b", re.IGNORECASE
        ),
        0.80,
        "shell_command",
    ),
    (
        re.compile(r"\.\./|\.\.\\|/etc/|/proc/|/dev/|C:\\Windows", re.IGNORECASE),
        0.70,
        "path_traversal",
    ),
    (
        re.compile(
            r"\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|UNION)\s+.*\b(?:FROM|INTO|TABLE|WHERE)\b",
            re.IGNORECASE,
        ),
        0.60,
        "sql_injection",
    ),
    (
        re.compile(r"(?:--|;)\s*(?:DROP|DELETE|ALTER|EXEC)\b", re.IGNORECASE),
        0.80,
        "sql_destructive",
    ),
    (
        re.compile(r"\b(?:exec|eval|__import__|os\.system|subprocess|open\s*\()\b", re.IGNORECASE),
        0.70,
        "code_execution",
    ),
    (
        re.compile(r"\$\{?\w*(?:KEY|SECRET|TOKEN|PASS|API|AUTH)\w*\}?", re.IGNORECASE),
        0.60,
        "env_var_access",
    ),
]

_SOCIAL_ENGINEERING_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(
            r"\b(?:urgent|emergency|immediately|asap)\b.*\b(?:reveal|share|tell|show|give)\b",
            re.IGNORECASE,
        ),
        0.50,
        "urgency_manipulation",
    ),
    (
        re.compile(
            r"\b(?:i\s+am|this\s+is)\s+(?:the\s+)?(?:admin|developer|owner|creator)\b",
            re.IGNORECASE,
        ),
        0.60,
        "impersonation",
    ),
    (
        re.compile(
            r"\b(?:show|tell|reveal|expose|dump|list)\s+(?:me\s+)?(?:all|your|the)\s+(?:users?|data|config|secrets?|passwords?|tokens?|keys?)\b",
            re.IGNORECASE,
        ),
        0.70,
        "data_extraction",
    ),
]

_URL_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (re.compile(r"data:(?:text|application)/[^;]+;base64,", re.IGNORECASE), 0.80, "data_uri"),
    (re.compile(r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", re.IGNORECASE), 0.40, "ip_url"),
]

_TOKEN_SMUGGLING_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(r"\b(?:INST|SYS)\b|<<\s*SYS\s*>>|<\|im_start\||<\|im_end\|", re.IGNORECASE),
        0.80,
        "token_smuggling",
    ),
    (re.compile(r"\[INST\]|\[/INST\]|\[SYS\]|\[/SYS\]", re.IGNORECASE), 0.80, "inst_tags"),
]

# All pattern groups for convenient iteration
_ALL_PATTERN_GROUPS: list[tuple[ThreatCategory, list[tuple[re.Pattern[str], float, str]]]] = [
    (ThreatCategory.PROMPT_INJECTION, _INJECTION_PATTERNS),
    (ThreatCategory.COMMAND_INJECTION, _COMMAND_PATTERNS),
    (ThreatCategory.SOCIAL_ENGINEERING, _SOCIAL_ENGINEERING_PATTERNS),
    (ThreatCategory.SUSPICIOUS_URL, _URL_PATTERNS),
    (ThreatCategory.TOKEN_SMUGGLING, _TOKEN_SMUGGLING_PATTERNS),
]


def check_all_patterns(content: str) -> list[ThreatSignal]:
    """Run all regex pattern groups against *content*.

    Returns a list of :class:`ThreatSignal` for every match found.
    """
    signals: list[ThreatSignal] = []
    for category, patterns in _ALL_PATTERN_GROUPS:
        for pattern, score, name in patterns:
            match = pattern.search(content)
            if match:
                signals.append(
                    ThreatSignal(
                        category=category,
                        pattern_name=name,
                        matched_text=match.group(0)[:100],
                        score=score,
                    )
                )
    return signals


def check_heuristics(content: str) -> list[ThreatSignal]:
    """Run heuristic checks that don't fit a simple regex pattern."""
    signals: list[ThreatSignal] = []
    lower = content.lower()

    # Excessive roleplay markers
    roleplay_count = lower.count("[") + lower.count("(system")
    if roleplay_count > 5:
        signals.append(
            ThreatSignal(
                category=ThreatCategory.PROMPT_INJECTION,
                pattern_name="excessive_roleplay_markers",
                matched_text=f"count={roleplay_count}",
                score=min(0.9, 0.3 + roleplay_count * 0.1),
            )
        )

    # Excessive special characters
    if len(content) > 20:
        special_count = sum(1 for c in content if not c.isalnum() and not c.isspace())
        special_ratio = special_count / len(content)
        if special_ratio > 0.4:
            signals.append(
                ThreatSignal(
                    category=ThreatCategory.EXCESSIVE_SPECIAL_CHARS,
                    pattern_name="high_special_char_ratio",
                    matched_text=f"ratio={special_ratio:.2f}",
                    score=min(0.8, special_ratio),
                )
            )

    # Control characters (zero-width, RTL override, etc.)
    control_chars = [
        c for c in content if unicodedata.category(c) in ("Cf", "Cc") and c not in "\n\r\t"
    ]
    if control_chars:
        signals.append(
            ThreatSignal(
                category=ThreatCategory.CONTROL_CHARACTERS,
                pattern_name="invisible_control_chars",
                matched_text=f"count={len(control_chars)}",
                score=0.7,
            )
        )

    # Unicode homoglyph / obfuscation
    try:
        normalized = unicodedata.normalize("NFKC", content)
        if len(normalized) != len(content):
            diff_ratio = abs(len(normalized) - len(content)) / max(len(content), 1)
            if diff_ratio > 0.05:
                signals.append(
                    ThreatSignal(
                        category=ThreatCategory.UNICODE_OBFUSCATION,
                        pattern_name="homoglyph_detected",
                        matched_text=f"diff_ratio={diff_ratio:.2f}",
                        score=min(0.8, 0.4 + diff_ratio * 3),
                    )
                )
    except Exception:
        pass  # nosec B110 — Graceful degradation

    # Excessive length
    if len(content) > 4000:
        signals.append(
            ThreatSignal(
                category=ThreatCategory.PROMPT_INJECTION,
                pattern_name="excessive_length",
                matched_text=f"length={len(content)}",
                score=0.3,
            )
        )

    return signals
