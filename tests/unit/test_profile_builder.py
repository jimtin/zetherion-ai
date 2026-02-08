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


class TestProfileBuilderWithMemory:
    """Tests for ProfileBuilder methods that interact with Qdrant memory."""

    @pytest.fixture
    def mock_memory(self):
        """Create a mock QdrantMemory instance."""
        memory = AsyncMock()
        memory.store_memory = AsyncMock(return_value="mem-id-123")
        memory.search_memories = AsyncMock(return_value=[])
        memory.filter_by_field = AsyncMock(return_value=[])
        memory.delete_by_id = AsyncMock(return_value=True)
        memory.ensure_collection = AsyncMock()
        memory.store_with_payload = AsyncMock()
        memory.get_by_id = AsyncMock(return_value=None)
        return memory

    @pytest.fixture
    def builder_with_memory(self, mock_memory, tmp_path):
        """Create a profile builder with mock memory."""
        db_path = tmp_path / "test_profiles.db"
        return ProfileBuilder(
            memory=mock_memory,
            inference_broker=None,
            storage=ProfileStorage(db_path=str(db_path)),
            cache=ProfileCache(),
            tier1_only=True,
        )

    @pytest.fixture
    def builder_no_memory(self, tmp_path):
        """Create a profile builder without memory."""
        db_path = tmp_path / "test_profiles_nomem.db"
        return ProfileBuilder(
            memory=None,
            inference_broker=None,
            storage=ProfileStorage(db_path=str(db_path)),
            cache=ProfileCache(),
            tier1_only=True,
        )

    # --- Qdrant persistence tests ---

    @pytest.mark.asyncio
    async def test_persist_to_qdrant(self, builder_with_memory, mock_memory):
        """Test that _persist_to_qdrant stores a profile entry in memory."""
        update = ProfileUpdate(
            profile="user",
            field_name="timezone",
            action="set",
            value="UTC",
            confidence=0.8,
        )
        await builder_with_memory._persist_to_qdrant("user123", update, "UTC")
        mock_memory.store_memory.assert_called_once()
        call_kwargs = mock_memory.store_memory.call_args
        assert call_kwargs.kwargs["memory_type"] == "profile"

    @pytest.mark.asyncio
    async def test_load_profile_no_memory(self, builder_no_memory):
        """Test that _load_profile returns empty list when memory is None."""
        result = await builder_no_memory._load_profile("user123")
        assert result == []

    @pytest.mark.asyncio
    async def test_load_profile_with_results(self, builder_with_memory, mock_memory):
        """Test that _load_profile returns ProfileEntry objects from Qdrant results."""
        from uuid import uuid4

        entry_id = str(uuid4())
        now = datetime.now().isoformat()
        mock_memory.search_memories.return_value = [
            {
                "content": "timezone: UTC",
                "metadata": {
                    "id": entry_id,
                    "user_id": "user123",
                    "category": "preferences",
                    "key": "timezone",
                    "value": "UTC",
                    "confidence": 0.9,
                    "source": "conversation",
                    "created_at": now,
                    "last_confirmed": now,
                    "decay_rate": 0.01,
                },
            }
        ]
        result = await builder_with_memory._load_profile("user123")
        assert len(result) == 1
        assert result[0].key == "timezone"
        assert result[0].value == "UTC"

    @pytest.mark.asyncio
    async def test_save_employment_profile_no_memory(self, builder_no_memory):
        """Test that save_employment_profile does nothing when memory is None."""
        from zetherion_ai.profile.employment import EmploymentProfile

        profile = EmploymentProfile(user_id="user123")
        # Should not raise
        await builder_no_memory.save_employment_profile(profile)

    @pytest.mark.asyncio
    async def test_save_employment_profile_success(self, builder_with_memory, mock_memory):
        """Test that save_employment_profile stores to memory."""
        from zetherion_ai.profile.employment import EmploymentProfile

        profile = EmploymentProfile(user_id="user123")
        await builder_with_memory.save_employment_profile(profile)
        mock_memory.store_memory.assert_called_once()
        call_kwargs = mock_memory.store_memory.call_args
        assert call_kwargs.kwargs["memory_type"] == "employment_profile"

    @pytest.mark.asyncio
    async def test_save_employment_profile_exception(self, builder_with_memory, mock_memory):
        """Test that save_employment_profile catches exceptions."""
        from zetherion_ai.profile.employment import EmploymentProfile

        mock_memory.store_memory.side_effect = RuntimeError("Qdrant down")
        profile = EmploymentProfile(user_id="user123")
        # Should not raise
        await builder_with_memory.save_employment_profile(profile)

    @pytest.mark.asyncio
    async def test_load_employment_profile_no_memory(self, builder_no_memory):
        """Test that _load_employment_profile returns None when memory is None."""
        result = await builder_no_memory._load_employment_profile("user123")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_employment_profile_not_found(self, builder_with_memory, mock_memory):
        """Test that _load_employment_profile returns None when no results."""
        mock_memory.search_memories.return_value = []
        result = await builder_with_memory._load_employment_profile("user123")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_employment_profile_found(self, builder_with_memory, mock_memory):
        """Test that _load_employment_profile returns EmploymentProfile when found."""
        from zetherion_ai.profile.employment import EmploymentProfile

        profile = EmploymentProfile(user_id="user123")
        profile_dict = profile.to_dict()
        mock_memory.search_memories.return_value = [
            {
                "content": "employment profile for user123",
                "metadata": profile_dict,
            }
        ]
        result = await builder_with_memory._load_employment_profile("user123")
        assert result is not None
        assert isinstance(result, EmploymentProfile)
        assert result.user_id == "user123"

    @pytest.mark.asyncio
    async def test_load_employment_profile_exception(self, builder_with_memory, mock_memory):
        """Test that _load_employment_profile returns None on exception."""
        mock_memory.search_memories.side_effect = RuntimeError("Qdrant error")
        result = await builder_with_memory._load_employment_profile("user123")
        assert result is None

    # --- Relationship tracker tests ---

    @pytest.mark.asyncio
    async def test_get_relationship_tracker_cached(self, builder_with_memory):
        """Test that get_relationship_tracker returns cached tracker."""
        from zetherion_ai.profile.relationship import RelationshipTracker

        tracker = RelationshipTracker(user_id="user123")
        builder_with_memory._cache.set_relationship_tracker("user123", tracker)
        result = await builder_with_memory.get_relationship_tracker("user123")
        assert result is tracker

    @pytest.mark.asyncio
    async def test_get_relationship_tracker_new(self, builder_with_memory, mock_memory):
        """Test that get_relationship_tracker creates new tracker when not cached."""
        from zetherion_ai.profile.relationship import RelationshipTracker

        mock_memory.search_memories.return_value = []
        result = await builder_with_memory.get_relationship_tracker("user123")
        assert isinstance(result, RelationshipTracker)
        assert result.user_id == "user123"

    @pytest.mark.asyncio
    async def test_save_relationship_tracker_success(self, builder_with_memory, mock_memory):
        """Test that save_relationship_tracker stores to memory."""
        from zetherion_ai.profile.relationship import RelationshipTracker

        tracker = RelationshipTracker(user_id="user123")
        await builder_with_memory.save_relationship_tracker(tracker)
        mock_memory.store_memory.assert_called_once()
        call_kwargs = mock_memory.store_memory.call_args
        assert call_kwargs.kwargs["memory_type"] == "relationship_tracker"

    @pytest.mark.asyncio
    async def test_record_relationship_event(self, builder_with_memory, mock_memory):
        """Test that record_relationship_event updates tracker and saves."""
        from zetherion_ai.profile.relationship import RelationshipEvent

        mock_memory.search_memories.return_value = []
        updates = await builder_with_memory.record_relationship_event(
            user_id="user123",
            event=RelationshipEvent.MESSAGE_RECEIVED,
        )
        assert isinstance(updates, list)
        # save_employment_profile and save_relationship_tracker both call store_memory
        assert mock_memory.store_memory.call_count >= 1

    # --- CRUD tests ---

    @pytest.mark.asyncio
    async def test_update_profile_entry_no_memory(self, builder_no_memory):
        """Test that update_profile_entry returns gracefully when memory is None."""
        await builder_no_memory.update_profile_entry(
            user_id="user123",
            category="preferences",
            key="timezone",
            value="UTC",
        )
        # No exception raised

    @pytest.mark.asyncio
    async def test_update_profile_entry_success(self, builder_with_memory, mock_memory):
        """Test that update_profile_entry stores to memory."""
        await builder_with_memory.update_profile_entry(
            user_id="user123",
            category="preferences",
            key="timezone",
            value="UTC",
            confidence=0.9,
        )
        mock_memory.store_memory.assert_called_once()
        call_kwargs = mock_memory.store_memory.call_args
        assert call_kwargs.kwargs["memory_type"] == "profile"

    @pytest.mark.asyncio
    async def test_delete_profile_entry_no_memory(self, builder_no_memory):
        """Test that delete_profile_entry returns False when memory is None."""
        result = await builder_no_memory.delete_profile_entry("user123", "entry-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_profile_entry_success(self, builder_with_memory, mock_memory):
        """Test that delete_profile_entry calls delete_by_id."""
        result = await builder_with_memory.delete_profile_entry("user123", "entry-id")
        mock_memory.delete_by_id.assert_called_once_with("user_profiles", "entry-id")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_profile_entry_by_key_not_found(self, builder_with_memory, mock_memory):
        """Test that delete_profile_entry_by_key returns False when entry not found."""
        mock_memory.filter_by_field.return_value = []
        result = await builder_with_memory.delete_profile_entry_by_key(
            user_id="user123",
            category="preferences",
            key="timezone",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_profile_entry_by_key_found(self, builder_with_memory, mock_memory):
        """Test that delete_profile_entry_by_key deletes matching entry."""
        mock_memory.filter_by_field.return_value = [
            {"id": "entry-42", "category": "preferences", "key": "timezone"},
        ]
        result = await builder_with_memory.delete_profile_entry_by_key(
            user_id="user123",
            category="preferences",
            key="timezone",
        )
        mock_memory.delete_by_id.assert_called_once_with("user_profiles", "entry-42")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_all_profile_entries_no_memory(self, builder_no_memory):
        """Test that get_all_profile_entries returns [] when memory is None."""
        result = await builder_no_memory.get_all_profile_entries("user123")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_profile_entries_success(self, builder_with_memory, mock_memory):
        """Test that get_all_profile_entries returns entries from memory."""
        mock_memory.filter_by_field.return_value = [
            {"id": "e1", "category": "preferences", "key": "tz", "value": "UTC"},
            {"id": "e2", "category": "identity", "key": "name", "value": "John"},
        ]
        result = await builder_with_memory.get_all_profile_entries("user123")
        assert len(result) == 2
        mock_memory.filter_by_field.assert_called_once_with("user_profiles", "user_id", "user123")


class TestProfileBuilderConfidenceBranches:
    """Tests for the different confidence thresholds in _process_updates."""

    @pytest.fixture
    def mock_memory(self):
        """Create a mock QdrantMemory instance."""
        memory = AsyncMock()
        memory.store_memory = AsyncMock(return_value="mem-id-123")
        memory.search_memories = AsyncMock(return_value=[])
        memory.filter_by_field = AsyncMock(return_value=[])
        memory.delete_by_id = AsyncMock(return_value=True)
        return memory

    @pytest.fixture
    def builder(self, mock_memory, tmp_path):
        """Create a profile builder with mock memory for confidence tests."""
        db_path = tmp_path / "test_conf.db"
        return ProfileBuilder(
            memory=mock_memory,
            inference_broker=None,
            storage=ProfileStorage(db_path=str(db_path)),
            cache=ProfileCache(),
            tier1_only=True,
        )

    @pytest.mark.asyncio
    async def test_process_updates_auto_apply(self, builder):
        """Test update with confidence >= CONFIDENCE_AUTO_APPLY (0.9) is applied."""
        from zetherion_ai.profile.models import CONFIDENCE_AUTO_APPLY

        update = ProfileUpdate(
            profile="user",
            field_name="name",
            action="set",
            value="Alice",
            confidence=CONFIDENCE_AUTO_APPLY,
        )
        applied = await builder._process_updates("user123", [update])
        assert len(applied) == 1
        assert applied[0].field_name == "name"

    @pytest.mark.asyncio
    async def test_process_updates_log_only(self, builder):
        """Test update with confidence between LOG_ONLY (0.7) and AUTO_APPLY (0.9)."""
        from zetherion_ai.profile.models import (
            CONFIDENCE_AUTO_APPLY,
            CONFIDENCE_LOG_ONLY,
        )

        confidence = (CONFIDENCE_LOG_ONLY + CONFIDENCE_AUTO_APPLY) / 2  # 0.8
        update = ProfileUpdate(
            profile="user",
            field_name="timezone",
            action="set",
            value="PST",
            confidence=confidence,
        )
        applied = await builder._process_updates("user123", [update])
        assert len(applied) == 1

    @pytest.mark.asyncio
    async def test_process_updates_flag_confirm(self, builder):
        """Test update with confidence between FLAG_CONFIRM (0.5) and LOG_ONLY (0.7)."""
        from zetherion_ai.profile.models import (
            CONFIDENCE_FLAG_CONFIRM,
            CONFIDENCE_LOG_ONLY,
        )

        confidence = (CONFIDENCE_FLAG_CONFIRM + CONFIDENCE_LOG_ONLY) / 2  # 0.6
        update = ProfileUpdate(
            profile="user",
            field_name="role",
            action="set",
            value="engineer",
            confidence=confidence,
        )
        applied = await builder._process_updates("user123", [update])
        assert len(applied) == 1

    @pytest.mark.asyncio
    async def test_process_updates_discard(self, builder):
        """Test update with confidence below CONFIDENCE_QUEUE_CONFIRM (0.3) is discarded."""
        from zetherion_ai.profile.models import CONFIDENCE_QUEUE_CONFIRM

        confidence = CONFIDENCE_QUEUE_CONFIRM - 0.1  # 0.2
        update = ProfileUpdate(
            profile="user",
            field_name="hobby",
            action="set",
            value="chess",
            confidence=confidence,
        )
        applied = await builder._process_updates("user123", [update])
        assert len(applied) == 0


class TestProfileBuilderCachePaths:
    """Tests for cache hit/miss paths in ProfileBuilder."""

    @pytest.fixture
    def mock_memory(self):
        """Create a mock QdrantMemory instance."""
        memory = AsyncMock()
        memory.store_memory = AsyncMock(return_value="mem-id-123")
        memory.search_memories = AsyncMock(return_value=[])
        memory.filter_by_field = AsyncMock(return_value=[])
        memory.delete_by_id = AsyncMock(return_value=True)
        return memory

    @pytest.fixture
    def builder(self, mock_memory, tmp_path):
        """Create a profile builder with mock memory for cache tests."""
        db_path = tmp_path / "test_cache.db"
        return ProfileBuilder(
            memory=mock_memory,
            inference_broker=None,
            storage=ProfileStorage(db_path=str(db_path)),
            cache=ProfileCache(),
            tier1_only=True,
        )

    @pytest.mark.asyncio
    async def test_get_profile_summary_no_cache(self, builder, mock_memory):
        """Test get_profile_summary loads from Qdrant when cache is empty."""
        from zetherion_ai.profile.cache import UserProfileSummary

        # No cache set, search_memories returns empty
        mock_memory.search_memories.return_value = []
        result = await builder.get_profile_summary("user123")
        assert isinstance(result, UserProfileSummary)
        # With no entries, build_summary returns default
        mock_memory.search_memories.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_full_employment_profile_cached(self, builder):
        """Test get_full_employment_profile returns cached profile."""
        from zetherion_ai.profile.employment import EmploymentProfile

        profile = EmploymentProfile(user_id="user123")
        builder._cache.set_full_employment_profile("user123", profile)
        result = await builder.get_full_employment_profile("user123")
        assert result is profile

    @pytest.mark.asyncio
    async def test_get_full_employment_profile_from_qdrant(self, builder, mock_memory):
        """Test get_full_employment_profile loads from Qdrant when not cached."""
        from zetherion_ai.profile.employment import EmploymentProfile

        profile = EmploymentProfile(user_id="user123")
        profile_dict = profile.to_dict()
        mock_memory.search_memories.return_value = [
            {
                "content": "employment profile for user123",
                "metadata": profile_dict,
            }
        ]
        result = await builder.get_full_employment_profile("user123")
        assert isinstance(result, EmploymentProfile)
        assert result.user_id == "user123"

    @pytest.mark.asyncio
    async def test_get_full_employment_profile_default(self, builder, mock_memory):
        """Test get_full_employment_profile returns default when nothing found."""
        from zetherion_ai.profile.employment import EmploymentProfile

        mock_memory.search_memories.return_value = []
        result = await builder.get_full_employment_profile("newuser")
        assert isinstance(result, EmploymentProfile)
        assert result.user_id == "newuser"
