"""Tests for analytics background job runner."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.analytics.jobs import AnalyticsJobRunner


class TestAnalyticsJobRunner:
    @pytest.mark.asyncio
    async def test_run_hourly_once(self) -> None:
        tm = AsyncMock()
        runner = AnalyticsJobRunner(tm)

        runner._aggregator = MagicMock()
        runner._aggregator.compute_daily_funnel = AsyncMock(
            return_value=[{"stage_name": "conversion", "conversion_rate": 0.02}]
        )
        runner._aggregator.detect_release_regression = AsyncMock(
            return_value={"regression": True, "has_release": True}
        )

        runner._engine = MagicMock()
        runner._engine.generate_candidates = MagicMock(return_value=[MagicMock()])
        runner._engine.persist_candidates = AsyncMock(return_value=[{"recommendation_id": "rec-1"}])

        result = await runner.run_hourly_once("tenant-1")
        assert result["tenant_id"] == "tenant-1"
        assert result["funnel_rows"] == 1
        assert result["release_regression"] is True
        assert result["recommendations_created"] == 1

    @pytest.mark.asyncio
    async def test_run_daily_once(self) -> None:
        tm = AsyncMock()
        tm.prune_web_events = AsyncMock(return_value=4)
        tm.prune_replay_chunks = AsyncMock(return_value=["replay/chunk-1.bin"])
        replay_store = AsyncMock()
        replay_store.delete_chunk = AsyncMock(return_value=True)
        runner = AnalyticsJobRunner(tm)
        runner._replay_store = replay_store

        runner._aggregator = MagicMock()
        runner._aggregator.compute_daily_funnel = AsyncMock(
            return_value=[{"stage_name": "page_view"}]
        )

        mock_settings = MagicMock(
            analytics_event_retention_days=90,
            analytics_replay_retention_days=14,
        )
        with patch("zetherion_ai.analytics.jobs.get_settings", return_value=mock_settings):
            result = await runner.run_daily_once("tenant-1", metric_date=date(2026, 2, 24))
        assert result["tenant_id"] == "tenant-1"
        assert result["metric_date"] == "2026-02-24"
        assert result["funnel_rows"] == 1
        assert result["events_pruned"] == 4
        assert result["replay_chunks_pruned"] == 1
        assert result["replay_objects_deleted"] == 1
        replay_store.delete_chunk.assert_awaited_once_with("replay/chunk-1.bin")

    @pytest.mark.asyncio
    async def test_run_all_tenants_once(self) -> None:
        tm = AsyncMock()
        tm.list_tenants = AsyncMock(return_value=[{"tenant_id": "t-1"}, {"tenant_id": "t-2"}])
        runner = AnalyticsJobRunner(tm)
        runner.run_hourly_once = AsyncMock(
            side_effect=[
                {"tenant_id": "t-1", "recommendations_created": 1},
                {"tenant_id": "t-2", "recommendations_created": 0},
            ]
        )

        results = await runner.run_all_tenants_once()
        assert len(results) == 2
        assert results[0]["tenant_id"] == "t-1"
        assert results[1]["tenant_id"] == "t-2"

    @pytest.mark.asyncio
    async def test_run_all_tenants_once_skips_failed_tenant(self) -> None:
        tm = AsyncMock()
        tm.list_tenants = AsyncMock(return_value=[{"tenant_id": "t-1"}, {"tenant_id": "t-2"}])
        runner = AnalyticsJobRunner(tm)
        runner.run_hourly_once = AsyncMock(
            side_effect=[RuntimeError("boom"), {"tenant_id": "t-2", "recommendations_created": 0}]
        )

        results = await runner.run_all_tenants_once()
        assert len(results) == 1
        assert results[0]["tenant_id"] == "t-2"

    @pytest.mark.asyncio
    async def test_run_daily_once_handles_replay_store_delete_exception(self) -> None:
        tm = AsyncMock()
        tm.prune_web_events = AsyncMock(return_value=1)
        tm.prune_replay_chunks = AsyncMock(return_value=["replay/chunk-1.bin"])
        replay_store = AsyncMock()
        replay_store.delete_chunk = AsyncMock(side_effect=RuntimeError("boom"))
        runner = AnalyticsJobRunner(tm, replay_store=replay_store)
        runner._aggregator = MagicMock()
        runner._aggregator.compute_daily_funnel = AsyncMock(
            return_value=[{"stage_name": "page_view"}]
        )

        mock_settings = MagicMock(
            analytics_event_retention_days=90,
            analytics_replay_retention_days=14,
        )
        with patch("zetherion_ai.analytics.jobs.get_settings", return_value=mock_settings):
            result = await runner.run_daily_once("tenant-1", metric_date=date(2026, 2, 24))
        assert result["replay_chunks_pruned"] == 1
        assert result["replay_objects_deleted"] == 0

    @pytest.mark.asyncio
    async def test_run_loop_handles_timeout_and_graceful_stop(self) -> None:
        tm = AsyncMock()
        tm.list_tenants = AsyncMock(return_value=[{"tenant_id": "tenant-1"}])
        runner = AnalyticsJobRunner(tm)
        runner.run_all_tenants_once = AsyncMock(return_value=[])
        runner.run_daily_once = AsyncMock(return_value={})
        stop_event = asyncio.Event()

        state = {"calls": 0}

        async def fake_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            state["calls"] += 1
            if state["calls"] == 1:
                awaitable.close()
                raise TimeoutError
            stop_event.set()
            return await awaitable

        with patch("zetherion_ai.analytics.jobs.asyncio.wait_for", side_effect=fake_wait_for):
            await runner.run_loop(stop_event)

        assert runner.run_all_tenants_once.await_count >= 2
        runner.run_daily_once.assert_awaited()

    @pytest.mark.asyncio
    async def test_run_loop_handles_daily_job_failure(self) -> None:
        tm = AsyncMock()
        tm.list_tenants = AsyncMock(return_value=[{"tenant_id": "tenant-1"}])
        runner = AnalyticsJobRunner(tm)
        runner.run_all_tenants_once = AsyncMock(return_value=[])
        runner.run_daily_once = AsyncMock(side_effect=RuntimeError("daily-failed"))
        stop_event = asyncio.Event()

        async def fake_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            stop_event.set()
            return await awaitable

        with patch("zetherion_ai.analytics.jobs.asyncio.wait_for", side_effect=fake_wait_for):
            await runner.run_loop(stop_event)

        runner.run_daily_once.assert_awaited_once()
