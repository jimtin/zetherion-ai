"""Tests for analytics aggregation helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.analytics.aggregator import AnalyticsAggregator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "expected_stage"),
    [
        ([{"event_type": "conversion"}], "converted"),
        ([{"event_type": "form_submit"}], "intent"),
        ([{"event_type": "form_start"}], "considering"),
        ([{"event_type": "page_view"}, {"event_type": "page_view"}], "engaged"),
        ([{"event_type": "click"}], "awareness"),
    ],
)
async def test_summarize_session_stage_mapping(
    events: list[dict[str, object]],
    expected_stage: str,
) -> None:
    tm = AsyncMock()
    tm.get_web_events = AsyncMock(return_value=events)
    aggregator = AnalyticsAggregator(tm)

    summary = await aggregator.summarize_session("tenant-1", session_id="sess-1")
    assert summary["funnel_stage"] == expected_stage


@pytest.mark.asyncio
async def test_summarize_session_includes_friction_and_perf() -> None:
    tm = AsyncMock()
    tm.get_web_events = AsyncMock(
        return_value=[
            {"event_type": "rage_click"},
            {"event_type": "dead_click"},
            {"event_type": "js_error"},
            {"event_type": "api_error"},
            {"event_type": "conversion"},
            {"event_type": "web_vitals", "properties": {"lcp": 2.1, "cls": 0.03, "inp": "bad"}},
            {"event_type": "web_vitals", "properties": {"lcp": 2.9, "cls": 0.05, "fcp": 1.2}},
        ]
    )
    aggregator = AnalyticsAggregator(tm)

    summary = await aggregator.summarize_session("tenant-1", web_session_id="ws-1")
    assert summary["event_count"] == 7
    assert summary["converted"] is True
    assert summary["friction"] == {
        "rage_clicks": 1,
        "dead_clicks": 1,
        "js_errors": 1,
        "api_errors": 1,
    }
    assert summary["performance"]["lcp"] == 2.5
    assert summary["performance"]["cls"] == 0.04
    assert summary["performance"]["fcp"] == 1.2
    assert "inp" not in summary["performance"]


@pytest.mark.asyncio
async def test_compute_daily_funnel_upserts_expected_counts_and_dropoff() -> None:
    target_day = date(2026, 2, 25)
    tm = AsyncMock()
    tm.get_web_events = AsyncMock(
        return_value=[
            # In-window events
            {
                "event_type": "page_view",
                "web_session_id": "s1",
                "occurred_at": "2026-02-25T10:00:00Z",
            },
            {
                "event_type": "page_view",
                "web_session_id": "s2",
                "occurred_at": "2026-02-25T10:01:00Z",
            },
            {
                "event_type": "page_view",
                "web_session_id": "s3",
                "occurred_at": "2026-02-25T10:02:00Z",
            },
            {
                "event_type": "form_start",
                "web_session_id": "s1",
                "occurred_at": "2026-02-25T10:03:00Z",
            },
            {
                "event_type": "form_start",
                "web_session_id": "s2",
                "occurred_at": "2026-02-25T10:04:00Z",
            },
            {
                "event_type": "form_submit",
                "web_session_id": "s1",
                "occurred_at": "2026-02-25T10:05:00Z",
            },
            {
                "event_type": "conversion",
                "web_session_id": "s1",
                "occurred_at": "2026-02-25T10:06:00Z",
            },
            # Out-of-window, ignored
            {
                "event_type": "page_view",
                "web_session_id": "s9",
                "occurred_at": "2026-02-24T23:59:59Z",
            },
        ]
    )

    async def _upsert(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {
            "stage_name": kwargs["stage_name"],
            "users_count": kwargs["users_count"],
            "drop_off_rate": kwargs["drop_off_rate"],
            "conversion_rate": kwargs["conversion_rate"],
        }

    tm.upsert_funnel_stage_daily = AsyncMock(side_effect=_upsert)
    aggregator = AnalyticsAggregator(tm)

    rows = await aggregator.compute_daily_funnel("tenant-1", metric_date=target_day)
    assert [row["stage_name"] for row in rows] == [
        "page_view",
        "form_start",
        "form_submit",
        "conversion",
    ]
    assert [row["users_count"] for row in rows] == [3, 2, 1, 1]
    assert rows[0]["drop_off_rate"] is None
    assert rows[1]["drop_off_rate"] == 0.3333
    assert rows[2]["drop_off_rate"] == 0.5
    assert rows[3]["drop_off_rate"] == 0.0
    assert rows[3]["conversion_rate"] == 0.3333
    assert tm.upsert_funnel_stage_daily.await_count == 4


@pytest.mark.asyncio
async def test_detect_release_regression_handles_missing_markers() -> None:
    tm = AsyncMock()
    tm.get_release_markers = AsyncMock(return_value=[])
    aggregator = AnalyticsAggregator(tm)

    result = await aggregator.detect_release_regression("tenant-1")
    assert result == {"has_release": False, "regression": False}


@pytest.mark.asyncio
async def test_detect_release_regression_true_and_false_paths() -> None:
    release_at = datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)
    tm = AsyncMock()
    tm.get_release_markers = AsyncMock(
        side_effect=[
            [{"deployed_at": release_at}],
            [{"deployed_at": release_at}],
        ]
    )
    tm.get_web_events = AsyncMock(
        side_effect=[
            [
                {"event_type": "page_view", "occurred_at": "2026-02-25T11:59:00Z"},
                {"event_type": "page_view", "occurred_at": "2026-02-25T11:58:00Z"},
                {"event_type": "js_error", "occurred_at": "2026-02-25T12:01:00Z"},
                {"event_type": "page_view", "occurred_at": "2026-02-25T12:02:00Z"},
            ],
            [
                {"event_type": "js_error", "occurred_at": "2026-02-25T11:58:00Z"},
                {"event_type": "page_view", "occurred_at": "2026-02-25T11:59:00Z"},
                {"event_type": "page_view", "occurred_at": "2026-02-25T12:01:00Z"},
                {"event_type": "page_view", "occurred_at": "2026-02-25T12:02:00Z"},
            ],
        ]
    )
    aggregator = AnalyticsAggregator(tm)

    regressed = await aggregator.detect_release_regression("tenant-1")
    assert regressed["has_release"] is True
    assert regressed["regression"] is True
    assert regressed["pre_error_rate"] == 0.0
    assert regressed["post_error_rate"] == 0.5

    stable = await aggregator.detect_release_regression("tenant-1")
    assert stable["has_release"] is True
    assert stable["regression"] is False


@pytest.mark.asyncio
async def test_detect_release_regression_ignores_missing_timestamps_and_empty_buckets() -> None:
    release_at = datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)
    tm = AsyncMock()
    tm.get_release_markers = AsyncMock(return_value=[{"deployed_at": release_at}])
    tm.get_web_events = AsyncMock(
        return_value=[
            {"event_type": "js_error"},  # missing occurred_at -> ignored
        ]
    )
    aggregator = AnalyticsAggregator(tm)

    result = await aggregator.detect_release_regression("tenant-1")
    assert result["has_release"] is True
    assert result["pre_error_rate"] == 0.0
    assert result["post_error_rate"] == 0.0
    assert result["regression"] is False


def test_as_datetime_parses_iso_z() -> None:
    parsed = AnalyticsAggregator._as_datetime("2026-02-25T12:00:00Z")
    assert parsed.tzinfo is not None
    assert parsed.year == 2026
