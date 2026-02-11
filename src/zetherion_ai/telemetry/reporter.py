"""Outbound telemetry reporter for deployed agents.

Aggregates anonymized metrics from local PostgreSQL health + cost tables
and POSTs a ``TelemetryReport`` to the central instance.  No message
content, user IDs, or PII is ever included.

Called from ``HealthAnalyzerSkill.on_heartbeat()`` once per day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from zetherion_ai import __version__
from zetherion_ai.logging import get_logger
from zetherion_ai.telemetry.models import TelemetryConsent, TelemetryReport

if TYPE_CHECKING:
    from zetherion_ai.health.storage import HealthStorage

log = get_logger("zetherion_ai.telemetry.reporter")


class TelemetryReporter:
    """Generates and sends anonymized telemetry reports."""

    def __init__(
        self,
        instance_id: str,
        central_url: str,
        api_key: str,
        consent: TelemetryConsent,
        storage: HealthStorage | None = None,
    ) -> None:
        self._instance_id = instance_id
        self._central_url = central_url.rstrip("/")
        self._api_key = api_key
        self._consent = consent
        self._storage = storage

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    async def generate_report(self) -> TelemetryReport:
        """Build a report from local health/cost data.

        Only includes categories that the owner has consented to.
        """
        metrics: dict[str, dict[str, Any]] = {}

        if self._consent.allows("health") and self._storage:
            metrics["health"] = await self._collect_health_metrics()

        if self._consent.allows("performance") and self._storage:
            metrics["performance"] = await self._collect_performance_metrics()

        if self._consent.allows("usage") and self._storage:
            metrics["usage"] = await self._collect_usage_metrics()

        if self._consent.allows("cost") and self._storage:
            metrics["cost"] = await self._collect_cost_metrics()

        if self._consent.allows("quality") and self._storage:
            metrics["quality"] = await self._collect_quality_metrics()

        return TelemetryReport(
            instance_id=self._instance_id,
            timestamp=datetime.now().isoformat(),
            version=__version__,
            consent=self._consent,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def send_report(self, report: TelemetryReport) -> bool:
        """POST a report to the central instance.

        Returns True if the central instance accepted the report.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._central_url}/api/v1/telemetry/ingest",
                    json=report.to_dict(),
                    headers={
                        "X-Instance-Key": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code == 200:
                log.info("telemetry_report_sent", instance=self._instance_id)
                return True
            log.warning(
                "telemetry_report_rejected",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        except httpx.RequestError as exc:
            log.warning("telemetry_send_failed", error=str(exc))
            return False

    async def request_deletion(self) -> bool:
        """Ask the central instance to delete all stored data for this instance."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(
                    f"{self._central_url}/api/v1/telemetry/instances/{self._instance_id}",
                    headers={"X-Instance-Key": self._api_key},
                )
            return resp.status_code == 200
        except httpx.RequestError as exc:
            log.warning("telemetry_deletion_request_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Metric collectors (aggregate-only, no PII)
    # ------------------------------------------------------------------

    async def _collect_health_metrics(self) -> dict[str, Any]:
        """Aggregate health snapshot data."""
        if self._storage is None:
            return {}
        try:
            now = datetime.now()
            snapshots = await self._storage.get_snapshots(
                start=now - timedelta(days=1),
                end=now,
                limit=1,
            )
            if not snapshots:
                return {}
            latest = snapshots[0]
            metrics = latest.metrics
            # Strip any fields that might leak user info
            return {
                "system": metrics.get("system", {}),
                "anomaly_count": len(latest.anomalies),
            }
        except Exception as exc:
            log.debug("health_metric_collection_failed", error=str(exc))
            return {}

    async def _collect_performance_metrics(self) -> dict[str, Any]:
        """Aggregate performance metrics (latency, error rates)."""
        if self._storage is None:
            return {}
        try:
            now = datetime.now()
            snapshots = await self._storage.get_snapshots(
                start=now - timedelta(days=1),
                end=now,
                limit=1,
            )
            if not snapshots:
                return {}
            metrics = snapshots[0].metrics
            perf: dict[str, Any] = metrics.get("performance", {})
            rel: dict[str, Any] = metrics.get("reliability", {})
            return {
                "latency": perf.get("latency", {}),
                "error_rates": rel.get("error_rates", {}),
            }
        except Exception as exc:
            log.debug("performance_metric_collection_failed", error=str(exc))
            return {}

    async def _collect_usage_metrics(self) -> dict[str, Any]:
        """Aggregate usage metrics (message counts, intent distribution)."""
        if self._storage is None:
            return {}
        try:
            now = datetime.now()
            snapshots = await self._storage.get_snapshots(
                start=now - timedelta(days=1),
                end=now,
                limit=1,
            )
            if not snapshots:
                return {}
            metrics = snapshots[0].metrics
            usage: dict[str, Any] = metrics.get("usage", {})
            # Only aggregate counts, never content
            return {
                "messages_total": usage.get("messages_total", 0),
                "intent_distribution": usage.get("intent_distribution", {}),
                "active_users_count": usage.get("active_users_count", 0),
            }
        except Exception as exc:
            log.debug("usage_metric_collection_failed", error=str(exc))
            return {}

    async def _collect_cost_metrics(self) -> dict[str, Any]:
        """Aggregate cost metrics (spend by provider)."""
        if self._storage is None:
            return {}
        try:
            now = datetime.now()
            snapshots = await self._storage.get_snapshots(
                start=now - timedelta(days=1),
                end=now,
                limit=1,
            )
            if not snapshots:
                return {}
            metrics = snapshots[0].metrics
            return dict(metrics.get("cost", {}))
        except Exception as exc:
            log.debug("cost_metric_collection_failed", error=str(exc))
            return {}

    async def _collect_quality_metrics(self) -> dict[str, Any]:
        """Aggregate quality metrics (confidence scores)."""
        if self._storage is None:
            return {}
        try:
            now = datetime.now()
            snapshots = await self._storage.get_snapshots(
                start=now - timedelta(days=1),
                end=now,
                limit=1,
            )
            if not snapshots:
                return {}
            metrics = snapshots[0].metrics
            return dict(metrics.get("quality", {}))
        except Exception as exc:
            log.debug("quality_metric_collection_failed", error=str(exc))
            return {}
