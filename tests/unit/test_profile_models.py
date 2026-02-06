"""Unit tests for the profile models."""

from datetime import datetime
from uuid import UUID

import pytest

from secureclaw.profile.models import (
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_FLAG_CONFIRM,
    CONFIDENCE_LOG_ONLY,
    CONFIDENCE_QUEUE_CONFIRM,
    ProfileCategory,
    ProfileEntry,
    ProfileSource,
    ProfileUpdate,
)


class TestProfileCategory:
    """Tests for ProfileCategory enum."""

    def test_all_categories_exist(self):
        """Test all expected categories are defined."""
        expected = [
            "IDENTITY",
            "PREFERENCES",
            "SCHEDULE",
            "PROJECTS",
            "RELATIONSHIPS",
            "SKILLS",
            "GOALS",
            "HABITS",
        ]
        for name in expected:
            assert hasattr(ProfileCategory, name)

    def test_category_values(self):
        """Test category values are lowercase strings."""
        assert ProfileCategory.IDENTITY.value == "identity"
        assert ProfileCategory.PREFERENCES.value == "preferences"
        assert ProfileCategory.SCHEDULE.value == "schedule"


class TestProfileSource:
    """Tests for ProfileSource enum."""

    def test_all_sources_exist(self):
        """Test all expected sources are defined."""
        expected = ["EXPLICIT", "CONVERSATION", "INFERRED", "CONFIRMED"]
        for name in expected:
            assert hasattr(ProfileSource, name)


class TestProfileEntry:
    """Tests for ProfileEntry dataclass."""

    def test_create_entry(self):
        """Test creating a profile entry."""
        entry = ProfileEntry.create(
            user_id="user123",
            category=ProfileCategory.IDENTITY,
            key="name",
            value="John",
            confidence=0.9,
            source=ProfileSource.EXPLICIT,
        )

        assert entry.user_id == "user123"
        assert entry.category == ProfileCategory.IDENTITY
        assert entry.key == "name"
        assert entry.value == "John"
        assert entry.confidence == 0.9
        assert entry.source == ProfileSource.EXPLICIT
        assert isinstance(entry.id, UUID)
        assert isinstance(entry.created_at, datetime)
        assert isinstance(entry.last_confirmed, datetime)

    def test_entry_validation_confidence(self):
        """Test validation of confidence range."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=1.5,  # Invalid
                source=ProfileSource.EXPLICIT,
            )

        with pytest.raises(ValueError, match="Confidence must be between"):
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=-0.1,  # Invalid
                source=ProfileSource.EXPLICIT,
            )

    def test_entry_validation_decay_rate(self):
        """Test validation of decay rate range."""
        with pytest.raises(ValueError, match="Decay rate must be between"):
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=0.9,
                source=ProfileSource.EXPLICIT,
                decay_rate=1.5,  # Invalid
            )

    def test_apply_decay(self):
        """Test confidence decay calculation."""
        entry = ProfileEntry.create(
            user_id="user123",
            category=ProfileCategory.PREFERENCES,
            key="theme",
            value="dark",
            confidence=0.9,
            source=ProfileSource.INFERRED,
            decay_rate=0.1,
        )

        # After 3 days at 0.1 decay rate: 0.9 - (0.1 * 3) = 0.6
        assert entry.apply_decay(3.0) == pytest.approx(0.6)

        # After 10 days at 0.1 decay rate: 0.9 - (0.1 * 10) = -0.1 -> clamped to 0
        assert entry.apply_decay(10.0) == 0.0

    def test_get_current_confidence(self):
        """Test getting current confidence with decay."""
        entry = ProfileEntry.create(
            user_id="user123",
            category=ProfileCategory.PREFERENCES,
            key="theme",
            value="dark",
            confidence=0.9,
            source=ProfileSource.INFERRED,
            decay_rate=0.01,
        )

        # Just created, should be close to original
        assert entry.get_current_confidence() >= 0.89

    def test_needs_confirmation(self):
        """Test needs confirmation check."""
        entry = ProfileEntry.create(
            user_id="user123",
            category=ProfileCategory.PREFERENCES,
            key="theme",
            value="dark",
            confidence=0.15,  # Below default threshold of 0.2
            source=ProfileSource.INFERRED,
            decay_rate=0.0,  # No decay for this test
        )

        assert entry.needs_confirmation() is True
        assert entry.needs_confirmation(threshold=0.1) is False

    def test_to_dict_and_from_dict(self):
        """Test serialization roundtrip."""
        entry = ProfileEntry.create(
            user_id="user123",
            category=ProfileCategory.IDENTITY,
            key="timezone",
            value="America/New_York",
            confidence=0.95,
            source=ProfileSource.EXPLICIT,
        )

        data = entry.to_dict()
        restored = ProfileEntry.from_dict(data)

        assert restored.user_id == entry.user_id
        assert restored.category == entry.category
        assert restored.key == entry.key
        assert restored.value == entry.value
        assert restored.confidence == entry.confidence
        assert restored.source == entry.source


class TestProfileUpdate:
    """Tests for ProfileUpdate dataclass."""

    def test_create_update(self):
        """Test creating a profile update."""
        update = ProfileUpdate(
            profile="user",
            field_name="timezone",
            value="UTC",
            confidence=0.9,
            source_tier=1,
        )

        assert update.profile == "user"
        assert update.field_name == "timezone"
        assert update.value == "UTC"
        assert update.confidence == 0.9
        assert update.source_tier == 1
        assert update.action == "set"
        assert update.requires_confirmation is False

    def test_update_validation_confidence(self):
        """Test validation of confidence range."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ProfileUpdate(
                profile="user",
                field_name="name",
                value="John",
                confidence=1.5,
            )

    def test_update_validation_source_tier(self):
        """Test validation of source tier range."""
        with pytest.raises(ValueError, match="Source tier must be 1-4"):
            ProfileUpdate(
                profile="user",
                field_name="name",
                value="John",
                source_tier=5,
            )

    def test_should_apply(self):
        """Test should_apply based on confidence threshold."""
        high_conf = ProfileUpdate(
            profile="user",
            field_name="name",
            value="John",
            confidence=0.9,
        )
        assert high_conf.should_apply(threshold=0.6) is True

        low_conf = ProfileUpdate(
            profile="user",
            field_name="name",
            value="John",
            confidence=0.5,
        )
        assert low_conf.should_apply(threshold=0.6) is False

        requires_confirm = ProfileUpdate(
            profile="user",
            field_name="name",
            value="John",
            confidence=0.9,
            requires_confirmation=True,
        )
        assert requires_confirm.should_apply(threshold=0.6) is False

    def test_to_confirmation_prompt(self):
        """Test generating confirmation prompts."""
        user_update = ProfileUpdate(
            profile="user",
            field_name="timezone",
            action="set",
            value="UTC",
        )
        prompt = user_update.to_confirmation_prompt()
        assert "timezone" in prompt
        assert "Is that right?" in prompt

        employment_update = ProfileUpdate(
            profile="employment",
            field_name="verbosity",
            action="increase",
        )
        prompt = employment_update.to_confirmation_prompt()
        assert "verbosity" in prompt
        assert "Should I" in prompt

    def test_describe_change_actions(self):
        """Test action descriptions."""
        actions = ["set", "increase", "decrease", "append", "increment"]
        for action in actions:
            update = ProfileUpdate(
                profile="user",
                field_name="test",
                action=action,  # type: ignore[arg-type]
                value="value",
            )
            desc = update._describe_change()
            assert "test" in desc

    def test_to_dict_and_from_dict(self):
        """Test serialization roundtrip."""
        update = ProfileUpdate(
            profile="user",
            field_name="role",
            action="set",
            value="developer",
            confidence=0.85,
            source_tier=2,
            category=ProfileCategory.IDENTITY,
            reason="Inferred from message",
        )

        data = update.to_dict()
        restored = ProfileUpdate.from_dict(data)

        assert restored.profile == update.profile
        assert restored.field_name == update.field_name
        assert restored.value == update.value
        assert restored.confidence == update.confidence
        assert restored.source_tier == update.source_tier
        assert restored.category == update.category


class TestConfidenceThresholds:
    """Tests for confidence threshold constants."""

    def test_thresholds_in_order(self):
        """Test thresholds are in descending order."""
        assert CONFIDENCE_AUTO_APPLY > CONFIDENCE_LOG_ONLY
        assert CONFIDENCE_LOG_ONLY > CONFIDENCE_FLAG_CONFIRM
        assert CONFIDENCE_FLAG_CONFIRM > CONFIDENCE_QUEUE_CONFIRM

    def test_threshold_values(self):
        """Test threshold values are reasonable."""
        assert CONFIDENCE_AUTO_APPLY == 0.9
        assert CONFIDENCE_LOG_ONLY == 0.7
        assert CONFIDENCE_FLAG_CONFIRM == 0.5
        assert CONFIDENCE_QUEUE_CONFIRM == 0.3
