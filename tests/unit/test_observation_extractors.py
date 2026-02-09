"""Unit tests for the observation extraction engine.

Covers Tier 1 (regex), Tier 2 (Ollama LLM), Tier 3 (Cloud LLM),
needs_escalation, and merge_extractions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.observation.extractors import (
    ESCALATION_HIGH,
    ESCALATION_LOW,
    MIN_CONTENT_LENGTH_FOR_LLM,
    extract_tier1,
    extract_tier2,
    extract_tier3,
    merge_extractions,
    needs_escalation,
)
from zetherion_ai.observation.models import (
    TIER_CLOUD,
    TIER_OLLAMA,
    TIER_REGEX,
    ExtractedItem,
    ItemType,
    ObservationEvent,
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_event(content: str, **kwargs) -> ObservationEvent:
    return ObservationEvent(
        source="test",
        source_id="msg-1",
        user_id=12345,
        author="test_user",
        author_is_owner=True,
        content=content,
        **kwargs,
    )


def _make_item(
    item_type: ItemType = ItemType.TASK,
    content: str = "some task",
    confidence: float = 0.7,
    extraction_tier: int = TIER_REGEX,
    event: ObservationEvent | None = None,
) -> ExtractedItem:
    return ExtractedItem(
        item_type=item_type,
        content=content,
        confidence=confidence,
        metadata={},
        source_event=event,
        extraction_tier=extraction_tier,
    )


def _mock_provider(
    return_value: list[dict[str, Any]] | None = None,
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock LLMProvider with an async extract method."""
    provider = AsyncMock()
    if side_effect is not None:
        provider.extract = AsyncMock(side_effect=side_effect)
    else:
        provider.extract = AsyncMock(return_value=return_value or [])
    return provider


# ===================================================================
# Tier 1 -- Regex extraction
# ===================================================================


class TestExtractTier1Tasks:
    """Tier 1 task/commitment pattern extraction."""

    def test_ill_handle_pattern(self):
        event = _make_event("I'll handle the deployment by Friday")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].extraction_tier == TIER_REGEX

    def test_i_will_send_pattern(self):
        event = _make_event("I will send the report")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert "send" in task_items[0].content.lower()

    def test_todo_colon_pattern(self):
        event = _make_event("TODO: Fix the login bug")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].confidence == 0.85
        assert "Fix the login bug" in task_items[0].content

    def test_task_colon_pattern(self):
        event = _make_event("TASK: Update dependencies")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].confidence == 0.85
        assert "Update dependencies" in task_items[0].content

    def test_action_colon_pattern(self):
        event = _make_event("ACTION: Deploy to staging")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].confidence == 0.85

    def test_need_to_review_pattern(self):
        event = _make_event("I need to review the PR")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1

    def test_should_update_pattern(self):
        event = _make_event("I should update the docs")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1

    def test_no_task_signal(self):
        event = _make_event("The weather is nice today.")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert task_items == []

    def test_task_with_date_gets_higher_confidence(self):
        """A task paired with a deadline raises confidence."""
        event = _make_event("I'll handle the deployment by tomorrow")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].confidence == 0.75

    def test_task_without_date_has_base_confidence(self):
        event = _make_event("I will review the proposal")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1
        assert task_items[0].confidence == 0.55

    def test_only_one_task_per_message(self):
        """Tier 1 yields at most one task per message."""
        event = _make_event("TODO: Fix login bug. I will send the report.")
        items = extract_tier1(event)
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) == 1


class TestExtractTier1Deadlines:
    """Tier 1 deadline/date pattern extraction."""

    def test_by_tomorrow(self):
        event = _make_event("Finish the spec by tomorrow")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert len(deadline_items) == 1
        assert "tomorrow" in deadline_items[0].content.lower()

    def test_by_next_monday(self):
        event = _make_event("Ship the patch by next Monday")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert len(deadline_items) == 1
        assert "monday" in deadline_items[0].content.lower()

    def test_due_iso_date(self):
        event = _make_event("This feature is due 2026-03-15")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert len(deadline_items) == 1
        assert "2026-03-15" in deadline_items[0].content

    def test_before_friday(self):
        event = _make_event("We must deploy before Friday")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert len(deadline_items) == 1
        assert "friday" in deadline_items[0].content.lower()

    def test_deadline_confidence(self):
        event = _make_event("Please finish by tomorrow")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert len(deadline_items) == 1
        assert deadline_items[0].confidence == 0.6

    def test_task_plus_deadline_produces_both(self):
        event = _make_event("I'll handle the deployment by next Monday")
        items = extract_tier1(event)
        types = {i.item_type for i in items}
        assert ItemType.TASK in types
        assert ItemType.DEADLINE in types

        task = next(i for i in items if i.item_type == ItemType.TASK)
        assert task.confidence == 0.75  # boosted by deadline

    def test_no_deadline_signal(self):
        event = _make_event("Just chatting about Python.")
        items = extract_tier1(event)
        deadline_items = [i for i in items if i.item_type == ItemType.DEADLINE]
        assert deadline_items == []


class TestExtractTier1Meetings:
    """Tier 1 meeting pattern extraction."""

    def test_lets_meet_tomorrow(self):
        event = _make_event("Let's meet tomorrow at 3pm")
        items = extract_tier1(event)
        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert len(meeting_items) == 1

    def test_schedule_a_meeting(self):
        event = _make_event("Can you schedule a meeting for next week?")
        items = extract_tier1(event)
        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert len(meeting_items) == 1

    def test_lets_sync_up(self):
        event = _make_event("Let's sync up on the project status")
        items = extract_tier1(event)
        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert len(meeting_items) == 1

    def test_meeting_confidence(self):
        event = _make_event("Let's meet tomorrow")
        items = extract_tier1(event)
        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert len(meeting_items) == 1
        assert meeting_items[0].confidence == 0.55

    def test_no_meeting_signal(self):
        event = _make_event("I like coffee in the morning.")
        items = extract_tier1(event)
        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert meeting_items == []


class TestExtractTier1Contacts:
    """Tier 1 email/contact pattern extraction."""

    def test_single_email(self):
        event = _make_event("Send the file to john@example.com please")
        items = extract_tier1(event)
        contact_items = [i for i in items if i.item_type == ItemType.CONTACT]
        assert len(contact_items) == 1
        assert contact_items[0].metadata["email"] == ("john@example.com")

    def test_multiple_emails(self):
        event = _make_event("CC alice@example.com and bob@corp.io on the thread")
        items = extract_tier1(event)
        contact_items = [i for i in items if i.item_type == ItemType.CONTACT]
        assert len(contact_items) == 2
        emails = {c.metadata["email"] for c in contact_items}
        assert emails == {"alice@example.com", "bob@corp.io"}

    def test_email_confidence_is_high(self):
        event = _make_event("Reach out to user@test.org")
        items = extract_tier1(event)
        contact_items = [i for i in items if i.item_type == ItemType.CONTACT]
        assert len(contact_items) == 1
        assert contact_items[0].confidence == 0.9

    def test_no_email(self):
        event = _make_event("No emails here at all.")
        items = extract_tier1(event)
        contact_items = [i for i in items if i.item_type == ItemType.CONTACT]
        assert contact_items == []


class TestExtractTier1Reminders:
    """Tier 1 reminder pattern extraction."""

    def test_remind_me(self):
        event = _make_event("Remind me to call Sarah")
        items = extract_tier1(event)
        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert len(reminder_items) == 1

    def test_dont_forget(self):
        event = _make_event("Don't forget the deadline")
        items = extract_tier1(event)
        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert len(reminder_items) == 1

    def test_remember_to(self):
        event = _make_event("Remember to submit the form")
        items = extract_tier1(event)
        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert len(reminder_items) == 1

    def test_reminder_confidence(self):
        event = _make_event("Remind me to buy groceries")
        items = extract_tier1(event)
        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert len(reminder_items) == 1
        assert reminder_items[0].confidence == 0.7

    def test_no_reminder_signal(self):
        event = _make_event("Here are the quarterly results.")
        items = extract_tier1(event)
        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert reminder_items == []


# ===================================================================
# Tier 2 -- Local LLM extraction
# ===================================================================


class TestExtractTier2:
    """Tier 2 (Ollama) LLM-based extraction."""

    @pytest.mark.asyncio
    async def test_valid_items_returned(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "task",
                    "content": "Deploy backend",
                    "confidence": 0.8,
                    "metadata": {"source": "llm"},
                }
            ]
        )
        event = _make_event("We should deploy the backend this sprint")
        items = await extract_tier2(event, provider)

        assert len(items) == 1
        assert items[0].item_type == ItemType.TASK
        assert items[0].content == "Deploy backend"
        assert items[0].confidence == 0.8
        assert items[0].extraction_tier == TIER_OLLAMA

    @pytest.mark.asyncio
    async def test_unknown_item_type_falls_back_to_fact(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "banana",
                    "content": "Something odd",
                    "confidence": 0.6,
                }
            ]
        )
        event = _make_event("This is a long enough sentence for LLM.")
        items = await extract_tier2(event, provider)

        assert len(items) == 1
        assert items[0].item_type == ItemType.FACT

    @pytest.mark.asyncio
    async def test_low_confidence_filtered_out(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "task",
                    "content": "Maybe do this",
                    "confidence": 0.1,
                }
            ]
        )
        event = _make_event("Not very sure about this particular item")
        items = await extract_tier2(event, provider)

        assert items == []

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_kept(self):
        """Items exactly at ESCALATION_LOW are kept."""
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "task",
                    "content": "Borderline task",
                    "confidence": ESCALATION_LOW,
                }
            ]
        )
        event = _make_event("Borderline signal that might be a task")
        items = await extract_tier2(event, provider)

        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_provider_exception_returns_empty(self):
        provider = _mock_provider(side_effect=RuntimeError("LLM crashed"))
        event = _make_event("Some content that is long enough for LLM.")
        items = await extract_tier2(event, provider)

        assert items == []

    @pytest.mark.asyncio
    async def test_short_content_skips_provider(self):
        provider = _mock_provider(
            return_value=[{"item_type": "task", "content": "x", "confidence": 0.9}]
        )
        short = "x" * (MIN_CONTENT_LENGTH_FOR_LLM - 1)
        event = _make_event(short)
        items = await extract_tier2(event, provider)

        assert items == []
        provider.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_conversation_history_forwarded(self):
        provider = _mock_provider(return_value=[])
        event = _make_event(
            "We should deploy the backend this sprint",
            conversation_history=["prior msg 1", "prior msg 2"],
        )
        await extract_tier2(event, provider)

        provider.extract.assert_called_once()
        call_kwargs = provider.extract.call_args
        assert call_kwargs.kwargs["conversation_history"] == [
            "prior msg 1",
            "prior msg 2",
        ]

    @pytest.mark.asyncio
    async def test_empty_conversation_history_sends_none(self):
        provider = _mock_provider(return_value=[])
        event = _make_event("A long enough sentence for LLM extraction.")
        # event.conversation_history defaults to []
        await extract_tier2(event, provider)

        call_kwargs = provider.extract.call_args
        assert call_kwargs.kwargs["conversation_history"] is None

    @pytest.mark.asyncio
    async def test_missing_content_key_uses_fallback(self):
        """If raw item has no 'content', first 100 chars used."""
        long_text = "A" * 150
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "fact",
                    "confidence": 0.7,
                }
            ]
        )
        event = _make_event(long_text)
        items = await extract_tier2(event, provider)

        assert len(items) == 1
        assert items[0].content == long_text[:100]


# ===================================================================
# Tier 3 -- Cloud LLM extraction
# ===================================================================


class TestExtractTier3:
    """Tier 3 (Claude/Cloud) LLM-based extraction."""

    @pytest.mark.asyncio
    async def test_valid_items_returned(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "commitment",
                    "content": "Promised to deliver",
                    "confidence": 0.9,
                    "metadata": {"parties": ["Alice"]},
                }
            ]
        )
        event = _make_event("I promised Alice I would deliver the spec")
        items = await extract_tier3(event, provider)

        assert len(items) == 1
        assert items[0].item_type == ItemType.COMMITMENT
        assert items[0].extraction_tier == TIER_CLOUD

    @pytest.mark.asyncio
    async def test_unknown_type_falls_back_to_fact(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "unknown_type_xyz",
                    "content": "Mystery item",
                    "confidence": 0.7,
                }
            ]
        )
        event = _make_event("Something the cloud LLM returned oddly")
        items = await extract_tier3(event, provider)

        assert len(items) == 1
        assert items[0].item_type == ItemType.FACT

    @pytest.mark.asyncio
    async def test_low_confidence_filtered(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "task",
                    "content": "Weak signal",
                    "confidence": 0.05,
                }
            ]
        )
        event = _make_event("Probably not a real task at all really")
        items = await extract_tier3(event, provider)
        assert items == []

    @pytest.mark.asyncio
    async def test_provider_exception_returns_empty(self):
        provider = _mock_provider(side_effect=ValueError("API error"))
        event = _make_event("Content that triggers an API failure.")
        items = await extract_tier3(event, provider)
        assert items == []

    @pytest.mark.asyncio
    async def test_short_content_skips_provider(self):
        provider = _mock_provider(
            return_value=[{"item_type": "task", "content": "x", "confidence": 0.9}]
        )
        short = "y" * (MIN_CONTENT_LENGTH_FOR_LLM - 1)
        event = _make_event(short)
        items = await extract_tier3(event, provider)

        assert items == []
        provider.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_items_returned(self):
        provider = _mock_provider(
            return_value=[
                {
                    "item_type": "task",
                    "content": "Task A",
                    "confidence": 0.8,
                },
                {
                    "item_type": "deadline",
                    "content": "By Friday",
                    "confidence": 0.7,
                },
                {
                    "item_type": "contact",
                    "content": "Email ref",
                    "confidence": 0.6,
                    "metadata": {"email": "a@b.com"},
                },
            ]
        )
        event = _make_event("Finish task A by Friday and email a@b.com")
        items = await extract_tier3(event, provider)

        assert len(items) == 3
        types = {i.item_type for i in items}
        assert types == {
            ItemType.TASK,
            ItemType.DEADLINE,
            ItemType.CONTACT,
        }
        for item in items:
            assert item.extraction_tier == TIER_CLOUD


# ===================================================================
# needs_escalation
# ===================================================================


class TestNeedsEscalation:
    """Tests for the escalation decision function."""

    def test_all_high_confidence_no_escalation(self):
        items = [
            _make_item(confidence=0.8),
            _make_item(confidence=0.9),
            _make_item(confidence=0.7),
        ]
        assert needs_escalation(items) is False

    def test_exactly_at_high_threshold_no_escalation(self):
        items = [
            _make_item(confidence=ESCALATION_HIGH),
        ]
        assert needs_escalation(items) is False

    def test_uncertain_range_triggers_escalation(self):
        items = [
            _make_item(confidence=0.8),
            _make_item(confidence=0.45),
        ]
        assert needs_escalation(items) is True

    def test_at_low_boundary_triggers_escalation(self):
        items = [
            _make_item(confidence=ESCALATION_LOW),
        ]
        assert needs_escalation(items) is True

    def test_just_below_high_triggers_escalation(self):
        items = [
            _make_item(confidence=ESCALATION_HIGH - 0.01),
        ]
        assert needs_escalation(items) is True

    def test_below_low_threshold_no_escalation(self):
        items = [
            _make_item(confidence=0.1),
            _make_item(confidence=0.2),
        ]
        assert needs_escalation(items) is False

    def test_empty_items_no_escalation(self):
        assert needs_escalation([]) is False

    def test_mixed_confident_and_uncertain(self):
        items = [
            _make_item(confidence=0.9),
            _make_item(confidence=0.5),
            _make_item(confidence=0.85),
        ]
        assert needs_escalation(items) is True


# ===================================================================
# merge_extractions
# ===================================================================


class TestMergeExtractions:
    """Tests for multi-tier extraction merging."""

    def test_tier1_only(self):
        tier1 = [
            _make_item(
                content="Task from regex",
                extraction_tier=TIER_REGEX,
            ),
        ]
        result = merge_extractions(tier1, [])
        assert len(result) == 1
        assert result[0].content == "Task from regex"

    def test_higher_tier_overrides_same_content(self):
        event = _make_event("I'll handle the deployment")
        tier1 = [
            _make_item(
                content="I'll handle the deployment",
                extraction_tier=TIER_REGEX,
                event=event,
            ),
        ]
        tier2 = [
            _make_item(
                content="I'll handle the deployment",
                extraction_tier=TIER_OLLAMA,
                event=event,
            ),
        ]
        result = merge_extractions(tier1, tier2)

        # The higher-tier version should win
        assert len(result) == 1
        assert result[0].extraction_tier == TIER_OLLAMA

    def test_tier3_overrides_tier2_and_tier1(self):
        tier1 = [
            _make_item(
                content="Same task content here",
                extraction_tier=TIER_REGEX,
            ),
        ]
        tier2 = [
            _make_item(
                content="Same task content here",
                extraction_tier=TIER_OLLAMA,
            ),
        ]
        tier3 = [
            _make_item(
                content="Same task content here",
                extraction_tier=TIER_CLOUD,
            ),
        ]
        result = merge_extractions(tier1, tier2, tier3)

        assert len(result) == 1
        assert result[0].extraction_tier == TIER_CLOUD

    def test_different_types_all_kept(self):
        tier1 = [
            _make_item(
                item_type=ItemType.TASK,
                content="A task from tier 1",
                extraction_tier=TIER_REGEX,
            ),
        ]
        tier2 = [
            _make_item(
                item_type=ItemType.MEETING,
                content="A meeting from tier 2",
                extraction_tier=TIER_OLLAMA,
            ),
        ]
        tier3 = [
            _make_item(
                item_type=ItemType.CONTACT,
                content="A contact from tier 3",
                extraction_tier=TIER_CLOUD,
            ),
        ]
        result = merge_extractions(tier1, tier2, tier3)

        types = {i.item_type for i in result}
        assert types == {
            ItemType.TASK,
            ItemType.MEETING,
            ItemType.CONTACT,
        }

    def test_empty_inputs_produce_empty_output(self):
        result = merge_extractions([], [], [])
        assert result == []

    def test_empty_tier3_none_accepted(self):
        tier1 = [
            _make_item(content="Only tier 1"),
        ]
        result = merge_extractions(tier1, [], None)
        assert len(result) == 1

    def test_different_content_same_type_both_kept(self):
        tier1 = [
            _make_item(
                item_type=ItemType.TASK,
                content="First task to complete",
                extraction_tier=TIER_REGEX,
            ),
        ]
        tier2 = [
            _make_item(
                item_type=ItemType.TASK,
                content="Second completely different task",
                extraction_tier=TIER_OLLAMA,
            ),
        ]
        result = merge_extractions(tier1, tier2)

        # Different content means different keys, both should appear
        assert len(result) == 2

    def test_preserves_metadata_of_winning_item(self):
        tier1 = [
            _make_item(
                content="Deploy the app to prod",
                extraction_tier=TIER_REGEX,
            ),
        ]
        tier1[0].metadata = {"raw_match": "regex-match"}

        tier2 = [
            _make_item(
                content="Deploy the app to prod",
                extraction_tier=TIER_OLLAMA,
            ),
        ]
        tier2[0].metadata = {"llm_source": "ollama"}

        result = merge_extractions(tier1, tier2)
        assert len(result) == 1
        assert result[0].metadata == {"llm_source": "ollama"}
