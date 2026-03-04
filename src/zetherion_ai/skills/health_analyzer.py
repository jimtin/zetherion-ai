"""Health Analyzer Skill for Zetherion AI.

Integrates the health/ package (collector, analyzer, healer, storage)
into the skill framework.  Every heartbeat it:

1. Collects a metrics snapshot via MetricsCollector
2. Stores the snapshot in PostgreSQL
3. Every 6th beat (~30 min): runs anomaly detection and self-healing
4. Every 288th beat (~24 h): generates a daily report

Users can query health via intents:
- ``health_check``  → current status summary
- ``health_report`` → latest daily report
- ``system_status`` → detailed system metrics
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.costs.storage import CostStorage
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.scheduler.heartbeat import HeartbeatStats
    from zetherion_ai.skills.registry import SkillRegistry

log = get_logger("zetherion_ai.skills.health_analyzer")

# Beat intervals for analysis cycles
_ANALYSIS_EVERY_N_BEATS = 6  # ~30 min at default 5-min interval
_DAILY_REPORT_EVERY_N_BEATS = 288  # ~24 h at default 5-min interval
_INCIDENT_DIGEST_FALLBACK_HOUR = 9
_INCIDENT_DIGEST_FALLBACK_MINUTE = 0


class HealthAnalyzerSkill(Skill):
    """Monitors instance health, detects anomalies, triggers self-healing."""

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        db_pool: asyncpg.Pool | None = None,
        cost_storage: CostStorage | None = None,
        heartbeat_stats: HeartbeatStats | None = None,
        skill_registry: SkillRegistry | None = None,
        self_healing_enabled: bool = True,
    ) -> None:
        super().__init__(memory)
        self._db_pool = db_pool
        self._cost_storage = cost_storage
        self._heartbeat_stats = heartbeat_stats
        self._skill_registry = skill_registry
        self._self_healing_enabled = self_healing_enabled

        # Lazily initialised components (require async init)
        self._storage: Any = None
        self._collector: Any = None
        self._analyzer: Any = None
        self._healer: Any = None

        # Beat counter for scheduling analysis/report cycles
        self._beat_count: int = 0
        self._incident_digest_fallback_hour: int = _INCIDENT_DIGEST_FALLBACK_HOUR
        self._incident_digest_fallback_minute: int = _INCIDENT_DIGEST_FALLBACK_MINUTE
        self._last_incident_digest_by_user: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Skill ABC implementation
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="health_analyzer",
            description="Monitors system health, detects anomalies, and triggers self-healing",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                    Permission.SEND_DM,
                }
            ),
            intents=["health_check", "health_report", "system_status"],
        )

    async def initialize(self) -> bool:
        """Set up health subsystem components."""
        from zetherion_ai.health.analyzer import HealthAnalyzer
        from zetherion_ai.health.collector import CollectorSources, MetricsCollector
        from zetherion_ai.health.healer import SelfHealer
        from zetherion_ai.health.storage import HealthStorage

        # Storage
        self._storage = HealthStorage()
        if self._db_pool is not None:
            await self._storage.initialize(self._db_pool)

        # Collector
        self._collector = MetricsCollector(
            CollectorSources(
                cost_storage=self._cost_storage,
                heartbeat_stats=self._heartbeat_stats,
                skill_registry=self._skill_registry,
            )
        )

        # Analyzer
        self._analyzer = HealthAnalyzer()

        # Self-healer
        self._healer = SelfHealer(
            storage=self._storage,
            skill_registry=self._skill_registry,
            db_pool=self._db_pool,
            enabled=self._self_healing_enabled,
        )

        log.info("health_analyzer_skill_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle health-related queries."""
        intent = request.intent

        if intent == "health_check":
            return await self._handle_health_check(request)
        elif intent == "health_report":
            return await self._handle_health_report(request)
        elif intent == "system_status":
            return await self._handle_system_status(request)
        else:
            return SkillResponse.error_response(request.id, f"Unknown health intent: {intent}")

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Collect metrics every beat; analyse and heal periodically."""
        self._beat_count += 1
        actions: list[HeartbeatAction] = []

        # 1. Always collect a snapshot
        snapshot = await self._collect_snapshot()

        # 2. Every Nth beat: run anomaly detection + self-healing
        if self._beat_count % _ANALYSIS_EVERY_N_BEATS == 0:
            analysis_actions = await self._run_analysis(snapshot, user_ids)
            actions.extend(analysis_actions)

        # 3. Every 288th beat (~24h): generate daily report
        if self._beat_count % _DAILY_REPORT_EVERY_N_BEATS == 0:
            await self._generate_daily_report()

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Inject a brief health summary into agent context."""
        if self._collector is None:
            return None

        try:
            metrics = self._collector.collect_all()
            reliability = metrics.get("reliability", {})
            usage = metrics.get("usage", {})
            skills = metrics.get("skills", {})

            uptime_h = round(reliability.get("uptime_seconds", 0) / 3600, 1)
            cost_today = round(usage.get("total_cost_usd_today", 0), 4)
            ready = skills.get("ready_count", 0)
            total = skills.get("total_skills", 0)

            return (
                f"[Health] Uptime: {uptime_h}h | "
                f"Cost today: ${cost_today} | "
                f"Skills: {ready}/{total} ready"
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal: snapshot collection
    # ------------------------------------------------------------------

    async def _collect_snapshot(self) -> dict[str, Any]:
        """Collect and persist a metrics snapshot."""
        from zetherion_ai.health.storage import MetricsSnapshot

        metrics: dict[str, Any] = self._collector.collect_all()

        snapshot = MetricsSnapshot(
            timestamp=datetime.now(),
            metrics=metrics,
        )

        if self._storage is not None and self._storage._pool is not None:
            try:
                await self._storage.save_snapshot(snapshot)
            except Exception as exc:
                log.warning("snapshot_save_failed", error=str(exc))

        return metrics

    # ------------------------------------------------------------------
    # Internal: analysis and self-healing
    # ------------------------------------------------------------------

    async def _run_analysis(
        self,
        current_metrics: dict[str, Any],
        user_ids: list[str],
    ) -> list[HeartbeatAction]:
        """Run anomaly detection against 24h baseline and trigger healing."""
        actions: list[HeartbeatAction] = []

        if self._analyzer is None or self._storage is None:
            return actions

        # Fetch 24h baseline snapshots
        try:
            now = datetime.now()
            baseline_snapshots = await self._storage.get_snapshots(
                start=now - timedelta(hours=24),
                end=now,
                limit=500,
            )
            baseline_metrics = [s.metrics for s in baseline_snapshots]
        except Exception as exc:
            log.warning("baseline_fetch_failed", error=str(exc))
            return actions

        # Detect anomalies
        result = self._analyzer.analyze_snapshot(current_metrics, baseline_metrics)

        # Store anomalies in the latest snapshot
        if result.anomalies and self._storage._pool is not None:
            from zetherion_ai.health.storage import MetricsSnapshot

            snapshot = MetricsSnapshot(
                timestamp=datetime.now(),
                metrics=current_metrics,
                anomalies=result.to_dict(),
            )
            import contextlib

            with contextlib.suppress(Exception):
                await self._storage.save_snapshot(snapshot)

        # Self-heal if recommended
        if result.recommended_actions and self._healer is not None:
            try:
                await self._healer.execute_recommended(
                    result.recommended_actions, trigger="anomaly_detection"
                )
            except Exception as exc:
                log.warning("self_healing_failed", error=str(exc))

        # Incident-aware notification lifecycle:
        # immediate first/escalation alert + deduped repeat suppression + morning digest.
        incident_actions = await self._build_incident_actions(result.anomalies, user_ids)
        actions.extend(incident_actions)

        return actions

    @staticmethod
    def _severity_rank(severity: "IncidentSeverity") -> int:
        """Return comparable rank for incident severities."""
        from zetherion_ai.health.storage import IncidentSeverity

        ranks = {
            IncidentSeverity.LOW: 1,
            IncidentSeverity.MEDIUM: 2,
            IncidentSeverity.HIGH: 3,
            IncidentSeverity.CRITICAL: 4,
        }
        return ranks.get(severity, 0)

    @staticmethod
    def _map_anomaly_severity(raw_severity: str) -> "IncidentSeverity":
        """Map analyzer severity labels to persistent incident severity."""
        from zetherion_ai.health.storage import IncidentSeverity

        normalized = str(raw_severity or "").strip().lower()
        if normalized == "critical":
            return IncidentSeverity.CRITICAL
        if normalized == "warning":
            return IncidentSeverity.HIGH
        return IncidentSeverity.MEDIUM

    @staticmethod
    def _incident_identity(anomaly: Any) -> tuple[str, str, str]:
        """Build provider+fingerprint identity for anomaly incident tracking."""
        metric_path = str(getattr(anomaly, "metric_path", "") or "").strip()
        description = str(getattr(anomaly, "description", "") or "").strip()
        if not description:
            description = "Health anomaly detected."

        provider = "system"
        if metric_path:
            parts = [p for p in metric_path.split(".") if p]
            if len(parts) >= 3:
                provider = parts[-1]
            elif parts:
                provider = parts[0]
            if provider.isdigit():
                provider = parts[0]

        fingerprint_base = metric_path or description.lower()
        fingerprint = hashlib.sha256(fingerprint_base.encode("utf-8")).hexdigest()
        return provider, fingerprint, description

    async def _learn_digest_time(self) -> tuple[int, int]:
        """Learn preferred digest send time from historical digest notifications."""
        fallback = (self._incident_digest_fallback_hour, self._incident_digest_fallback_minute)
        if self._storage is None or self._storage._pool is None:
            return fallback

        try:
            timestamps = await self._storage.get_recent_incident_digest_times(limit=40)
        except Exception:
            return fallback

        if len(timestamps) < 3:
            return fallback

        buckets = Counter((ts.hour, (ts.minute // 15) * 15) for ts in timestamps if ts is not None)
        if not buckets:
            return fallback
        return buckets.most_common(1)[0][0]

    async def _build_incident_actions(
        self,
        anomalies: list[Any],
        user_ids: list[str],
    ) -> list[HeartbeatAction]:
        """Build incident-aware notification actions (immediate + digest)."""
        actions: list[HeartbeatAction] = []
        if not user_ids:
            return actions

        # If persistence is unavailable, keep legacy critical alert behavior.
        if self._storage is None or self._storage._pool is None:
            critical = [
                str(getattr(a, "description", "")).strip()
                for a in anomalies
                if str(getattr(a, "severity", "")).strip().lower() == "critical"
            ]
            critical = [line for line in critical if line]
            if critical:
                actions.append(
                    HeartbeatAction(
                        skill_name="health_analyzer",
                        action_type="send_message",
                        user_id=user_ids[0],
                        data={
                            "message": (
                                "**Health Alert**: Critical anomalies detected\n"
                                + "\n".join(f"- {line}" for line in critical[:5])
                            )
                        },
                        priority=9,
                    )
                )
            return actions

        from zetherion_ai.health.storage import NotificationIncident

        now = datetime.now()
        active_keys: set[tuple[str, str]] = set()
        immediate_lines: list[str] = []

        for anomaly in anomalies:
            if str(getattr(anomaly, "severity", "")).strip().lower() != "critical":
                continue
            provider, fingerprint, description = self._incident_identity(anomaly)
            severity = self._map_anomaly_severity(str(getattr(anomaly, "severity", "")))
            active_keys.add((provider, fingerprint))

            existing = await self._storage.get_notification_incident(provider, fingerprint)
            if existing is None:
                incident = NotificationIncident(
                    provider=provider,
                    fingerprint=fingerprint,
                    severity=severity,
                    description=description,
                    first_seen=now,
                    last_seen=now,
                    state="open",
                    occurrence_count=1,
                    last_state_change_at=now,
                )
                incident_id = await self._storage.create_notification_incident(incident)
                await self._storage.mark_notification_incident_notified(incident_id, now)
                immediate_lines.append(f"- [{severity.value}] {description} ({provider})")
                continue

            severity_escalated = self._severity_rank(severity) > self._severity_rank(existing.severity)
            state_reopened = existing.state != "open"
            next_severity = severity if severity_escalated else existing.severity
            next_state = "open" if state_reopened else None
            state_changed = severity_escalated or state_reopened

            await self._storage.update_notification_incident_observation(
                existing.id,  # type: ignore[arg-type]
                severity=next_severity,
                description=description,
                observed_at=now,
                state=next_state,
                state_changed=state_changed,
            )

            if state_changed:
                await self._storage.mark_notification_incident_notified(existing.id, now)  # type: ignore[arg-type]
                if severity_escalated and state_reopened:
                    reason = "reopened + escalated"
                elif severity_escalated:
                    reason = "escalated"
                else:
                    reason = "reopened"
                immediate_lines.append(f"- [{next_severity.value}] {description} ({provider}, {reason})")

        open_incidents = await self._storage.list_open_notification_incidents()
        for incident in open_incidents:
            if (incident.provider, incident.fingerprint) not in active_keys:
                await self._storage.resolve_notification_incident(
                    incident.id,  # type: ignore[arg-type]
                    resolved_at=now,
                )

        if immediate_lines:
            lines = immediate_lines[:5]
            if len(immediate_lines) > len(lines):
                lines.append(f"- +{len(immediate_lines) - len(lines)} more incident(s)")
            actions.append(
                HeartbeatAction(
                    skill_name="health_analyzer",
                    action_type="send_message",
                    user_id=user_ids[0],
                    data={"message": "**Health Incident Alert**\n" + "\n".join(lines)},
                    priority=9,
                )
            )
            return actions

        unresolved = await self._storage.list_open_notification_incidents()
        if not unresolved:
            return actions

        digest_hour, digest_minute = await self._learn_digest_time()
        owner_id = user_ids[0]
        today = now.date().isoformat()
        if self._last_incident_digest_by_user.get(owner_id) == today:
            return actions
        if any(
            inc.last_digest_notified_at and inc.last_digest_notified_at.date() == now.date()
            for inc in unresolved
        ):
            self._last_incident_digest_by_user[owner_id] = today
            return actions
        if (now.hour, now.minute) < (digest_hour, digest_minute):
            return actions

        lines = [
            f"- [{incident.severity.value}] {incident.description} ({incident.provider})"
            for incident in unresolved[:10]
        ]
        if len(unresolved) > 10:
            lines.append(f"- +{len(unresolved) - 10} more unresolved incident(s)")
        actions.append(
            HeartbeatAction(
                skill_name="health_analyzer",
                action_type="send_message",
                user_id=owner_id,
                data={
                    "message": (
                        f"**Morning health digest**: {len(unresolved)} unresolved incident(s)\n"
                        + "\n".join(lines)
                    )
                },
                priority=5,
            )
        )
        self._last_incident_digest_by_user[owner_id] = today
        for incident in unresolved:
            await self._storage.mark_notification_incident_digest_notified(
                incident.id,  # type: ignore[arg-type]
                now,
            )

        return actions

    # ------------------------------------------------------------------
    # Internal: daily report
    # ------------------------------------------------------------------

    async def _generate_daily_report(self) -> None:
        """Generate and persist a daily health report."""
        if self._analyzer is None or self._storage is None:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            snapshots = await self._storage.get_snapshots(start=day_start, end=now, limit=500)
            snapshot_metrics = [s.metrics for s in snapshots]

            report_data = self._analyzer.generate_daily_report(today, snapshot_metrics)

            from zetherion_ai.health.storage import DailyReport

            report = DailyReport(
                date=report_data.date,
                summary=report_data.summary,
                recommendations=report_data.recommendations,
                overall_score=report_data.overall_score,
            )
            await self._storage.save_daily_report(report)
            log.info("daily_report_generated", date=today, score=report_data.overall_score)

        except Exception as exc:
            log.error("daily_report_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_health_check(self, request: SkillRequest) -> SkillResponse:
        """Return current health status."""
        metrics = self._collector.collect_all() if self._collector else {}

        # Quick score estimate from current data
        reliability = metrics.get("reliability", {})
        skills = metrics.get("skills", {})

        status = "healthy"
        if skills.get("error_count", 0) > 0:
            status = "degraded"
        error_rates = list(reliability.get("error_rate_by_provider", {}).values())
        if error_rates and max(error_rates) > 0.1:
            status = "degraded"
        if skills.get("ready_count", 0) == 0 and skills.get("total_skills", 0) > 0:
            status = "critical"

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"System status: **{status}**",
            data={
                "status": status,
                "metrics": metrics,
            },
        )

    async def _handle_health_report(self, request: SkillRequest) -> SkillResponse:
        """Return the latest daily report."""
        if self._storage is None or self._storage._pool is None:
            return SkillResponse.error_response(request.id, "Health storage not available")

        today = datetime.now().strftime("%Y-%m-%d")
        report = await self._storage.get_daily_report(today)

        if report is None:
            # Try yesterday
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            report = await self._storage.get_daily_report(yesterday)

        if report is None:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No health reports available yet. Reports are generated every 24 hours.",
                data={},
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Health report for {report.date}: score **{report.overall_score}/100**",
            data=report.to_dict(),
        )

    async def _handle_system_status(self, request: SkillRequest) -> SkillResponse:
        """Return detailed system metrics."""
        metrics = self._collector.collect_all() if self._collector else {}

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Detailed system status",
            data={"metrics": metrics},
        )
