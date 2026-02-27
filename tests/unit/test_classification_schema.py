"""Unit tests for the email classification schema and prompt builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zetherion_ai.routing.classification import (
    ContactSignal,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailSentiment,
    ThreadContext,
    UrgencyTrend,
)
from zetherion_ai.routing.classification_prompt import (
    SYSTEM_PROMPT,
    build_classification_prompt,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _valid_classification_dict() -> dict:
    """Return a minimal valid classification dict."""
    return {
        "category": "work_client",
        "action": "reply_normal",
        "urgency": 0.4,
        "confidence": 0.85,
        "sentiment": "neutral",
        "topics": ["project update", "deadline"],
        "summary": "Client asking about project timeline",
        "thread": {
            "is_thread": True,
            "thread_position": 3,
            "thread_summary": "Ongoing project discussion",
            "urgency_trend": "stable",
        },
        "contact": {
            "name": "Alice Smith",
            "email": "alice@example.com",
            "role": "Project Manager",
            "company": "Acme Corp",
            "relationship": "client",
            "communication_style": "formal",
            "sentiment": "neutral",
            "importance_signal": 0.7,
        },
        "reasoning": "Client email about project timeline",
    }


# ---------------------------------------------------------------------------
# EmailClassification — happy path
# ---------------------------------------------------------------------------


class TestEmailClassificationValidation:
    """Test EmailClassification.model_validate with various inputs."""

    def test_full_valid_dict(self) -> None:
        data = _valid_classification_dict()
        c = EmailClassification.model_validate(data)

        assert c.category == "work_client"
        assert c.action == EmailAction.REPLY_NORMAL
        assert c.urgency == 0.4
        assert c.confidence == 0.85
        assert c.sentiment == EmailSentiment.NEUTRAL
        assert c.topics == ["project update", "deadline"]
        assert c.summary == "Client asking about project timeline"
        assert c.thread.is_thread is True
        assert c.thread.thread_position == 3
        assert c.thread.urgency_trend == UrgencyTrend.STABLE
        assert c.contact.name == "Alice Smith"
        assert c.contact.company == "Acme Corp"
        assert c.contact.importance_signal == 0.7

    def test_minimal_dict(self) -> None:
        c = EmailClassification.model_validate({"action": "archive"})
        assert c.category == "unknown"
        assert c.action == EmailAction.ARCHIVE
        assert c.urgency == 0.5
        assert c.confidence == 0.5
        assert c.topics == []
        assert c.contact.name == ""
        assert c.thread.is_thread is False

    def test_empty_dict_uses_defaults(self) -> None:
        c = EmailClassification.model_validate({})
        assert c.category == "unknown"
        assert c.action == EmailAction.READ_ONLY
        assert c.urgency == 0.5

    def test_extra_fields_ignored(self) -> None:
        data = _valid_classification_dict()
        data["unknown_field"] = "should be ignored"
        data["another_field"] = 42
        c = EmailClassification.model_validate(data)
        assert c.category == "work_client"

    def test_from_dict_classmethod(self) -> None:
        data = _valid_classification_dict()
        c = EmailClassification.from_dict(data)
        assert c.category == "work_client"
        assert c.action == EmailAction.REPLY_NORMAL


# ---------------------------------------------------------------------------
# Category normalisation
# ---------------------------------------------------------------------------


class TestCategoryNormalisation:
    """Test that category values are normalised to lowercase."""

    def test_uppercase_normalised(self) -> None:
        c = EmailClassification.model_validate({"category": "WORK_CLIENT"})
        assert c.category == "work_client"

    def test_mixed_case_normalised(self) -> None:
        c = EmailClassification.model_validate({"category": "Newsletter"})
        assert c.category == "newsletter"

    def test_whitespace_stripped(self) -> None:
        c = EmailClassification.model_validate({"category": "  personal  "})
        assert c.category == "personal"

    def test_custom_category_accepted(self) -> None:
        c = EmailClassification.model_validate({"category": "my_custom_category"})
        assert c.category == "my_custom_category"

    def test_empty_category_becomes_empty(self) -> None:
        c = EmailClassification.model_validate({"category": ""})
        assert c.category == ""


# ---------------------------------------------------------------------------
# Topic normalisation
# ---------------------------------------------------------------------------


class TestTopicNormalisation:
    """Test topic deduplication and capping."""

    def test_deduplication(self) -> None:
        c = EmailClassification.model_validate({"topics": ["Project", "project", "PROJECT"]})
        assert c.topics == ["project"]

    def test_cap_at_10(self) -> None:
        topics = [f"topic_{i}" for i in range(20)]
        c = EmailClassification.model_validate({"topics": topics})
        assert len(c.topics) == 10

    def test_empty_topics_filtered(self) -> None:
        c = EmailClassification.model_validate({"topics": ["valid", "", "  ", "also valid"]})
        assert c.topics == ["valid", "also valid"]

    def test_whitespace_stripped(self) -> None:
        c = EmailClassification.model_validate({"topics": ["  deadline  ", "  meeting  "]})
        assert c.topics == ["deadline", "meeting"]


# ---------------------------------------------------------------------------
# to_route_tag / to_route_mode
# ---------------------------------------------------------------------------


class TestRouteTagMapping:
    """Test backward-compatibility mapping to RouteTag."""

    @pytest.mark.parametrize(
        ("action", "expected_tag"),
        [
            (EmailAction.REPLY_URGENT, "reply_candidate"),
            (EmailAction.REPLY_NORMAL, "reply_candidate"),
            (EmailAction.ACTION_REQUIRED, "task_candidate"),
            (EmailAction.CREATE_TASK, "task_candidate"),
            (EmailAction.CREATE_EVENT, "calendar_candidate"),
            (EmailAction.READ_ONLY, "digest_only"),
            (EmailAction.ARCHIVE, "ignore"),
            (EmailAction.IGNORE, "ignore"),
        ],
    )
    def test_action_to_route_tag(self, action: EmailAction, expected_tag: str) -> None:
        c = EmailClassification(action=action)
        assert c.to_route_tag() == expected_tag

    @pytest.mark.parametrize(
        ("action", "expected_mode"),
        [
            (EmailAction.REPLY_URGENT, "draft"),
            (EmailAction.REPLY_NORMAL, "draft"),
            (EmailAction.ACTION_REQUIRED, "ask"),
            (EmailAction.CREATE_TASK, "draft"),
            (EmailAction.CREATE_EVENT, "draft"),
            (EmailAction.READ_ONLY, "skip"),
            (EmailAction.ARCHIVE, "skip"),
            (EmailAction.IGNORE, "skip"),
        ],
    )
    def test_action_to_route_mode(self, action: EmailAction, expected_mode: str) -> None:
        c = EmailClassification(action=action)
        assert c.to_route_mode() == expected_mode


# ---------------------------------------------------------------------------
# is_urgent
# ---------------------------------------------------------------------------


class TestIsUrgent:
    """Test urgency threshold logic."""

    def test_high_urgency_is_urgent(self) -> None:
        c = EmailClassification(urgency=0.9)
        assert c.is_urgent() is True

    def test_low_urgency_not_urgent(self) -> None:
        c = EmailClassification(urgency=0.3)
        assert c.is_urgent() is False

    def test_threshold_boundary_not_urgent(self) -> None:
        c = EmailClassification(urgency=0.69)
        assert c.is_urgent() is False

    def test_threshold_boundary_urgent(self) -> None:
        c = EmailClassification(urgency=0.7)
        assert c.is_urgent() is True

    def test_reply_urgent_action_always_urgent(self) -> None:
        c = EmailClassification(action=EmailAction.REPLY_URGENT, urgency=0.1)
        assert c.is_urgent() is True

    def test_custom_threshold(self) -> None:
        c = EmailClassification(urgency=0.5)
        assert c.is_urgent(threshold=0.4) is True
        assert c.is_urgent(threshold=0.6) is False


# ---------------------------------------------------------------------------
# ContactSignal defaults
# ---------------------------------------------------------------------------


class TestContactSignal:
    """Test ContactSignal model defaults and validation."""

    def test_defaults(self) -> None:
        c = ContactSignal()
        assert c.name == ""
        assert c.email == ""
        assert c.role == ""
        assert c.company == ""
        assert c.relationship == "unknown"
        assert c.communication_style == ""
        assert c.sentiment == EmailSentiment.NEUTRAL
        assert c.importance_signal == 0.5

    def test_importance_clamped_to_range(self) -> None:
        with pytest.raises(ValidationError):
            ContactSignal(importance_signal=1.5)
        with pytest.raises(ValidationError):
            ContactSignal(importance_signal=-0.1)

    def test_valid_importance_boundaries(self) -> None:
        c0 = ContactSignal(importance_signal=0.0)
        assert c0.importance_signal == 0.0
        c1 = ContactSignal(importance_signal=1.0)
        assert c1.importance_signal == 1.0


# ---------------------------------------------------------------------------
# ThreadContext defaults
# ---------------------------------------------------------------------------


class TestThreadContext:
    """Test ThreadContext model defaults."""

    def test_defaults(self) -> None:
        t = ThreadContext()
        assert t.is_thread is False
        assert t.thread_position == 1
        assert t.thread_summary == ""
        assert t.urgency_trend == UrgencyTrend.STABLE

    def test_thread_position_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ThreadContext(thread_position=0)


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    """Test to_dict / from_dict round-trip."""

    def test_round_trip(self) -> None:
        data = _valid_classification_dict()
        c1 = EmailClassification.from_dict(data)
        serialised = c1.to_dict()
        c2 = EmailClassification.from_dict(serialised)

        assert c1.category == c2.category
        assert c1.action == c2.action
        assert c1.urgency == c2.urgency
        assert c1.confidence == c2.confidence
        assert c1.contact.name == c2.contact.name
        assert c1.contact.company == c2.contact.company
        assert c1.topics == c2.topics
        assert c1.thread.is_thread == c2.thread.is_thread

    def test_to_dict_returns_plain_dict(self) -> None:
        c = EmailClassification.model_validate(_valid_classification_dict())
        d = c.to_dict()
        assert isinstance(d, dict)
        assert isinstance(d["contact"], dict)
        assert isinstance(d["thread"], dict)


# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------


class TestEnumCompleteness:
    """Ensure all enum members are accounted for in mappings."""

    def test_all_actions_have_route_tag(self) -> None:
        for action in EmailAction:
            c = EmailClassification(action=action)
            tag = c.to_route_tag()
            assert isinstance(tag, str) and tag, f"No route tag for {action}"

    def test_all_actions_have_route_mode(self) -> None:
        for action in EmailAction:
            c = EmailClassification(action=action)
            mode = c.to_route_mode()
            assert isinstance(mode, str) and mode, f"No route mode for {action}"

    def test_email_category_seed_count(self) -> None:
        assert len(EmailCategory) == 15


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


class TestBuildClassificationPrompt:
    """Test the prompt builder function."""

    def test_returns_string(self) -> None:
        prompt = build_classification_prompt(
            subject="Test Subject",
            from_email="sender@example.com",
            to_emails="recipient@example.com",
            body_text="Hello, this is a test email.",
        )
        assert isinstance(prompt, str)
        assert "Test Subject" in prompt
        assert "sender@example.com" in prompt

    def test_body_truncation(self) -> None:
        long_body = "x" * 10000
        prompt = build_classification_prompt(
            subject="Test",
            from_email="a@b.com",
            to_emails="c@d.com",
            body_text=long_body,
            max_body_chars=100,
        )
        # Body should be truncated
        assert len(prompt) < len(long_body)

    def test_custom_categories_included(self) -> None:
        prompt = build_classification_prompt(
            subject="Test",
            from_email="a@b.com",
            to_emails="c@d.com",
            body_text="Hello",
            custom_categories=["my_special_cat", "another_cat"],
        )
        assert "my_special_cat" in prompt
        assert "another_cat" in prompt

    def test_all_seed_categories_in_prompt(self) -> None:
        prompt = build_classification_prompt(
            subject="Test",
            from_email="a@b.com",
            to_emails="c@d.com",
            body_text="Hello",
        )
        for cat in EmailCategory:
            assert cat.value in prompt, f"Missing category {cat.value} in prompt"

    def test_all_actions_in_prompt(self) -> None:
        prompt = build_classification_prompt(
            subject="Test",
            from_email="a@b.com",
            to_emails="c@d.com",
            body_text="Hello",
        )
        for action in EmailAction:
            assert action.value in prompt, f"Missing action {action.value} in prompt"

    def test_system_prompt_is_non_empty(self) -> None:
        assert len(SYSTEM_PROMPT) > 20
        assert "JSON" in SYSTEM_PROMPT
