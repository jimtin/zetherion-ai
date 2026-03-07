"""Tests for the owner portfolio derivation pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.portfolio.pipeline import (
    OwnerPortfolioPipeline,
    aggregate_tenant_interactions,
)

_TENANT = {
    "tenant_id": uuid4(),
    "name": "Bob's Plumbing",
    "domain": "private.example.com",
}

_INTERACTIONS = [
    {
        "interaction_id": uuid4(),
        "sentiment": "positive",
        "intent": "quote",
        "outcome": "resolved",
    },
    {
        "interaction_id": uuid4(),
        "sentiment": "negative",
        "intent": "complaint",
        "outcome": "escalated",
    },
    {
        "interaction_id": uuid4(),
        "interaction_type": "web_behavior_summary",
        "entities": {
            "web_behavior_summary": {
                "funnel_stage": "pricing",
                "converted": True,
            }
        },
    },
]


def test_aggregate_tenant_interactions_strips_raw_fields() -> None:
    summary = aggregate_tenant_interactions(_TENANT, _INTERACTIONS)
    assert summary["name"] == "Bob's Plumbing"
    assert summary["domain"] == "private.example.com"
    assert summary["total_interactions"] == 3
    assert summary["behavior_sessions"] == 1
    assert summary["behavior_conversion_rate"] == 1.0
    assert "interaction_id" not in summary


@pytest.mark.asyncio
async def test_refresh_tenant_health_snapshot_reads_raw_and_persists_owner_safe_outputs() -> None:
    tenant_manager = MagicMock()
    tenant_manager.get_interactions = AsyncMock(return_value=_INTERACTIONS)

    storage = MagicMock()
    storage.upsert_tenant_derived_dataset = AsyncMock(
        return_value={
            "dataset_id": "tds_123",
            "summary": {
                "tenant_id": str(_TENANT["tenant_id"]),
                "tenant_name": "Bob's Plumbing",
                "health_indicator": "red",
                "total_interactions": 3,
                "avg_sentiment": 0.0,
                "escalation_rate": 0.333,
                "resolution_rate": 0.5,
                "behavior_sessions": 1,
                "behavior_conversion_rate": 1.0,
                "top_intents": {"quote": 1, "complaint": 1},
                "top_funnel_stages": {"pricing": 1},
            },
        }
    )
    storage.upsert_owner_portfolio_snapshot = AsyncMock(
        return_value={
            "snapshot_id": "ops_123",
            "source_dataset_id": "tds_123",
            "summary": {"tenant_name": "Bob's Plumbing", "health_indicator": "red"},
            "provenance": {
                "input_trust_domain": "tenant_derived",
                "output_trust_domain": "owner_portfolio",
                "source_dataset_id": "tds_123",
            },
        }
    )

    pipeline = OwnerPortfolioPipeline(
        tenant_manager=tenant_manager,
        portfolio_storage=storage,
    )
    snapshot = await pipeline.refresh_tenant_health_snapshot(
        _TENANT,
        source="test.refresh",
    )

    tenant_manager.get_interactions.assert_awaited_once_with(str(_TENANT["tenant_id"]), limit=100)
    storage.upsert_tenant_derived_dataset.assert_awaited_once()
    derived_kwargs = storage.upsert_tenant_derived_dataset.await_args.kwargs
    assert derived_kwargs["summary"]["tenant_name"] == "Bob's Plumbing"
    assert derived_kwargs["provenance"]["input_trust_domain"] == "tenant_raw"
    assert derived_kwargs["provenance"]["tenant_domain"] == "private.example.com"

    storage.upsert_owner_portfolio_snapshot.assert_awaited_once()
    snapshot_kwargs = storage.upsert_owner_portfolio_snapshot.await_args.kwargs
    assert snapshot_kwargs["source_dataset_id"] == "tds_123"
    assert snapshot_kwargs["provenance"]["input_trust_domain"] == "tenant_derived"
    assert snapshot_kwargs["provenance"]["output_trust_domain"] == "owner_portfolio"
    assert snapshot_kwargs["provenance"]["source_dataset_id"] == "tds_123"
    assert snapshot["snapshot_id"] == "ops_123"


@pytest.mark.asyncio
async def test_refresh_all_tenant_health_snapshots_iterates_visible_tenants() -> None:
    tenant_manager = MagicMock()
    tenant_manager.list_tenants = AsyncMock(
        return_value=[_TENANT, {**_TENANT, "tenant_id": uuid4()}]
    )
    storage = MagicMock()
    pipeline = OwnerPortfolioPipeline(
        tenant_manager=tenant_manager,
        portfolio_storage=storage,
    )
    pipeline.refresh_tenant_health_snapshot = AsyncMock(
        side_effect=[
            {"snapshot_id": "ops_1"},
            {"snapshot_id": "ops_2"},
        ]
    )

    snapshots = await pipeline.refresh_all_tenant_health_snapshots(source="test.refresh_all")

    tenant_manager.list_tenants.assert_awaited_once_with(active_only=True)
    assert pipeline.refresh_tenant_health_snapshot.await_count == 2
    assert snapshots == [{"snapshot_id": "ops_1"}, {"snapshot_id": "ops_2"}]


@pytest.mark.asyncio
async def test_refresh_requires_tenant_manager() -> None:
    pipeline = OwnerPortfolioPipeline(portfolio_storage=MagicMock())

    with pytest.raises(RuntimeError, match="tenant_manager"):
        await pipeline.refresh_all_tenant_health_snapshots(source="test.refresh_all")
