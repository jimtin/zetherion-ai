"""Aggregation helpers for app watcher behavior analytics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zetherion_ai.api.tenant import TenantManager


class AnalyticsAggregator:
    """Compute session summaries and daily funnel metrics from tenant events."""

    def __init__(self, tenant_manager: TenantManager) -> None:
        self._tenant_manager = tenant_manager

    async def summarize_session(
        self,
        tenant_id: str,
        *,
        web_session_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a behavior summary for one web/chat session."""
        events = await self._tenant_manager.get_web_events(
            tenant_id,
            web_session_id=web_session_id,
            session_id=session_id,
            limit=1000,
        )
        by_type = Counter(e.get("event_type", "unknown") for e in events)

        stage = "awareness"
        if by_type.get("conversion", 0) > 0:
            stage = "converted"
        elif by_type.get("form_submit", 0) > 0:
            stage = "intent"
        elif by_type.get("form_start", 0) > 0:
            stage = "considering"
        elif by_type.get("page_view", 0) > 1:
            stage = "engaged"

        friction = {
            "rage_clicks": by_type.get("rage_click", 0),
            "dead_clicks": by_type.get("dead_click", 0),
            "js_errors": by_type.get("js_error", 0),
            "api_errors": by_type.get("api_error", 0),
        }

        web_vitals: dict[str, list[float]] = defaultdict(list)
        for ev in events:
            if ev.get("event_type") != "web_vitals":
                continue
            props = ev.get("properties") or {}
            for metric in ("lcp", "inp", "cls", "fcp"):
                raw_val = props.get(metric)
                if isinstance(raw_val, float | int):
                    web_vitals[metric].append(float(raw_val))

        perf = {
            metric: round(sum(values) / len(values), 3)
            for metric, values in web_vitals.items()
            if values
        }

        return {
            "event_count": len(events),
            "events_by_type": dict(by_type),
            "funnel_stage": stage,
            "friction": friction,
            "performance": perf,
            "converted": by_type.get("conversion", 0) > 0,
        }

    async def compute_daily_funnel(
        self,
        tenant_id: str,
        *,
        metric_date: date | None = None,
        funnel_name: str = "primary",
    ) -> list[dict[str, Any]]:
        """Compute and upsert a daily funnel for one tenant."""
        metric_date = metric_date or datetime.now(tz=UTC).date()
        next_day = metric_date + timedelta(days=1)

        events = await self._tenant_manager.get_web_events(tenant_id, limit=10000)
        day_events = [
            e
            for e in events
            if e.get("occurred_at")
            and metric_date <= self._as_datetime(e["occurred_at"]).date() < next_day
        ]

        stage_order = ["page_view", "form_start", "form_submit", "conversion"]
        sessions_by_stage: dict[str, set[str]] = {stage: set() for stage in stage_order}

        for event in day_events:
            stage = event.get("event_type")
            if stage not in sessions_by_stage:
                continue
            key = str(
                event.get("web_session_id")
                or event.get("session_id")
                or event.get("event_id")
                or "unknown"
            )
            sessions_by_stage[stage].add(key)

        root = max(1, len(sessions_by_stage["page_view"]))
        rows: list[dict[str, Any]] = []
        prev_count = 0
        for idx, stage in enumerate(stage_order):
            count = len(sessions_by_stage[stage])
            if idx == 0 or prev_count == 0:
                drop_off = None
            else:
                drop_off = round(max(0.0, (prev_count - count) / prev_count), 4)
            conversion = round(count / root, 4)

            row = await self._tenant_manager.upsert_funnel_stage_daily(
                tenant_id,
                metric_date=metric_date,
                funnel_name=funnel_name,
                stage_name=stage,
                stage_order=idx,
                users_count=count,
                drop_off_rate=drop_off,
                conversion_rate=conversion,
                metadata={"source": "analytics_aggregator"},
            )
            rows.append(row)
            prev_count = count

        return rows

    async def detect_release_regression(self, tenant_id: str) -> dict[str, Any]:
        """Compare pre/post release error rates around the latest release marker."""
        markers = await self._tenant_manager.get_release_markers(tenant_id, limit=1)
        if not markers:
            return {"has_release": False, "regression": False}

        release_dt = self._as_datetime(markers[0]["deployed_at"])
        events = await self._tenant_manager.get_web_events(tenant_id, limit=10000)

        pre: list[dict[str, Any]] = []
        post: list[dict[str, Any]] = []
        for event in events:
            occurred_at_raw = event.get("occurred_at")
            if not occurred_at_raw:
                continue
            occurred_at = self._as_datetime(occurred_at_raw)
            if occurred_at < release_dt:
                pre.append(event)
            else:
                post.append(event)

        def _error_rate(rows: list[dict[str, Any]]) -> float:
            if not rows:
                return 0.0
            errors = sum(1 for row in rows if row.get("event_type") in {"js_error", "api_error"})
            return errors / max(1, len(rows))

        pre_rate = _error_rate(pre)
        post_rate = _error_rate(post)
        regressed = post_rate > max(0.02, pre_rate * 1.5)

        return {
            "has_release": True,
            "release_marker": markers[0],
            "pre_error_rate": round(pre_rate, 4),
            "post_error_rate": round(post_rate, 4),
            "regression": regressed,
        }

    @staticmethod
    def _as_datetime(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
