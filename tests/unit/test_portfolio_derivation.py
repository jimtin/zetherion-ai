"""Tests for owner portfolio derivation helpers."""

from __future__ import annotations

from zetherion_ai.portfolio.derivation import (
    DERIVATION_KIND_TENANT_HEALTH,
    build_owner_portfolio_snapshot,
    build_tenant_health_derived_dataset,
    health_indicator_for_summary,
)


def test_health_indicator_thresholds() -> None:
    assert health_indicator_for_summary({"escalation_rate": 0.16, "avg_sentiment": 0.4}) == "red"
    assert health_indicator_for_summary({"escalation_rate": 0.1, "avg_sentiment": -0.3}) == "amber"
    assert health_indicator_for_summary({"escalation_rate": 0.1, "avg_sentiment": 0.1}) == "green"


def test_build_tenant_health_derived_dataset_normalizes_summary() -> None:
    derived = build_tenant_health_derived_dataset(
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        tenant_name="Bob's Plumbing",
        raw_summary={
            "total_interactions": "7",
            "avg_sentiment": "0.2456",
            "escalation_rate": "0.143",
            "resolution_rate": 0.8,
            "behavior_sessions": "3",
            "behavior_conversion_rate": "0.667",
            "top_intents": {"enquiry": "5", "repair": 2},
            "top_funnel_stages": {"pricing": "2"},
            "name": "Should Not Win",
        },
        source="test.derive",
        provenance={"input_count": 7},
    )

    assert derived["derivation_kind"] == DERIVATION_KIND_TENANT_HEALTH
    assert derived["summary"] == {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "tenant_name": "Bob's Plumbing",
        "total_interactions": 7,
        "avg_sentiment": 0.246,
        "escalation_rate": 0.143,
        "resolution_rate": 0.8,
        "behavior_sessions": 3,
        "behavior_conversion_rate": 0.667,
        "top_intents": {"enquiry": 5, "repair": 2},
        "top_funnel_stages": {"pricing": 2},
        "health_indicator": "green",
    }
    assert derived["provenance"]["input_trust_domain"] == "tenant_raw"
    assert derived["provenance"]["output_trust_domain"] == "tenant_derived"
    assert derived["provenance"]["input_count"] == 7


def test_build_tenant_health_derived_dataset_handles_invalid_inputs() -> None:
    derived = build_tenant_health_derived_dataset(
        zetherion_tenant_id="tenant-x",
        tenant_name="",
        raw_summary={
            "name": "Fallback Tenant",
            "total_interactions": object(),
            "avg_sentiment": object(),
            "escalation_rate": None,
            "resolution_rate": "not-a-number",
            "behavior_sessions": None,
            "behavior_conversion_rate": object(),
            "top_intents": ["bad-shape"],
            "top_funnel_stages": {" ": 2, "pricing": "oops"},
        },
        source="test.invalid",
    )

    assert derived["summary"]["tenant_name"] == "Fallback Tenant"
    assert derived["summary"]["total_interactions"] == 0
    assert derived["summary"]["avg_sentiment"] == 0.0
    assert derived["summary"]["resolution_rate"] == 0.0
    assert derived["summary"]["top_intents"] == {}
    assert derived["summary"]["top_funnel_stages"] == {"pricing": 0}


def test_build_owner_portfolio_snapshot_projects_derived_summary() -> None:
    snapshot = build_owner_portfolio_snapshot(
        source_dataset_id="tds_abc123",
        derived_summary={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "tenant_name": "Bob's Plumbing",
            "domain": "private.example.com",
            "health_indicator": "amber",
            "total_interactions": 12,
            "avg_sentiment": -0.23,
            "escalation_rate": 0.11,
            "resolution_rate": 0.52,
            "behavior_sessions": 5,
            "behavior_conversion_rate": 0.4,
            "top_intents": {"quote": 4},
            "top_funnel_stages": {"pricing": 3},
        },
        source="test.snapshot",
        provenance={"tenant_domain": "private.example.com"},
    )

    assert snapshot["derivation_kind"] == DERIVATION_KIND_TENANT_HEALTH
    assert snapshot["summary"] == {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "tenant_name": "Bob's Plumbing",
        "health_indicator": "amber",
        "total_interactions": 12,
        "avg_sentiment": -0.23,
        "escalation_rate": 0.11,
        "resolution_rate": 0.52,
        "behavior_sessions": 5,
        "behavior_conversion_rate": 0.4,
        "top_intents": {"quote": 4},
        "top_funnel_stages": {"pricing": 3},
    }
    assert "domain" not in snapshot["summary"]
    assert snapshot["provenance"]["input_trust_domain"] == "tenant_derived"
    assert snapshot["provenance"]["output_trust_domain"] == "owner_portfolio"
    assert snapshot["provenance"]["source_dataset_id"] == "tds_abc123"


def test_build_owner_portfolio_snapshot_derives_health_indicator_when_missing() -> None:
    snapshot = build_owner_portfolio_snapshot(
        source_dataset_id="tds_missing_health",
        derived_summary={
            "tenant_id": "tenant-y",
            "tenant_name": "Fallback Tenant",
            "escalation_rate": 0.2,
            "avg_sentiment": 0.1,
            "top_intents": {"quote": 1},
            "top_funnel_stages": None,
        },
        source="test.snapshot.fallback",
    )

    assert snapshot["summary"]["health_indicator"] == "red"
    assert snapshot["summary"]["top_funnel_stages"] == {}
