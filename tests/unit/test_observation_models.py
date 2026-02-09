"""Unit tests for the observation models."""

from datetime import datetime

import pytest

from zetherion_ai.observation.models import (
    TIER_CLOUD,
    TIER_OLLAMA,
    TIER_REGEX,
    ExtractedItem,
    ItemType,
    ObservationEvent,
)


class TestItemType:
    """Tests for ItemType enum."""

    def test_all_item_types_exist(self):
        """Test all expected item types are defined."""
        expected = [
            "TASK",
            "DEADLINE",
            "COMMITMENT",
            "CONTACT",
            "FACT",
            "MEETING",
            "REMINDER",
            "ACTION_ITEM",
        ]
        for name in expected:
            assert hasattr(ItemType, name)

    def test_item_type_values(self):
        """Test item type values are lowercase strings."""
        assert ItemType.TASK.value == "task"
        assert ItemType.DEADLINE.value == "deadline"
        assert ItemType.COMMITMENT.value == "commitment"
        assert ItemType.CONTACT.value == "contact"
        assert ItemType.FACT.value == "fact"
        assert ItemType.MEETING.value == "meeting"
        assert ItemType.REMINDER.value == "reminder"
        assert ItemType.ACTION_ITEM.value == "action_item"

    def test_item_type_is_str_enum(self):
        """Test ItemType inherits from StrEnum."""
        # StrEnum values should be strings
        for item_type in ItemType:
            assert isinstance(item_type.value, str)


class TestTierConstants:
    """Tests for extraction tier constants."""

    def test_tier_values(self):
        """Test tier constants have expected values."""
        assert TIER_REGEX == 1
        assert TIER_OLLAMA == 2
        assert TIER_CLOUD == 3

    def test_tier_order(self):
        """Test tiers are in ascending cost/complexity order."""
        assert TIER_REGEX < TIER_OLLAMA < TIER_CLOUD


class TestObservationEvent:
    """Tests for ObservationEvent dataclass."""

    def test_create_with_all_fields(self):
        """Test creating an observation event with all fields."""
        timestamp = datetime(2024, 1, 15, 10, 30)
        context = {"channel": "general", "server": "test"}
        history = ["previous message 1", "previous message 2"]

        event = ObservationEvent(
            source="discord",
            source_id="msg123",
            user_id=999,
            author="testuser",
            author_is_owner=True,
            content="This is a test message",
            timestamp=timestamp,
            context=context,
            conversation_history=history,
        )

        assert event.source == "discord"
        assert event.source_id == "msg123"
        assert event.user_id == 999
        assert event.author == "testuser"
        assert event.author_is_owner is True
        assert event.content == "This is a test message"
        assert event.timestamp == timestamp
        assert event.context == context
        assert event.conversation_history == history

    def test_create_with_defaults(self):
        """Test creating event with default values."""
        event = ObservationEvent(
            source="gmail",
            source_id="email456",
            user_id=888,
            author="sender@example.com",
            author_is_owner=False,
            content="Email content here",
        )

        # Should have defaults
        assert isinstance(event.timestamp, datetime)
        assert event.context == {}
        assert event.conversation_history == []

    def test_validation_empty_source(self):
        """Test validation rejects empty source."""
        with pytest.raises(ValueError, match="source must not be empty"):
            ObservationEvent(
                source="",
                source_id="id123",
                user_id=123,
                author="user",
                author_is_owner=True,
                content="content",
            )

    def test_validation_empty_source_id(self):
        """Test validation rejects empty source_id."""
        with pytest.raises(ValueError, match="source_id must not be empty"):
            ObservationEvent(
                source="slack",
                source_id="",
                user_id=123,
                author="user",
                author_is_owner=False,
                content="content",
            )

    def test_validation_empty_content(self):
        """Test validation rejects empty content."""
        with pytest.raises(ValueError, match="content must not be empty"):
            ObservationEvent(
                source="calendar",
                source_id="event789",
                user_id=123,
                author="calendar_bot",
                author_is_owner=True,
                content="",
            )


class TestExtractedItem:
    """Tests for ExtractedItem dataclass."""

    def test_create_with_all_fields(self):
        """Test creating an extracted item with all fields."""
        event = ObservationEvent(
            source="discord",
            source_id="msg123",
            user_id=999,
            author="testuser",
            author_is_owner=True,
            content="Remember to call mom tomorrow",
        )
        metadata = {"priority": "high", "category": "personal"}

        item = ExtractedItem(
            item_type=ItemType.REMINDER,
            content="Call mom tomorrow",
            confidence=0.85,
            metadata=metadata,
            source_event=event,
            extraction_tier=TIER_OLLAMA,
        )

        assert item.item_type == ItemType.REMINDER
        assert item.content == "Call mom tomorrow"
        assert item.confidence == 0.85
        assert item.metadata == metadata
        assert item.source_event == event
        assert item.extraction_tier == TIER_OLLAMA

    def test_create_with_defaults(self):
        """Test creating item with default values."""
        item = ExtractedItem(
            item_type=ItemType.TASK,
            content="Complete project",
            confidence=0.9,
        )

        # Should have defaults
        assert item.metadata == {}
        assert item.source_event is None
        assert item.extraction_tier == TIER_REGEX

    def test_validation_empty_content(self):
        """Test validation rejects empty content."""
        with pytest.raises(ValueError, match="content must not be empty"):
            ExtractedItem(
                item_type=ItemType.TASK,
                content="",
                confidence=0.9,
            )

    def test_validation_confidence_negative(self):
        """Test validation rejects negative confidence."""
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0, got -0.1"):
            ExtractedItem(
                item_type=ItemType.DEADLINE,
                content="Submit report by Friday",
                confidence=-0.1,
            )

    def test_validation_confidence_too_high(self):
        """Test validation rejects confidence greater than 1.0."""
        with pytest.raises(ValueError, match="confidence must be 0.0-1.0, got 1.5"):
            ExtractedItem(
                item_type=ItemType.COMMITMENT,
                content="I will attend the meeting",
                confidence=1.5,
            )

    def test_validation_confidence_boundary_zero(self):
        """Test confidence 0.0 is valid (boundary value)."""
        item = ExtractedItem(
            item_type=ItemType.FACT,
            content="Sky is blue",
            confidence=0.0,
        )
        assert item.confidence == 0.0

    def test_validation_confidence_boundary_one(self):
        """Test confidence 1.0 is valid (boundary value)."""
        item = ExtractedItem(
            item_type=ItemType.CONTACT,
            content="John Doe - john@example.com",
            confidence=1.0,
        )
        assert item.confidence == 1.0

    def test_validation_invalid_extraction_tier_zero(self):
        """Test validation rejects tier 0."""
        with pytest.raises(ValueError, match="extraction_tier must be 1, 2, or 3, got 0"):
            ExtractedItem(
                item_type=ItemType.MEETING,
                content="Team standup at 10am",
                confidence=0.8,
                extraction_tier=0,
            )

    def test_validation_invalid_extraction_tier_four(self):
        """Test validation rejects tier 4."""
        with pytest.raises(ValueError, match="extraction_tier must be 1, 2, or 3, got 4"):
            ExtractedItem(
                item_type=ItemType.ACTION_ITEM,
                content="Review pull request",
                confidence=0.7,
                extraction_tier=4,
            )

    def test_validation_valid_tier_regex(self):
        """Test TIER_REGEX (1) is valid."""
        item = ExtractedItem(
            item_type=ItemType.TASK,
            content="Buy groceries",
            confidence=0.6,
            extraction_tier=TIER_REGEX,
        )
        assert item.extraction_tier == TIER_REGEX

    def test_validation_valid_tier_ollama(self):
        """Test TIER_OLLAMA (2) is valid."""
        item = ExtractedItem(
            item_type=ItemType.DEADLINE,
            content="Project due next week",
            confidence=0.75,
            extraction_tier=TIER_OLLAMA,
        )
        assert item.extraction_tier == TIER_OLLAMA

    def test_validation_valid_tier_cloud(self):
        """Test TIER_CLOUD (3) is valid."""
        item = ExtractedItem(
            item_type=ItemType.COMMITMENT,
            content="Promised to help with documentation",
            confidence=0.95,
            extraction_tier=TIER_CLOUD,
        )
        assert item.extraction_tier == TIER_CLOUD
