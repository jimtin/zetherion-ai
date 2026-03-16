"""Tests for canonical CI run reports and coaching payloads."""

from __future__ import annotations

from zetherion_ai.owner_ci.run_reports import (
    build_agent_coaching_feedback,
    build_preflight_coaching_payloads,
    build_recurring_diagnostic_coaching_payloads,
    build_run_report,
)


def test_build_run_report_emits_graph_correlation_and_filtered_evidence() -> None:
    report = build_run_report(
        run={
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "status": "failed",
            "trigger": "manual",
            "metadata": {
                "git_sha": "abc123",
                "environment": "staging",
            },
            "shards": [
                {
                    "shard_id": "shard-1",
                    "lane_id": "unit-full",
                    "lane_label": "Unit full",
                    "status": "failed",
                    "started_at": "2026-03-16T09:00:00Z",
                    "completed_at": "2026-03-16T09:05:00Z",
                    "metadata": {"depends_on": [], "resource_class": "cpu"},
                    "result": {
                        "steps": [
                            {
                                "step_id": "setup",
                                "label": "Setup",
                                "status": "succeeded",
                                "started_at": "2026-03-16T09:00:00Z",
                                "completed_at": "2026-03-16T09:01:00Z",
                            },
                            {
                                "step_id": "test",
                                "label": "Run tests",
                                "status": "failed",
                                "depends_on": ["setup"],
                                "started_at": "2026-03-16T09:01:00Z",
                                "completed_at": "2026-03-16T09:05:00Z",
                            },
                        ],
                        "artifacts": [
                            {
                                "artifact_id": "coverage-summary",
                                "node_id": "step:run-1:shard-1:test",
                                "kind": "coverage_summary",
                                "path": ".artifacts/coverage/coverage-summary.json",
                            }
                        ],
                        "evidence_references": [
                            {
                                "evidence_ref_id": "cloudwatch-1",
                                "node_id": "shard:run-1:shard-1",
                                "provider": "cloudwatch",
                                "service": "skills-runtime",
                                "query": "run-1 AND shard-1",
                                "trace_id": "trace-1",
                                "request_id": "req-1",
                            }
                        ],
                        "correlation_context": {
                            "trace_ids": ["trace-1"],
                            "request_ids": ["req-1"],
                            "services": ["skills-runtime"],
                            "containers": ["worker-1"],
                        },
                        "debug_bundle": {
                            "artifact_receipt_paths": {
                                "coverage_gaps": ".artifacts/coverage/coverage-gaps.json"
                            }
                        },
                        "coverage_summary": {
                            "passed": False,
                            "metrics": {"branches": {"passed": False}},
                        },
                        "coverage_gaps": {
                            "gaps": [{"identifier": "foo", "metric": "branches"}]
                        },
                    },
                    "error": {"message": "usage: pytest shard spec invalid"},
                }
            ],
        },
        logs=[{"message": "Database unavailable while starting Playwright browser"}],
        debug_bundle=None,
        coaching_feedback=[
            {
                "feedback_id": "coach-1",
                "summary": "Update AGENTS.md for coverage fixes.",
            }
        ],
    )

    graph = report["run_graph"]
    node_ids = {node["node_id"] for node in graph["nodes"]}
    assert "run:run-1" in node_ids
    assert "shard:run-1:shard-1" in node_ids
    assert "step:run-1:shard-1:test" in node_ids
    assert report["package"]["root"] == "run_report"
    assert {entry["kind"] for entry in report["package"]["files"]} >= {
        "run_graph",
        "correlation_context",
        "diagnostic_summary",
        "diagnostic_findings",
        "coverage_summary",
        "coverage_gaps",
        "artifacts_index",
        "evidence_index",
        "coaching",
    }
    assert report["correlation_context"]["trace_ids"] == ["trace-1"]
    assert report["correlation_context"]["containers"] == ["worker-1"]
    assert report["artifacts"][0]["kind"] == "coverage_summary"
    assert report["evidence"][0]["provider"] == "cloudwatch"
    assert report["diagnostic_summary"]["blocking"] is True
    assert report["diagnostic_findings"]
    assert report["coaching"][0]["feedback_id"] == "coach-1"


def test_build_preflight_and_recurring_coaching_payloads_are_actionable() -> None:
    preflight_payloads = build_preflight_coaching_payloads(
        principal_id="codex-agent-1",
        repo_id="zetherion-ai",
        commit_sha="abc123",
        violations=[
            {
                "rule_code": "missing_preflight_check",
                "summary": "Mandatory certification preflight check `gitleaks` is missing.",
                "remediation": "Run `gitleaks` and include it in the attestation.",
            }
        ],
    )
    assert preflight_payloads[0]["metadata"]["record_kind"] == "agent_coaching"
    assert (
        preflight_payloads[0]["metadata"]["recommendations"][0]["agents_md_update"]
    )

    recurring_payloads = build_recurring_diagnostic_coaching_payloads(
        report={
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "correlation_context": {"commit_sha": "abc123"},
            "diagnostic_findings": [
                {
                    "code": "coverage_gate_failed",
                    "summary": "Coverage gate failed.",
                    "recommended_fix": "Use coverage-gaps.json before rerunning the full gate.",
                    "blocking": True,
                    "severity": "high",
                }
            ],
            "all_evidence_references": [],
        },
        principal_id="codex-agent-1",
        historical_occurrences={"coverage_gate_failed": 1},
    )
    assert recurring_payloads[0]["metadata"]["coaching_kind"] == "recurring_issue"
    assert recurring_payloads[0]["observed_request"]["recurrence_count"] == 2


def test_build_agent_coaching_feedback_transforms_gap_events() -> None:
    feedback = build_agent_coaching_feedback(
        [
            {
                "gap_id": "gap-1",
                "principal_id": "codex-agent-1",
                "repo_id": "zetherion-ai",
                "run_id": "run-1",
                "gap_type": "agent_recurring_coverage_gate_failed",
                "suggested_fix": "Use coverage-gaps.json before rerunning the full gate.",
                "blocker": True,
                "status": "open",
                "occurrence_count": 2,
                "first_seen_at": "2026-03-16T09:00:00Z",
                "updated_at": "2026-03-16T09:10:00Z",
                "metadata": {
                    "record_kind": "agent_coaching",
                    "coaching_kind": "recurring_issue",
                    "rule_code": "coverage_gate_failed",
                    "summary": "Coverage failures keep recurring.",
                    "recommendations": [
                        {
                            "title": "Update AGENTS.md",
                            "instructions": ["Require coverage gap review before reruns."],
                            "agents_md_update": "Require reviewing coverage-gaps.json.",
                        }
                    ],
                },
            }
        ]
    )

    assert feedback[0]["principal_id"] == "codex-agent-1"
    assert feedback[0]["recurrence_count"] == 2
    assert feedback[0]["recommendations"][0]["agents_md_update"] == (
        "Require reviewing coverage-gaps.json."
    )
