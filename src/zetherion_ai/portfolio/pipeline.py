"""Explicit tenant-raw to owner-portfolio derivation pipeline helpers."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.portfolio.derivation import (
    DERIVATION_KIND_TENANT_HEALTH,
    build_owner_portfolio_snapshot,
    build_tenant_health_derived_dataset,
)

if TYPE_CHECKING:
    from zetherion_ai.api.tenant import TenantManager
    from zetherion_ai.portfolio.storage import PortfolioStorage

log = get_logger("zetherion_ai.portfolio.pipeline")


def aggregate_tenant_interactions(
    tenant: dict[str, Any],
    interactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse tenant interactions into a bounded raw health summary."""

    sentiment_map = {
        "very_negative": -1.0,
        "negative": -0.5,
        "neutral": 0.0,
        "positive": 0.5,
        "very_positive": 1.0,
    }

    total = len(interactions)
    sentiments = [
        sentiment_map.get(interaction.get("sentiment", "neutral"), 0.0)
        for interaction in interactions
        if interaction.get("sentiment")
    ]
    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

    outcomes = [
        interaction.get("outcome") for interaction in interactions if interaction.get("outcome")
    ]
    escalated = sum(1 for outcome in outcomes if outcome == "escalated")
    resolved = sum(1 for outcome in outcomes if outcome == "resolved")

    escalation_rate = escalated / total if total > 0 else 0.0
    resolution_rate = resolved / len(outcomes) if outcomes else 0.0

    intents = [
        interaction.get("intent") for interaction in interactions if interaction.get("intent")
    ]
    intent_counts: dict[str, int] = {}
    for intent in intents:
        if intent is not None:
            intent_counts[str(intent)] = intent_counts.get(str(intent), 0) + 1

    behavior_summaries = [
        interaction
        for interaction in interactions
        if interaction.get("interaction_type") == "web_behavior_summary"
        and isinstance(interaction.get("entities"), dict)
    ]
    behavior_total = len(behavior_summaries)
    behavior_converted = 0
    funnel_counter: Counter[str] = Counter()
    for interaction in behavior_summaries:
        entities = interaction.get("entities") or {}
        if not isinstance(entities, dict):
            continue
        summary = entities.get("web_behavior_summary") or {}
        if not isinstance(summary, dict):
            continue
        stage = str(summary.get("funnel_stage", "unknown"))
        funnel_counter[stage] += 1
        if bool(summary.get("converted")):
            behavior_converted += 1

    behavior_conversion_rate = behavior_converted / behavior_total if behavior_total > 0 else 0.0

    return {
        "tenant_id": str(tenant.get("tenant_id", "")),
        "name": tenant.get("name", "Unknown"),
        "domain": tenant.get("domain"),
        "total_interactions": total,
        "avg_sentiment": round(avg_sentiment, 2),
        "escalation_rate": round(escalation_rate, 3),
        "resolution_rate": round(resolution_rate, 3),
        "top_intents": dict(
            sorted(intent_counts.items(), key=lambda item: item[1], reverse=True)[:5]
        ),
        "behavior_sessions": behavior_total,
        "behavior_conversion_rate": round(behavior_conversion_rate, 3),
        "top_funnel_stages": dict(funnel_counter.most_common(5)),
    }


class OwnerPortfolioPipeline:
    """Persist tenant-derived datasets and owner portfolio snapshots."""

    def __init__(
        self,
        *,
        portfolio_storage: PortfolioStorage,
        tenant_manager: TenantManager | None = None,
    ) -> None:
        self._portfolio_storage = portfolio_storage
        self._tenant_manager = tenant_manager

    async def persist_tenant_health_snapshot(
        self,
        *,
        zetherion_tenant_id: str,
        tenant_name: str,
        raw_summary: dict[str, Any],
        source: str,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Transform a bounded tenant raw summary into owner-portfolio state."""

        derived_payload = build_tenant_health_derived_dataset(
            zetherion_tenant_id=zetherion_tenant_id,
            tenant_name=tenant_name,
            raw_summary=raw_summary,
            source=source,
            provenance=provenance,
        )
        derived_dataset = await self._portfolio_storage.upsert_tenant_derived_dataset(
            zetherion_tenant_id=zetherion_tenant_id,
            tenant_name=tenant_name,
            derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
            source=source,
            summary=derived_payload["summary"],
            provenance=derived_payload["provenance"],
        )
        snapshot_payload = build_owner_portfolio_snapshot(
            source_dataset_id=str(derived_dataset.get("dataset_id") or ""),
            derived_summary=(derived_dataset.get("summary") or {}),
            source=source,
            provenance={
                **(provenance or {}),
                "source_dataset_id": str(derived_dataset.get("dataset_id") or ""),
            },
        )
        snapshot = await self._portfolio_storage.upsert_owner_portfolio_snapshot(
            zetherion_tenant_id=zetherion_tenant_id,
            tenant_name=tenant_name,
            derivation_kind=DERIVATION_KIND_TENANT_HEALTH,
            source_dataset_id=str(derived_dataset.get("dataset_id") or ""),
            source=source,
            summary=snapshot_payload["summary"],
            provenance=snapshot_payload["provenance"],
        )
        log.info(
            "owner_portfolio_snapshot_persisted",
            tenant_id=zetherion_tenant_id,
            source=source,
            dataset_id=derived_dataset.get("dataset_id"),
            snapshot_id=snapshot.get("snapshot_id"),
        )
        return snapshot

    async def refresh_tenant_health_snapshot(
        self,
        tenant: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Read raw tenant interactions and refresh one owner snapshot."""

        if self._tenant_manager is None:
            raise RuntimeError("OwnerPortfolioPipeline requires tenant_manager for refresh")

        tenant_id = str(tenant.get("tenant_id") or "").strip()
        interactions = await self._tenant_manager.get_interactions(tenant_id, limit=100)
        raw_summary = aggregate_tenant_interactions(tenant, interactions)
        return await self.persist_tenant_health_snapshot(
            zetherion_tenant_id=tenant_id,
            tenant_name=str(tenant.get("name") or raw_summary.get("name") or "Unknown"),
            raw_summary=raw_summary,
            source=source,
            provenance={
                "input_count": len(interactions),
                "tenant_domain": tenant.get("domain"),
            },
        )

    async def refresh_all_tenant_health_snapshots(
        self,
        *,
        source: str,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Refresh owner snapshots for all tenants visible to the pipeline."""

        if self._tenant_manager is None:
            raise RuntimeError("OwnerPortfolioPipeline requires tenant_manager for refresh")

        tenants = await self._tenant_manager.list_tenants(active_only=active_only)
        snapshots: list[dict[str, Any]] = []
        for tenant in tenants:
            snapshots.append(await self.refresh_tenant_health_snapshot(tenant, source=source))
        return snapshots
