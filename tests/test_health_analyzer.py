"""Tests for the HealthAnalyzer anomaly detection and report generation engine."""

from __future__ import annotations

import math
import statistics
from copy import deepcopy

import pytest

from zetherion_ai.health.analyzer import (
    AnalysisResult,
    Anomaly,
    DailyReportData,
    HealthAnalyzer,
    _flatten_dict,
)

# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture()
def analyzer() -> HealthAnalyzer:
    """Return a fresh HealthAnalyzer instance."""
    return HealthAnalyzer()


@pytest.fixture()
def normal_metrics() -> dict:
    """A single metrics snapshot with normal/healthy values."""
    return {
        "performance": {
            "avg_latency_ms": {"ollama": 500.0, "gemini": 200.0},
            "p95_latency_ms": {"ollama": 800.0, "gemini": 350.0},
        },
        "reliability": {
            "error_rate_by_provider": {"ollama": 0.02, "gemini": 0.01},
            "rate_limit_count": 0,
            "uptime_seconds": 86400.0,
        },
        "usage": {
            "total_cost_usd_today": 0.50,
        },
        "system": {
            "memory_rss_mb": 256.0,
        },
        "skills": {
            "total_skills": 3,
            "ready_count": 3,
            "error_count": 0,
        },
    }


@pytest.fixture()
def baseline_snapshots(normal_metrics: dict) -> list[dict]:
    """Seven identical normal snapshots -- enough to form a valid baseline."""
    return [deepcopy(normal_metrics) for _ in range(7)]


def _make_snapshot(
    latency_ollama: float = 500.0,
    latency_gemini: float = 200.0,
    error_rate_ollama: float = 0.02,
    error_rate_gemini: float = 0.01,
    rate_limit_count: int = 0,
    memory_rss_mb: float = 256.0,
    skill_error_count: int = 0,
    uptime_seconds: float = 86400.0,
) -> dict:
    """Helper to build a metrics snapshot with customisable values."""
    return {
        "performance": {
            "avg_latency_ms": {"ollama": latency_ollama, "gemini": latency_gemini},
            "p95_latency_ms": {"ollama": latency_ollama * 1.6, "gemini": latency_gemini * 1.75},
        },
        "reliability": {
            "error_rate_by_provider": {
                "ollama": error_rate_ollama,
                "gemini": error_rate_gemini,
            },
            "rate_limit_count": rate_limit_count,
            "uptime_seconds": uptime_seconds,
        },
        "usage": {"total_cost_usd_today": 0.50},
        "system": {"memory_rss_mb": memory_rss_mb},
        "skills": {"total_skills": 3, "ready_count": 3, "error_count": skill_error_count},
    }


# =====================================================================
# _flatten_dict
# =====================================================================


class TestFlattenDict:
    """Tests for the _flatten_dict helper."""

    def test_simple_nested(self) -> None:
        result = _flatten_dict({"a": {"b": 1}})
        assert result == {"a.b": 1}

    def test_deeply_nested(self) -> None:
        result = _flatten_dict({"a": {"b": {"c": {"d": 42}}}})
        assert result == {"a.b.c.d": 42}

    def test_multiple_keys(self) -> None:
        result = _flatten_dict({"x": 1, "y": {"z": 2}})
        assert result == {"x": 1, "y.z": 2}

    def test_empty_dict(self) -> None:
        assert _flatten_dict({}) == {}

    def test_no_nesting(self) -> None:
        result = _flatten_dict({"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_custom_separator(self) -> None:
        result = _flatten_dict({"a": {"b": 1}}, sep="/")
        assert result == {"a/b": 1}

    def test_custom_parent_key(self) -> None:
        result = _flatten_dict({"b": 1}, parent_key="a")
        assert result == {"a.b": 1}

    def test_preserves_non_dict_values(self) -> None:
        result = _flatten_dict({"a": [1, 2, 3], "b": "text", "c": None, "d": True})
        assert result == {"a": [1, 2, 3], "b": "text", "c": None, "d": True}

    def test_realistic_metrics_structure(self, normal_metrics: dict) -> None:
        flat = _flatten_dict(normal_metrics)
        assert "performance.avg_latency_ms.ollama" in flat
        assert flat["performance.avg_latency_ms.ollama"] == 500.0
        assert "system.memory_rss_mb" in flat
        assert flat["skills.error_count"] == 0


# =====================================================================
# analyze_snapshot — baseline requirements
# =====================================================================


class TestAnalyzeSnapshotBaseline:
    """Tests for baseline size requirements and rolling window behavior."""

    def test_fewer_than_five_baseline_points_returns_empty(
        self, analyzer: HealthAnalyzer, normal_metrics: dict
    ) -> None:
        """With fewer than 5 baselines, no meaningful analysis is possible."""
        for count in range(5):
            baselines = [deepcopy(normal_metrics) for _ in range(count)]
            result = analyzer.analyze_snapshot(normal_metrics, baselines)
            assert result.anomalies == []
            assert result.has_critical is False
            assert result.recommended_actions == []

    def test_exactly_five_baselines_is_sufficient(
        self, analyzer: HealthAnalyzer, normal_metrics: dict
    ) -> None:
        baselines = [deepcopy(normal_metrics) for _ in range(5)]
        result = analyzer.analyze_snapshot(normal_metrics, baselines)
        assert isinstance(result, AnalysisResult)
        # Normal metrics matching baseline should produce no anomalies
        assert result.anomalies == []

    def test_rolling_window_mean_and_stddev(self, analyzer: HealthAnalyzer) -> None:
        """Verify that the baseline statistics are computed correctly."""
        # Build 7 baselines with known latency values
        latencies = [100.0, 110.0, 90.0, 105.0, 95.0, 100.0, 102.0]
        baselines = [_make_snapshot(latency_ollama=v) for v in latencies]

        mean = statistics.mean(latencies)
        stddev = statistics.pstdev(latencies)

        # Current value is the mean -- should produce no anomaly
        current = _make_snapshot(latency_ollama=mean)
        result = analyzer.analyze_snapshot(current, baselines)

        latency_anomalies = [
            a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert latency_anomalies == []

        # Current value far above mean -- should trigger
        extreme = mean + 4 * stddev
        current_extreme = _make_snapshot(latency_ollama=extreme)
        result2 = analyzer.analyze_snapshot(current_extreme, baselines)
        latency_anomalies2 = [
            a for a in result2.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert len(latency_anomalies2) == 1
        assert latency_anomalies2[0].severity == "critical"
        # Verify z-score is approximately 4.0
        assert abs(latency_anomalies2[0].z_score - 4.0) < 0.1


# =====================================================================
# analyze_snapshot — anomaly detection
# =====================================================================


class TestAnomalyDetection:
    """Tests for real-time anomaly detection via z-score."""

    def test_normal_values_no_anomaly(
        self,
        analyzer: HealthAnalyzer,
        normal_metrics: dict,
        baseline_snapshots: list[dict],
    ) -> None:
        """When current matches baseline, no anomalies should be flagged."""
        result = analyzer.analyze_snapshot(normal_metrics, baseline_snapshots)
        assert result.anomalies == []
        assert result.has_critical is False
        assert result.recommended_actions == []

    def test_latency_spike_detected(self, analyzer: HealthAnalyzer) -> None:
        """A 3x spike in latency should be detected as an anomaly."""
        baselines = [_make_snapshot(latency_ollama=500.0 + i * 2) for i in range(7)]
        # 3x the baseline -- dramatic spike
        current = _make_snapshot(latency_ollama=1500.0)
        result = analyzer.analyze_snapshot(current, baselines)

        latency_anomalies = [
            a for a in result.anomalies if "avg_latency_ms.ollama" in a.metric_path
        ]
        assert len(latency_anomalies) >= 1
        assert latency_anomalies[0].current_value == 1500.0
        assert latency_anomalies[0].z_score > 0  # value is above baseline

    def test_creeping_error_rate_detected(self, analyzer: HealthAnalyzer) -> None:
        """An error rate climbing well above baseline should be flagged."""
        baselines = [_make_snapshot(error_rate_ollama=0.02) for _ in range(7)]
        # Error rate jumps to 50% -- well beyond any reasonable stddev
        current = _make_snapshot(error_rate_ollama=0.50)
        result = analyzer.analyze_snapshot(current, baselines)

        err_anomalies = [a for a in result.anomalies if "error_rate" in a.metric_path]
        assert len(err_anomalies) >= 1

    def test_warning_vs_critical_thresholds(self, analyzer: HealthAnalyzer) -> None:
        """Values at exactly 2-sigma and 3-sigma trigger warning/critical."""
        # Build baseline with known mean=100, controlled stddev
        values = [95.0, 100.0, 105.0, 98.0, 102.0, 97.0, 103.0]
        baselines = [_make_snapshot(latency_ollama=v) for v in values]

        mean = statistics.mean(values)
        stddev = statistics.pstdev(values)

        # Just above 2 sigma -> warning
        warning_val = mean + 2.1 * stddev
        current_warning = _make_snapshot(latency_ollama=warning_val)
        result_w = analyzer.analyze_snapshot(current_warning, baselines)
        latency_w = [
            a for a in result_w.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert len(latency_w) == 1
        assert latency_w[0].severity == "warning"
        assert result_w.has_critical is False

        # Just above 3 sigma -> critical
        critical_val = mean + 3.1 * stddev
        current_critical = _make_snapshot(latency_ollama=critical_val)
        result_c = analyzer.analyze_snapshot(current_critical, baselines)
        latency_c = [
            a for a in result_c.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert len(latency_c) == 1
        assert latency_c[0].severity == "critical"
        assert result_c.has_critical is True

    def test_below_baseline_also_detected(self, analyzer: HealthAnalyzer) -> None:
        """Values far below the baseline should also trigger anomalies."""
        values = [500.0, 510.0, 490.0, 505.0, 495.0, 502.0, 498.0]
        baselines = [_make_snapshot(latency_ollama=v) for v in values]

        mean = statistics.mean(values)
        stddev = statistics.pstdev(values)

        # Far below baseline
        low_val = mean - 4.0 * stddev
        current = _make_snapshot(latency_ollama=low_val)
        result = analyzer.analyze_snapshot(current, baselines)
        latency_anomalies = [
            a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert len(latency_anomalies) == 1
        assert latency_anomalies[0].z_score < 0  # below baseline
        assert "below" in latency_anomalies[0].description

    def test_just_below_warning_threshold_no_anomaly(self, analyzer: HealthAnalyzer) -> None:
        """A value just under 2-sigma should not trigger an anomaly."""
        values = [100.0, 102.0, 98.0, 101.0, 99.0, 100.5, 99.5]
        baselines = [_make_snapshot(latency_ollama=v) for v in values]

        mean = statistics.mean(values)
        stddev = statistics.pstdev(values)

        safe_val = mean + 1.9 * stddev
        current = _make_snapshot(latency_ollama=safe_val)
        result = analyzer.analyze_snapshot(current, baselines)
        latency_anomalies = [
            a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"
        ]
        assert latency_anomalies == []

    def test_anomaly_description_contains_direction_and_values(
        self, analyzer: HealthAnalyzer
    ) -> None:
        """Anomaly descriptions should include direction, current, and mean."""
        baselines = [_make_snapshot(latency_ollama=100.0 + i) for i in range(7)]
        current = _make_snapshot(latency_ollama=5000.0)
        result = analyzer.analyze_snapshot(current, baselines)
        lat = [a for a in result.anomalies if "avg_latency_ms.ollama" in a.metric_path]
        assert len(lat) >= 1
        desc = lat[0].description
        assert "above" in desc
        assert "5000.0" in desc or "current=5000.0" in desc

    def test_non_numeric_metrics_ignored(self, analyzer: HealthAnalyzer) -> None:
        """Non-numeric values in the metrics dict should be silently skipped."""
        snap = {
            "metadata": {"version": "1.2.3", "active": True},
            "performance": {"avg_latency_ms": {"ollama": 100.0}},
        }
        baselines = [deepcopy(snap) for _ in range(7)]
        result = analyzer.analyze_snapshot(snap, baselines)
        # version string should not cause errors
        assert isinstance(result, AnalysisResult)


# =====================================================================
# Zero stddev edge case (constant baseline)
# =====================================================================


class TestZeroStddev:
    """When all baseline values are identical, stddev is 0."""

    def test_constant_baseline_same_value_no_anomaly(self, analyzer: HealthAnalyzer) -> None:
        """If current equals the constant baseline, no anomaly should fire."""
        baselines = [_make_snapshot(latency_ollama=100.0) for _ in range(7)]
        current = _make_snapshot(latency_ollama=100.0)
        result = analyzer.analyze_snapshot(current, baselines)
        lat = [a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"]
        assert lat == []

    def test_constant_baseline_different_value_flagged(self, analyzer: HealthAnalyzer) -> None:
        """If current differs from a constant baseline, flag a warning with inf z-score."""
        baselines = [_make_snapshot(latency_ollama=100.0) for _ in range(7)]
        current = _make_snapshot(latency_ollama=101.0)
        result = analyzer.analyze_snapshot(current, baselines)
        lat = [a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"]
        assert len(lat) == 1
        assert lat[0].severity == "warning"
        assert math.isinf(lat[0].z_score)
        assert lat[0].baseline_stddev == 0.0
        assert lat[0].baseline_mean == 100.0
        assert "constant baseline" in lat[0].description


# =====================================================================
# Edge cases for analyze_snapshot
# =====================================================================


class TestAnalyzeSnapshotEdgeCases:
    """Edge cases: empty, single, minimal inputs."""

    def test_empty_baseline_list(self, analyzer: HealthAnalyzer, normal_metrics: dict) -> None:
        result = analyzer.analyze_snapshot(normal_metrics, [])
        assert result.anomalies == []

    def test_single_baseline_snapshot(self, analyzer: HealthAnalyzer, normal_metrics: dict) -> None:
        result = analyzer.analyze_snapshot(normal_metrics, [normal_metrics])
        assert result.anomalies == []

    def test_empty_current_metrics(
        self, analyzer: HealthAnalyzer, baseline_snapshots: list[dict]
    ) -> None:
        result = analyzer.analyze_snapshot({}, baseline_snapshots)
        assert result.anomalies == []

    def test_all_identical_snapshots_no_anomaly(self, analyzer: HealthAnalyzer) -> None:
        """All snapshots (including current) are identical -- no anomalies."""
        snap = _make_snapshot()
        baselines = [deepcopy(snap) for _ in range(10)]
        result = analyzer.analyze_snapshot(snap, baselines)
        assert result.anomalies == []

    def test_metric_path_missing_from_some_baselines(self, analyzer: HealthAnalyzer) -> None:
        """If a metric is absent in some baselines, only present values are used."""
        baselines = []
        for i in range(7):
            snap = _make_snapshot(latency_ollama=100.0)
            if i < 3:
                # Remove ollama latency from first 3
                del snap["performance"]["avg_latency_ms"]["ollama"]
            baselines.append(snap)
        # Only 4 baselines have the path -- below _MIN_BASELINE_POINTS (5)
        current = _make_snapshot(latency_ollama=9999.0)
        result = analyzer.analyze_snapshot(current, baselines)
        lat = [a for a in result.anomalies if a.metric_path == "performance.avg_latency_ms.ollama"]
        # Not enough data points for that specific metric -- should be empty
        assert lat == []


# =====================================================================
# _recommend_actions
# =====================================================================


class TestRecommendActions:
    """Tests for the action recommendation engine."""

    def test_error_rate_anomaly_triggers_restart_skill(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="reliability.error_rate_by_provider.ollama",
                current_value=0.5,
                baseline_mean=0.02,
                baseline_stddev=0.01,
                z_score=48.0,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "restart_skill" in actions

    def test_rate_limit_anomaly_triggers_adjust(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="reliability.rate_limit_count",
                current_value=50,
                baseline_mean=2.0,
                baseline_stddev=1.0,
                z_score=48.0,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "adjust_rate_limits" in actions

    def test_memory_anomaly_triggers_clear_connections(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="system.memory_rss_mb",
                current_value=2048.0,
                baseline_mean=256.0,
                baseline_stddev=30.0,
                z_score=59.7,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "clear_stale_connections" in actions

    def test_memory_anomaly_below_baseline_no_clear(self, analyzer: HealthAnalyzer) -> None:
        """Memory anomaly below baseline (z < 0) should NOT trigger clear_stale_connections."""
        anomalies = [
            Anomaly(
                metric_path="system.memory_rss_mb",
                current_value=10.0,
                baseline_mean=256.0,
                baseline_stddev=30.0,
                z_score=-8.2,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "clear_stale_connections" not in actions

    def test_latency_anomaly_triggers_warm_models(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="performance.avg_latency_ms.ollama",
                current_value=5000.0,
                baseline_mean=500.0,
                baseline_stddev=50.0,
                z_score=90.0,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "warm_ollama_models" in actions

    def test_latency_below_baseline_no_warm(self, analyzer: HealthAnalyzer) -> None:
        """Latency below baseline (z < 0) should NOT trigger warm_ollama_models."""
        anomalies = [
            Anomaly(
                metric_path="performance.avg_latency_ms.ollama",
                current_value=50.0,
                baseline_mean=500.0,
                baseline_stddev=50.0,
                z_score=-9.0,
                severity="critical",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "warm_ollama_models" not in actions

    def test_skill_failure_triggers_restart(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="skills.skill_failure_count",
                current_value=5,
                baseline_mean=0.0,
                baseline_stddev=0.0,
                z_score=float("inf"),
                severity="warning",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "restart_skill" in actions

    def test_skill_error_triggers_restart(self, analyzer: HealthAnalyzer) -> None:
        anomalies = [
            Anomaly(
                metric_path="skills.skill_error_rate",
                current_value=0.5,
                baseline_mean=0.0,
                baseline_stddev=0.0,
                z_score=float("inf"),
                severity="warning",
                description="test",
            )
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "restart_skill" in actions

    def test_no_duplicate_actions(self, analyzer: HealthAnalyzer) -> None:
        """Multiple anomalies matching the same action should not duplicate it."""
        anomalies = [
            Anomaly(
                metric_path="reliability.error_rate_by_provider.ollama",
                current_value=0.5,
                baseline_mean=0.02,
                baseline_stddev=0.01,
                z_score=48.0,
                severity="critical",
                description="test",
            ),
            Anomaly(
                metric_path="reliability.error_rate_by_provider.gemini",
                current_value=0.4,
                baseline_mean=0.01,
                baseline_stddev=0.005,
                z_score=78.0,
                severity="critical",
                description="test",
            ),
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert actions.count("restart_skill") == 1

    def test_empty_anomalies_no_actions(self, analyzer: HealthAnalyzer) -> None:
        actions = analyzer._recommend_actions([])
        assert actions == []

    def test_multiple_different_actions(self, analyzer: HealthAnalyzer) -> None:
        """Multiple anomaly types should produce multiple distinct actions."""
        anomalies = [
            Anomaly(
                metric_path="reliability.error_rate_by_provider.ollama",
                current_value=0.5,
                baseline_mean=0.02,
                baseline_stddev=0.01,
                z_score=48.0,
                severity="critical",
                description="test",
            ),
            Anomaly(
                metric_path="reliability.rate_limit_count",
                current_value=50,
                baseline_mean=2.0,
                baseline_stddev=1.0,
                z_score=48.0,
                severity="critical",
                description="test",
            ),
            Anomaly(
                metric_path="system.memory_rss_mb",
                current_value=2048.0,
                baseline_mean=256.0,
                baseline_stddev=30.0,
                z_score=59.7,
                severity="critical",
                description="test",
            ),
            Anomaly(
                metric_path="performance.avg_latency_ms.ollama",
                current_value=5000.0,
                baseline_mean=500.0,
                baseline_stddev=50.0,
                z_score=90.0,
                severity="critical",
                description="test",
            ),
        ]
        actions = analyzer._recommend_actions(anomalies)
        assert "restart_skill" in actions
        assert "adjust_rate_limits" in actions
        assert "clear_stale_connections" in actions
        assert "warm_ollama_models" in actions


# =====================================================================
# _compute_health_score
# =====================================================================


class TestComputeHealthScore:
    """Tests for the 0-100 health score calculation."""

    def test_perfect_score(self, analyzer: HealthAnalyzer) -> None:
        """No issues at all should produce 100."""
        score = analyzer._compute_health_score(
            error_rates=[0.0, 0.0, 0.0],
            rate_limit_counts=[0, 0, 0],
            skill_error_counts=[0, 0, 0],
            memory_usages=[256.0, 300.0, 280.0],
        )
        assert score == 100.0

    def test_error_rate_penalty_linear(self, analyzer: HealthAnalyzer) -> None:
        """5% error rate -> 15 point deduction (0.05 * 300 = 15)."""
        score = analyzer._compute_health_score(
            error_rates=[0.05],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == 85.0

    def test_error_rate_penalty_capped_at_30(self, analyzer: HealthAnalyzer) -> None:
        """Error rate >= 10% should cap the penalty at 30."""
        score_10 = analyzer._compute_health_score(
            error_rates=[0.10],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        score_50 = analyzer._compute_health_score(
            error_rates=[0.50],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score_10 == 70.0
        assert score_50 == 70.0  # capped at same level

    def test_rate_limit_penalty(self, analyzer: HealthAnalyzer) -> None:
        """Each rate limit event costs 2 points, capped at 20."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[5],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == 90.0  # 100 - (5 * 2)

    def test_rate_limit_penalty_capped_at_20(self, analyzer: HealthAnalyzer) -> None:
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[100],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == 80.0  # 100 - 20 (capped)

    def test_rate_limit_penalty_summed_across_snapshots(self, analyzer: HealthAnalyzer) -> None:
        """Rate limit counts from multiple snapshots are summed."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[3, 3, 4],  # total = 10
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == 80.0  # 100 - (10 * 2) = 80

    def test_skill_error_penalty(self, analyzer: HealthAnalyzer) -> None:
        """Each max skill error costs 5 points, capped at 20."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[2],
            memory_usages=[256.0],
        )
        assert score == 90.0  # 100 - (2 * 5)

    def test_skill_error_penalty_uses_max(self, analyzer: HealthAnalyzer) -> None:
        """Skill error penalty is based on max across snapshots."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[1, 3, 2],
            memory_usages=[256.0],
        )
        assert score == 85.0  # 100 - (3 * 5)

    def test_skill_error_penalty_capped_at_20(self, analyzer: HealthAnalyzer) -> None:
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[10],
            memory_usages=[256.0],
        )
        assert score == 80.0  # 100 - 20 (capped)

    def test_memory_penalty_below_1gb_no_deduction(self, analyzer: HealthAnalyzer) -> None:
        """Memory under 1024 MB should not cause a penalty."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[1024.0],
        )
        assert score == 100.0

    def test_memory_penalty_above_1gb(self, analyzer: HealthAnalyzer) -> None:
        """Memory above 1024 MB: (max - 1024) / 100, capped at 10."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[1524.0],
        )
        # (1524 - 1024) / 100 = 5.0 points deducted
        assert score == 95.0

    def test_memory_penalty_capped_at_10(self, analyzer: HealthAnalyzer) -> None:
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[5000.0],
        )
        assert score == 90.0  # 100 - 10 (capped)

    def test_memory_penalty_uses_max(self, analyzer: HealthAnalyzer) -> None:
        """Memory penalty is based on max across snapshots."""
        score = analyzer._compute_health_score(
            error_rates=[0.0],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0, 1224.0, 512.0],
        )
        # (1224 - 1024) / 100 = 2.0
        assert score == 98.0

    def test_all_penalties_combined(self, analyzer: HealthAnalyzer) -> None:
        """Multiple issues compound the deductions."""
        score = analyzer._compute_health_score(
            error_rates=[0.05],  # -15
            rate_limit_counts=[5],  # -10
            skill_error_counts=[2],  # -10
            memory_usages=[1524.0],  # -5
        )
        expected = 100.0 - 15.0 - 10.0 - 10.0 - 5.0
        assert score == expected

    def test_score_never_below_zero(self, analyzer: HealthAnalyzer) -> None:
        """Score should be clamped at 0, never negative."""
        score = analyzer._compute_health_score(
            error_rates=[1.0],  # -30 (capped)
            rate_limit_counts=[100],  # -20 (capped)
            skill_error_counts=[100],  # -20 (capped)
            memory_usages=[10000.0],  # -10 (capped)
        )
        assert score == 20.0  # 100 - 30 - 20 - 20 - 10

    def test_empty_error_rates_slight_penalty(self, analyzer: HealthAnalyzer) -> None:
        """Empty error rates list incurs a 5-point penalty."""
        score = analyzer._compute_health_score(
            error_rates=[],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == 95.0

    def test_score_is_rounded_to_one_decimal(self, analyzer: HealthAnalyzer) -> None:
        """Score should be rounded to 1 decimal place."""
        # 0.03 error rate -> 0.03 * 300 = 9.0 ... clean, but let's
        # use a value that forces fractional rounding
        score = analyzer._compute_health_score(
            error_rates=[0.033],  # 0.033 * 300 = 9.9
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert score == round(100.0 - 9.9, 1)


# =====================================================================
# generate_daily_report
# =====================================================================


class TestGenerateDailyReport:
    """Tests for daily report generation."""

    def test_empty_snapshots(self, analyzer: HealthAnalyzer) -> None:
        """No snapshots should produce score 0 and an error summary."""
        report = analyzer.generate_daily_report("2025-01-15", [])
        assert report.date == "2025-01-15"
        assert report.overall_score == 0.0
        assert report.summary == {"error": "no_data"}
        assert "No data collected" in report.recommendations["items"][0]

    def test_single_snapshot_report(self, analyzer: HealthAnalyzer) -> None:
        """A single healthy snapshot should produce a high score."""
        snap = _make_snapshot()
        report = analyzer.generate_daily_report("2025-01-15", [snap])
        assert isinstance(report, DailyReportData)
        assert report.overall_score > 80
        assert report.summary["snapshot_count"] == 1
        assert report.summary["avg_latency_ms"] is not None

    def test_report_summary_fields(self, analyzer: HealthAnalyzer) -> None:
        """Verify all expected summary fields are present and correct."""
        snaps = [_make_snapshot() for _ in range(5)]
        report = analyzer.generate_daily_report("2025-06-01", snaps)

        assert report.summary["snapshot_count"] == 5
        assert report.summary["avg_latency_ms"] is not None
        assert report.summary["avg_error_rate"] is not None
        assert "total_rate_limits" in report.summary
        assert "max_memory_rss_mb" in report.summary
        assert "max_skill_errors" in report.summary
        assert "uptime_hours" in report.summary

    def test_report_score_reflects_errors(self, analyzer: HealthAnalyzer) -> None:
        """Snapshots with high error rates should lower the overall score."""
        healthy_snaps = [_make_snapshot() for _ in range(5)]
        report_healthy = analyzer.generate_daily_report("2025-01-01", healthy_snaps)

        unhealthy_snaps = [
            _make_snapshot(error_rate_ollama=0.20, error_rate_gemini=0.15) for _ in range(5)
        ]
        report_unhealthy = analyzer.generate_daily_report("2025-01-01", unhealthy_snaps)

        assert report_unhealthy.overall_score < report_healthy.overall_score

    def test_report_score_reflects_rate_limits(self, analyzer: HealthAnalyzer) -> None:
        rl_snaps = [_make_snapshot(rate_limit_count=5) for _ in range(5)]
        report = analyzer.generate_daily_report("2025-01-01", rl_snaps)
        # total_rl = 25, penalty = min(25*2, 20) = 20
        assert report.summary["total_rate_limits"] == 25
        assert report.overall_score <= 80

    def test_report_score_reflects_skill_errors(self, analyzer: HealthAnalyzer) -> None:
        snaps = [_make_snapshot(skill_error_count=3) for _ in range(5)]
        report = analyzer.generate_daily_report("2025-01-01", snaps)
        assert report.summary["max_skill_errors"] == 3
        assert report.overall_score <= 85

    def test_report_score_reflects_high_memory(self, analyzer: HealthAnalyzer) -> None:
        snaps = [_make_snapshot(memory_rss_mb=2048.0) for _ in range(3)]
        report = analyzer.generate_daily_report("2025-01-01", snaps)
        assert report.summary["max_memory_rss_mb"] == 2048.0
        assert report.overall_score < 100

    def test_report_latency_averages_across_providers(self, analyzer: HealthAnalyzer) -> None:
        """avg_latency_ms in summary should be the mean across providers and snapshots."""
        # Each snapshot: ollama=400, gemini=200 -> per-snap mean = 300
        snaps = [_make_snapshot(latency_ollama=400.0, latency_gemini=200.0) for _ in range(3)]
        report = analyzer.generate_daily_report("2025-01-01", snaps)
        assert report.summary["avg_latency_ms"] == 300.0

    def test_report_uptime_in_hours(self, analyzer: HealthAnalyzer) -> None:
        snaps = [_make_snapshot(uptime_seconds=36000.0)]  # 10 hours
        report = analyzer.generate_daily_report("2025-01-01", snaps)
        assert report.summary["uptime_hours"] == 10.0

    def test_to_dict_roundtrip(self, analyzer: HealthAnalyzer) -> None:
        """DailyReportData.to_dict should produce a JSON-serialisable dict."""
        snaps = [_make_snapshot() for _ in range(5)]
        report = analyzer.generate_daily_report("2025-01-01", snaps)
        d = report.to_dict()
        assert d["date"] == "2025-01-01"
        assert isinstance(d["overall_score"], float)
        assert isinstance(d["summary"], dict)
        assert isinstance(d["recommendations"], dict)


# =====================================================================
# _daily_recommendations
# =====================================================================


class TestDailyRecommendations:
    """Tests for human-readable daily recommendations."""

    def test_all_nominal(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert len(recs["items"]) == 1
        assert "nominal" in recs["items"][0].lower()
        assert "generated_at" in recs

    def test_high_error_rate_recommendation(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.10],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert any("error rate" in item.lower() for item in recs["items"])

    def test_rate_limit_recommendation(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[5, 4, 3],  # total = 12 > 10
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert any("rate limit" in item.lower() for item in recs["items"])

    def test_rate_limit_below_threshold_no_recommendation(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[3, 3, 3],  # total = 9 <= 10
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        rate_limit_recs = [item for item in recs["items"] if "rate limit" in item.lower()]
        assert rate_limit_recs == []

    def test_skill_error_recommendation(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[0],
            skill_error_counts=[1],
            memory_usages=[256.0],
        )
        assert any("skill" in item.lower() for item in recs["items"])

    def test_high_memory_recommendation(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[2048.0],
        )
        assert any("memory" in item.lower() for item in recs["items"])
        assert any("2048" in item for item in recs["items"])

    def test_multiple_recommendations(self, analyzer: HealthAnalyzer) -> None:
        """Multiple issues should produce multiple recommendations."""
        recs = analyzer._daily_recommendations(
            error_rates=[0.10],
            rate_limit_counts=[20],
            skill_error_counts=[2],
            memory_usages=[2048.0],
        )
        # All 4 issues present
        assert len(recs["items"]) == 4

    def test_recommendations_include_generated_at(self, analyzer: HealthAnalyzer) -> None:
        recs = analyzer._daily_recommendations(
            error_rates=[0.01],
            rate_limit_counts=[0],
            skill_error_counts=[0],
            memory_usages=[256.0],
        )
        assert "generated_at" in recs


# =====================================================================
# Dataclass serialisation
# =====================================================================


class TestDataclassSerialization:
    """Tests for Anomaly and AnalysisResult to_dict methods."""

    def test_anomaly_to_dict(self) -> None:
        a = Anomaly(
            metric_path="test.metric",
            current_value=100.0,
            baseline_mean=50.12345,
            baseline_stddev=5.67891,
            z_score=8.79654,
            severity="critical",
            description="test anomaly",
        )
        d = a.to_dict()
        assert d["metric_path"] == "test.metric"
        assert d["current_value"] == 100.0
        assert d["baseline_mean"] == 50.1234  # rounded to 4 decimals
        assert d["baseline_stddev"] == 5.6789  # rounded to 4 decimals
        assert d["z_score"] == 8.80  # rounded to 2 decimals
        assert d["severity"] == "critical"
        assert d["description"] == "test anomaly"

    def test_analysis_result_to_dict_empty(self) -> None:
        r = AnalysisResult()
        d = r.to_dict()
        assert d == {"anomalies": [], "has_critical": False, "recommended_actions": []}

    def test_analysis_result_to_dict_with_anomalies(self) -> None:
        a = Anomaly(
            metric_path="p",
            current_value=1.0,
            baseline_mean=0.0,
            baseline_stddev=0.1,
            z_score=10.0,
            severity="critical",
            description="d",
        )
        r = AnalysisResult(anomalies=[a], has_critical=True, recommended_actions=["restart_skill"])
        d = r.to_dict()
        assert len(d["anomalies"]) == 1
        assert d["has_critical"] is True
        assert d["recommended_actions"] == ["restart_skill"]

    def test_daily_report_to_dict(self) -> None:
        r = DailyReportData(
            date="2025-01-01",
            overall_score=85.5,
            summary={"snapshot_count": 10},
            recommendations={"items": ["All good"]},
        )
        d = r.to_dict()
        assert d["date"] == "2025-01-01"
        assert d["overall_score"] == 85.5
        assert d["summary"]["snapshot_count"] == 10


# =====================================================================
# Integration-style: full analyze_snapshot + actions
# =====================================================================


class TestEndToEndAnalysis:
    """End-to-end tests combining anomaly detection with action recommendations."""

    def test_high_error_rate_triggers_restart_recommendation(
        self, analyzer: HealthAnalyzer
    ) -> None:
        baselines = [_make_snapshot(error_rate_ollama=0.02) for _ in range(7)]
        current = _make_snapshot(error_rate_ollama=0.80)
        result = analyzer.analyze_snapshot(current, baselines)
        assert result.has_critical or len(result.anomalies) > 0
        assert "restart_skill" in result.recommended_actions

    def test_rate_limit_spike_triggers_adjust(self, analyzer: HealthAnalyzer) -> None:
        baselines = [_make_snapshot(rate_limit_count=i) for i in range(7)]
        current = _make_snapshot(rate_limit_count=500)
        result = analyzer.analyze_snapshot(current, baselines)
        assert "adjust_rate_limits" in result.recommended_actions

    def test_memory_spike_triggers_clear_connections(self, analyzer: HealthAnalyzer) -> None:
        baselines = [_make_snapshot(memory_rss_mb=256.0 + i * 2) for i in range(7)]
        current = _make_snapshot(memory_rss_mb=4096.0)
        result = analyzer.analyze_snapshot(current, baselines)
        assert "clear_stale_connections" in result.recommended_actions

    def test_latency_spike_triggers_warm_models(self, analyzer: HealthAnalyzer) -> None:
        baselines = [_make_snapshot(latency_ollama=500.0 + i) for i in range(7)]
        current = _make_snapshot(latency_ollama=10000.0)
        result = analyzer.analyze_snapshot(current, baselines)
        assert "warm_ollama_models" in result.recommended_actions

    def test_completely_healthy_no_actions(
        self,
        analyzer: HealthAnalyzer,
        normal_metrics: dict,
        baseline_snapshots: list[dict],
    ) -> None:
        result = analyzer.analyze_snapshot(normal_metrics, baseline_snapshots)
        assert result.anomalies == []
        assert result.has_critical is False
        assert result.recommended_actions == []

    def test_multiple_simultaneous_anomalies(self, analyzer: HealthAnalyzer) -> None:
        """When multiple metrics spike simultaneously, all are detected."""
        # Add slight variance to baselines so stddev > 0 and z-scores are finite
        baselines = [
            _make_snapshot(
                latency_ollama=500.0 + i * 2,
                error_rate_ollama=0.02 + i * 0.001,
                memory_rss_mb=256.0 + i,
                rate_limit_count=i,
            )
            for i in range(7)
        ]
        current = _make_snapshot(
            latency_ollama=10000.0,
            error_rate_ollama=0.90,
            memory_rss_mb=4096.0,
            rate_limit_count=500,
        )
        result = analyzer.analyze_snapshot(current, baselines)
        assert len(result.anomalies) >= 3  # at least latency, error, memory
        assert result.has_critical is True
        # Should recommend multiple actions
        assert len(result.recommended_actions) >= 2
