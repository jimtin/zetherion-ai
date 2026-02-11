"""Tests for MetricsCollector health data collection.

Verifies that the collector correctly gathers performance, reliability, usage,
system, and skill-health metrics from its sources, and degrades gracefully when
sources are unavailable or raise exceptions.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from zetherion_ai.costs.storage import UsageRecord
from zetherion_ai.health.collector import (
    CollectorSources,
    MetricsCollector,
    PerformanceMetrics,
    ReliabilityMetrics,
    SkillHealthMetrics,
    SystemMetrics,
    UsageMetrics,
)
from zetherion_ai.scheduler.heartbeat import HeartbeatStats

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def usage_records_single_provider():
    """Usage records from a single provider with known latencies."""
    return [
        UsageRecord(
            provider="openai",
            model="gpt-4",
            tokens_input=100,
            tokens_output=50,
            cost_usd=0.01,
            latency_ms=200,
            success=True,
            rate_limit_hit=False,
        ),
        UsageRecord(
            provider="openai",
            model="gpt-4",
            tokens_input=150,
            tokens_output=80,
            cost_usd=0.02,
            latency_ms=300,
            success=True,
            rate_limit_hit=False,
        ),
        UsageRecord(
            provider="openai",
            model="gpt-4",
            tokens_input=120,
            tokens_output=60,
            cost_usd=0.015,
            latency_ms=250,
            success=False,
            rate_limit_hit=False,
        ),
    ]


@pytest.fixture
def usage_records_multi_provider():
    """Usage records from multiple providers."""
    return [
        UsageRecord(
            provider="openai",
            model="gpt-4",
            tokens_input=100,
            tokens_output=50,
            cost_usd=0.01,
            latency_ms=200,
            success=True,
            rate_limit_hit=False,
        ),
        UsageRecord(
            provider="openai",
            model="gpt-4",
            tokens_input=150,
            tokens_output=80,
            cost_usd=0.02,
            latency_ms=400,
            success=True,
            rate_limit_hit=True,
        ),
        UsageRecord(
            provider="anthropic",
            model="claude-3",
            tokens_input=200,
            tokens_output=100,
            cost_usd=0.03,
            latency_ms=150,
            success=True,
            rate_limit_hit=False,
        ),
        UsageRecord(
            provider="anthropic",
            model="claude-3",
            tokens_input=180,
            tokens_output=90,
            cost_usd=0.025,
            latency_ms=350,
            success=False,
            rate_limit_hit=True,
        ),
    ]


@pytest.fixture
def mock_cost_storage(usage_records_multi_provider):
    """Mock CostStorage that returns multi-provider records by default."""
    storage = MagicMock()
    storage.get_usage_by_date_range.return_value = usage_records_multi_provider
    storage.get_total_cost.return_value = 0.085
    storage.get_total_cost_by_provider.return_value = {
        "openai": 0.03,
        "anthropic": 0.055,
    }
    return storage


@pytest.fixture
def mock_heartbeat_stats():
    """Mock HeartbeatStats with realistic values."""
    return HeartbeatStats(
        total_beats=50,
        total_actions=120,
        successful_actions=100,
        failed_actions=20,
        rate_limited=5,
    )


@pytest.fixture
def mock_skill_registry():
    """Mock SkillRegistry with a plausible status summary."""
    registry = MagicMock()
    registry.get_status_summary.return_value = {
        "total_skills": 5,
        "ready_count": 3,
        "error_count": 2,
        "by_status": {
            "ready": ["weather", "calendar", "memory"],
            "error": ["gmail", "spotify"],
        },
    }
    return registry


@pytest.fixture
def full_sources(mock_cost_storage, mock_heartbeat_stats, mock_skill_registry):
    """CollectorSources with all dependencies populated."""
    return CollectorSources(
        cost_storage=mock_cost_storage,
        heartbeat_stats=mock_heartbeat_stats,
        skill_registry=mock_skill_registry,
        data_dir="/tmp",
    )


@pytest.fixture
def collector(full_sources):
    """MetricsCollector wired up with full_sources."""
    return MetricsCollector(sources=full_sources)


@pytest.fixture
def empty_collector():
    """MetricsCollector with no sources (all None)."""
    return MetricsCollector(sources=CollectorSources())


# ---------------------------------------------------------------------------
# 1. Collection from mocked CostStorage (latency, error rates, rate limits)
# ---------------------------------------------------------------------------


class TestCollectPerformanceFromCostStorage:
    """Tests for performance metrics collected from CostStorage."""

    def test_total_requests_counted(self, collector, usage_records_multi_provider):
        """Total request count matches the number of usage records."""
        result = collector.collect_performance()

        assert result.total_requests == len(usage_records_multi_provider)

    def test_requests_counted_per_provider(self, collector):
        """Requests are broken down by provider."""
        result = collector.collect_performance()

        assert result.requests_by_provider["openai"] == 2
        assert result.requests_by_provider["anthropic"] == 2

    def test_avg_latency_computed(self, collector):
        """Average latency is computed per provider."""
        result = collector.collect_performance()

        # openai: (200 + 400) / 2 = 300
        assert result.avg_latency_ms["openai"] == 300.0
        # anthropic: (150 + 350) / 2 = 250
        assert result.avg_latency_ms["anthropic"] == 250.0

    def test_p95_latency_computed(self, collector):
        """P95 latency is computed per provider."""
        result = collector.collect_performance()

        # With 2 values, p95_idx = int(2 * 0.95) = 1, min(1, 1) = 1 -> highest
        assert result.p95_latency_ms["openai"] == 400.0
        assert result.p95_latency_ms["anthropic"] == 350.0

    def test_records_with_none_latency_excluded_from_latency_stats(self, collector):
        """Records with latency_ms=None are excluded from latency aggregation."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=None,
                success=True,
                rate_limit_hit=False,
            ),
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=500,
                success=True,
                rate_limit_hit=False,
            ),
        ]
        collector.sources.cost_storage.get_usage_by_date_range.return_value = records

        result = collector.collect_performance()

        assert result.total_requests == 2
        assert result.avg_latency_ms["openai"] == 500.0
        # Only one value, so p95 == that value
        assert result.p95_latency_ms["openai"] == 500.0


# ---------------------------------------------------------------------------
# 2. Collection from mocked HeartbeatStats
# ---------------------------------------------------------------------------


class TestCollectFromHeartbeatStats:
    """Tests for metrics drawn from HeartbeatStats."""

    def test_usage_captures_beat_counts(self, collector):
        """collect_usage picks up total_beats and total_actions."""
        result = collector.collect_usage()

        assert result.heartbeat_total_beats == 50
        assert result.heartbeat_total_actions == 120

    def test_reliability_success_rate(self, collector):
        """Heartbeat success rate is correctly computed."""
        result = collector.collect_reliability()

        # successful=100, failed=20 => total=120 => rate=100/120
        expected = round(100 / 120, 4)
        assert result.heartbeat_success_rate == expected

    def test_reliability_all_successful(self):
        """Success rate is 1.0 when there are no failures."""
        hb = HeartbeatStats(
            total_beats=10,
            total_actions=30,
            successful_actions=30,
            failed_actions=0,
        )
        sources = CollectorSources(heartbeat_stats=hb)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        assert result.heartbeat_success_rate == 1.0

    def test_reliability_zero_actions(self):
        """Success rate defaults to 1.0 when no actions have been executed."""
        hb = HeartbeatStats(
            total_beats=5,
            total_actions=0,
            successful_actions=0,
            failed_actions=0,
        )
        sources = CollectorSources(heartbeat_stats=hb)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        # total = 0+0 => short-circuit, keeps default 1.0
        assert result.heartbeat_success_rate == 1.0


# ---------------------------------------------------------------------------
# 3. Collection from mocked SkillRegistry
# ---------------------------------------------------------------------------


class TestCollectSkillHealth:
    """Tests for skill health metrics collected from SkillRegistry."""

    def test_skill_counts(self, collector):
        """Total, ready, and error counts are populated."""
        result = collector.collect_skill_health()

        assert result.total_skills == 5
        assert result.ready_count == 3
        assert result.error_count == 2

    def test_skills_by_status(self, collector):
        """by_status dict is propagated from the registry summary."""
        result = collector.collect_skill_health()

        assert "ready" in result.skills_by_status
        assert "error" in result.skills_by_status
        assert "weather" in result.skills_by_status["ready"]
        assert "gmail" in result.skills_by_status["error"]

    def test_skill_error_names_in_reliability(self, collector):
        """Skill error names are surfaced in reliability metrics."""
        result = collector.collect_reliability()

        assert result.skill_failure_count == 2
        assert "gmail" in result.skill_error_names
        assert "spotify" in result.skill_error_names


# ---------------------------------------------------------------------------
# 4. System metrics (memory via psutil -- mock psutil)
# ---------------------------------------------------------------------------


class TestCollectSystem:
    """Tests for system resource metrics."""

    def test_memory_metrics_via_psutil(self, collector):
        """Memory metrics are populated when psutil is available."""
        mock_proc = MagicMock()
        mock_proc.memory_info.return_value = MagicMock(
            rss=256 * 1024 * 1024,  # 256 MB
        )
        mock_proc.memory_percent.return_value = 3.5

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value = mock_proc

        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            result = collector.collect_system()

        assert result.memory_rss_mb == 256.0
        assert result.memory_percent == 3.5

    def test_disk_usage_metrics(self, collector):
        """Disk metrics are populated from shutil.disk_usage."""
        mock_disk = MagicMock()
        mock_disk.total = 500 * (1024**3)  # 500 GB
        mock_disk.used = 200 * (1024**3)  # 200 GB
        mock_disk.free = 300 * (1024**3)  # 300 GB

        with patch("zetherion_ai.health.collector.shutil.disk_usage", return_value=mock_disk):
            result = collector.collect_system()

        assert result.disk_total_gb == 500.0
        assert result.disk_used_gb == 200.0
        assert result.disk_free_gb == 300.0
        assert result.disk_usage_percent == 40.0

    def test_system_graceful_when_psutil_missing(self, collector):
        """System metrics return zeros when psutil is not installed."""
        import sys

        with (
            patch.dict(sys.modules, {"psutil": None}),
            patch(
                "zetherion_ai.health.collector.shutil.disk_usage",
                side_effect=OSError("no such dir"),
            ),
        ):
            result = collector.collect_system()

        assert result.memory_rss_mb == 0.0
        assert result.memory_percent == 0.0
        assert result.disk_total_gb == 0.0

    def test_disk_usage_uses_data_dir(self):
        """shutil.disk_usage is called with the configured data_dir."""
        sources = CollectorSources(data_dir="/custom/data")
        collector = MetricsCollector(sources=sources)

        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            collector.collect_system()

        mock_du.assert_called_once_with("/custom/data")


# ---------------------------------------------------------------------------
# 5. MetricsSnapshot-compatible output (collect_all returns correct structure)
# ---------------------------------------------------------------------------


class TestCollectAllStructure:
    """Tests for the collect_all() return structure."""

    def test_all_top_level_keys_present(self, collector):
        """collect_all returns all expected top-level keys."""
        with (
            patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du,
        ):
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        expected_keys = {
            "performance",
            "reliability",
            "usage",
            "system",
            "skills",
            "collection_time_ms",
            "collected_at",
        }
        assert set(result.keys()) == expected_keys

    def test_performance_sub_keys(self, collector):
        """Performance section has the expected keys."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        perf = result["performance"]
        assert "avg_latency_ms" in perf
        assert "p95_latency_ms" in perf
        assert "total_requests" in perf
        assert "requests_by_provider" in perf

    def test_reliability_sub_keys(self, collector):
        """Reliability section has the expected keys."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        rel = result["reliability"]
        assert "error_rate_by_provider" in rel
        assert "rate_limit_count" in rel
        assert "rate_limit_by_provider" in rel
        assert "heartbeat_success_rate" in rel
        assert "uptime_seconds" in rel
        assert "skill_failure_count" in rel
        assert "skill_error_names" in rel

    def test_usage_sub_keys(self, collector):
        """Usage section has the expected keys."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        usage = result["usage"]
        assert "total_cost_usd_today" in usage
        assert "cost_by_provider" in usage
        assert "total_tokens_input" in usage
        assert "total_tokens_output" in usage
        assert "heartbeat_total_beats" in usage
        assert "heartbeat_total_actions" in usage

    def test_system_sub_keys(self, collector):
        """System section has the expected keys."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        sys_metrics = result["system"]
        assert "memory_rss_mb" in sys_metrics
        assert "memory_percent" in sys_metrics
        assert "disk_total_gb" in sys_metrics
        assert "disk_used_gb" in sys_metrics
        assert "disk_free_gb" in sys_metrics
        assert "disk_usage_percent" in sys_metrics

    def test_skills_sub_keys(self, collector):
        """Skills section has the expected keys."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        skills = result["skills"]
        assert "total_skills" in skills
        assert "ready_count" in skills
        assert "error_count" in skills
        assert "skills_by_status" in skills

    def test_collection_time_ms_is_non_negative(self, collector):
        """collection_time_ms is a non-negative number."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        assert isinstance(result["collection_time_ms"], float)
        assert result["collection_time_ms"] >= 0

    def test_collected_at_is_iso_format(self, collector):
        """collected_at is a parseable ISO-8601 timestamp."""
        from datetime import datetime

        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        # Should not raise
        datetime.fromisoformat(result["collected_at"])

    def test_values_are_plain_dicts(self, collector):
        """Category values are plain dicts (from to_dict()), not dataclass instances."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        for key in ("performance", "reliability", "usage", "system", "skills"):
            assert isinstance(result[key], dict), f"{key} should be a plain dict"


# ---------------------------------------------------------------------------
# 6. Graceful handling when a source is None
# ---------------------------------------------------------------------------


class TestGracefulNoneSources:
    """Tests for graceful degradation when sources are None."""

    def test_performance_zeroed_without_cost_storage(self, empty_collector):
        """Performance metrics are zeroed when cost_storage is None."""
        result = empty_collector.collect_performance()

        assert isinstance(result, PerformanceMetrics)
        assert result.total_requests == 0
        assert result.avg_latency_ms == {}
        assert result.p95_latency_ms == {}
        assert result.requests_by_provider == {}

    def test_reliability_defaults_without_any_source(self, empty_collector):
        """Reliability metrics have safe defaults when all sources are None."""
        result = empty_collector.collect_reliability()

        assert isinstance(result, ReliabilityMetrics)
        assert result.error_rate_by_provider == {}
        assert result.rate_limit_count == 0
        assert result.heartbeat_success_rate == 1.0
        assert result.skill_failure_count == 0
        assert result.skill_error_names == []

    def test_usage_zeroed_without_sources(self, empty_collector):
        """Usage metrics are zeroed when both sources are None."""
        result = empty_collector.collect_usage()

        assert isinstance(result, UsageMetrics)
        assert result.total_cost_usd_today == 0.0
        assert result.cost_by_provider == {}
        assert result.total_tokens_input == 0
        assert result.total_tokens_output == 0
        assert result.heartbeat_total_beats == 0
        assert result.heartbeat_total_actions == 0

    def test_skill_health_zeroed_without_registry(self, empty_collector):
        """Skill health is zeroed when skill_registry is None."""
        result = empty_collector.collect_skill_health()

        assert isinstance(result, SkillHealthMetrics)
        assert result.total_skills == 0
        assert result.ready_count == 0
        assert result.error_count == 0
        assert result.skills_by_status == {}

    def test_collect_all_succeeds_without_sources(self, empty_collector):
        """collect_all succeeds and returns full structure with all None sources."""
        with (
            patch(
                "zetherion_ai.health.collector.shutil.disk_usage",
                side_effect=OSError("nope"),
            ),
        ):
            result = empty_collector.collect_all()

        assert "performance" in result
        assert "reliability" in result
        assert "usage" in result
        assert "system" in result
        assert "skills" in result

    def test_collector_created_without_any_sources(self):
        """MetricsCollector can be created with sources=None."""
        collector = MetricsCollector(sources=None)

        assert collector.sources is not None
        assert collector.sources.cost_storage is None
        assert collector.sources.heartbeat_stats is None
        assert collector.sources.skill_registry is None


# ---------------------------------------------------------------------------
# 7. Graceful handling when a source raises an exception
# ---------------------------------------------------------------------------


class TestGracefulExceptionHandling:
    """Tests for graceful degradation when a source raises."""

    def test_performance_returns_partial_on_cost_storage_error(self):
        """Performance returns zeroed metrics when cost_storage raises."""
        storage = MagicMock()
        storage.get_usage_by_date_range.side_effect = RuntimeError("DB locked")
        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_performance()

        assert isinstance(result, PerformanceMetrics)
        assert result.total_requests == 0

    def test_reliability_returns_partial_on_cost_storage_error(self):
        """Reliability still includes heartbeat data even if cost_storage raises."""
        storage = MagicMock()
        storage.get_usage_by_date_range.side_effect = RuntimeError("DB locked")

        hb = HeartbeatStats(
            total_beats=10,
            total_actions=8,
            successful_actions=6,
            failed_actions=2,
        )

        sources = CollectorSources(cost_storage=storage, heartbeat_stats=hb)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        # Cost-derived fields should be default
        assert result.error_rate_by_provider == {}
        assert result.rate_limit_count == 0
        # Heartbeat fields should still be populated
        assert result.heartbeat_success_rate == round(6 / 8, 4)

    def test_reliability_returns_partial_on_heartbeat_error(self):
        """Reliability still includes cost data even if heartbeat raises."""
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                success=False,
                rate_limit_hit=False,
            ),
        ]

        hb = MagicMock()
        hb.successful_actions = property(lambda self: (_ for _ in ()).throw(RuntimeError("oops")))
        # Simpler: make attribute access raise
        type(hb).successful_actions = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("oops"))
        )

        sources = CollectorSources(cost_storage=storage, heartbeat_stats=hb)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        # Cost-derived error rate should still be present
        assert "openai" in result.error_rate_by_provider
        # Heartbeat success rate stays at default 1.0 due to exception
        assert result.heartbeat_success_rate == 1.0

    def test_reliability_returns_partial_on_registry_error(self, mock_cost_storage):
        """Reliability still includes cost data even if skill registry raises."""
        registry = MagicMock()
        registry.get_status_summary.side_effect = RuntimeError("registry unavailable")

        sources = CollectorSources(
            cost_storage=mock_cost_storage,
            skill_registry=registry,
        )
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        assert result.skill_failure_count == 0
        assert result.skill_error_names == []
        # Cost data should still be populated
        assert len(result.error_rate_by_provider) > 0

    def test_usage_returns_partial_on_cost_error(self, mock_heartbeat_stats):
        """Usage still includes heartbeat data even if cost_storage raises."""
        storage = MagicMock()
        storage.get_total_cost.side_effect = RuntimeError("DB error")

        sources = CollectorSources(
            cost_storage=storage,
            heartbeat_stats=mock_heartbeat_stats,
        )
        collector = MetricsCollector(sources=sources)

        result = collector.collect_usage()

        assert result.total_cost_usd_today == 0.0
        assert result.heartbeat_total_beats == 50
        assert result.heartbeat_total_actions == 120

    def test_usage_returns_partial_on_heartbeat_error(self, mock_cost_storage):
        """Usage still includes cost data even if heartbeat raises."""
        hb = MagicMock()
        type(hb).total_beats = property(lambda self: (_ for _ in ()).throw(RuntimeError("oops")))

        sources = CollectorSources(
            cost_storage=mock_cost_storage,
            heartbeat_stats=hb,
        )
        collector = MetricsCollector(sources=sources)

        result = collector.collect_usage()

        # Cost data should still be populated
        assert result.total_cost_usd_today == 0.085
        # Heartbeat fields stay at defaults due to exception
        assert result.heartbeat_total_beats == 0

    def test_skill_health_returns_zeroed_on_registry_error(self):
        """Skill health returns zeroed metrics when registry raises."""
        registry = MagicMock()
        registry.get_status_summary.side_effect = RuntimeError("gone")

        sources = CollectorSources(skill_registry=registry)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_skill_health()

        assert isinstance(result, SkillHealthMetrics)
        assert result.total_skills == 0
        assert result.ready_count == 0
        assert result.error_count == 0

    def test_collect_all_survives_all_sources_raising(self):
        """collect_all still returns a full dict even if every source raises."""
        storage = MagicMock()
        storage.get_usage_by_date_range.side_effect = RuntimeError("boom")
        storage.get_total_cost.side_effect = RuntimeError("boom")
        storage.get_total_cost_by_provider.side_effect = RuntimeError("boom")

        hb = MagicMock()
        type(hb).successful_actions = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        type(hb).total_beats = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        registry = MagicMock()
        registry.get_status_summary.side_effect = RuntimeError("boom")

        sources = CollectorSources(
            cost_storage=storage,
            heartbeat_stats=hb,
            skill_registry=registry,
        )
        collector = MetricsCollector(sources=sources)

        with patch(
            "zetherion_ai.health.collector.shutil.disk_usage",
            side_effect=OSError("nope"),
        ):
            result = collector.collect_all()

        # Should still have all top-level keys
        assert "performance" in result
        assert "reliability" in result
        assert "usage" in result
        assert "system" in result
        assert "skills" in result
        assert "collection_time_ms" in result
        assert "collected_at" in result


# ---------------------------------------------------------------------------
# 8. P95 latency calculation
# ---------------------------------------------------------------------------


class TestP95LatencyCalculation:
    """Tests for the P95 latency computation."""

    def _make_collector_with_records(self, records):
        """Helper to create a collector with specific records."""
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = records
        sources = CollectorSources(cost_storage=storage)
        return MetricsCollector(sources=sources)

    def test_p95_single_record(self):
        """P95 of a single record equals that record's latency."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=500,
                success=True,
                rate_limit_hit=False,
            ),
        ]
        collector = self._make_collector_with_records(records)

        result = collector.collect_performance()

        assert result.p95_latency_ms["openai"] == 500.0

    def test_p95_twenty_records(self):
        """P95 with 20 records picks the correct index.

        Sorted latencies: 100, 200, ..., 2000 (20 values).
        p95_idx = int(20 * 0.95) = 19 => min(19, 19) = 19 => value 2000.
        """
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=(i + 1) * 100,
                success=True,
                rate_limit_hit=False,
            )
            for i in range(20)
        ]
        collector = self._make_collector_with_records(records)

        result = collector.collect_performance()

        assert result.p95_latency_ms["openai"] == 2000.0

    def test_p95_hundred_records(self):
        """P95 with 100 records picks value at 95th index.

        Sorted latencies: 10, 20, ..., 1000 (100 values).
        p95_idx = int(100 * 0.95) = 95 => min(95, 99) = 95 => value 960.
        """
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=(i + 1) * 10,
                success=True,
                rate_limit_hit=False,
            )
            for i in range(100)
        ]
        collector = self._make_collector_with_records(records)

        result = collector.collect_performance()

        assert result.p95_latency_ms["openai"] == 960.0

    def test_p95_all_same_latency(self):
        """P95 when all latencies are identical returns that value."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=250,
                success=True,
                rate_limit_hit=False,
            )
            for _ in range(50)
        ]
        collector = self._make_collector_with_records(records)

        result = collector.collect_performance()

        assert result.p95_latency_ms["openai"] == 250.0

    def test_p95_unsorted_input(self):
        """P95 works correctly even when records arrive unsorted."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                latency_ms=lat,
                success=True,
                rate_limit_hit=False,
            )
            for lat in [900, 100, 500, 300, 700, 200, 800, 400, 600, 1000]
        ]
        collector = self._make_collector_with_records(records)

        result = collector.collect_performance()

        # Sorted: 100..1000, 10 values, p95_idx = int(10*0.95) = 9 => value 1000
        assert result.p95_latency_ms["openai"] == 1000.0


# ---------------------------------------------------------------------------
# 9. Error rate calculation per provider
# ---------------------------------------------------------------------------


class TestErrorRatePerProvider:
    """Tests for error rate calculation by provider."""

    def test_error_rate_with_mixed_results(self, collector):
        """Error rate is computed correctly for mixed success/failure records."""
        result = collector.collect_reliability()

        # openai: 2 total, 0 errors => 0.0
        assert result.error_rate_by_provider["openai"] == 0.0
        # anthropic: 2 total, 1 error => 0.5
        assert result.error_rate_by_provider["anthropic"] == 0.5

    def test_error_rate_all_failures(self):
        """Error rate is 1.0 when all requests fail."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                success=False,
                rate_limit_hit=False,
            ),
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                success=False,
                rate_limit_hit=False,
            ),
        ]
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = records
        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        assert result.error_rate_by_provider["openai"] == 1.0

    def test_error_rate_all_successes(self):
        """Error rate is 0.0 when all requests succeed."""
        records = [
            UsageRecord(
                provider="anthropic",
                model="claude-3",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                success=True,
                rate_limit_hit=False,
            )
            for _ in range(10)
        ]
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = records
        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        assert result.error_rate_by_provider["anthropic"] == 0.0

    def test_rate_limit_counting(self, collector):
        """Rate limits are counted overall and per provider."""
        result = collector.collect_reliability()

        # multi_provider fixture: openai has 1 rate_limit_hit, anthropic has 1
        assert result.rate_limit_count == 2
        assert result.rate_limit_by_provider["openai"] == 1
        assert result.rate_limit_by_provider["anthropic"] == 1

    def test_no_records_means_no_error_rates(self):
        """Empty records result in empty error rate dict."""
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = []
        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_reliability()

        assert result.error_rate_by_provider == {}
        assert result.rate_limit_count == 0


# ---------------------------------------------------------------------------
# 10. collect_all() includes all categories
# ---------------------------------------------------------------------------


class TestCollectAllIntegration:
    """Integration-level tests confirming collect_all wires everything together."""

    def test_collect_all_includes_data_from_all_sources(self, collector):
        """collect_all aggregates data from cost, heartbeat, and registry."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            result = collector.collect_all()

        # Performance from cost storage
        assert result["performance"]["total_requests"] == 4

        # Reliability from all three sources
        assert "openai" in result["reliability"]["error_rate_by_provider"]
        assert result["reliability"]["heartbeat_success_rate"] == round(100 / 120, 4)
        assert result["reliability"]["skill_failure_count"] == 2

        # Usage from cost + heartbeat
        assert result["usage"]["total_cost_usd_today"] == 0.085
        assert result["usage"]["heartbeat_total_beats"] == 50

        # Skills from registry
        assert result["skills"]["total_skills"] == 5
        assert result["skills"]["ready_count"] == 3

    def test_collect_all_cost_storage_called_for_multiple_collectors(
        self, collector, mock_cost_storage
    ):
        """CostStorage is called from both performance and reliability collectors."""
        with patch("zetherion_ai.health.collector.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=1, used=0, free=1)
            collector.collect_all()

        # get_usage_by_date_range is called by performance, reliability, and usage
        assert mock_cost_storage.get_usage_by_date_range.call_count == 3

    def test_update_sources_replaces_references(self):
        """update_sources replaces the collector's source references."""
        collector = MetricsCollector()
        assert collector.sources.cost_storage is None

        new_storage = MagicMock()
        new_storage.get_usage_by_date_range.return_value = []
        new_sources = CollectorSources(cost_storage=new_storage)
        collector.update_sources(new_sources)

        assert collector.sources.cost_storage is new_storage

        result = collector.collect_performance()
        new_storage.get_usage_by_date_range.assert_called_once()
        assert isinstance(result, PerformanceMetrics)


# ---------------------------------------------------------------------------
# Dataclass to_dict round-trip checks
# ---------------------------------------------------------------------------


class TestDataclassToDict:
    """Verify that each metrics dataclass serialises correctly via to_dict()."""

    def test_performance_to_dict(self):
        """PerformanceMetrics.to_dict returns all fields."""
        m = PerformanceMetrics(
            avg_latency_ms={"a": 1.0},
            p95_latency_ms={"a": 2.0},
            total_requests=5,
            requests_by_provider={"a": 5},
        )
        d = m.to_dict()
        assert d == {
            "avg_latency_ms": {"a": 1.0},
            "p95_latency_ms": {"a": 2.0},
            "total_requests": 5,
            "requests_by_provider": {"a": 5},
        }

    def test_reliability_to_dict(self):
        """ReliabilityMetrics.to_dict returns all fields."""
        m = ReliabilityMetrics(
            error_rate_by_provider={"x": 0.1},
            rate_limit_count=3,
            rate_limit_by_provider={"x": 3},
            skill_failure_count=1,
            skill_error_names=["bad"],
            heartbeat_success_rate=0.9,
            uptime_seconds=1234.5,
        )
        d = m.to_dict()
        assert d["error_rate_by_provider"] == {"x": 0.1}
        assert d["rate_limit_count"] == 3
        assert d["heartbeat_success_rate"] == 0.9
        assert d["uptime_seconds"] == 1234.5

    def test_usage_to_dict(self):
        """UsageMetrics.to_dict returns all fields."""
        m = UsageMetrics(
            total_cost_usd_today=0.5,
            cost_by_provider={"a": 0.5},
            total_tokens_input=100,
            total_tokens_output=50,
            heartbeat_total_beats=10,
            heartbeat_total_actions=20,
        )
        d = m.to_dict()
        assert d["total_cost_usd_today"] == 0.5
        assert d["total_tokens_input"] == 100

    def test_system_to_dict(self):
        """SystemMetrics.to_dict returns all fields."""
        m = SystemMetrics(
            memory_rss_mb=256.0,
            memory_percent=3.5,
            disk_total_gb=500.0,
            disk_used_gb=200.0,
            disk_free_gb=300.0,
            disk_usage_percent=40.0,
        )
        d = m.to_dict()
        assert d["memory_rss_mb"] == 256.0
        assert d["disk_usage_percent"] == 40.0

    def test_skill_health_to_dict(self):
        """SkillHealthMetrics.to_dict returns all fields."""
        m = SkillHealthMetrics(
            total_skills=5,
            ready_count=3,
            error_count=2,
            skills_by_status={"ready": ["a"], "error": ["b"]},
        )
        d = m.to_dict()
        assert d["total_skills"] == 5
        assert d["skills_by_status"]["ready"] == ["a"]


# ---------------------------------------------------------------------------
# Edge cases for usage collection (token totals)
# ---------------------------------------------------------------------------


class TestUsageTokenTotals:
    """Tests for token input/output totalling in collect_usage."""

    def test_token_totals_summed(self):
        """Tokens from all records are summed correctly."""
        records = [
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=100,
                tokens_output=50,
                cost_usd=0.01,
            ),
            UsageRecord(
                provider="openai",
                model="gpt-4",
                tokens_input=200,
                tokens_output=100,
                cost_usd=0.02,
            ),
            UsageRecord(
                provider="anthropic",
                model="claude-3",
                tokens_input=300,
                tokens_output=150,
                cost_usd=0.03,
            ),
        ]
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = records
        storage.get_total_cost.return_value = 0.06
        storage.get_total_cost_by_provider.return_value = {"openai": 0.03, "anthropic": 0.03}

        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_usage()

        assert result.total_tokens_input == 600
        assert result.total_tokens_output == 300

    def test_empty_records_zero_tokens(self):
        """No records means zero token totals."""
        storage = MagicMock()
        storage.get_usage_by_date_range.return_value = []
        storage.get_total_cost.return_value = 0.0
        storage.get_total_cost_by_provider.return_value = {}

        sources = CollectorSources(cost_storage=storage)
        collector = MetricsCollector(sources=sources)

        result = collector.collect_usage()

        assert result.total_tokens_input == 0
        assert result.total_tokens_output == 0


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------


class TestUptime:
    """Tests for uptime_seconds in reliability metrics."""

    def test_uptime_is_positive(self, empty_collector):
        """Uptime is a positive number (module was imported some time ago)."""
        result = empty_collector.collect_reliability()

        assert result.uptime_seconds >= 0.0
