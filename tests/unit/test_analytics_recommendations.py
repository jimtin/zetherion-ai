"""Tests for analytics recommendation engine."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zetherion_ai.analytics.recommendations import (
    RecommendationCandidate,
    RecommendationEngine,
)


def test_generate_candidates_all_detectors() -> None:
    engine = RecommendationEngine(AsyncMock())
    candidates = engine.generate_candidates(
        session_summary={
            "events_by_type": {"form_start": 8, "form_submit": 2},
            "friction": {
                "rage_clicks": 3,
                "dead_clicks": 4,
                "js_errors": 1,
                "api_errors": 1,
            },
        },
        funnel_rows=[{"stage_name": "conversion", "conversion_rate": 0.02}],
        release_regression={"regression": True, "release_id": "rel-1"},
    )

    kinds = {candidate.recommendation_type for candidate in candidates}
    assert kinds == {
        "ux_friction",
        "cta_clarity",
        "form_optimization",
        "reliability",
        "funnel_conversion",
        "release_regression",
    }


def test_generate_candidates_below_thresholds() -> None:
    engine = RecommendationEngine(AsyncMock())
    candidates = engine.generate_candidates(
        session_summary={
            "events_by_type": {"form_start": 2, "form_submit": 2},
            "friction": {
                "rage_clicks": 1,
                "dead_clicks": 1,
                "js_errors": 0,
                "api_errors": 0,
            },
        },
        funnel_rows=[{"stage_name": "conversion", "conversion_rate": 0.5}],
        release_regression={"regression": False},
    )
    assert candidates == []


@pytest.mark.asyncio
async def test_persist_candidates_calls_tenant_manager() -> None:
    tm = AsyncMock()
    tm.create_recommendation = AsyncMock(
        side_effect=[
            {"recommendation_id": "rec-1"},
            {"recommendation_id": "rec-2"},
        ]
    )
    engine = RecommendationEngine(tm)
    candidates = [
        RecommendationCandidate(
            recommendation_type="ux_friction",
            title="Fix rage clicks",
            description="desc1",
            evidence={"rage_clicks": 4},
            confidence=0.7,
        ),
        RecommendationCandidate(
            recommendation_type="reliability",
            title="Reduce JS errors",
            description="desc2",
            evidence={"js_errors": 2},
            risk_class="medium",
            confidence=0.8,
        ),
    ]

    rows = await engine.persist_candidates("tenant-1", candidates, source="unit-test")
    assert [row["recommendation_id"] for row in rows] == ["rec-1", "rec-2"]
    assert tm.create_recommendation.await_count == 2
