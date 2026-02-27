"""Background analytics jobs for hourly and daily aggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.analytics.aggregator import AnalyticsAggregator
from zetherion_ai.analytics.recommendations import RecommendationEngine
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.analytics.replay_store import ReplayStore
    from zetherion_ai.api.tenant import TenantManager

log = get_logger("zetherion_ai.analytics.jobs")


class AnalyticsJobRunner:
    """Runs periodic per-tenant analytics and recommendation jobs."""

    def __init__(
        self,
        tenant_manager: TenantManager,
        *,
        replay_store: ReplayStore | None = None,
        hourly_interval_seconds: int = 3600,
        daily_interval_seconds: int = 86400,
    ) -> None:
        self._tenant_manager = tenant_manager
        self._replay_store = replay_store
        self._hourly_interval_seconds = max(60, hourly_interval_seconds)
        self._daily_interval_seconds = max(300, daily_interval_seconds)
        self._aggregator = AnalyticsAggregator(tenant_manager)
        self._engine = RecommendationEngine(tenant_manager)

    async def run_hourly_once(self, tenant_id: str) -> dict[str, Any]:
        """Run hourly aggregation for one tenant."""
        funnel = await self._aggregator.compute_daily_funnel(tenant_id)
        release = await self._aggregator.detect_release_regression(tenant_id)
        candidates = self._engine.generate_candidates(
            session_summary={
                "events_by_type": {},
                "friction": {
                    "rage_clicks": 0,
                    "dead_clicks": 0,
                    "js_errors": 0,
                    "api_errors": 0,
                },
            },
            funnel_rows=funnel,
            release_regression=release,
        )
        persisted = await self._engine.persist_candidates(
            tenant_id, candidates, source="hourly_job"
        )
        return {
            "tenant_id": tenant_id,
            "funnel_rows": len(funnel),
            "release_regression": release.get("regression", False),
            "recommendations_created": len(persisted),
        }

    async def run_daily_once(
        self, tenant_id: str, *, metric_date: date | None = None
    ) -> dict[str, Any]:
        """Run daily rollup for one tenant."""
        target_date = metric_date or datetime.now(tz=UTC).date()
        funnel = await self._aggregator.compute_daily_funnel(tenant_id, metric_date=target_date)
        settings = get_settings()
        events_pruned = await self._tenant_manager.prune_web_events(
            tenant_id,
            retention_days=max(1, int(settings.analytics_event_retention_days)),
        )
        replay_keys = await self._tenant_manager.prune_replay_chunks(
            tenant_id,
            retention_days=max(1, int(settings.analytics_replay_retention_days)),
        )
        replay_objects_deleted = 0
        if self._replay_store is not None and replay_keys:
            for key in replay_keys:
                try:
                    deleted = await self._replay_store.delete_chunk(key)
                except Exception:
                    log.exception("replay_chunk_delete_failed", tenant_id=tenant_id, object_key=key)
                    continue
                if deleted:
                    replay_objects_deleted += 1

        return {
            "tenant_id": tenant_id,
            "metric_date": target_date.isoformat(),
            "funnel_rows": len(funnel),
            "events_pruned": events_pruned,
            "replay_chunks_pruned": len(replay_keys),
            "replay_objects_deleted": replay_objects_deleted,
        }

    async def run_all_tenants_once(self) -> list[dict[str, Any]]:
        """Run one full hourly pass across all active tenants."""
        tenants = await self._tenant_manager.list_tenants()
        results: list[dict[str, Any]] = []
        for tenant in tenants:
            tenant_id = str(tenant["tenant_id"])
            try:
                result = await self.run_hourly_once(tenant_id)
            except Exception:
                log.exception("analytics_hourly_job_failed", tenant_id=tenant_id)
                continue
            results.append(result)
        return results

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """Run hourly and daily loops until stop_event is set."""
        last_daily_run: date | None = None
        while not stop_event.is_set():
            now = datetime.now(tz=UTC)
            await self.run_all_tenants_once()

            if last_daily_run != now.date():
                tenants = await self._tenant_manager.list_tenants()
                for tenant in tenants:
                    tenant_id = str(tenant["tenant_id"])
                    try:
                        await self.run_daily_once(tenant_id, metric_date=now.date())
                    except Exception:
                        log.exception("analytics_daily_job_failed", tenant_id=tenant_id)
                last_daily_run = now.date()

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=min(self._hourly_interval_seconds, self._daily_interval_seconds),
                )
            except TimeoutError:
                continue
