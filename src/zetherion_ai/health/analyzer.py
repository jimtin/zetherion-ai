"""Anomaly detection and health report generation.

Uses simple z-score analysis against a rolling 24-hour baseline to
detect metric anomalies.  No external ML dependencies — only stdlib
``statistics``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.health.analyzer")

# Thresholds for z-score anomaly detection
_Z_SCORE_WARNING = 2.0
_Z_SCORE_CRITICAL = 3.0

# Minimum number of data points to compute a meaningful baseline
_MIN_BASELINE_POINTS = 5


@dataclass
class Anomaly:
    """A single detected anomaly."""

    metric_path: str  # e.g. "performance.avg_latency_ms.ollama"
    current_value: float
    baseline_mean: float
    baseline_stddev: float
    z_score: float
    severity: str  # "warning" or "critical"
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_path": self.metric_path,
            "current_value": self.current_value,
            "baseline_mean": round(self.baseline_mean, 4),
            "baseline_stddev": round(self.baseline_stddev, 4),
            "z_score": round(self.z_score, 2),
            "severity": self.severity,
            "description": self.description,
        }


@dataclass
class AnalysisResult:
    """Result of analysing a single snapshot against a baseline."""

    anomalies: list[Anomaly] = field(default_factory=list)
    has_critical: bool = False
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomalies": [a.to_dict() for a in self.anomalies],
            "has_critical": self.has_critical,
            "recommended_actions": self.recommended_actions,
        }


@dataclass
class DailyReportData:
    """Computed data for a daily health report."""

    date: str
    overall_score: float  # 0–100
    summary: dict[str, Any] = field(default_factory=dict)
    recommendations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "overall_score": self.overall_score,
            "summary": self.summary,
            "recommendations": self.recommendations,
        }


class HealthAnalyzer:
    """Stateless analysis engine.

    Call ``analyze_snapshot`` on each heartbeat to detect real-time anomalies
    and ``generate_daily_report`` at the end of each day for trend summaries.
    """

    # ------------------------------------------------------------------
    # Real-time anomaly detection
    # ------------------------------------------------------------------

    def analyze_snapshot(
        self,
        current_metrics: dict[str, Any],
        baseline_snapshots: list[dict[str, Any]],
    ) -> AnalysisResult:
        """Detect anomalies in *current_metrics* relative to the baseline.

        Args:
            current_metrics: The latest ``MetricsSnapshot.metrics`` dict.
            baseline_snapshots: A list of previous ``MetricsSnapshot.metrics``
                dicts forming the rolling baseline (typically 24 h).

        Returns:
            An ``AnalysisResult`` with any detected anomalies and
            recommended self-healing actions.
        """
        result = AnalysisResult()

        if len(baseline_snapshots) < _MIN_BASELINE_POINTS:
            return result  # Not enough data for meaningful detection

        # Flatten current and baseline metrics into dotted-path -> value
        current_flat = _flatten_dict(current_metrics)
        baseline_flats = [_flatten_dict(s) for s in baseline_snapshots]

        for path, current_value in current_flat.items():
            if not isinstance(current_value, int | float):
                continue

            # Gather historical values for this path
            historical: list[float] = []
            for bf in baseline_flats:
                val = bf.get(path)
                if isinstance(val, int | float):
                    historical.append(float(val))

            if len(historical) < _MIN_BASELINE_POINTS:
                continue

            mean = statistics.mean(historical)
            stddev = statistics.pstdev(historical)

            if stddev == 0:
                # All historical values are identical — only flag if current
                # differs (use a simple inequality check).
                if float(current_value) != mean:
                    anomaly = Anomaly(
                        metric_path=path,
                        current_value=float(current_value),
                        baseline_mean=mean,
                        baseline_stddev=0.0,
                        z_score=float("inf"),
                        severity="warning",
                        description=f"{path} changed from constant baseline {mean}",
                    )
                    result.anomalies.append(anomaly)
                continue

            z_score = (float(current_value) - mean) / stddev

            if abs(z_score) >= _Z_SCORE_CRITICAL:
                severity = "critical"
                result.has_critical = True
            elif abs(z_score) >= _Z_SCORE_WARNING:
                severity = "warning"
            else:
                continue

            direction = "above" if z_score > 0 else "below"
            anomaly = Anomaly(
                metric_path=path,
                current_value=float(current_value),
                baseline_mean=mean,
                baseline_stddev=stddev,
                z_score=z_score,
                severity=severity,
                description=(
                    f"{path} is {abs(z_score):.1f}σ {direction} baseline "
                    f"(current={current_value}, mean={mean:.2f})"
                ),
            )
            result.anomalies.append(anomaly)

        # Generate recommended actions from anomalies
        result.recommended_actions = self._recommend_actions(result.anomalies)

        return result

    # ------------------------------------------------------------------
    # Daily report generation
    # ------------------------------------------------------------------

    def generate_daily_report(
        self,
        date: str,
        snapshots: list[dict[str, Any]],
    ) -> DailyReportData:
        """Summarise a day's worth of snapshots into a health report.

        Args:
            date: The date string (YYYY-MM-DD).
            snapshots: List of ``MetricsSnapshot.metrics`` dicts for the day.

        Returns:
            A ``DailyReportData`` with an overall score and summary.
        """
        if not snapshots:
            return DailyReportData(
                date=date,
                overall_score=0.0,
                summary={"error": "no_data"},
                recommendations={"items": ["No data collected — check heartbeat scheduler"]},
            )

        # Aggregate key signals across the day
        avg_latencies: list[float] = []
        error_rates: list[float] = []
        rate_limit_counts: list[int] = []
        memory_usages: list[float] = []
        skill_error_counts: list[int] = []
        uptime_values: list[float] = []

        for snap in snapshots:
            perf = snap.get("performance", {})
            rel = snap.get("reliability", {})
            sys = snap.get("system", {})
            skills = snap.get("skills", {})

            # Average latency across all providers for this snapshot
            lat_vals = list(perf.get("avg_latency_ms", {}).values())
            if lat_vals:
                avg_latencies.append(statistics.mean(lat_vals))

            # Mean error rate across providers
            err_vals = list(rel.get("error_rate_by_provider", {}).values())
            if err_vals:
                error_rates.append(statistics.mean(err_vals))

            rate_limit_counts.append(rel.get("rate_limit_count", 0))
            memory_usages.append(sys.get("memory_rss_mb", 0.0))
            skill_error_counts.append(skills.get("error_count", 0))
            uptime_values.append(rel.get("uptime_seconds", 0.0))

        # Compute the overall health score (0-100)
        score = self._compute_health_score(
            error_rates=error_rates,
            rate_limit_counts=rate_limit_counts,
            skill_error_counts=skill_error_counts,
            memory_usages=memory_usages,
        )

        summary: dict[str, Any] = {
            "snapshot_count": len(snapshots),
            "avg_latency_ms": round(statistics.mean(avg_latencies), 1) if avg_latencies else None,
            "avg_error_rate": round(statistics.mean(error_rates), 4) if error_rates else None,
            "total_rate_limits": sum(rate_limit_counts),
            "max_memory_rss_mb": round(max(memory_usages), 1) if memory_usages else None,
            "max_skill_errors": max(skill_error_counts) if skill_error_counts else 0,
            "uptime_hours": (round(max(uptime_values) / 3600, 1) if uptime_values else 0.0),
        }

        # Build recommendations
        recommendations = self._daily_recommendations(
            error_rates=error_rates,
            rate_limit_counts=rate_limit_counts,
            skill_error_counts=skill_error_counts,
            memory_usages=memory_usages,
        )

        return DailyReportData(
            date=date,
            overall_score=score,
            summary=summary,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_health_score(
        self,
        error_rates: list[float],
        rate_limit_counts: list[int],
        skill_error_counts: list[int],
        memory_usages: list[float],
    ) -> float:
        """Compute a 0–100 health score from day signals.

        Starts at 100 and deducts points for issues:
        - High error rate: up to -30
        - Rate limiting: up to -20
        - Skill errors: up to -20
        - High memory: up to -10
        - Missing data: -20
        """
        score = 100.0

        # Error rate penalty
        if error_rates:
            avg_err = statistics.mean(error_rates)
            # 0% errors = 0 penalty, 10%+ = -30
            score -= min(avg_err * 300, 30.0)
        else:
            score -= 5.0  # slight penalty for no data

        # Rate limit penalty
        if rate_limit_counts:
            total_rl = sum(rate_limit_counts)
            # Each rate limit event costs 2 points, capped at 20
            score -= min(total_rl * 2.0, 20.0)

        # Skill error penalty
        if skill_error_counts:
            max_errors = max(skill_error_counts)
            # Each errored skill costs 5 points, capped at 20
            score -= min(max_errors * 5.0, 20.0)

        # Memory penalty (high memory = potential leak)
        if memory_usages:
            max_mem = max(memory_usages)
            # Over 1 GB = start deducting, over 2 GB = -10
            if max_mem > 1024:
                score -= min((max_mem - 1024) / 100, 10.0)

        return round(max(score, 0.0), 1)

    def _recommend_actions(self, anomalies: list[Anomaly]) -> list[str]:
        """Generate self-healing recommendations from anomalies."""
        actions: list[str] = []
        seen: set[str] = set()

        for a in anomalies:
            path = a.metric_path.lower()

            if "error_rate" in path and "restart_skill" not in seen:
                actions.append("restart_skill")
                seen.add("restart_skill")

            if "rate_limit" in path and "adjust_rate_limits" not in seen:
                actions.append("adjust_rate_limits")
                seen.add("adjust_rate_limits")

            if "memory" in path and a.z_score > 0 and "clear_stale_connections" not in seen:
                actions.append("clear_stale_connections")
                seen.add("clear_stale_connections")

            if ("skill_failure" in path or "skill_error" in path) and "restart_skill" not in seen:
                actions.append("restart_skill")
                seen.add("restart_skill")

            if "latency" in path and a.z_score > 0 and "warm_ollama_models" not in seen:
                actions.append("warm_ollama_models")
                seen.add("warm_ollama_models")

        return actions

    def _daily_recommendations(
        self,
        error_rates: list[float],
        rate_limit_counts: list[int],
        skill_error_counts: list[int],
        memory_usages: list[float],
    ) -> dict[str, Any]:
        """Generate human-readable recommendations for a daily report."""
        items: list[str] = []

        if error_rates and statistics.mean(error_rates) > 0.05:
            items.append(
                "Error rate averaged above 5% — review provider health "
                "and consider fallback routing"
            )

        if rate_limit_counts and sum(rate_limit_counts) > 10:
            items.append(
                "Frequent rate limiting detected — consider adjusting "
                "request concurrency or upgrading tier"
            )

        if skill_error_counts and max(skill_error_counts) > 0:
            items.append(
                "One or more skills in error state — check skill logs and restart if needed"
            )

        if memory_usages and max(memory_usages) > 1024:
            items.append(
                f"Peak memory usage reached {max(memory_usages):.0f} MB — "
                "monitor for potential memory leaks"
            )

        if not items:
            items.append("All systems nominal — no recommendations at this time")

        return {"items": items, "generated_at": datetime.now().isoformat()}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _flatten_dict(
    d: dict[str, Any],
    parent_key: str = "",
    sep: str = ".",
) -> dict[str, Any]:
    """Flatten a nested dict into dot-separated keys.

    Example::

        {"a": {"b": 1}} -> {"a.b": 1}
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
