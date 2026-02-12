"""Tests for YouTube prompt templates."""

import re
import string

import pytest

from zetherion_ai.skills.youtube.prompts import (
    AUDIENCE_SYNTHESIS_SYSTEM,
    AUDIENCE_SYNTHESIS_USER,
    CHANNEL_HEALTH_SYSTEM,
    CHANNEL_HEALTH_USER,
    COMMENT_ANALYSIS_SYSTEM,
    COMMENT_ANALYSIS_USER,
    COMMENT_BATCH_SYSTEM,
    COMMENT_BATCH_USER,
    ONBOARDING_FOLLOWUP_SYSTEM,
    ONBOARDING_FOLLOWUP_USER,
    REPLY_GENERATION_SYSTEM,
    REPLY_GENERATION_USER,
    STRATEGY_GENERATION_SYSTEM,
    STRATEGY_GENERATION_USER,
    TAG_SUGGESTION_SYSTEM,
    TAG_SUGGESTION_USER,
)

# All prompt constants paired as (name, value) for parametrised checks.
ALL_PROMPTS = [
    ("COMMENT_ANALYSIS_SYSTEM", COMMENT_ANALYSIS_SYSTEM),
    ("COMMENT_ANALYSIS_USER", COMMENT_ANALYSIS_USER),
    ("COMMENT_BATCH_SYSTEM", COMMENT_BATCH_SYSTEM),
    ("COMMENT_BATCH_USER", COMMENT_BATCH_USER),
    ("AUDIENCE_SYNTHESIS_SYSTEM", AUDIENCE_SYNTHESIS_SYSTEM),
    ("AUDIENCE_SYNTHESIS_USER", AUDIENCE_SYNTHESIS_USER),
    ("REPLY_GENERATION_SYSTEM", REPLY_GENERATION_SYSTEM),
    ("REPLY_GENERATION_USER", REPLY_GENERATION_USER),
    ("TAG_SUGGESTION_SYSTEM", TAG_SUGGESTION_SYSTEM),
    ("TAG_SUGGESTION_USER", TAG_SUGGESTION_USER),
    ("CHANNEL_HEALTH_SYSTEM", CHANNEL_HEALTH_SYSTEM),
    ("CHANNEL_HEALTH_USER", CHANNEL_HEALTH_USER),
    ("STRATEGY_GENERATION_SYSTEM", STRATEGY_GENERATION_SYSTEM),
    ("STRATEGY_GENERATION_USER", STRATEGY_GENERATION_USER),
    ("ONBOARDING_FOLLOWUP_SYSTEM", ONBOARDING_FOLLOWUP_SYSTEM),
    ("ONBOARDING_FOLLOWUP_USER", ONBOARDING_FOLLOWUP_USER),
]

# System prompts should be fully static — no single-brace placeholders.
SYSTEM_PROMPTS = [
    ("COMMENT_ANALYSIS_SYSTEM", COMMENT_ANALYSIS_SYSTEM),
    ("COMMENT_BATCH_SYSTEM", COMMENT_BATCH_SYSTEM),
    ("AUDIENCE_SYNTHESIS_SYSTEM", AUDIENCE_SYNTHESIS_SYSTEM),
    ("TAG_SUGGESTION_SYSTEM", TAG_SUGGESTION_SYSTEM),
    ("CHANNEL_HEALTH_SYSTEM", CHANNEL_HEALTH_SYSTEM),
    ("STRATEGY_GENERATION_SYSTEM", STRATEGY_GENERATION_SYSTEM),
    ("ONBOARDING_FOLLOWUP_SYSTEM", ONBOARDING_FOLLOWUP_SYSTEM),
]

# User prompts with their expected placeholder names.
USER_PROMPTS_AND_PLACEHOLDERS = [
    ("COMMENT_ANALYSIS_USER", COMMENT_ANALYSIS_USER, ["comment_text"]),
    ("COMMENT_BATCH_USER", COMMENT_BATCH_USER, ["comments_json"]),
    (
        "AUDIENCE_SYNTHESIS_USER",
        AUDIENCE_SYNTHESIS_USER,
        [
            "channel_name",
            "total_comments",
            "sentiment_summary",
            "top_topics",
            "questions",
            "complaints",
            "video_performance",
            "channel_stats",
            "assumptions",
        ],
    ),
    (
        "REPLY_GENERATION_USER",
        REPLY_GENERATION_USER,
        ["video_title", "author", "comment_text", "category"],
    ),
    (
        "TAG_SUGGESTION_USER",
        TAG_SUGGESTION_USER,
        [
            "video_title",
            "video_description",
            "current_tags",
            "top_topics",
            "keyword_targets",
        ],
    ),
    (
        "CHANNEL_HEALTH_USER",
        CHANNEL_HEALTH_USER,
        [
            "channel_name",
            "description",
            "video_count",
            "subscriber_count",
            "has_playlists",
            "upload_frequency",
            "default_tags",
            "about_section",
        ],
    ),
    (
        "STRATEGY_GENERATION_USER",
        STRATEGY_GENERATION_USER,
        [
            "intelligence_report",
            "client_documents",
            "assumptions",
            "previous_strategy",
            "trust_level",
            "reply_stats",
        ],
    ),
    (
        "ONBOARDING_FOLLOWUP_USER",
        ONBOARDING_FOLLOWUP_USER,
        ["answers_so_far", "missing_categories"],
    ),
]

# REPLY_GENERATION_SYSTEM also has placeholders (tone, topics, exclusions).
SYSTEM_PROMPTS_WITH_PLACEHOLDERS = [
    (
        "REPLY_GENERATION_SYSTEM",
        REPLY_GENERATION_SYSTEM,
        ["tone", "topics", "exclusions"],
    ),
]


def _extract_field_names(template: str) -> set[str]:
    """Return the set of field names from a format string (single-brace only)."""
    formatter = string.Formatter()
    return {
        field_name for _, field_name, _, _ in formatter.parse(template) if field_name is not None
    }


# ---------------------------------------------------------------------------
# 1. All prompt constants are non-empty strings
# ---------------------------------------------------------------------------


class TestPromptsAreNonEmptyStrings:
    """Every prompt constant must be a non-empty string."""

    @pytest.mark.parametrize("name,value", ALL_PROMPTS, ids=[p[0] for p in ALL_PROMPTS])
    def test_is_non_empty_string(self, name: str, value: str) -> None:
        assert isinstance(value, str), f"{name} is not a string"
        assert len(value.strip()) > 0, f"{name} is empty or whitespace-only"


# ---------------------------------------------------------------------------
# 2. User prompts can be formatted without KeyError
# ---------------------------------------------------------------------------


class TestUserPromptsFormattable:
    """User prompts with placeholders must format without errors."""

    @pytest.mark.parametrize(
        "name,template,placeholders",
        USER_PROMPTS_AND_PLACEHOLDERS,
        ids=[p[0] for p in USER_PROMPTS_AND_PLACEHOLDERS],
    )
    def test_user_prompt_formats_without_error(
        self, name: str, template: str, placeholders: list[str]
    ) -> None:
        kwargs = {k: f"<{k}>" for k in placeholders}
        result = template.format(**kwargs)
        assert isinstance(result, str)
        # The formatted result should no longer contain any unfilled placeholders.
        remaining = _extract_field_names(result)
        assert remaining == set(), (
            f"{name} still has unfilled placeholders after formatting: {remaining}"
        )

    @pytest.mark.parametrize(
        "name,template,placeholders",
        SYSTEM_PROMPTS_WITH_PLACEHOLDERS,
        ids=[p[0] for p in SYSTEM_PROMPTS_WITH_PLACEHOLDERS],
    )
    def test_system_prompt_with_placeholders_formats(
        self, name: str, template: str, placeholders: list[str]
    ) -> None:
        kwargs = {k: f"<{k}>" for k in placeholders}
        result = template.format(**kwargs)
        assert isinstance(result, str)
        remaining = _extract_field_names(result)
        assert remaining == set(), (
            f"{name} still has unfilled placeholders after formatting: {remaining}"
        )


# ---------------------------------------------------------------------------
# 3. System prompts without intended placeholders are static
# ---------------------------------------------------------------------------

# Regex that matches single-brace placeholders like {foo} but NOT double-brace
# literals like {{foo}}.  We look for `{identifier}` not preceded/followed by
# another brace.
_SINGLE_BRACE_RE = re.compile(r"(?<!\{)\{([a-zA-Z_]\w*)\}(?!\})")

# These system prompts should be entirely static (no user-facing placeholders).
STATIC_SYSTEM_PROMPTS = [
    ("COMMENT_ANALYSIS_SYSTEM", COMMENT_ANALYSIS_SYSTEM),
    ("COMMENT_BATCH_SYSTEM", COMMENT_BATCH_SYSTEM),
    ("AUDIENCE_SYNTHESIS_SYSTEM", AUDIENCE_SYNTHESIS_SYSTEM),
    ("TAG_SUGGESTION_SYSTEM", TAG_SUGGESTION_SYSTEM),
    ("CHANNEL_HEALTH_SYSTEM", CHANNEL_HEALTH_SYSTEM),
    ("STRATEGY_GENERATION_SYSTEM", STRATEGY_GENERATION_SYSTEM),
    ("ONBOARDING_FOLLOWUP_SYSTEM", ONBOARDING_FOLLOWUP_SYSTEM),
]


class TestStaticSystemPrompts:
    """System prompts that are meant to be static must not contain
    single-brace placeholders (double-brace JSON examples are fine)."""

    @pytest.mark.parametrize(
        "name,value",
        STATIC_SYSTEM_PROMPTS,
        ids=[p[0] for p in STATIC_SYSTEM_PROMPTS],
    )
    def test_no_single_brace_placeholders(self, name: str, value: str) -> None:
        matches = _SINGLE_BRACE_RE.findall(value)
        assert matches == [], f"{name} contains unexpected single-brace placeholders: {matches}"


# ---------------------------------------------------------------------------
# 4. User prompts contain the expected placeholder names
# ---------------------------------------------------------------------------


class TestUserPromptsContainExpectedPlaceholders:
    """Each user prompt must expose exactly the expected set of placeholders."""

    @pytest.mark.parametrize(
        "name,template,expected",
        USER_PROMPTS_AND_PLACEHOLDERS,
        ids=[p[0] for p in USER_PROMPTS_AND_PLACEHOLDERS],
    )
    def test_expected_placeholders_present(
        self, name: str, template: str, expected: list[str]
    ) -> None:
        actual = _extract_field_names(template)
        expected_set = set(expected)
        missing = expected_set - actual
        extra = actual - expected_set
        assert missing == set(), f"{name} is missing placeholders: {missing}"
        assert extra == set(), f"{name} has unexpected extra placeholders: {extra}"

    @pytest.mark.parametrize(
        "name,template,expected",
        SYSTEM_PROMPTS_WITH_PLACEHOLDERS,
        ids=[p[0] for p in SYSTEM_PROMPTS_WITH_PLACEHOLDERS],
    )
    def test_system_prompt_placeholders_match(
        self, name: str, template: str, expected: list[str]
    ) -> None:
        actual = _extract_field_names(template)
        expected_set = set(expected)
        missing = expected_set - actual
        extra = actual - expected_set
        assert missing == set(), f"{name} is missing placeholders: {missing}"
        assert extra == set(), f"{name} has unexpected extra placeholders: {extra}"


# ---------------------------------------------------------------------------
# 5. Prompt pairs exist (every _SYSTEM has a matching _USER)
# ---------------------------------------------------------------------------


class TestPromptPairsExist:
    """Each skill should have both a _SYSTEM and _USER prompt."""

    PREFIXES = [
        "COMMENT_ANALYSIS",
        "COMMENT_BATCH",
        "AUDIENCE_SYNTHESIS",
        "REPLY_GENERATION",
        "TAG_SUGGESTION",
        "CHANNEL_HEALTH",
        "STRATEGY_GENERATION",
        "ONBOARDING_FOLLOWUP",
    ]

    @pytest.mark.parametrize("prefix", PREFIXES)
    def test_system_and_user_pair(self, prefix: str) -> None:
        from zetherion_ai.skills.youtube import prompts as mod

        system_attr = f"{prefix}_SYSTEM"
        user_attr = f"{prefix}_USER"
        assert hasattr(mod, system_attr), f"Missing {system_attr}"
        assert hasattr(mod, user_attr), f"Missing {user_attr}"
        assert isinstance(getattr(mod, system_attr), str)
        assert isinstance(getattr(mod, user_attr), str)
