"""Unit tests for the profile builder."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.profile.builder import (
    ProfileBuilder,
    extract_profile_updates_background,
    schedule_profile_extraction,
)
from zetherion_ai.profile.cache import ProfileCache
from zetherion_ai.profile.models import (
    ProfileUpdate,
)
from zetherion_ai.profile.storage import ProfileStorage


class TestProfileBuilder:
    """Tests for ProfileBuilder."""

    @pytest.fixture
    def mock_storage(self, tmp_path):
        """Create a mock storage instance."""
        db_path = tmp_path / "test_profiles.db"
        return ProfileStorage(db_path=str(db_path))

    @pytest.fixture
    def mock_cache(self):
        """Create a mock cache instance."""
        return ProfileCache()

    @pytest.fixture
    def builder(self, mock_storage, mock_cache):
        """Create a profile builder with mocked dependencies."""
        return ProfileBuilder(
            memory=None,
            inference_broker=None,
            storage=mock_storage,
            cache=mock_cache,
            tier1_only=True,
        )

    @pytest.mark.asyncio
    async def test_process_message_extracts_updates(self, builder):
        """Test that process_message extracts profile updates."""
        updates = await builder.process_message(
            user_id="user123",
            message="Call me John, I'm a software developer",
        )

        assert len(updates) >= 1
        fields = [u.field_name for u in updates]
        assert "name" in fields or "role" in fields

    @pytest.mark.asyncio
    async def test_process_message_records_tier_usage(self, builder, mock_storage):
        """Test that tier usage is recorded."""
        await builder.process_message(
            user_id="user123",
            message="This is urgent! Need help ASAP",
        )

        usage = mock_storage.get_tier_usage(days=1)
        assert 1 in usage  # Tier 1 should be used

    @pytest.mark.asyncio
    async def test_process_message_handles_empty_updates(self, builder):
        """Test handling messages with no profile updates."""
        updates = await builder.process_message(
            user_id="user123",
            message="Hello there",
        )

        # May have some updates or none
        assert isinstance(updates, list)

    @pytest.mark.asyncio
    async def test_process_message_with_response_time(self, builder):
        """Test processing with response time metadata."""
        updates = await builder.process_message(
            user_id="user123",
            message="Yes!",
            response_time_ms=1000,  # Quick response
        )

        # Should detect engagement
        engagement_updates = [u for u in updates if u.field_name == "engagement_level"]
        assert len(engagement_updates) == 1

    def test_compute_new_value_set(self, builder):
        """Test computing new value for 'set' action."""
        update = ProfileUpdate(
            profile="user",
            field_name="timezone",
            action="set",
            value="EST",
        )

        new_value = builder._compute_new_value(None, update)
        assert new_value == "EST"

    def test_compute_new_value_increase(self, builder):
        """Test computing new value for 'increase' action."""
        update = ProfileUpdate(
            profile="employment",
            field_name="verbosity",
            action="increase",
            value=0.2,
        )

        # From 0.5, increase by 0.2
        new_value = builder._compute_new_value(0.5, update)
        assert new_value == pytest.approx(0.7)

        # Capped at 1.0
        new_value = builder._compute_new_value(0.9, update)
        assert new_value == 1.0

    def test_compute_new_value_decrease(self, builder):
        """Test computing new value for 'decrease' action."""
        update = ProfileUpdate(
            profile="employment",
            field_name="verbosity",
            action="decrease",
            value=0.2,
        )

        # From 0.5, decrease by 0.2
        new_value = builder._compute_new_value(0.5, update)
        assert new_value == pytest.approx(0.3)

        # Floored at 0.0
        new_value = builder._compute_new_value(0.1, update)
        assert new_value == 0.0

    def test_compute_new_value_append(self, builder):
        """Test computing new value for 'append' action."""
        update = ProfileUpdate(
            profile="user",
            field_name="skills",
            action="append",
            value="Python",
        )

        # From empty
        new_value = builder._compute_new_value(None, update)
        assert new_value == ["Python"]

        # From existing list
        new_value = builder._compute_new_value(["JavaScript"], update)
        assert new_value == ["JavaScript", "Python"]

        # From single value
        new_value = builder._compute_new_value("JavaScript", update)
        assert new_value == ["JavaScript", "Python"]

    def test_compute_new_value_increment(self, builder):
        """Test computing new value for 'increment' action."""
        update = ProfileUpdate(
            profile="employment",
            field_name="interactions",
            action="increment",
            value=1,
        )

        # From 0
        new_value = builder._compute_new_value(None, update)
        assert new_value == 1

        # From existing
        new_value = builder._compute_new_value(10, update)
        assert new_value == 11

    @pytest.mark.asyncio
    async def test_get_profile_summary_uses_cache(self, builder, mock_cache):
        """Test that get_profile_summary uses cache."""
        from zetherion_ai.profile.cache import UserProfileSummary

        # Pre-populate cache
        summary = UserProfileSummary(user_id="user123", name="John")
        mock_cache.set_summary("user123", summary)

        result = await builder.get_profile_summary("user123")

        assert result.name == "John"

    @pytest.mark.asyncio
    async def test_get_employment_profile_returns_default(self, builder):
        """Test that get_employment_profile returns default for new user."""
        profile = await builder.get_employment_profile("newuser")

        assert profile.user_id == "newuser"
        assert profile.formality == 0.5
        assert profile.verbosity == 0.5
        assert profile.trust_level == 0.3

    @pytest.mark.asyncio
    async def test_confirm_update_confirmed(self, builder, mock_storage):
        """Test confirming an update."""
        # Record an update and add pending confirmation
        update_id = mock_storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        from datetime import timedelta

        expires_at = datetime.now() + timedelta(hours=72)
        conf_id = mock_storage.add_pending_confirmation(
            user_id="user123",
            update_id=update_id,
            expires_at=expires_at,
            priority=1,
        )

        # Confirm
        await builder.confirm_update("user123", conf_id, confirmed=True)

        # Should be removed from pending
        pending = mock_storage.get_pending_confirmations("user123")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_confirm_update_rejected(self, builder, mock_storage):
        """Test rejecting an update."""
        update_id = mock_storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        from datetime import timedelta

        expires_at = datetime.now() + timedelta(hours=72)
        conf_id = mock_storage.add_pending_confirmation(
            user_id="user123",
            update_id=update_id,
            expires_at=expires_at,
            priority=1,
        )

        # Reject
        await builder.confirm_update("user123", conf_id, confirmed=False)

        # Should be removed from pending
        pending = mock_storage.get_pending_confirmations("user123")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_get_pending_confirmations(self, builder, mock_storage):
        """Test getting pending confirmations as prompts."""
        # Add updates and confirmations
        for i in range(2):
            update_id = mock_storage.record_update(
                user_id="user123",
                profile="user",
                field=f"field{i}",
                old_value=None,
                new_value=f"value{i}",
                confidence=0.5,
                source_tier=1,
            )
            from datetime import timedelta

            mock_storage.add_pending_confirmation(
                user_id="user123",
                update_id=update_id,
                expires_at=datetime.now() + timedelta(hours=72),
                priority=i,
            )

        prompts = await builder.get_pending_confirmations("user123")

        assert len(prompts) == 2
        for conf_id, prompt in prompts:
            assert isinstance(conf_id, int)
            assert isinstance(prompt, str)
            assert "?" in prompt  # Should be a question

    @pytest.mark.asyncio
    async def test_cleanup_runs_without_error(self, builder):
        """Test that cleanup runs without errors."""
        await builder.cleanup()  # Should not raise

    def test_get_tier_usage_report(self, builder, mock_storage):
        """Test tier usage report generation."""
        # Record some usage
        for _ in range(10):
            mock_storage.record_tier_usage(1)
        for _ in range(5):
            mock_storage.record_tier_usage(2)

        report = builder.get_tier_usage_report(days=7)

        assert report["period_days"] == 7
        assert report["total_invocations"] == 15
        assert report["by_tier"][1] == 10
        assert report["by_tier"][2] == 5
        assert "percentages" in report


class TestBackgroundExtraction:
    """Tests for background extraction functions."""

    @pytest.mark.asyncio
    async def test_extract_profile_updates_background(self, tmp_path):
        """Test background extraction function."""
        db_path = tmp_path / "test_profiles.db"
        storage = ProfileStorage(db_path=str(db_path))
        builder = ProfileBuilder(
            storage=storage,
            tier1_only=True,
        )

        # Should not raise even with valid input
        await extract_profile_updates_background(
            builder=builder,
            user_id="user123",
            message="Call me John",
        )

    @pytest.mark.asyncio
    async def test_extract_profile_updates_background_handles_error(self, tmp_path):
        """Test background extraction handles errors gracefully."""
        db_path = tmp_path / "test_profiles.db"
        storage = ProfileStorage(db_path=str(db_path))
        builder = ProfileBuilder(
            storage=storage,
            tier1_only=True,
        )

        # Mock process_message to raise
        builder.process_message = AsyncMock(side_effect=Exception("Test error"))

        # Should not raise
        await extract_profile_updates_background(
            builder=builder,
            user_id="user123",
            message="Test",
        )

    @pytest.mark.asyncio
    async def test_schedule_profile_extraction_returns_task(self, tmp_path):
        """Test schedule_profile_extraction returns an asyncio task."""
        import asyncio

        db_path = tmp_path / "test_profiles.db"
        storage = ProfileStorage(db_path=str(db_path))
        builder = ProfileBuilder(
            storage=storage,
            tier1_only=True,
        )

        task = schedule_profile_extraction(
            builder=builder,
            user_id="user123",
            message="Test message",
        )

        assert isinstance(task, asyncio.Task)

        # Wait for task to complete
        await task
