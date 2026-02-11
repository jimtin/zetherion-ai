"""Comprehensive unit tests for TelemetryReceiver.

TelemetryStorage is an AsyncMock throughout. bcrypt hashing is exercised
for real so that validate_key tests prove actual password verification.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.telemetry.models import (
    InstanceRegistration,
    TelemetryConsent,
    TelemetryReport,
)
from zetherion_ai.telemetry.receiver import _KEY_PREFIX, TelemetryReceiver

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture()
def mock_storage() -> AsyncMock:
    """A mock TelemetryStorage with sensible defaults."""
    storage = AsyncMock()
    storage.register_instance = AsyncMock()
    storage.get_instance = AsyncMock(return_value=None)
    storage.save_report = AsyncMock()
    storage.delete_instance = AsyncMock(return_value=True)
    storage.list_instances = AsyncMock(return_value=[])
    return storage


@pytest.fixture()
def receiver(mock_storage: AsyncMock) -> TelemetryReceiver:
    """A TelemetryReceiver wired to the mock storage."""
    return TelemetryReceiver(storage=mock_storage)


# =====================================================================
# register_instance
# =====================================================================


class TestRegisterInstance:
    """Tests for TelemetryReceiver.register_instance."""

    async def test_returns_key_with_prefix(self, receiver: TelemetryReceiver) -> None:
        """Returned key should start with the zt_inst_ prefix."""
        raw_key = await receiver.register_instance("inst-001")
        assert raw_key.startswith(_KEY_PREFIX)

    async def test_key_is_long_enough(self, receiver: TelemetryReceiver) -> None:
        """The key should be substantially longer than the prefix alone."""
        raw_key = await receiver.register_instance("inst-001")
        # token_urlsafe(32) adds ~43 chars, total > 50
        assert len(raw_key) > 50

    async def test_stores_registration_in_storage(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """register_instance should call storage.register_instance."""
        await receiver.register_instance("inst-001")

        mock_storage.register_instance.assert_awaited_once()
        reg = mock_storage.register_instance.call_args[0][0]
        assert isinstance(reg, InstanceRegistration)
        assert reg.instance_id == "inst-001"

    async def test_stores_bcrypt_hash_not_raw_key(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """The stored registration should contain a bcrypt hash, not the raw key."""
        raw_key = await receiver.register_instance("inst-001")

        reg = mock_storage.register_instance.call_args[0][0]
        assert reg.api_key_hash != raw_key
        assert reg.api_key_hash.startswith("$2b$")  # bcrypt hash prefix

    async def test_caches_key_hash(self, receiver: TelemetryReceiver) -> None:
        """After registration, the hash should be cached for fast lookups."""
        await receiver.register_instance("inst-001")
        assert "inst-001" in receiver._key_cache

    async def test_default_consent_is_empty(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Without explicit consent, a default (empty) TelemetryConsent is used."""
        await receiver.register_instance("inst-001")

        reg = mock_storage.register_instance.call_args[0][0]
        assert isinstance(reg.consent, TelemetryConsent)
        assert reg.consent.categories == set()

    async def test_explicit_consent_is_stored(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Explicit consent should be passed through to the registration."""
        consent = TelemetryConsent(categories={"health", "usage"})
        await receiver.register_instance("inst-001", consent=consent)

        reg = mock_storage.register_instance.call_args[0][0]
        assert reg.consent.allows("health")
        assert reg.consent.allows("usage")

    async def test_registration_timestamps_set(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """first_seen and last_seen should be set on registration."""
        await receiver.register_instance("inst-001")

        reg = mock_storage.register_instance.call_args[0][0]
        assert isinstance(reg.first_seen, datetime)
        assert isinstance(reg.last_seen, datetime)

    async def test_unique_keys_per_instance(self, receiver: TelemetryReceiver) -> None:
        """Each registration should produce a unique key."""
        key1 = await receiver.register_instance("inst-001")
        key2 = await receiver.register_instance("inst-002")
        assert key1 != key2


# =====================================================================
# validate_key
# =====================================================================


class TestValidateKey:
    """Tests for TelemetryReceiver.validate_key."""

    async def test_valid_key_returns_true(self, receiver: TelemetryReceiver) -> None:
        """validate_key should return True for the correct raw key."""
        raw_key = await receiver.register_instance("inst-001")
        valid = await receiver.validate_key("inst-001", raw_key)
        assert valid is True

    async def test_invalid_key_returns_false(self, receiver: TelemetryReceiver) -> None:
        """validate_key should return False for an incorrect key."""
        await receiver.register_instance("inst-001")
        valid = await receiver.validate_key("inst-001", "wrong_key")
        assert valid is False

    async def test_unknown_instance_returns_false(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """validate_key for an unknown instance should return False."""
        mock_storage.get_instance = AsyncMock(return_value=None)
        valid = await receiver.validate_key("unknown-inst", "any_key")
        assert valid is False

    async def test_cache_hit_does_not_query_storage(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """When the hash is cached, storage.get_instance should NOT be called."""
        raw_key = await receiver.register_instance("inst-001")

        # Reset mock to track new calls
        mock_storage.get_instance.reset_mock()

        await receiver.validate_key("inst-001", raw_key)
        mock_storage.get_instance.assert_not_awaited()

    async def test_cache_miss_fetches_from_storage(self, mock_storage: AsyncMock) -> None:
        """When the hash is NOT cached, storage.get_instance should be called."""
        # Create a fresh receiver without any cached keys
        receiver = TelemetryReceiver(storage=mock_storage)

        # Register an instance to get the hash, then clear cache to simulate cold start
        raw_key = await receiver.register_instance("inst-001")
        reg = mock_storage.register_instance.call_args[0][0]

        # Clear cache to force storage lookup
        receiver._key_cache.clear()
        mock_storage.get_instance = AsyncMock(return_value=reg)

        valid = await receiver.validate_key("inst-001", raw_key)
        assert valid is True
        mock_storage.get_instance.assert_awaited_once_with("inst-001")

    async def test_cache_populated_after_storage_fetch(self, mock_storage: AsyncMock) -> None:
        """After fetching from storage, the hash should be cached."""
        receiver = TelemetryReceiver(storage=mock_storage)
        raw_key = await receiver.register_instance("inst-001")
        reg = mock_storage.register_instance.call_args[0][0]

        receiver._key_cache.clear()
        mock_storage.get_instance = AsyncMock(return_value=reg)

        await receiver.validate_key("inst-001", raw_key)
        assert "inst-001" in receiver._key_cache


# =====================================================================
# ingest
# =====================================================================


class TestIngest:
    """Tests for TelemetryReceiver.ingest."""

    async def test_valid_key_stores_report(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """With a valid key, the report should be stored and return True."""
        raw_key = await receiver.register_instance("inst-001")

        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-02-11T12:00:00",
            version="0.1.0",
            consent=TelemetryConsent(categories={"health"}),
            metrics={"health": {"system": {"cpu": 10}}},
        )
        result = await receiver.ingest(report, raw_key)

        assert result is True
        mock_storage.save_report.assert_awaited_once()

    async def test_invalid_key_rejects_report(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """With an invalid key, the report should be rejected."""
        await receiver.register_instance("inst-001")

        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-02-11T12:00:00",
            version="0.1.0",
            consent=TelemetryConsent(categories={"health"}),
            metrics={"health": {}},
        )
        result = await receiver.ingest(report, "wrong_key")

        assert result is False
        mock_storage.save_report.assert_not_awaited()

    async def test_filters_metrics_by_consent(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Only consented categories should remain in the stored report."""
        raw_key = await receiver.register_instance("inst-001")

        # Report claims health + usage, but consent only allows health
        consent = TelemetryConsent(categories={"health"})
        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-02-11T12:00:00",
            version="0.1.0",
            consent=consent,
            metrics={
                "health": {"system": {"cpu": 10}},
                "usage": {"messages_total": 100},
                "cost": {"total_usd": 5.0},
            },
        )
        await receiver.ingest(report, raw_key)

        # The saved report should only have "health"
        saved_report = mock_storage.save_report.call_args[0][0]
        assert "health" in saved_report.metrics
        assert "usage" not in saved_report.metrics
        assert "cost" not in saved_report.metrics

    async def test_all_consented_categories_kept(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """When all categories are consented, all should be kept."""
        raw_key = await receiver.register_instance("inst-001")

        consent = TelemetryConsent(categories={"health", "performance", "usage", "cost", "quality"})
        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-02-11T12:00:00",
            version="0.1.0",
            consent=consent,
            metrics={
                "health": {"data": 1},
                "performance": {"data": 2},
                "usage": {"data": 3},
                "cost": {"data": 4},
                "quality": {"data": 5},
            },
        )
        await receiver.ingest(report, raw_key)

        saved_report = mock_storage.save_report.call_args[0][0]
        assert len(saved_report.metrics) == 5

    async def test_empty_metrics_accepted(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """A report with empty metrics should still be accepted with valid key."""
        raw_key = await receiver.register_instance("inst-001")

        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-02-11T12:00:00",
            version="0.1.0",
            consent=TelemetryConsent(),
            metrics={},
        )
        result = await receiver.ingest(report, raw_key)
        assert result is True


# =====================================================================
# delete_instance
# =====================================================================


class TestDeleteInstance:
    """Tests for TelemetryReceiver.delete_instance."""

    async def test_valid_key_deletes_and_returns_true(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """With a valid key, the instance should be deleted."""
        raw_key = await receiver.register_instance("inst-001")
        mock_storage.delete_instance = AsyncMock(return_value=True)

        result = await receiver.delete_instance("inst-001", raw_key)

        assert result is True
        mock_storage.delete_instance.assert_awaited_once_with("inst-001")

    async def test_invalid_key_rejects_deletion(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """With an invalid key, deletion should be rejected."""
        await receiver.register_instance("inst-001")

        result = await receiver.delete_instance("inst-001", "wrong_key")

        assert result is False
        mock_storage.delete_instance.assert_not_awaited()

    async def test_clears_cache_after_deletion(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """After successful deletion, the key cache entry should be removed."""
        raw_key = await receiver.register_instance("inst-001")
        mock_storage.delete_instance = AsyncMock(return_value=True)
        assert "inst-001" in receiver._key_cache

        await receiver.delete_instance("inst-001", raw_key)

        assert "inst-001" not in receiver._key_cache

    async def test_storage_returns_false_propagated(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """If storage.delete_instance returns False, result should be False."""
        raw_key = await receiver.register_instance("inst-001")
        mock_storage.delete_instance = AsyncMock(return_value=False)

        result = await receiver.delete_instance("inst-001", raw_key)
        assert result is False

    async def test_cache_cleared_even_when_storage_returns_false(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Cache should be cleared regardless of storage.delete_instance result."""
        raw_key = await receiver.register_instance("inst-001")
        mock_storage.delete_instance = AsyncMock(return_value=False)

        await receiver.delete_instance("inst-001", raw_key)
        assert "inst-001" not in receiver._key_cache


# =====================================================================
# get_fleet_summary
# =====================================================================


class TestGetFleetSummary:
    """Tests for TelemetryReceiver.get_fleet_summary."""

    async def test_no_instances_returns_zeros(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """With no instances, should return zero counts."""
        mock_storage.list_instances = AsyncMock(return_value=[])

        summary = await receiver.get_fleet_summary()

        assert summary["total_instances"] == 0
        assert summary["versions"] == {}
        assert summary["last_report"] is None

    async def test_single_instance_counted(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """A single instance should be reflected in the summary."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 11, 12, 0, 0),
                }
            ]
        )

        summary = await receiver.get_fleet_summary()

        assert summary["total_instances"] == 1
        assert summary["versions"] == {"0.1.0": 1}
        assert summary["last_report"] == "2026-02-11T12:00:00"

    async def test_multiple_instances_and_versions(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Multiple instances with different versions should be counted separately."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 10, 12, 0, 0),
                },
                {
                    "instance_id": "inst-002",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 9, 12, 0, 0),
                },
                {
                    "instance_id": "inst-003",
                    "current_version": "0.2.0",
                    "last_seen": datetime(2026, 2, 11, 12, 0, 0),
                },
            ]
        )

        summary = await receiver.get_fleet_summary()

        assert summary["total_instances"] == 3
        assert summary["versions"] == {"0.1.0": 2, "0.2.0": 1}

    async def test_tracks_latest_seen(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """last_report should be the most recent last_seen across all instances."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 1, 1, 0, 0, 0),
                },
                {
                    "instance_id": "inst-002",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 11, 15, 30, 0),
                },
                {
                    "instance_id": "inst-003",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 5, 8, 0, 0),
                },
            ]
        )

        summary = await receiver.get_fleet_summary()
        assert summary["last_report"] == "2026-02-11T15:30:00"

    async def test_missing_version_defaults_to_unknown(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Instances without current_version should be counted as 'unknown'."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "last_seen": datetime(2026, 2, 11, 12, 0, 0),
                },
            ]
        )

        summary = await receiver.get_fleet_summary()
        assert summary["versions"] == {"unknown": 1}

    async def test_missing_last_seen_excluded_from_latest(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """Instances without last_seen should not affect the last_report field."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "current_version": "0.1.0",
                    "last_seen": None,
                },
                {
                    "instance_id": "inst-002",
                    "current_version": "0.1.0",
                    "last_seen": datetime(2026, 2, 1, 0, 0, 0),
                },
            ]
        )

        summary = await receiver.get_fleet_summary()
        assert summary["total_instances"] == 2
        assert summary["last_report"] == "2026-02-01T00:00:00"

    async def test_all_missing_last_seen_returns_none(
        self, receiver: TelemetryReceiver, mock_storage: AsyncMock
    ) -> None:
        """If all instances lack last_seen, last_report should be None."""
        mock_storage.list_instances = AsyncMock(
            return_value=[
                {
                    "instance_id": "inst-001",
                    "current_version": "0.1.0",
                    "last_seen": None,
                },
            ]
        )

        summary = await receiver.get_fleet_summary()
        assert summary["last_report"] is None
