"""Comprehensive unit tests for TelemetryStorage.

All database interactions are mocked via AsyncMock so no real PostgreSQL
connection is needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from zetherion_ai.telemetry.models import (
    InstanceRegistration,
    TelemetryConsent,
    TelemetryReport,
)
from zetherion_ai.telemetry.storage import (
    _CREATE_AGGREGATES_TABLE,
    _CREATE_INSTANCES_TABLE,
    _CREATE_REPORTS_TABLE,
    TelemetryStorage,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool that yields an async connection context."""
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.fixture
def storage():
    """Return an uninitialised TelemetryStorage instance."""
    return TelemetryStorage()


@pytest.fixture
def now():
    """A deterministic UTC timestamp for tests."""
    return datetime(2026, 2, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_consent():
    """A TelemetryConsent with a couple of opted-in categories."""
    return TelemetryConsent(categories={"performance", "usage"})


@pytest.fixture
def sample_registration(now, sample_consent):
    """A sample InstanceRegistration for testing."""
    return InstanceRegistration(
        instance_id="inst-001",
        api_key_hash="$2b$12$fakehash",
        first_seen=now,
        last_seen=now,
        current_version="1.2.3",
        consent=sample_consent,
    )


@pytest.fixture
def sample_report(sample_consent):
    """A sample TelemetryReport for testing."""
    return TelemetryReport(
        instance_id="inst-001",
        timestamp="2026-02-11T12:00:00+00:00",
        version="1.2.3",
        consent=sample_consent,
        metrics={
            "performance": {"avg_latency_ms": 42.0, "p99_latency_ms": 120.0},
            "usage": {"messages_processed": 1500},
        },
    )


# ------------------------------------------------------------------
# 1. TestInit
# ------------------------------------------------------------------


class TestInit:
    """Tests for TelemetryStorage.__init__()."""

    def test_pool_is_none_initially(self):
        """TelemetryStorage starts with _pool = None."""
        s = TelemetryStorage()
        assert s._pool is None


# ------------------------------------------------------------------
# 2. TestInitialize
# ------------------------------------------------------------------


class TestInitialize:
    """Tests for TelemetryStorage.initialize()."""

    async def test_calls_execute_three_times_for_three_tables(self, storage, mock_pool):
        """initialize() executes DDL for instances, reports, and aggregates tables."""
        pool, conn = mock_pool

        await storage.initialize(pool)

        assert conn.execute.await_count == 3
        conn.execute.assert_has_awaits(
            [
                call(_CREATE_INSTANCES_TABLE),
                call(_CREATE_REPORTS_TABLE),
                call(_CREATE_AGGREGATES_TABLE),
            ]
        )

    async def test_stores_pool_reference(self, storage, mock_pool):
        """initialize() stores the pool reference on the instance."""
        pool, conn = mock_pool

        await storage.initialize(pool)

        assert storage._pool is pool


# ------------------------------------------------------------------
# 3. TestRegisterInstance
# ------------------------------------------------------------------


class TestRegisterInstance:
    """Tests for TelemetryStorage.register_instance()."""

    async def test_no_pool_returns_none(self, storage, sample_registration):
        """register_instance() is a no-op when pool is None."""
        result = await storage.register_instance(sample_registration)

        assert result is None

    async def test_inserts_with_correct_parameters(
        self, storage, mock_pool, sample_registration, sample_consent
    ):
        """register_instance() passes correct values to the INSERT statement."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage.register_instance(sample_registration)

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        sql = args[0]
        assert "INSERT INTO telemetry_instances" in sql
        assert args[1] == "inst-001"
        assert args[2] == "$2b$12$fakehash"
        assert args[3] == sample_registration.first_seen
        assert args[4] == sample_registration.last_seen
        assert args[5] == "1.2.3"
        assert json.loads(args[6]) == sample_consent.to_dict()

    async def test_upsert_on_conflict(self, storage, mock_pool, sample_registration):
        """register_instance() SQL includes ON CONFLICT clause for upsert."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage.register_instance(sample_registration)

        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql
        assert "last_seen" in sql
        assert "current_version" in sql
        assert "consent_json" in sql


# ------------------------------------------------------------------
# 4. TestGetInstance
# ------------------------------------------------------------------


class TestGetInstance:
    """Tests for TelemetryStorage.get_instance()."""

    async def test_no_pool_returns_none(self, storage):
        """get_instance() returns None when pool is None."""
        result = await storage.get_instance("inst-001")

        assert result is None

    async def test_instance_not_found_returns_none(self, storage, mock_pool):
        """get_instance() returns None when no row matches."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        result = await storage.get_instance("nonexistent")

        assert result is None
        conn.fetchrow.assert_awaited_once()
        args = conn.fetchrow.call_args[0]
        assert "SELECT * FROM telemetry_instances" in args[0]
        assert args[1] == "nonexistent"

    async def test_instance_found_returns_instance_registration(self, storage, mock_pool, now):
        """get_instance() returns an InstanceRegistration when row exists."""
        pool, conn = mock_pool
        storage._pool = pool

        consent_dict = {"categories": ["performance", "usage"]}
        conn.fetchrow.return_value = {
            "instance_id": "inst-001",
            "api_key_hash": "$2b$12$fakehash",
            "first_seen": now,
            "last_seen": now,
            "current_version": "1.2.3",
            "consent_json": json.dumps(consent_dict),
        }

        result = await storage.get_instance("inst-001")

        assert result is not None
        assert isinstance(result, InstanceRegistration)
        assert result.instance_id == "inst-001"
        assert result.api_key_hash == "$2b$12$fakehash"
        assert result.first_seen == now
        assert result.last_seen == now
        assert result.current_version == "1.2.3"
        assert result.consent.allows("performance")
        assert result.consent.allows("usage")
        assert not result.consent.allows("cost")

    async def test_instance_found_with_empty_consent(self, storage, mock_pool, now):
        """get_instance() handles empty/falsy consent_json gracefully."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "instance_id": "inst-002",
            "api_key_hash": "hash",
            "first_seen": now,
            "last_seen": now,
            "current_version": "0.1.0",
            "consent_json": "",
        }

        result = await storage.get_instance("inst-002")

        assert result is not None
        assert result.consent.categories == set()


# ------------------------------------------------------------------
# 5. TestListInstances
# ------------------------------------------------------------------


class TestListInstances:
    """Tests for TelemetryStorage.list_instances()."""

    async def test_no_pool_returns_empty_list(self, storage):
        """list_instances() returns [] when pool is None."""
        result = await storage.list_instances()

        assert result == []

    async def test_returns_list_of_dicts(self, storage, mock_pool, now):
        """list_instances() converts rows to list of dicts."""
        pool, conn = mock_pool
        storage._pool = pool

        row1 = MagicMock()
        row1.__iter__ = MagicMock(
            return_value=iter([("instance_id", "inst-001"), ("last_seen", now)])
        )
        row1.keys = MagicMock(return_value=["instance_id", "last_seen"])
        row1.__getitem__ = lambda self, k: {"instance_id": "inst-001", "last_seen": now}[k]

        row2 = MagicMock()
        row2.__iter__ = MagicMock(
            return_value=iter([("instance_id", "inst-002"), ("last_seen", now)])
        )
        row2.keys = MagicMock(return_value=["instance_id", "last_seen"])
        row2.__getitem__ = lambda self, k: {"instance_id": "inst-002", "last_seen": now}[k]

        # asyncpg rows support dict() conversion
        conn.fetch.return_value = [
            {"instance_id": "inst-001", "last_seen": now},
            {"instance_id": "inst-002", "last_seen": now},
        ]

        result = await storage.list_instances()

        assert len(result) == 2
        assert result[0]["instance_id"] == "inst-001"
        assert result[1]["instance_id"] == "inst-002"
        conn.fetch.assert_awaited_once()
        sql = conn.fetch.call_args[0][0]
        assert "ORDER BY last_seen DESC" in sql

    async def test_returns_empty_list_when_no_rows(self, storage, mock_pool):
        """list_instances() returns [] when no rows found in DB."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        result = await storage.list_instances()

        assert result == []


# ------------------------------------------------------------------
# 6. TestDeleteInstance
# ------------------------------------------------------------------


class TestDeleteInstance:
    """Tests for TelemetryStorage.delete_instance()."""

    async def test_no_pool_returns_false(self, storage):
        """delete_instance() returns False when pool is None."""
        result = await storage.delete_instance("inst-001")

        assert result is False

    async def test_successful_delete_returns_true(self, storage, mock_pool):
        """delete_instance() returns True when exactly one row is deleted."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 1"

        result = await storage.delete_instance("inst-001")

        assert result is True
        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert "DELETE FROM telemetry_instances" in args[0]
        assert args[1] == "inst-001"

    async def test_instance_not_found_returns_false(self, storage, mock_pool):
        """delete_instance() returns False when no row matches."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_instance("nonexistent")

        assert result is False


# ------------------------------------------------------------------
# 7. TestSaveReport
# ------------------------------------------------------------------


class TestSaveReport:
    """Tests for TelemetryStorage.save_report()."""

    async def test_no_pool_noop(self, storage, sample_report):
        """save_report() is a no-op when pool is None."""
        # Should not raise
        await storage.save_report(sample_report)

    async def test_saves_report_and_touches_instance(self, storage, mock_pool, sample_report):
        """save_report() inserts report and calls _touch_instance."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage.save_report(sample_report)

        # First execute call: INSERT INTO telemetry_reports
        # Second execute call: UPDATE telemetry_instances (from _touch_instance)
        assert conn.execute.await_count == 2

        # Verify the INSERT call
        insert_args = conn.execute.call_args_list[0][0]
        assert "INSERT INTO telemetry_reports" in insert_args[0]
        assert insert_args[1] == "inst-001"
        assert insert_args[2] == datetime.fromisoformat("2026-02-11T12:00:00+00:00")
        assert insert_args[3] == "1.2.3"
        expected_metrics = {
            "performance": {"avg_latency_ms": 42.0, "p99_latency_ms": 120.0},
            "usage": {"messages_processed": 1500},
        }
        assert json.loads(insert_args[4]) == expected_metrics

        # Verify the _touch_instance call (UPDATE)
        touch_args = conn.execute.call_args_list[1][0]
        assert "UPDATE telemetry_instances" in touch_args[0]
        assert touch_args[1] == "inst-001"
        assert touch_args[2] == "1.2.3"

    async def test_report_timestamp_parsed_as_datetime(self, storage, mock_pool, sample_consent):
        """save_report() converts ISO timestamp string to datetime."""
        pool, conn = mock_pool
        storage._pool = pool

        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2026-06-15T08:30:00+00:00",
            version="2.0.0",
            consent=sample_consent,
            metrics={},
        )

        await storage.save_report(report)

        insert_args = conn.execute.call_args_list[0][0]
        parsed_ts = insert_args[2]
        assert isinstance(parsed_ts, datetime)
        assert parsed_ts == datetime.fromisoformat("2026-06-15T08:30:00+00:00")


# ------------------------------------------------------------------
# 8. TestGetReports
# ------------------------------------------------------------------


class TestGetReports:
    """Tests for TelemetryStorage.get_reports()."""

    async def test_no_pool_returns_empty_list(self, storage):
        """get_reports() returns [] when pool is None."""
        result = await storage.get_reports()

        assert result == []

    async def test_no_filters_returns_all(self, storage, mock_pool, now):
        """get_reports() without filters returns all rows up to limit."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "instance_id": "inst-001", "timestamp": now, "version": "1.0.0"},
            {"id": 2, "instance_id": "inst-002", "timestamp": now, "version": "1.1.0"},
        ]

        result = await storage.get_reports()

        assert len(result) == 2
        conn.fetch.assert_awaited_once()
        args = conn.fetch.call_args
        sql = args[0][0]
        # No WHERE clause when no filters
        assert "WHERE" not in sql
        assert "ORDER BY timestamp DESC" in sql
        assert "LIMIT $1" in sql
        # Default limit of 100
        assert args[0][1] == 100

    async def test_with_instance_id_filter(self, storage, mock_pool, now):
        """get_reports() with instance_id applies WHERE instance_id = $1."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "instance_id": "inst-001", "timestamp": now},
        ]

        result = await storage.get_reports(instance_id="inst-001")

        assert len(result) == 1
        args = conn.fetch.call_args
        sql = args[0][0]
        assert "WHERE" in sql
        assert "instance_id = $1" in sql
        assert args[0][1] == "inst-001"
        # limit is $2 when instance_id filter used
        assert "LIMIT $2" in sql
        assert args[0][2] == 100

    async def test_with_since_filter(self, storage, mock_pool, now):
        """get_reports() with since applies WHERE timestamp >= $1."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = []
        since_dt = datetime(2026, 1, 1, tzinfo=UTC)

        result = await storage.get_reports(since=since_dt)

        assert result == []
        args = conn.fetch.call_args
        sql = args[0][0]
        assert "WHERE" in sql
        assert "timestamp >= $1" in sql
        assert args[0][1] == since_dt
        assert "LIMIT $2" in sql
        assert args[0][2] == 100

    async def test_with_both_filters(self, storage, mock_pool, now):
        """get_reports() with both instance_id and since applies both WHERE clauses."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 5, "instance_id": "inst-001", "timestamp": now},
        ]
        since_dt = datetime(2026, 2, 1, tzinfo=UTC)

        result = await storage.get_reports(instance_id="inst-001", since=since_dt)

        assert len(result) == 1
        args = conn.fetch.call_args
        sql = args[0][0]
        assert "WHERE" in sql
        assert "instance_id = $1" in sql
        assert "timestamp >= $2" in sql
        assert "AND" in sql
        assert args[0][1] == "inst-001"
        assert args[0][2] == since_dt
        assert "LIMIT $3" in sql
        assert args[0][3] == 100

    async def test_custom_limit(self, storage, mock_pool):
        """get_reports() respects a custom limit argument."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        await storage.get_reports(limit=10)

        args = conn.fetch.call_args
        # limit is the last positional param
        assert args[0][-1] == 10

    async def test_returns_dicts(self, storage, mock_pool, now):
        """get_reports() converts rows to dicts."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "instance_id": "inst-001", "timestamp": now, "version": "1.0.0"},
        ]

        result = await storage.get_reports()

        assert isinstance(result, list)
        assert isinstance(result[0], dict)
        assert result[0]["instance_id"] == "inst-001"


# ------------------------------------------------------------------
# 9. TestSaveAggregate
# ------------------------------------------------------------------


class TestSaveAggregate:
    """Tests for TelemetryStorage.save_aggregate()."""

    async def test_no_pool_noop(self, storage, now):
        """save_aggregate() is a no-op when pool is None."""
        await storage.save_aggregate(
            period_start=now,
            period_end=now,
            metric_name="test",
            aggregation={"count": 1},
        )
        # No exception = success

    async def test_saves_aggregate(self, storage, mock_pool, now):
        """save_aggregate() inserts with correct parameters."""
        pool, conn = mock_pool
        storage._pool = pool

        start = datetime(2026, 2, 10, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 11, 0, 0, 0, tzinfo=UTC)
        aggregation = {"avg_latency_ms": 55.3, "total_requests": 12000}

        await storage.save_aggregate(
            period_start=start,
            period_end=end,
            metric_name="performance",
            aggregation=aggregation,
        )

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        sql = args[0]
        assert "INSERT INTO fleet_aggregates" in sql
        assert args[1] == start
        assert args[2] == end
        assert args[3] == "performance"
        assert json.loads(args[4]) == aggregation


# ------------------------------------------------------------------
# 10. TestGetAggregates
# ------------------------------------------------------------------


class TestGetAggregates:
    """Tests for TelemetryStorage.get_aggregates()."""

    async def test_no_pool_returns_empty_list(self, storage):
        """get_aggregates() returns [] when pool is None."""
        result = await storage.get_aggregates()

        assert result == []

    async def test_without_metric_name_filter(self, storage, mock_pool, now):
        """get_aggregates() without metric_name returns all rows."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "metric_name": "performance", "period_start": now},
            {"id": 2, "metric_name": "usage", "period_start": now},
        ]

        result = await storage.get_aggregates()

        assert len(result) == 2
        conn.fetch.assert_awaited_once()
        args = conn.fetch.call_args
        sql = args[0][0]
        assert "WHERE" not in sql
        assert "ORDER BY period_start DESC" in sql
        assert "LIMIT $1" in sql
        # Default limit of 50
        assert args[0][1] == 50

    async def test_with_metric_name_filter(self, storage, mock_pool, now):
        """get_aggregates() with metric_name applies WHERE clause."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "metric_name": "performance", "period_start": now},
        ]

        result = await storage.get_aggregates(metric_name="performance")

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        args = conn.fetch.call_args
        sql = args[0][0]
        assert "WHERE metric_name = $1" in sql
        assert "LIMIT $2" in sql
        assert args[0][1] == "performance"
        assert args[0][2] == 50

    async def test_custom_limit(self, storage, mock_pool):
        """get_aggregates() respects a custom limit argument."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        await storage.get_aggregates(limit=10)

        args = conn.fetch.call_args
        assert args[0][-1] == 10

    async def test_returns_dicts(self, storage, mock_pool, now):
        """get_aggregates() converts rows to dicts."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {"id": 1, "metric_name": "health", "aggregation_json": "{}"},
        ]

        result = await storage.get_aggregates()

        assert isinstance(result[0], dict)
        assert result[0]["metric_name"] == "health"


# ------------------------------------------------------------------
# 11. TestTouchInstance
# ------------------------------------------------------------------


class TestTouchInstance:
    """Tests for TelemetryStorage._touch_instance()."""

    async def test_no_pool_noop(self, storage):
        """_touch_instance() is a no-op when pool is None."""
        await storage._touch_instance("inst-001", "1.0.0")
        # No exception = success

    async def test_updates_last_seen(self, storage, mock_pool):
        """_touch_instance() executes UPDATE with correct parameters."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage._touch_instance("inst-001", "2.0.0")

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        sql = args[0]
        assert "UPDATE telemetry_instances" in sql
        assert "last_seen = NOW()" in sql
        assert "current_version = $2" in sql
        assert "WHERE instance_id = $1" in sql
        assert args[1] == "inst-001"
        assert args[2] == "2.0.0"
