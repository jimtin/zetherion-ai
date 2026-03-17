"""Tests for canonical CI run reports and coaching payloads."""

from __future__ import annotations

from zetherion_ai.owner_ci.run_reports import (
    _agents_md_update_for_rule,
    _debug_bundle_artifacts,
    _normalize_mapping_list,
    _normalize_time_range,
    _normalized_state,
    _step_artifacts,
    _submitted_evidence_references,
    build_agent_coaching_feedback,
    build_correlation_context,
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


def test_run_report_helpers_normalize_states_artifacts_and_evidence_defaults() -> None:
    assert _normalized_state("success") == "succeeded"
    assert _normalized_state("blocked") == "failed"
    assert _normalized_state("planned") == "queued"
    assert _normalized_state("  ") == "unknown"
    assert _normalize_mapping_list("not-a-list") == []
    assert _normalize_mapping_list([{"ok": True}, "skip"]) == [{"ok": True}]
    assert _normalize_time_range(
        {"from": "2026-03-16T09:00:00Z", "to": "2026-03-16T10:00:00Z"}
    ) == {
        "start": "2026-03-16T09:00:00Z",
        "end": "2026-03-16T10:00:00Z",
    }
    assert _normalize_time_range("invalid") == {}

    artifacts = _step_artifacts(
        run_id="run-1",
        shard_id="shard-1",
        artifacts=[
            {"title": "Coverage", "state": "success", "metadata": {"kind": "summary"}},
            {"path": " ", "artifact_id": "", "kind": "", "state": ""},
        ],
    )
    assert artifacts[0].artifact_id == "artifact:run-1:shard-1:Coverage"
    assert artifacts[0].kind == "artifact"
    assert artifacts[0].state == "succeeded"
    assert artifacts[1].artifact_id == "artifact:run-1:shard-1:artifact-2"
    assert artifacts[1].path is None

    debug_bundle_artifacts = _debug_bundle_artifacts(
        run_id="run-1",
        shard_id="shard-1",
        debug_bundle={
            "bundle": {
                "artifact_receipt_paths": {
                    "coverage_gaps": ".artifacts/coverage/coverage-gaps.json",
                    "blank": " ",
                }
            }
        },
    )
    assert [artifact.title for artifact in debug_bundle_artifacts] == ["coverage_gaps"]

    evidence_refs = _submitted_evidence_references(
        shard_id="shard-1",
        result={
            "evidence_references": [
                {
                    "provider": "",
                    "query": "run-1",
                    "time_range": {"start": "2026-03-16T09:00:00Z"},
                },
                "skip",
            ]
        },
    )
    assert evidence_refs[0].evidence_ref_id == "shard-1:evidence-1"
    assert evidence_refs[0].provider == "unknown"
    assert evidence_refs[0].time_range == {"start": "2026-03-16T09:00:00Z"}


def test_run_report_helpers_cover_running_correlation_and_blank_fallbacks() -> None:
    assert _normalized_state("running_disconnected") == "running"
    assert _normalize_time_range({"start": "2026-03-16T09:00:00Z"}) == {
        "start": "2026-03-16T09:00:00Z"
    }
    assert _normalize_time_range({"end": "2026-03-16T10:00:00Z"}) == {
        "end": "2026-03-16T10:00:00Z"
    }

    evidence_refs = _submitted_evidence_references(
        shard_id="shard-2",
        result={
            "evidence_references": [
                {
                    "evidence_ref_id": " ",
                    "provider": "cloudwatch",
                    "service": "skills-runtime",
                    "trace_id": "trace-2",
                    "request_id": "req-2",
                }
            ]
        },
    )
    assert evidence_refs[0].evidence_ref_id == "shard-2:evidence-1"

    correlation_context = build_correlation_context(
        {
            "run_id": "run-2",
            "repo_id": "zetherion-ai",
            "trigger": "manual",
            "metadata": {"head_sha": "def456", "target_environment": "preview"},
            "shards": [
                {
                    "result": {
                        "correlation_context": {
                            "trace_ids": ["trace-2", "trace-2"],
                            "request_ids": ["req-2"],
                            "services": ["skills-runtime"],
                            "containers": ["worker-2", "worker-2"],
                        },
                        "evidence_references": [
                            {
                                "trace_id": "trace-3",
                                "request_id": "req-3",
                                "service": "gateway",
                            },
                            {
                                "trace_id": "trace-2",
                                "request_id": "req-2",
                                "service": "skills-runtime",
                            },
                        ],
                    }
                }
            ],
        }
    )
    assert correlation_context["commit_sha"] == "def456"
    assert correlation_context["environment"] == "preview"
    assert correlation_context["trace_ids"] == ["trace-2", "trace-3"]
    assert correlation_context["request_ids"] == ["req-2", "req-3"]
    assert correlation_context["services"] == ["skills-runtime", "gateway"]
    assert correlation_context["containers"] == ["worker-2"]


def test_build_run_report_skips_blank_shards_and_assigns_default_step_and_evidence_nodes(
) -> None:
    report = build_run_report(
        run={
            "run_id": "run-3",
            "repo_id": "zetherion-ai",
            "status": "running_disconnected",
            "shards": [
                {
                    "shard_id": " ",
                    "lane_id": " ",
                    "status": "failed",
                    "result": {},
                },
                {
                    "shard_id": "shard-2",
                    "lane_id": "unit-smoke",
                    "status": "running_disconnected",
                    "result": {
                        "steps": [
                            {
                                "step_id": " ",
                                "label": "Unnamed step",
                                "status": "running_disconnected",
                            }
                        ],
                        "evidence_references": [
                            {
                                "evidence_ref_id": "ev-1",
                                "provider": "cloudwatch",
                            }
                        ],
                    },
                },
            ],
        },
        logs=[],
        debug_bundle=None,
    )

    graph = report["run_graph"]
    node_ids = {node["node_id"] for node in graph["nodes"]}
    assert "shard:run-3:shard-2" in node_ids
    assert "step:run-3:shard-2:step-1" in node_ids
    assert not any(node_id.endswith(": ") for node_id in node_ids)
    assert not any(
        node["kind"] == "shard" and not str(node.get("shard_id") or "").strip()
        for node in graph["nodes"]
    )

    step_node = next(
        node for node in graph["nodes"] if node["node_id"] == "step:run-3:shard-2:step-1"
    )
    assert step_node["state"] == "running"

    evidence_ref = graph["evidence_references"][0]
    assert evidence_ref["node_id"] == "shard:run-3:shard-2"
    assert report["correlation_context"]["run_id"] == "run-3"


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


def test_build_preflight_and_recurring_payloads_cover_default_and_skip_paths() -> None:
    preflight_payloads = build_preflight_coaching_payloads(
        principal_id=None,
        repo_id="zetherion-ai",
        commit_sha=None,
        violations=[{"summary": "Missing attestation.", "remediation": "Add it."}],
    )
    recommendation = preflight_payloads[0]["metadata"]["recommendations"][0]
    assert recommendation["agents_md_update"] == _agents_md_update_for_rule(
        "missing_preflight_check",
        repo_id="zetherion-ai",
    )

    recurring_payloads = build_recurring_diagnostic_coaching_payloads(
        report={
            "run_id": "run-2",
            "repo_id": "zetherion-ai",
            "correlation_context": {"commit_sha": "def456"},
            "diagnostic_findings": [
                {
                    "code": "artifact_contract_failed",
                    "summary": "Artifacts were missing.",
                    "recommended_fix": "Write the required artifacts.",
                    "blocking": False,
                    "severity": "medium",
                    "node_id": "shard:run-2:shard-2",
                    "evidence_ref_ids": ["e-1"],
                },
                {
                    "code": "",
                    "summary": "Skip me.",
                },
            ],
            "all_evidence_references": [
                {"evidence_ref_id": "e-1", "node_id": "shard:run-2:shard-2"},
                {"evidence_ref_id": "e-2", "node_id": "shard:run-2:other"},
            ],
        },
        principal_id="codex-agent-2",
        historical_occurrences={"artifact_contract_failed": 0},
    )
    assert recurring_payloads == []

    recurring_payloads = build_recurring_diagnostic_coaching_payloads(
        report={
            "run_id": "run-2",
            "repo_id": "zetherion-ai",
            "correlation_context": {"commit_sha": "def456"},
            "diagnostic_findings": [
                {
                    "code": "artifact_contract_failed",
                    "summary": "Artifacts were missing.",
                    "recommended_fix": "Write the required artifacts.",
                    "blocking": False,
                    "severity": "medium",
                    "node_id": "shard:run-2:shard-2",
                    "evidence_ref_ids": ["e-1"],
                }
            ],
            "all_evidence_references": [
                {"evidence_ref_id": "e-1", "node_id": "shard:run-2:shard-2"},
                {"evidence_ref_id": "e-2", "node_id": "shard:run-2:other"},
            ],
        },
        principal_id="codex-agent-2",
        historical_occurrences={"artifact_contract_failed": 2},
    )
    assert recurring_payloads[0]["metadata"]["evidence_references"] == [
        {"evidence_ref_id": "e-1", "node_id": "shard:run-2:shard-2"}
    ]


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


def test_build_agent_coaching_feedback_skips_non_coaching_records_and_defaults_rule_text() -> None:
    feedback = build_agent_coaching_feedback(
        [
            {
                "gap_id": "gap-ignored",
                "metadata": {"record_kind": "diagnostic"},
            },
            {
                "gap_id": "gap-2",
                "principal_id": "",
                "repo_id": "zetherion-ai",
                "gap_type": "custom_gap",
                "suggested_fix": "Add a preventative rule.",
                "blocker": False,
                "occurrence_count": 1,
                "metadata": {
                    "record_kind": "agent_coaching",
                    "recommendations": [{}],
                },
            },
        ]
    )

    assert len(feedback) == 1
    assert feedback[0]["summary"] == "Add a preventative rule."
    assert feedback[0]["recommendations"][0]["title"] == "Update AGENTS.md"
    assert feedback[0]["rule_violations"][0]["rule_code"] == "custom_gap"
