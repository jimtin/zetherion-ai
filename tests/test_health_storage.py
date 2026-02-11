"""Comprehensive unit tests for HealthStorage and its data models.

All database interactions are mocked via AsyncMock so no real PostgreSQL
connection is needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.health.storage import (
    DailyReport,
    HealingAction,
    HealthStorage,
    Incident,
    IncidentSeverity,
    MetricsSnapshot,
    UpdateRecord,
    UpdateStatus,
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
    """Return an uninitialised HealthStorage instance."""
    return HealthStorage()


@pytest.fixture
def now():
    """A deterministic UTC timestamp for tests."""
    return datetime(2026, 2, 11, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_metrics():
    """Sample metrics dict with nested structure."""
    return {
        "cpu_percent": 42.5,
        "memory_mb": 1024,
        "disk_io": {"read_bytes": 1000, "write_bytes": 2000},
    }


@pytest.fixture
def sample_anomalies():
    """Sample anomalies dict."""
    return {"cpu_spike": {"value": 98.2, "threshold": 90.0}}


# ------------------------------------------------------------------
# 1. Table creation via initialize()
# ------------------------------------------------------------------


class TestInitialize:
    """Tests for HealthStorage.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_stores_pool_and_creates_schema(self, storage, mock_pool):
        """initialize() stores the pool reference and executes the schema DDL."""
        pool, conn = mock_pool

        await storage.initialize(pool)

        assert storage._pool is pool
        conn.execute.assert_awaited_once()
        # The argument should contain our table-creation SQL
        schema_sql = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS health_snapshots" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS health_daily_reports" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS health_healing_actions" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS health_incidents" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS update_history" in schema_sql


# ------------------------------------------------------------------
# 2. Snapshot insert and retrieval
# ------------------------------------------------------------------


class TestSnapshots:
    """Tests for snapshot save / get methods."""

    @pytest.mark.asyncio
    async def test_save_snapshot_returns_id(
        self, storage, mock_pool, now, sample_metrics, sample_anomalies
    ):
        """save_snapshot() INSERTs and returns the row id."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 7}

        snapshot = MetricsSnapshot(
            timestamp=now, metrics=sample_metrics, anomalies=sample_anomalies
        )
        result = await storage.save_snapshot(snapshot)

        assert result == 7
        conn.fetchrow.assert_awaited_once()
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO health_snapshots" in args[0]
        assert args[1] is now
        assert json.loads(args[2]) == sample_metrics
        assert json.loads(args[3]) == sample_anomalies

    @pytest.mark.asyncio
    async def test_get_snapshots_returns_list(
        self, storage, mock_pool, now, sample_metrics, sample_anomalies
    ):
        """get_snapshots() deserialises JSONB columns."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 1,
                "timestamp": now,
                "metrics": json.dumps(sample_metrics),
                "anomalies": json.dumps(sample_anomalies),
            },
            {
                "id": 2,
                "timestamp": now,
                "metrics": json.dumps({"cpu_percent": 10.0}),
                "anomalies": json.dumps({}),
            },
        ]

        start = datetime(2026, 2, 11, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 11, 23, 59, 59, tzinfo=UTC)

        results = await storage.get_snapshots(start, end, limit=100)

        assert len(results) == 2
        assert results[0].id == 1
        assert results[0].metrics == sample_metrics
        assert results[0].anomalies == sample_anomalies
        assert results[1].metrics == {"cpu_percent": 10.0}

    @pytest.mark.asyncio
    async def test_get_snapshots_empty(self, storage, mock_pool):
        """get_snapshots() returns empty list when no rows."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        results = await storage.get_snapshots(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_get_latest_snapshot(self, storage, mock_pool, now, sample_metrics):
        """get_latest_snapshot() returns a single MetricsSnapshot."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "id": 42,
            "timestamp": now,
            "metrics": json.dumps(sample_metrics),
            "anomalies": json.dumps({}),
        }

        result = await storage.get_latest_snapshot()

        assert result is not None
        assert isinstance(result, MetricsSnapshot)
        assert result.id == 42
        assert result.timestamp == now
        assert result.metrics == sample_metrics
        assert result.anomalies == {}

    @pytest.mark.asyncio
    async def test_get_latest_snapshot_none(self, storage, mock_pool):
        """get_latest_snapshot() returns None when table is empty."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        result = await storage.get_latest_snapshot()

        assert result is None


# ------------------------------------------------------------------
# 3. Daily report upsert
# ------------------------------------------------------------------


class TestDailyReports:
    """Tests for daily report save / get methods."""

    @pytest.mark.asyncio
    async def test_save_daily_report_upsert(self, storage, mock_pool):
        """save_daily_report() executes an INSERT ... ON CONFLICT upsert."""
        pool, conn = mock_pool
        storage._pool = pool

        report = DailyReport(
            date="2026-02-11",
            summary={"uptime": "99.9%", "errors": 0},
            recommendations={"action": "none needed"},
            overall_score=95.5,
        )

        await storage.save_daily_report(report)

        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO health_daily_reports" in sql
        assert "ON CONFLICT (date) DO UPDATE" in sql
        assert conn.execute.call_args[0][1] == "2026-02-11"
        assert json.loads(conn.execute.call_args[0][2]) == report.summary
        assert json.loads(conn.execute.call_args[0][3]) == report.recommendations
        assert conn.execute.call_args[0][4] == 95.5

    @pytest.mark.asyncio
    async def test_save_daily_report_update_existing(self, storage, mock_pool):
        """save_daily_report() with same date should invoke the upsert path."""
        pool, conn = mock_pool
        storage._pool = pool

        # Save twice for the same date
        report_v1 = DailyReport(
            date="2026-02-11",
            summary={"errors": 3},
            recommendations={},
            overall_score=70.0,
        )
        report_v2 = DailyReport(
            date="2026-02-11",
            summary={"errors": 0},
            recommendations={"action": "keep it up"},
            overall_score=99.0,
        )

        await storage.save_daily_report(report_v1)
        await storage.save_daily_report(report_v2)

        assert conn.execute.await_count == 2
        # Both calls use the same upsert SQL
        for call in conn.execute.call_args_list:
            assert "ON CONFLICT (date) DO UPDATE" in call[0][0]

    @pytest.mark.asyncio
    async def test_get_daily_report_found(self, storage, mock_pool):
        """get_daily_report() returns a DailyReport when the row exists."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "id": 10,
            "date": "2026-02-11",
            "summary": json.dumps({"uptime": "99.9%"}),
            "recommendations": json.dumps({"action": "none"}),
            "overall_score": 95.5,
        }

        result = await storage.get_daily_report("2026-02-11")

        assert result is not None
        assert isinstance(result, DailyReport)
        assert result.id == 10
        assert result.date == "2026-02-11"
        assert result.summary == {"uptime": "99.9%"}
        assert result.recommendations == {"action": "none"}
        assert result.overall_score == 95.5

    @pytest.mark.asyncio
    async def test_get_daily_report_not_found(self, storage, mock_pool):
        """get_daily_report() returns None when no row matches."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        result = await storage.get_daily_report("1999-01-01")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_daily_reports_range(self, storage, mock_pool):
        """get_daily_reports() returns a list of DailyReport objects."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 1,
                "date": "2026-02-11",
                "summary": json.dumps({"day": "good"}),
                "recommendations": json.dumps({}),
                "overall_score": 90.0,
            },
            {
                "id": 2,
                "date": "2026-02-10",
                "summary": json.dumps({"day": "ok"}),
                "recommendations": json.dumps({"tip": "restart"}),
                "overall_score": 75.0,
            },
        ]

        results = await storage.get_daily_reports("2026-02-10", "2026-02-11")

        assert len(results) == 2
        assert results[0].date == "2026-02-11"
        assert results[1].overall_score == 75.0


# ------------------------------------------------------------------
# 4. Healing action logging and querying
# ------------------------------------------------------------------


class TestHealingActions:
    """Tests for healing action save / get methods."""

    @pytest.mark.asyncio
    async def test_save_healing_action_returns_id(self, storage, mock_pool, now):
        """save_healing_action() inserts and returns the row id."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 33}

        action = HealingAction(
            timestamp=now,
            action_type="restart_service",
            trigger="high_cpu",
            result="success",
            details={"service": "ollama", "duration_s": 2.3},
        )

        result = await storage.save_healing_action(action)

        assert result == 33
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO health_healing_actions" in args[0]
        assert args[1] is now
        assert args[2] == "restart_service"
        assert args[3] == "high_cpu"
        assert args[4] == "success"
        assert json.loads(args[5]) == {"service": "ollama", "duration_s": 2.3}

    @pytest.mark.asyncio
    async def test_get_healing_actions(self, storage, mock_pool, now):
        """get_healing_actions() returns deserialised HealingAction list."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 1,
                "timestamp": now,
                "action_type": "restart_service",
                "trigger": "oom",
                "result": "success",
                "details": json.dumps({"mem_mb": 8192}),
            },
        ]

        start = datetime(2026, 2, 11, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 11, 23, 59, 59, tzinfo=UTC)
        results = await storage.get_healing_actions(start, end, limit=50)

        assert len(results) == 1
        assert results[0].action_type == "restart_service"
        assert results[0].details == {"mem_mb": 8192}


# ------------------------------------------------------------------
# 5. Recent healing action cooldown check
# ------------------------------------------------------------------


class TestRecentHealingAction:
    """Tests for the cooldown-check method get_recent_healing_action()."""

    @pytest.mark.asyncio
    async def test_recent_action_found(self, storage, mock_pool, now):
        """Returns a HealingAction when one exists within the window."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "id": 5,
            "timestamp": now,
            "action_type": "restart_service",
            "trigger": "high_cpu",
            "result": "success",
            "details": json.dumps({}),
        }

        result = await storage.get_recent_healing_action("restart_service", within_seconds=300)

        assert result is not None
        assert result.action_type == "restart_service"
        assert result.id == 5
        # Verify query parameters
        args = conn.fetchrow.call_args[0]
        assert args[1] == "restart_service"
        assert args[2] == 300

    @pytest.mark.asyncio
    async def test_recent_action_not_found(self, storage, mock_pool):
        """Returns None when no action within the cooldown window."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        result = await storage.get_recent_healing_action("restart_service", within_seconds=60)

        assert result is None

    @pytest.mark.asyncio
    async def test_recent_action_default_window(self, storage, mock_pool):
        """Uses default 300-second window when within_seconds not specified."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        await storage.get_recent_healing_action("some_action")

        args = conn.fetchrow.call_args[0]
        assert args[2] == 300


# ------------------------------------------------------------------
# 6. Incident creation, resolution, querying
# ------------------------------------------------------------------


class TestIncidents:
    """Tests for incident CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_incident_returns_id(self, storage, mock_pool, now):
        """create_incident() inserts and returns the row id."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 99}

        incident = Incident(
            start_time=now,
            severity=IncidentSeverity.HIGH,
            description="Database connection pool exhausted",
        )

        result = await storage.create_incident(incident)

        assert result == 99
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO health_incidents" in args[0]
        assert args[1] is now
        assert args[2] == "high"  # enum .value
        assert args[3] == "Database connection pool exhausted"
        assert args[4] is False  # default resolved

    @pytest.mark.asyncio
    async def test_resolve_incident(self, storage, mock_pool):
        """resolve_incident() sets resolved=TRUE, end_time, and resolution."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage.resolve_incident(99, "Restarted connection pool")

        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "UPDATE health_incidents" in sql
        assert "resolved = TRUE" in sql
        assert "end_time = NOW()" in sql
        assert conn.execute.call_args[0][1] == "Restarted connection pool"
        assert conn.execute.call_args[0][2] == 99

    @pytest.mark.asyncio
    async def test_get_open_incidents(self, storage, mock_pool, now):
        """get_open_incidents() returns only unresolved incidents."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 10,
                "start_time": now,
                "end_time": None,
                "severity": "critical",
                "description": "Service down",
                "resolved": False,
                "resolution": None,
            },
            {
                "id": 11,
                "start_time": now,
                "end_time": None,
                "severity": "low",
                "description": "Slow response",
                "resolved": False,
                "resolution": None,
            },
        ]

        results = await storage.get_open_incidents()

        assert len(results) == 2
        assert results[0].severity == IncidentSeverity.CRITICAL
        assert results[0].resolved is False
        assert results[0].end_time is None
        assert results[1].severity == IncidentSeverity.LOW

    @pytest.mark.asyncio
    async def test_get_open_incidents_empty(self, storage, mock_pool):
        """get_open_incidents() returns empty list when all resolved."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        results = await storage.get_open_incidents()
        assert results == []


# ------------------------------------------------------------------
# 7. Update history insert and retrieval
# ------------------------------------------------------------------


class TestUpdateHistory:
    """Tests for update record save / get methods."""

    @pytest.mark.asyncio
    async def test_save_update_record_returns_id(self, storage, mock_pool, now):
        """save_update_record() inserts and returns the row id."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 5}

        record = UpdateRecord(
            timestamp=now,
            version="2.1.0",
            previous_version="2.0.0",
            git_sha="abc123def",
            status=UpdateStatus.APPLYING,
            health_check_result={"passed": True},
        )

        result = await storage.save_update_record(record)

        assert result == 5
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO update_history" in args[0]
        assert args[1] is now
        assert args[2] == "2.1.0"
        assert args[3] == "2.0.0"
        assert args[4] == "abc123def"
        assert args[5] == "applying"  # enum .value
        assert json.loads(args[6]) == {"passed": True}

    @pytest.mark.asyncio
    async def test_update_update_status_with_health_check(self, storage, mock_pool):
        """update_update_status() with health_check_result updates both columns."""
        pool, conn = mock_pool
        storage._pool = pool

        health_result = {"all_services": "healthy", "latency_ms": 12}
        await storage.update_update_status(
            record_id=5,
            status=UpdateStatus.SUCCESS,
            health_check_result=health_result,
        )

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert "status = $1" in args[0]
        assert "health_check_result = $2" in args[0]
        assert args[1] == "success"
        assert json.loads(args[2]) == health_result
        assert args[3] == 5

    @pytest.mark.asyncio
    async def test_update_update_status_without_health_check(self, storage, mock_pool):
        """update_update_status() without health_check_result updates only status."""
        pool, conn = mock_pool
        storage._pool = pool

        await storage.update_update_status(
            record_id=5,
            status=UpdateStatus.FAILED,
        )

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert "status = $1" in args[0]
        assert "health_check_result" not in args[0]
        assert args[1] == "failed"
        assert args[2] == 5

    @pytest.mark.asyncio
    async def test_get_latest_update_found(self, storage, mock_pool, now):
        """get_latest_update() returns an UpdateRecord."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "id": 3,
            "timestamp": now,
            "version": "2.1.0",
            "previous_version": "2.0.0",
            "git_sha": "abc123",
            "status": "success",
            "health_check_result": json.dumps({"ok": True}),
        }

        result = await storage.get_latest_update()

        assert result is not None
        assert isinstance(result, UpdateRecord)
        assert result.id == 3
        assert result.version == "2.1.0"
        assert result.status == UpdateStatus.SUCCESS
        assert result.health_check_result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_latest_update_none(self, storage, mock_pool):
        """get_latest_update() returns None when no updates exist."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = None

        result = await storage.get_latest_update()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_update_history(self, storage, mock_pool, now):
        """get_update_history() returns a list of UpdateRecord."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 3,
                "timestamp": now,
                "version": "2.1.0",
                "previous_version": "2.0.0",
                "git_sha": "abc123",
                "status": "success",
                "health_check_result": json.dumps({}),
            },
            {
                "id": 2,
                "timestamp": now,
                "version": "2.0.0",
                "previous_version": "1.9.0",
                "git_sha": "def456",
                "status": "rolled_back",
                "health_check_result": json.dumps({"error": "timeout"}),
            },
        ]

        results = await storage.get_update_history(limit=10)

        assert len(results) == 2
        assert results[0].status == UpdateStatus.SUCCESS
        assert results[1].status == UpdateStatus.ROLLED_BACK
        assert results[1].health_check_result == {"error": "timeout"}
        # Verify limit was passed
        args = conn.fetch.call_args[0]
        assert args[1] == 10

    @pytest.mark.asyncio
    async def test_get_update_history_default_limit(self, storage, mock_pool):
        """get_update_history() uses default limit of 20."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        await storage.get_update_history()

        args = conn.fetch.call_args[0]
        assert args[1] == 20


# ------------------------------------------------------------------
# 8. JSONB column serialization for metrics/anomalies
# ------------------------------------------------------------------


class TestJSONBSerialization:
    """Tests that JSONB columns are properly serialized/deserialized."""

    @pytest.mark.asyncio
    async def test_nested_metrics_json_roundtrip(self, storage, mock_pool, now):
        """Deeply nested metrics are serialized to JSON and deserialized back."""
        pool, conn = mock_pool
        storage._pool = pool

        deep_metrics = {
            "system": {
                "cpu": {"cores": [10.5, 20.3, 5.1, 80.0]},
                "memory": {"used_mb": 4096, "total_mb": 16384},
            },
            "services": [
                {"name": "ollama", "healthy": True},
                {"name": "qdrant", "healthy": False, "error": "timeout"},
            ],
        }
        conn.fetchrow.return_value = {"id": 1}

        snapshot = MetricsSnapshot(timestamp=now, metrics=deep_metrics)
        await storage.save_snapshot(snapshot)

        # Verify the serialized JSON string was passed to the query
        serialized = conn.fetchrow.call_args[0][2]
        assert json.loads(serialized) == deep_metrics

    @pytest.mark.asyncio
    async def test_empty_anomalies_serialization(self, storage, mock_pool, now):
        """Empty dict anomalies are properly serialized."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 1}

        snapshot = MetricsSnapshot(timestamp=now, metrics={"cpu": 50.0})
        await storage.save_snapshot(snapshot)

        serialized_anomalies = conn.fetchrow.call_args[0][3]
        assert json.loads(serialized_anomalies) == {}

    @pytest.mark.asyncio
    async def test_healing_action_details_json(self, storage, mock_pool, now):
        """HealingAction details dict is serialized as JSON for storage."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 1}

        details = {"restart_count": 3, "services": ["bot", "ollama"]}
        action = HealingAction(
            timestamp=now,
            action_type="restart",
            trigger="crash",
            result="success",
            details=details,
        )
        await storage.save_healing_action(action)

        serialized = conn.fetchrow.call_args[0][5]
        assert json.loads(serialized) == details

    @pytest.mark.asyncio
    async def test_update_record_health_check_json(self, storage, mock_pool, now):
        """UpdateRecord health_check_result is serialized as JSON."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 1}

        health_check = {
            "services": {"bot": "ok", "qdrant": "ok"},
            "response_time_ms": 45,
        }
        record = UpdateRecord(
            timestamp=now,
            version="3.0.0",
            previous_version="2.9.0",
            git_sha="aaa111",
            status=UpdateStatus.VALIDATING,
            health_check_result=health_check,
        )
        await storage.save_update_record(record)

        serialized = conn.fetchrow.call_args[0][6]
        assert json.loads(serialized) == health_check


# ------------------------------------------------------------------
# 9. Data model .to_dict() and .from_dict() round-trips
# ------------------------------------------------------------------


class TestDataModelSerialization:
    """Tests for to_dict() / from_dict() on data models."""

    def test_metrics_snapshot_to_dict(self, now, sample_metrics, sample_anomalies):
        """MetricsSnapshot.to_dict() includes all fields."""
        snap = MetricsSnapshot(
            id=1,
            timestamp=now,
            metrics=sample_metrics,
            anomalies=sample_anomalies,
        )
        d = snap.to_dict()

        assert d["id"] == 1
        assert d["timestamp"] == now.isoformat()
        assert d["metrics"] == sample_metrics
        assert d["anomalies"] == sample_anomalies

    def test_metrics_snapshot_from_dict_with_string_timestamp(self, now, sample_metrics):
        """MetricsSnapshot.from_dict() parses ISO string timestamps."""
        data = {
            "id": 2,
            "timestamp": now.isoformat(),
            "metrics": sample_metrics,
            "anomalies": {},
        }
        snap = MetricsSnapshot.from_dict(data)

        assert snap.id == 2
        assert snap.timestamp == now
        assert snap.metrics == sample_metrics
        assert snap.anomalies == {}

    def test_metrics_snapshot_from_dict_with_datetime_timestamp(self, now, sample_metrics):
        """MetricsSnapshot.from_dict() accepts datetime objects directly."""
        data = {
            "id": 3,
            "timestamp": now,
            "metrics": sample_metrics,
            "anomalies": {"spike": True},
        }
        snap = MetricsSnapshot.from_dict(data)

        assert snap.timestamp is now
        assert snap.anomalies == {"spike": True}

    def test_metrics_snapshot_roundtrip(self, now, sample_metrics, sample_anomalies):
        """MetricsSnapshot survives a to_dict -> from_dict round-trip."""
        original = MetricsSnapshot(
            id=5,
            timestamp=now,
            metrics=sample_metrics,
            anomalies=sample_anomalies,
        )
        restored = MetricsSnapshot.from_dict(original.to_dict())

        assert restored.id == original.id
        assert restored.timestamp == original.timestamp
        assert restored.metrics == original.metrics
        assert restored.anomalies == original.anomalies

    def test_metrics_snapshot_from_dict_missing_optional_fields(self, now):
        """MetricsSnapshot.from_dict() defaults missing optional fields."""
        data = {"timestamp": now, "metrics": {"cpu": 50.0}}
        snap = MetricsSnapshot.from_dict(data)

        assert snap.id is None
        assert snap.anomalies == {}

    def test_daily_report_to_dict(self):
        """DailyReport.to_dict() includes all fields."""
        report = DailyReport(
            id=10,
            date="2026-02-11",
            summary={"errors": 0},
            recommendations={"tip": "all good"},
            overall_score=98.0,
        )
        d = report.to_dict()

        assert d["id"] == 10
        assert d["date"] == "2026-02-11"
        assert d["summary"] == {"errors": 0}
        assert d["recommendations"] == {"tip": "all good"}
        assert d["overall_score"] == 98.0

    def test_healing_action_to_dict(self, now):
        """HealingAction.to_dict() includes all fields."""
        action = HealingAction(
            id=7,
            timestamp=now,
            action_type="scale_up",
            trigger="high_load",
            result="success",
            details={"replicas": 3},
        )
        d = action.to_dict()

        assert d["id"] == 7
        assert d["timestamp"] == now.isoformat()
        assert d["action_type"] == "scale_up"
        assert d["trigger"] == "high_load"
        assert d["result"] == "success"
        assert d["details"] == {"replicas": 3}

    def test_incident_to_dict_unresolved(self, now):
        """Incident.to_dict() with no end_time sets end_time to None."""
        incident = Incident(
            id=1,
            start_time=now,
            severity=IncidentSeverity.MEDIUM,
            description="Degraded latency",
        )
        d = incident.to_dict()

        assert d["id"] == 1
        assert d["start_time"] == now.isoformat()
        assert d["end_time"] is None
        assert d["severity"] == "medium"
        assert d["description"] == "Degraded latency"
        assert d["resolved"] is False
        assert d["resolution"] is None

    def test_incident_to_dict_resolved(self, now):
        """Incident.to_dict() with end_time includes it as ISO string."""
        end = datetime(2026, 2, 11, 13, 0, 0, tzinfo=UTC)
        incident = Incident(
            id=2,
            start_time=now,
            severity=IncidentSeverity.CRITICAL,
            description="Total outage",
            end_time=end,
            resolved=True,
            resolution="Failover to backup",
        )
        d = incident.to_dict()

        assert d["end_time"] == end.isoformat()
        assert d["severity"] == "critical"
        assert d["resolved"] is True
        assert d["resolution"] == "Failover to backup"

    def test_update_record_to_dict(self, now):
        """UpdateRecord.to_dict() includes all fields with enum value."""
        record = UpdateRecord(
            id=4,
            timestamp=now,
            version="3.0.0",
            previous_version="2.9.0",
            git_sha="deadbeef",
            status=UpdateStatus.ROLLED_BACK,
            health_check_result={"error": "validation failed"},
        )
        d = record.to_dict()

        assert d["id"] == 4
        assert d["timestamp"] == now.isoformat()
        assert d["version"] == "3.0.0"
        assert d["previous_version"] == "2.9.0"
        assert d["git_sha"] == "deadbeef"
        assert d["status"] == "rolled_back"
        assert d["health_check_result"] == {"error": "validation failed"}


# ------------------------------------------------------------------
# 10. Enum serialization/deserialization
# ------------------------------------------------------------------


class TestEnumSerialization:
    """Tests for IncidentSeverity and UpdateStatus enums."""

    def test_incident_severity_values(self):
        """IncidentSeverity has all expected members."""
        assert IncidentSeverity.LOW.value == "low"
        assert IncidentSeverity.MEDIUM.value == "medium"
        assert IncidentSeverity.HIGH.value == "high"
        assert IncidentSeverity.CRITICAL.value == "critical"

    def test_incident_severity_from_string(self):
        """IncidentSeverity can be constructed from string values."""
        assert IncidentSeverity("low") == IncidentSeverity.LOW
        assert IncidentSeverity("critical") == IncidentSeverity.CRITICAL

    def test_incident_severity_invalid(self):
        """IncidentSeverity raises ValueError for unknown string."""
        with pytest.raises(ValueError):
            IncidentSeverity("unknown")

    def test_update_status_values(self):
        """UpdateStatus has all expected members."""
        assert UpdateStatus.CHECKING.value == "checking"
        assert UpdateStatus.DOWNLOADING.value == "downloading"
        assert UpdateStatus.APPLYING.value == "applying"
        assert UpdateStatus.VALIDATING.value == "validating"
        assert UpdateStatus.SUCCESS.value == "success"
        assert UpdateStatus.FAILED.value == "failed"
        assert UpdateStatus.ROLLED_BACK.value == "rolled_back"

    def test_update_status_from_string(self):
        """UpdateStatus can be constructed from string values."""
        assert UpdateStatus("checking") == UpdateStatus.CHECKING
        assert UpdateStatus("rolled_back") == UpdateStatus.ROLLED_BACK

    def test_update_status_invalid(self):
        """UpdateStatus raises ValueError for unknown string."""
        with pytest.raises(ValueError):
            UpdateStatus("pending")

    @pytest.mark.asyncio
    async def test_incident_severity_stored_as_value(self, storage, mock_pool, now):
        """create_incident() stores severity as its string .value."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 1}

        incident = Incident(
            start_time=now,
            severity=IncidentSeverity.CRITICAL,
            description="test",
        )
        await storage.create_incident(incident)

        args = conn.fetchrow.call_args[0]
        assert args[2] == "critical"

    @pytest.mark.asyncio
    async def test_incident_severity_deserialised_from_db(self, storage, mock_pool, now):
        """get_open_incidents() reconstructs IncidentSeverity from string."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetch.return_value = [
            {
                "id": 1,
                "start_time": now,
                "end_time": None,
                "severity": "high",
                "description": "test",
                "resolved": False,
                "resolution": None,
            },
        ]

        results = await storage.get_open_incidents()
        assert results[0].severity == IncidentSeverity.HIGH

    @pytest.mark.asyncio
    async def test_update_status_stored_as_value(self, storage, mock_pool, now):
        """save_update_record() stores status as its string .value."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetchrow.return_value = {"id": 1}

        record = UpdateRecord(
            timestamp=now,
            version="1.0.0",
            previous_version="0.9.0",
            git_sha="aaa",
            status=UpdateStatus.DOWNLOADING,
        )
        await storage.save_update_record(record)

        args = conn.fetchrow.call_args[0]
        assert args[5] == "downloading"

    @pytest.mark.asyncio
    async def test_update_status_deserialised_from_db(self, storage, mock_pool, now):
        """get_latest_update() reconstructs UpdateStatus from string."""
        pool, conn = mock_pool
        storage._pool = pool

        conn.fetchrow.return_value = {
            "id": 1,
            "timestamp": now,
            "version": "1.0.0",
            "previous_version": "0.9.0",
            "git_sha": "bbb",
            "status": "failed",
            "health_check_result": json.dumps({}),
        }

        result = await storage.get_latest_update()
        assert result is not None
        assert result.status == UpdateStatus.FAILED


# ------------------------------------------------------------------
# 11. prune_old_snapshots returns count
# ------------------------------------------------------------------


class TestPruneOldSnapshots:
    """Tests for the maintenance method prune_old_snapshots()."""

    @pytest.mark.asyncio
    async def test_prune_returns_delete_count(self, storage, mock_pool):
        """prune_old_snapshots() parses the DELETE result string for count."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 15"

        count = await storage.prune_old_snapshots(days=30)

        assert count == 15
        args = conn.execute.call_args[0]
        assert "DELETE FROM health_snapshots" in args[0]
        assert args[1] == 30

    @pytest.mark.asyncio
    async def test_prune_zero_rows(self, storage, mock_pool):
        """prune_old_snapshots() returns 0 when no old rows exist."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 0"

        count = await storage.prune_old_snapshots(days=7)

        assert count == 0

    @pytest.mark.asyncio
    async def test_prune_default_days(self, storage, mock_pool):
        """prune_old_snapshots() defaults to 30 days."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 0"

        await storage.prune_old_snapshots()

        args = conn.execute.call_args[0]
        assert args[1] == 30

    @pytest.mark.asyncio
    async def test_prune_empty_result(self, storage, mock_pool):
        """prune_old_snapshots() returns 0 when execute returns empty string."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = ""

        count = await storage.prune_old_snapshots(days=1)

        assert count == 0

    @pytest.mark.asyncio
    async def test_prune_custom_days(self, storage, mock_pool):
        """prune_old_snapshots() passes custom days value to query."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.execute.return_value = "DELETE 100"

        count = await storage.prune_old_snapshots(days=90)

        assert count == 100
        args = conn.execute.call_args[0]
        assert args[1] == 90


# ------------------------------------------------------------------
# 12. Edge cases and defaults
# ------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge-case tests."""

    def test_health_storage_initial_state(self):
        """HealthStorage starts with _pool = None."""
        s = HealthStorage()
        assert s._pool is None

    def test_metrics_snapshot_default_anomalies(self, now):
        """MetricsSnapshot defaults anomalies to empty dict."""
        snap = MetricsSnapshot(timestamp=now, metrics={"cpu": 50.0})
        assert snap.anomalies == {}
        assert snap.id is None

    def test_healing_action_default_details(self, now):
        """HealingAction defaults details to empty dict."""
        action = HealingAction(
            timestamp=now,
            action_type="test",
            trigger="test",
            result="success",
        )
        assert action.details == {}
        assert action.id is None

    def test_update_record_default_health_check(self, now):
        """UpdateRecord defaults health_check_result to empty dict."""
        record = UpdateRecord(
            timestamp=now,
            version="1.0.0",
            previous_version="0.9.0",
            git_sha="abc",
            status=UpdateStatus.CHECKING,
        )
        assert record.health_check_result == {}
        assert record.id is None

    def test_incident_defaults(self, now):
        """Incident defaults to unresolved with no end_time."""
        incident = Incident(
            start_time=now,
            severity=IncidentSeverity.LOW,
            description="test",
        )
        assert incident.end_time is None
        assert incident.resolved is False
        assert incident.resolution is None
        assert incident.id is None

    @pytest.mark.asyncio
    async def test_get_snapshots_passes_limit(self, storage, mock_pool):
        """get_snapshots() passes the limit argument to the query."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await storage.get_snapshots(start, end, limit=5)

        args = conn.fetch.call_args[0]
        assert args[3] == 5

    @pytest.mark.asyncio
    async def test_get_snapshots_default_limit(self, storage, mock_pool):
        """get_snapshots() uses default limit of 1000."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await storage.get_snapshots(start, end)

        args = conn.fetch.call_args[0]
        assert args[3] == 1000

    @pytest.mark.asyncio
    async def test_get_healing_actions_default_limit(self, storage, mock_pool):
        """get_healing_actions() uses default limit of 100."""
        pool, conn = mock_pool
        storage._pool = pool
        conn.fetch.return_value = []

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await storage.get_healing_actions(start, end)

        args = conn.fetch.call_args[0]
        assert args[3] == 100

    def test_daily_report_to_dict_with_none_id(self):
        """DailyReport.to_dict() includes None id when not set."""
        report = DailyReport(
            date="2026-01-01",
            summary={},
            recommendations={},
            overall_score=0.0,
        )
        d = report.to_dict()
        assert d["id"] is None

    def test_metrics_snapshot_to_dict_with_none_id(self, now):
        """MetricsSnapshot.to_dict() includes None id when not set."""
        snap = MetricsSnapshot(timestamp=now, metrics={})
        d = snap.to_dict()
        assert d["id"] is None

    def test_all_incident_severities_in_to_dict(self, now):
        """All IncidentSeverity values serialize correctly via to_dict()."""
        for severity in IncidentSeverity:
            incident = Incident(
                start_time=now,
                severity=severity,
                description="test",
            )
            d = incident.to_dict()
            assert d["severity"] == severity.value

    def test_all_update_statuses_in_to_dict(self, now):
        """All UpdateStatus values serialize correctly via to_dict()."""
        for status in UpdateStatus:
            record = UpdateRecord(
                timestamp=now,
                version="1.0.0",
                previous_version="0.9.0",
                git_sha="abc",
                status=status,
            )
            d = record.to_dict()
            assert d["status"] == status.value
