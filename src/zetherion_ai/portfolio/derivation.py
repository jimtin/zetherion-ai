"""Explicit tenant-derived to owner-portfolio transformation helpers."""

from __future__ import annotations

from typing import Any

DERIVATION_KIND_TENANT_HEALTH = "tenant_health_summary"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dict_of_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, raw in value.items():
        rendered = str(key).strip()
        if not rendered:
            continue
        cleaned[rendered] = _int(raw)
    return cleaned


def health_indicator_for_summary(summary: dict[str, Any]) -> str:
    """Return a portfolio-safe health indicator for a tenant summary."""

    escalation_rate = _float(summary.get("escalation_rate"))
    avg_sentiment = _float(summary.get("avg_sentiment"))
    if escalation_rate > 0.15:
        return "red"
    if avg_sentiment < -0.2:
        return "amber"
    return "green"


def build_tenant_health_derived_dataset(
    *,
    zetherion_tenant_id: str,
    tenant_name: str,
    raw_summary: dict[str, Any],
    source: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a tenant health summary into a tenant-derived dataset."""

    summary = {
        "tenant_id": str(zetherion_tenant_id),
        "tenant_name": str(tenant_name or raw_summary.get("name") or "Unknown"),
        "total_interactions": _int(raw_summary.get("total_interactions")),
        "avg_sentiment": round(_float(raw_summary.get("avg_sentiment")), 3),
        "escalation_rate": round(_float(raw_summary.get("escalation_rate")), 3),
        "resolution_rate": round(_float(raw_summary.get("resolution_rate")), 3),
        "behavior_sessions": _int(raw_summary.get("behavior_sessions")),
        "behavior_conversion_rate": round(
            _float(raw_summary.get("behavior_conversion_rate")),
            3,
        ),
        "top_intents": _dict_of_ints(raw_summary.get("top_intents")),
        "top_funnel_stages": _dict_of_ints(raw_summary.get("top_funnel_stages")),
    }
    summary["health_indicator"] = health_indicator_for_summary(summary)
    return {
        "derivation_kind": DERIVATION_KIND_TENANT_HEALTH,
        "source": source,
        "summary": summary,
        "provenance": {
            "input_trust_domain": "tenant_raw",
            "output_trust_domain": "tenant_derived",
            "derivation_kind": DERIVATION_KIND_TENANT_HEALTH,
            "source": source,
            **(provenance or {}),
        },
    }


def build_owner_portfolio_snapshot(
    *,
    source_dataset_id: str,
    derived_summary: dict[str, Any],
    source: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a tenant-derived dataset into an owner-portfolio snapshot."""

    snapshot_summary = {
        "tenant_id": str(derived_summary.get("tenant_id") or ""),
        "tenant_name": str(derived_summary.get("tenant_name") or "Unknown"),
        "health_indicator": str(
            derived_summary.get("health_indicator") or health_indicator_for_summary(derived_summary)
        ),
        "total_interactions": _int(derived_summary.get("total_interactions")),
        "avg_sentiment": round(_float(derived_summary.get("avg_sentiment")), 3),
        "escalation_rate": round(_float(derived_summary.get("escalation_rate")), 3),
        "resolution_rate": round(_float(derived_summary.get("resolution_rate")), 3),
        "behavior_sessions": _int(derived_summary.get("behavior_sessions")),
        "behavior_conversion_rate": round(
            _float(derived_summary.get("behavior_conversion_rate")),
            3,
        ),
        "top_intents": _dict_of_ints(derived_summary.get("top_intents")),
        "top_funnel_stages": _dict_of_ints(derived_summary.get("top_funnel_stages")),
    }
    return {
        "derivation_kind": DERIVATION_KIND_TENANT_HEALTH,
        "source": source,
        "summary": snapshot_summary,
        "provenance": {
            "input_trust_domain": "tenant_derived",
            "output_trust_domain": "owner_portfolio",
            "source": source,
            "source_dataset_id": source_dataset_id,
            **(provenance or {}),
        },
    }
