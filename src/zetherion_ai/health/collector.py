"""Metrics collection from existing system sources.

Gathers performance, reliability, usage, cost, and system-health metrics
from the various subsystems that already exist in Zetherion AI:

- CostStorage / CostTracker — API latency, error rates, cost trends
- HeartbeatStats — beat counts, action success/failure
- SkillRegistry — skill statuses, handle counts
- System resources — memory (psutil), disk usage
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.costs.storage import CostStorage
    from zetherion_ai.scheduler.heartbeat import HeartbeatStats
    from zetherion_ai.skills.registry import SkillRegistry

log = get_logger("zetherion_ai.health.collector")

# Boot time recorded when the module is first imported
_BOOT_TIME: datetime = datetime.now()


@dataclass
class CollectorSources:
    """Optional references to the subsystems the collector reads from.

    All fields are optional so the collector degrades gracefully when a
    source is unavailable (e.g. during tests or if a subsystem failed to
    start).
    """

    cost_storage: CostStorage | None = None
    heartbeat_stats: HeartbeatStats | None = None
    skill_registry: SkillRegistry | None = None
    data_dir: str = "data"


@dataclass
class PerformanceMetrics:
    """LLM and system performance metrics."""

    avg_latency_ms: dict[str, float] = field(default_factory=dict)  # provider -> avg ms
    p95_latency_ms: dict[str, float] = field(default_factory=dict)  # provider -> p95 ms
    total_requests: int = 0
    requests_by_provider: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "total_requests": self.total_requests,
            "requests_by_provider": self.requests_by_provider,
        }


@dataclass
class ReliabilityMetrics:
    """Error rates and uptime metrics."""

    error_rate_by_provider: dict[str, float] = field(default_factory=dict)  # 0.0-1.0
    rate_limit_count: int = 0
    rate_limit_by_provider: dict[str, int] = field(default_factory=dict)
    skill_failure_count: int = 0
    skill_error_names: list[str] = field(default_factory=list)
    heartbeat_success_rate: float = 1.0
    uptime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_rate_by_provider": self.error_rate_by_provider,
            "rate_limit_count": self.rate_limit_count,
            "rate_limit_by_provider": self.rate_limit_by_provider,
            "skill_failure_count": self.skill_failure_count,
            "skill_error_names": self.skill_error_names,
            "heartbeat_success_rate": self.heartbeat_success_rate,
            "uptime_seconds": self.uptime_seconds,
        }


@dataclass
class UsageMetrics:
    """Feature usage and activity metrics."""

    total_cost_usd_today: float = 0.0
    cost_by_provider: dict[str, float] = field(default_factory=dict)
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    heartbeat_total_beats: int = 0
    heartbeat_total_actions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost_usd_today": self.total_cost_usd_today,
            "cost_by_provider": self.cost_by_provider,
            "total_tokens_input": self.total_tokens_input,
            "total_tokens_output": self.total_tokens_output,
            "heartbeat_total_beats": self.heartbeat_total_beats,
            "heartbeat_total_actions": self.heartbeat_total_actions,
        }


@dataclass
class SystemMetrics:
    """OS-level resource metrics."""

    memory_rss_mb: float = 0.0
    memory_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_usage_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_rss_mb": self.memory_rss_mb,
            "memory_percent": self.memory_percent,
            "disk_total_gb": self.disk_total_gb,
            "disk_used_gb": self.disk_used_gb,
            "disk_free_gb": self.disk_free_gb,
            "disk_usage_percent": self.disk_usage_percent,
        }


@dataclass
class SkillHealthMetrics:
    """Per-skill health information."""

    total_skills: int = 0
    ready_count: int = 0
    error_count: int = 0
    skills_by_status: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_skills": self.total_skills,
            "ready_count": self.ready_count,
            "error_count": self.error_count,
            "skills_by_status": self.skills_by_status,
        }


class MetricsCollector:
    """Collects metrics from all available system sources.

    Designed for graceful degradation: if a source is unavailable the
    collector returns zeroed-out metrics for that category rather than
    raising an exception.
    """

    def __init__(self, sources: CollectorSources | None = None) -> None:
        self._sources = sources or CollectorSources()

    @property
    def sources(self) -> CollectorSources:
        return self._sources

    def update_sources(self, sources: CollectorSources) -> None:
        """Replace the source references (e.g. after late initialisation)."""
        self._sources = sources

    def collect_all(self) -> dict[str, Any]:
        """Collect all metric categories and return a flat dict.

        This is the primary entry point — the health skill calls this
        once per heartbeat and feeds the result into a ``MetricsSnapshot``.
        """
        start = time.monotonic()

        performance = self.collect_performance()
        reliability = self.collect_reliability()
        usage = self.collect_usage()
        system = self.collect_system()
        skills = self.collect_skill_health()

        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        return {
            "performance": performance.to_dict(),
            "reliability": reliability.to_dict(),
            "usage": usage.to_dict(),
            "system": system.to_dict(),
            "skills": skills.to_dict(),
            "collection_time_ms": elapsed_ms,
            "collected_at": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    def collect_performance(self) -> PerformanceMetrics:
        """Collect LLM performance metrics from CostStorage."""
        metrics = PerformanceMetrics()

        storage = self._sources.cost_storage
        if storage is None:
            return metrics

        try:
            end = datetime.now()
            start = end - timedelta(hours=1)
            records = storage.get_usage_by_date_range(start, end)

            if not records:
                return metrics

            # Group latencies by provider
            latencies: dict[str, list[int]] = {}
            for rec in records:
                metrics.total_requests += 1
                prov = rec.provider
                metrics.requests_by_provider[prov] = metrics.requests_by_provider.get(prov, 0) + 1
                if rec.latency_ms is not None:
                    latencies.setdefault(prov, []).append(rec.latency_ms)

            # Compute averages and P95
            for prov, lats in latencies.items():
                if not lats:
                    continue
                lats_sorted = sorted(lats)
                metrics.avg_latency_ms[prov] = round(sum(lats_sorted) / len(lats_sorted), 1)
                p95_idx = int(len(lats_sorted) * 0.95)
                p95_idx = min(p95_idx, len(lats_sorted) - 1)
                metrics.p95_latency_ms[prov] = float(lats_sorted[p95_idx])

        except Exception as exc:
            log.warning("collect_performance_failed", error=str(exc))

        return metrics

    # ------------------------------------------------------------------
    # Reliability
    # ------------------------------------------------------------------

    def collect_reliability(self) -> ReliabilityMetrics:
        """Collect reliability metrics (error rates, uptime, rate limits)."""
        metrics = ReliabilityMetrics()
        metrics.uptime_seconds = (datetime.now() - _BOOT_TIME).total_seconds()

        storage = self._sources.cost_storage
        if storage is not None:
            try:
                end = datetime.now()
                start = end - timedelta(hours=1)
                records = storage.get_usage_by_date_range(start, end)

                # Error rate by provider
                provider_total: dict[str, int] = {}
                provider_errors: dict[str, int] = {}
                for rec in records:
                    prov = rec.provider
                    provider_total[prov] = provider_total.get(prov, 0) + 1
                    if not rec.success:
                        provider_errors[prov] = provider_errors.get(prov, 0) + 1
                    if rec.rate_limit_hit:
                        metrics.rate_limit_count += 1
                        metrics.rate_limit_by_provider[prov] = (
                            metrics.rate_limit_by_provider.get(prov, 0) + 1
                        )

                for prov, total in provider_total.items():
                    errors = provider_errors.get(prov, 0)
                    rate = round(errors / total, 4) if total else 0.0
                    metrics.error_rate_by_provider[prov] = rate

            except Exception as exc:
                log.warning("collect_reliability_cost_failed", error=str(exc))

        # Heartbeat stats
        hb = self._sources.heartbeat_stats
        if hb is not None:
            try:
                total = hb.successful_actions + hb.failed_actions
                if total > 0:
                    metrics.heartbeat_success_rate = round(hb.successful_actions / total, 4)
            except Exception as exc:
                log.warning("collect_reliability_heartbeat_failed", error=str(exc))

        # Skill errors
        registry = self._sources.skill_registry
        if registry is not None:
            try:
                status_summary = registry.get_status_summary()
                metrics.skill_failure_count = status_summary.get("error_count", 0)
                metrics.skill_error_names = status_summary.get("by_status", {}).get("error", [])
            except Exception as exc:
                log.warning("collect_reliability_skills_failed", error=str(exc))

        return metrics

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------

    def collect_usage(self) -> UsageMetrics:
        """Collect usage/cost metrics from CostStorage."""
        metrics = UsageMetrics()

        storage = self._sources.cost_storage
        if storage is not None:
            try:
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                today_end = today_start + timedelta(days=1)

                metrics.total_cost_usd_today = storage.get_total_cost(today_start, today_end)
                metrics.cost_by_provider = storage.get_total_cost_by_provider(
                    today_start, today_end
                )

                # Token totals from today's records
                records = storage.get_usage_by_date_range(today_start, today_end)
                for rec in records:
                    metrics.total_tokens_input += rec.tokens_input
                    metrics.total_tokens_output += rec.tokens_output

            except Exception as exc:
                log.warning("collect_usage_failed", error=str(exc))

        hb = self._sources.heartbeat_stats
        if hb is not None:
            try:
                metrics.heartbeat_total_beats = hb.total_beats
                metrics.heartbeat_total_actions = hb.total_actions
            except Exception as exc:
                log.warning("collect_usage_heartbeat_failed", error=str(exc))

        return metrics

    # ------------------------------------------------------------------
    # System resources
    # ------------------------------------------------------------------

    def collect_system(self) -> SystemMetrics:
        """Collect OS-level resource metrics."""
        metrics = SystemMetrics()

        # Memory via psutil (optional dependency)
        try:
            import psutil  # type: ignore[import-untyped]

            proc = psutil.Process()
            mem_info = proc.memory_info()
            metrics.memory_rss_mb = round(mem_info.rss / (1024 * 1024), 1)
            metrics.memory_percent = round(proc.memory_percent(), 1)
        except (ImportError, Exception) as exc:
            log.debug("collect_system_memory_failed", error=str(exc))

        # Disk usage of data directory
        try:
            disk = shutil.disk_usage(self._sources.data_dir)
            metrics.disk_total_gb = round(disk.total / (1024**3), 2)
            metrics.disk_used_gb = round(disk.used / (1024**3), 2)
            metrics.disk_free_gb = round(disk.free / (1024**3), 2)
            metrics.disk_usage_percent = (
                round(disk.used / disk.total * 100, 1) if disk.total else 0.0
            )
        except Exception as exc:
            log.debug("collect_system_disk_failed", error=str(exc))

        return metrics

    # ------------------------------------------------------------------
    # Skill health
    # ------------------------------------------------------------------

    def collect_skill_health(self) -> SkillHealthMetrics:
        """Collect skill registry status information."""
        metrics = SkillHealthMetrics()

        registry = self._sources.skill_registry
        if registry is None:
            return metrics

        try:
            summary = registry.get_status_summary()
            metrics.total_skills = summary.get("total_skills", 0)
            metrics.ready_count = summary.get("ready_count", 0)
            metrics.error_count = summary.get("error_count", 0)
            metrics.skills_by_status = summary.get("by_status", {})
        except Exception as exc:
            log.warning("collect_skill_health_failed", error=str(exc))

        return metrics
