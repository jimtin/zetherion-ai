"""Unit tests for the profile storage module."""

from datetime import datetime, timedelta

import pytest

from secureclaw.profile.storage import (
    ProfileStats,
    ProfileStorage,
)


class TestProfileStorage:
    """Tests for ProfileStorage SQLite operations."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create a temporary storage instance."""
        db_path = tmp_path / "test_profiles.db"
        return ProfileStorage(db_path=str(db_path))

    def test_initialization(self, tmp_path):
        """Test storage initialization creates database."""
        db_path = tmp_path / "new_profiles.db"
        _storage = ProfileStorage(db_path=str(db_path))  # noqa: F841

        assert db_path.exists()

    # === Profile Stats Tests ===

    def test_get_stats_not_found(self, storage):
        """Test getting stats for non-existent user."""
        stats = storage.get_stats("nonexistent")
        assert stats is None

    def test_upsert_and_get_stats(self, storage):
        """Test upserting and retrieving stats."""
        stats = ProfileStats(
            user_id="user123",
            profile_version=1,
            last_updated=datetime.now(),
            total_entries=10,
            high_confidence_entries=5,
            pending_confirmations=2,
        )

        storage.upsert_stats(stats)

        retrieved = storage.get_stats("user123")
        assert retrieved is not None
        assert retrieved.user_id == "user123"
        assert retrieved.profile_version == 1
        assert retrieved.total_entries == 10
        assert retrieved.high_confidence_entries == 5
        assert retrieved.pending_confirmations == 2

    def test_upsert_stats_update(self, storage):
        """Test updating existing stats."""
        # Initial insert
        stats1 = ProfileStats(
            user_id="user123",
            profile_version=1,
            last_updated=datetime.now(),
            total_entries=5,
            high_confidence_entries=3,
            pending_confirmations=1,
        )
        storage.upsert_stats(stats1)

        # Update
        stats2 = ProfileStats(
            user_id="user123",
            profile_version=2,
            last_updated=datetime.now(),
            total_entries=10,
            high_confidence_entries=7,
            pending_confirmations=0,
        )
        storage.upsert_stats(stats2)

        retrieved = storage.get_stats("user123")
        assert retrieved is not None
        assert retrieved.profile_version == 2
        assert retrieved.total_entries == 10

    # === Profile Updates Tests ===

    def test_record_update(self, storage):
        """Test recording a profile update."""
        update_id = storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.9,
            source_tier=1,
        )

        assert update_id > 0

    def test_get_recent_updates(self, storage):
        """Test getting recent updates."""
        # Record multiple updates
        for i in range(5):
            storage.record_update(
                user_id="user123",
                profile="user",
                field=f"field{i}",
                old_value=None,
                new_value=f"value{i}",
                confidence=0.8,
                source_tier=1,
            )

        updates = storage.get_recent_updates("user123", limit=3)

        assert len(updates) == 3
        # Check all returned updates are from our user and have valid fields
        fields = [u.field for u in updates]
        assert all(f.startswith("field") for f in fields)
        assert all(u.user_id == "user123" for u in updates)

    def test_get_pending_updates(self, storage):
        """Test getting pending (unconfirmed) updates."""
        # Record some updates
        id1 = storage.record_update(
            user_id="user123",
            profile="user",
            field="field1",
            old_value=None,
            new_value="value1",
            confidence=0.5,
            source_tier=1,
        )
        storage.record_update(
            user_id="user123",
            profile="user",
            field="field2",
            old_value=None,
            new_value="value2",
            confidence=0.5,
            source_tier=1,
        )

        # Confirm one
        storage.confirm_update(id1, confirmed=True)

        pending = storage.get_pending_updates("user123")

        assert len(pending) == 1
        assert pending[0].field == "field2"

    def test_confirm_update(self, storage):
        """Test confirming an update."""
        update_id = storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        # Initially pending
        pending = storage.get_pending_updates("user123")
        assert len(pending) == 1

        # Confirm
        storage.confirm_update(update_id, confirmed=True)

        # No longer pending
        pending = storage.get_pending_updates("user123")
        assert len(pending) == 0

    # === Pending Confirmations Tests ===

    def test_add_pending_confirmation(self, storage):
        """Test adding a pending confirmation."""
        update_id = storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        expires_at = datetime.now() + timedelta(hours=72)
        conf_id = storage.add_pending_confirmation(
            user_id="user123",
            update_id=update_id,
            expires_at=expires_at,
            priority=5,
        )

        assert conf_id > 0

    def test_get_pending_confirmations(self, storage):
        """Test getting pending confirmations."""
        # Create updates and confirmations
        for i in range(3):
            update_id = storage.record_update(
                user_id="user123",
                profile="user",
                field=f"field{i}",
                old_value=None,
                new_value=f"value{i}",
                confidence=0.5,
                source_tier=1,
            )
            storage.add_pending_confirmation(
                user_id="user123",
                update_id=update_id,
                expires_at=datetime.now() + timedelta(hours=72),
                priority=i,  # 0, 1, 2
            )

        confirmations = storage.get_pending_confirmations("user123", limit=5)

        assert len(confirmations) == 3
        # Should be sorted by priority (highest first)
        assert confirmations[0].priority == 2
        assert confirmations[2].priority == 0

    def test_get_pending_confirmations_excludes_expired(self, storage):
        """Test that expired confirmations are excluded."""
        update_id = storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        # Add expired confirmation
        storage.add_pending_confirmation(
            user_id="user123",
            update_id=update_id,
            expires_at=datetime.now() - timedelta(hours=1),  # Already expired
            priority=1,
        )

        confirmations = storage.get_pending_confirmations("user123")
        assert len(confirmations) == 0

    def test_remove_pending_confirmation(self, storage):
        """Test removing a pending confirmation."""
        update_id = storage.record_update(
            user_id="user123",
            profile="user",
            field="timezone",
            old_value=None,
            new_value="EST",
            confidence=0.5,
            source_tier=1,
        )

        conf_id = storage.add_pending_confirmation(
            user_id="user123",
            update_id=update_id,
            expires_at=datetime.now() + timedelta(hours=72),
            priority=1,
        )

        # Verify it exists
        confirmations = storage.get_pending_confirmations("user123")
        assert len(confirmations) == 1

        # Remove it
        storage.remove_pending_confirmation(conf_id)

        # Verify it's gone
        confirmations = storage.get_pending_confirmations("user123")
        assert len(confirmations) == 0

    def test_cleanup_expired_confirmations(self, storage):
        """Test cleaning up expired confirmations."""
        # Create updates
        id1 = storage.record_update(
            user_id="user123",
            profile="user",
            field="field1",
            old_value=None,
            new_value="value1",
            confidence=0.5,
            source_tier=1,
        )
        id2 = storage.record_update(
            user_id="user123",
            profile="user",
            field="field2",
            old_value=None,
            new_value="value2",
            confidence=0.5,
            source_tier=1,
        )

        # Add expired confirmation
        storage.add_pending_confirmation(
            user_id="user123",
            update_id=id1,
            expires_at=datetime.now() - timedelta(hours=1),
            priority=1,
        )

        # Add non-expired confirmation
        storage.add_pending_confirmation(
            user_id="user123",
            update_id=id2,
            expires_at=datetime.now() + timedelta(hours=72),
            priority=1,
        )

        # Cleanup
        cleaned = storage.cleanup_expired_confirmations()

        assert cleaned == 1

        # Verify only non-expired remains
        confirmations = storage.get_pending_confirmations("user123")
        assert len(confirmations) == 1

    # === Inference Tier Usage Tests ===

    def test_record_tier_usage(self, storage):
        """Test recording tier usage."""
        storage.record_tier_usage(1)
        storage.record_tier_usage(1)
        storage.record_tier_usage(2)

        usage = storage.get_tier_usage(days=1)

        assert usage.get(1) == 2
        assert usage.get(2) == 1

    def test_get_tier_usage_multiple_days(self, storage):
        """Test tier usage aggregation."""
        # Record usage
        for _ in range(10):
            storage.record_tier_usage(1)
        for _ in range(5):
            storage.record_tier_usage(2)
        for _ in range(2):
            storage.record_tier_usage(3)

        usage = storage.get_tier_usage(days=7)

        assert usage.get(1) == 10
        assert usage.get(2) == 5
        assert usage.get(3) == 2

    def test_get_daily_tier_usage(self, storage):
        """Test daily tier usage breakdown."""
        # Record some usage
        storage.record_tier_usage(1)
        storage.record_tier_usage(1)
        storage.record_tier_usage(2)

        daily = storage.get_daily_tier_usage(days=1)

        assert len(daily) >= 1
        # Find today's entries
        today = datetime.now().date().isoformat()
        today_entries = [d for d in daily if d["date"] == today]
        assert len(today_entries) >= 1
