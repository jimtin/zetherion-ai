"""End-to-end integration tests for the central telemetry system.

Tests the full flow against a real PostgreSQL database:
instance registration, report ingestion, fleet summary, and deletion.

PostgreSQL is expected on localhost:15432 (the Docker test environment).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

try:
    import asyncpg  # type: ignore[import-not-found,import-untyped]
except ImportError:
    asyncpg = None  # type: ignore[assignment]

from zetherion_ai.telemetry.models import (
    TelemetryConsent,
    TelemetryReport,
    generate_instance_id,
)
from zetherion_ai.telemetry.receiver import TelemetryReceiver
from zetherion_ai.telemetry.storage import TelemetryStorage

POSTGRES_DSN = "postgresql://zetherion:password@localhost:15432/zetherion"
SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest_asyncio.fixture()
async def pg_pool():
    """Create an asyncpg connection pool to the test PostgreSQL instance."""
    if SKIP_INTEGRATION:
        pytest.skip("Integration tests disabled")
    if asyncpg is None:
        pytest.skip("asyncpg not installed")
    try:
        pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3, timeout=5)
    except Exception:
        pytest.skip("PostgreSQL not reachable at localhost:15432")
    yield pool
    await pool.close()


@pytest_asyncio.fixture()
async def storage(pg_pool):
    """Initialise TelemetryStorage and clean up after each test."""
    s = TelemetryStorage()
    await s.initialize(pg_pool)
    yield s
    # Clean up test data in correct dependency order
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM telemetry_reports")
        await conn.execute("DELETE FROM fleet_aggregates")
        await conn.execute("DELETE FROM telemetry_instances")


@pytest_asyncio.fixture()
async def receiver(storage):
    """Create a TelemetryReceiver backed by the test storage."""
    return TelemetryReceiver(storage)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_report(
    instance_id: str,
    *,
    version: str = "1.0.0",
    consent_categories: set[str] | None = None,
    metrics: dict | None = None,
    timestamp: str | None = None,
) -> TelemetryReport:
    """Build a TelemetryReport with sensible defaults."""
    return TelemetryReport(
        instance_id=instance_id,
        timestamp=timestamp or datetime.now(tz=UTC).isoformat(),
        version=version,
        consent=TelemetryConsent(categories=consent_categories or {"performance", "health"}),
        metrics=metrics
        or {
            "performance": {"avg_latency_ms": 42},
            "health": {"uptime_hours": 100},
        },
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_storage_initialize(pg_pool, storage):
    """Verify that initialize() creates the required tables."""
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN (
                  'telemetry_instances',
                  'telemetry_reports',
                  'fleet_aggregates'
              )
            ORDER BY tablename
            """
        )
    table_names = sorted(r["tablename"] for r in rows)
    assert table_names == [
        "fleet_aggregates",
        "telemetry_instances",
        "telemetry_reports",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_and_retrieve_instance(storage, receiver):
    """Register an instance via the receiver, then retrieve it from storage."""
    iid = generate_instance_id()
    consent = TelemetryConsent(categories={"performance", "health"})

    raw_key = await receiver.register_instance(iid, consent=consent)
    assert raw_key.startswith("zt_inst_")

    reg = await storage.get_instance(iid)
    assert reg is not None
    assert reg.instance_id == iid
    assert reg.api_key_hash != raw_key  # stored as bcrypt hash, not raw
    assert reg.consent.allows("performance")
    assert reg.consent.allows("health")
    assert not reg.consent.allows("cost")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_key_validation(receiver):
    """Correct key validates; wrong key does not."""
    iid = generate_instance_id()
    raw_key = await receiver.register_instance(iid)

    assert await receiver.validate_key(iid, raw_key) is True
    assert await receiver.validate_key(iid, "zt_inst_wrong_key") is False
    assert await receiver.validate_key("nonexistent-id", raw_key) is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_report(storage, receiver):
    """Ingest a report and verify it is persisted."""
    iid = generate_instance_id()
    consent = TelemetryConsent(categories={"performance", "health"})
    raw_key = await receiver.register_instance(iid, consent=consent)

    report = _make_report(iid, consent_categories={"performance", "health"})
    accepted = await receiver.ingest(report, raw_key)
    assert accepted is True

    reports = await storage.get_reports(instance_id=iid)
    assert len(reports) == 1

    stored = reports[0]
    assert stored["instance_id"] == iid
    assert stored["version"] == "1.0.0"

    # report_json stores the (possibly filtered) metrics
    metrics = (
        json.loads(stored["report_json"])
        if isinstance(stored["report_json"], str)
        else stored["report_json"]
    )
    assert "performance" in metrics
    assert "health" in metrics


@pytest.mark.integration
@pytest.mark.asyncio
async def test_consent_filtering(storage, receiver):
    """Only consented metric categories are stored; non-consented are dropped."""
    iid = generate_instance_id()
    consent = TelemetryConsent(categories={"health", "performance"})
    raw_key = await receiver.register_instance(iid, consent=consent)

    report = _make_report(
        iid,
        consent_categories={"health", "performance"},
        metrics={
            "health": {"uptime_hours": 50},
            "performance": {"avg_latency_ms": 30},
            "cost": {"monthly_usd": 9.99},
        },
    )
    accepted = await receiver.ingest(report, raw_key)
    assert accepted is True

    reports = await storage.get_reports(instance_id=iid)
    assert len(reports) == 1

    metrics = (
        json.loads(reports[0]["report_json"])
        if isinstance(reports[0]["report_json"], str)
        else reports[0]["report_json"]
    )
    assert "health" in metrics
    assert "performance" in metrics
    assert "cost" not in metrics, "Non-consented 'cost' category should be filtered out"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fleet_summary(receiver):
    """Fleet summary reflects correct instance count and version distribution."""
    ids_and_versions = [
        (generate_instance_id(), "2.0.0"),
        (generate_instance_id(), "2.0.0"),
        (generate_instance_id(), "1.9.0"),
    ]
    for iid, version in ids_and_versions:
        consent = TelemetryConsent(categories={"health"})
        raw_key = await receiver.register_instance(iid, consent=consent)
        # Ingest one report so last_seen / current_version are updated
        report = _make_report(
            iid,
            version=version,
            consent_categories={"health"},
            metrics={"health": {"ok": True}},
        )
        await receiver.ingest(report, raw_key)

    summary = await receiver.get_fleet_summary()
    assert summary["total_instances"] == 3
    assert summary["versions"]["2.0.0"] == 2
    assert summary["versions"]["1.9.0"] == 1
    assert summary["last_report"] is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_instance(storage, receiver):
    """Deleting an instance removes the instance and cascades to its reports."""
    iid = generate_instance_id()
    consent = TelemetryConsent(categories={"health"})
    raw_key = await receiver.register_instance(iid, consent=consent)

    report = _make_report(
        iid,
        consent_categories={"health"},
        metrics={"health": {"uptime_hours": 10}},
    )
    await receiver.ingest(report, raw_key)

    # Verify data exists before deletion
    assert await storage.get_instance(iid) is not None
    reports_before = await storage.get_reports(instance_id=iid)
    assert len(reports_before) == 1

    # Delete
    deleted = await receiver.delete_instance(iid, raw_key)
    assert deleted is True

    # Instance gone
    assert await storage.get_instance(iid) is None

    # Reports gone (CASCADE)
    reports_after = await storage.get_reports(instance_id=iid)
    assert len(reports_after) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aggregate_storage(storage):
    """Save a fleet aggregate and retrieve it."""
    now = datetime.now(tz=UTC)
    period_start = now - timedelta(hours=1)
    period_end = now
    metric_name = "avg_latency_ms"
    aggregation = {"mean": 42.5, "p99": 120.0, "sample_count": 500}

    await storage.save_aggregate(
        period_start=period_start,
        period_end=period_end,
        metric_name=metric_name,
        aggregation=aggregation,
    )

    aggregates = await storage.get_aggregates(metric_name=metric_name)
    assert len(aggregates) >= 1

    agg = aggregates[0]
    assert agg["metric_name"] == metric_name
    # Verify the JSON payload round-trips correctly
    agg_data = (
        json.loads(agg["aggregation_json"])
        if isinstance(agg["aggregation_json"], str)
        else agg["aggregation_json"]
    )
    assert agg_data["mean"] == 42.5
    assert agg_data["p99"] == 120.0
    assert agg_data["sample_count"] == 500


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_reports_for_instance(storage, receiver):
    """Multiple reports for the same instance are stored and returned in order."""
    iid = generate_instance_id()
    consent = TelemetryConsent(categories={"health"})
    raw_key = await receiver.register_instance(iid, consent=consent)

    base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        ts = (base_time + timedelta(hours=i)).isoformat()
        report = _make_report(
            iid,
            version=f"1.0.{i}",
            consent_categories={"health"},
            metrics={"health": {"seq": i}},
            timestamp=ts,
        )
        accepted = await receiver.ingest(report, raw_key)
        assert accepted is True

    reports = await storage.get_reports(instance_id=iid, limit=10)
    assert len(reports) == 3

    # get_reports orders by timestamp DESC, so newest first
    versions = [r["version"] for r in reports]
    assert versions == ["1.0.2", "1.0.1", "1.0.0"]
