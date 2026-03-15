"""Tests for canonical coverage and diagnostic artifacts."""

from __future__ import annotations

from pathlib import Path

from zetherion_ai.owner_ci.coverage_artifacts import build_coverage_artifacts
from zetherion_ai.owner_ci.diagnostics import (
    build_coverage_diagnostics,
    build_operation_diagnosis,
    build_run_diagnostics,
)


def test_build_coverage_artifacts_includes_ranked_function_and_branch_gaps(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    source_root = repo_root / "src" / "zetherion_ai"
    module_path = source_root / "example.py"
    source_root.mkdir(parents=True)
    module_path.write_text(
        "\n".join(
            [
                "def covered():",
                "    return 1",
                "",
                "def uncovered(flag: bool):",
                "    if flag:",
                "        return 2",
                "    return 3",
                "",
                "class Demo:",
                "    def method(self):",
                "        return covered()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    coverage_payload = {
        "files": {
            str(module_path): {
                "executed_lines": [1, 2, 10, 11],
                "missing_lines": [4, 5, 6, 7],
                "missing_branches": [[5, 0]],
            }
        },
        "totals": {
            "covered_lines": 4,
            "num_statements": 8,
            "covered_branches": 0,
            "num_branches": 1,
        },
    }

    summary, gaps, exit_code = build_coverage_artifacts(
        coverage_payload=coverage_payload,
        repo_root=repo_root,
        source_root=source_root,
        thresholds={
            "statements": 90,
            "lines": 90,
            "branches": 90,
            "functions": 90,
        },
        coverage_json_path=".artifacts/coverage/coverage.json",
        coverage_report_path=".artifacts/coverage/coverage-report.txt",
        html_index_path=".artifacts/coverage/html/index.html",
        repo_sha="abc123",
        run_id="run-1",
        lane_id="unit-full",
    )

    assert exit_code == 1
    assert summary["metrics"]["functions"]["covered"] == 2
    assert summary["metrics"]["functions"]["total"] == 3
    identifiers = {gap["identifier"] for gap in gaps["gaps"]}
    assert "uncovered" in identifiers
    assert "src/zetherion_ai/example.py:missing_branches" in identifiers


def test_build_coverage_diagnostics_returns_actionable_failure_package() -> None:
    summary, findings = build_coverage_diagnostics(
        coverage_summary={
            "metrics": {
                "branches": {"passed": False, "actual": 88.1},
                "functions": {"passed": False, "actual": 89.5},
            },
            "artifacts": {
                "coverage_json": ".artifacts/coverage/coverage.json",
                "coverage_report": ".artifacts/coverage/coverage-report.txt",
            },
        },
        coverage_gaps={"gaps": [{"identifier": "foo", "metric": "functions"}]},
        run_id="run-1",
        repo_id="zetherion-ai",
    )

    assert summary["blocking"] is True
    assert summary["finding_count"] == 1
    assert summary["diagnostic_artifacts"]
    assert findings[0]["code"] == "coverage_gate_failed"
    assert findings[0]["details"]["top_gaps"][0]["identifier"] == "foo"


def test_build_run_diagnostics_classifies_shard_contract_and_runtime_failures() -> None:
    diagnostic_summary, findings = build_run_diagnostics(
        run={
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "status": "failed",
            "shards": [
                {
                    "shard_id": "shard-1",
                    "lane_id": "unit-full",
                    "status": "failed",
                    "result": {
                        "coverage_summary": {
                            "passed": False,
                            "metrics": {"branches": {"passed": False}},
                            "artifacts": {"coverage_json": ".artifacts/coverage/coverage.json"},
                        },
                        "coverage_gaps": {"gaps": [{"identifier": "foo"}]},
                        "missing_evidence": ["coverage-summary.json"],
                    },
                    "error": {"message": "usage: pytest shard spec invalid"},
                }
            ],
        },
        logs=[{"message": "Database unavailable while starting Playwright browser"}],
        debug_bundle={
            "bundle": {
                "artifact_receipt_paths": {
                    "coverage_summary": ".artifacts/coverage/coverage-summary.json"
                }
            }
        },
    )

    codes = {finding["code"] for finding in findings}
    assert diagnostic_summary["blocking"] is True
    assert diagnostic_summary["diagnostic_artifacts"]
    assert {"coverage_gate_failed", "artifact_contract_failed", "shard_contract_invalid"} <= codes
    assert "runtime_dependency_missing" in codes


def test_build_run_diagnostics_classifies_required_paths_capacity_release_and_provider_failures(
) -> None:
    diagnostic_summary, findings = build_run_diagnostics(
        run={
            "run_id": "run-2",
            "repo_id": "zetherion-ai",
            "status": "failed",
            "metadata": {"release_verification": {}},
            "shards": [
                {
                    "shard_id": "shard-2",
                    "lane_id": "e2e-live",
                    "status": "failed",
                    "result": {
                        "failed_required_paths": [
                            "cgs_release_verification",
                            "discord_roundtrip",
                        ],
                        "admission_decision": {
                            "admitted": False,
                            "blocking_reasons": [
                                "cpu budget exhausted",
                                "parallel group busy",
                            ],
                        },
                    },
                    "error": {
                        "message": "release verification failed and missing webhook correlation"
                    },
                }
            ],
        },
        logs=[
            {
                "message": (
                    "Docker compose failed to start container; test harness unavailable; "
                    "github connector auth failed with 401; event id missing"
                )
            }
        ],
        debug_bundle={
            "bundle": {
                "artifact_receipt_paths": {
                    "coverage_summary": ".artifacts/coverage/coverage-summary.json",
                    "diagnostic": ".artifacts/diagnostics/summary.json",
                }
            }
        },
    )

    codes = {finding["code"] for finding in findings}
    assert diagnostic_summary["blocking"] is True
    assert diagnostic_summary["diagnostic_artifacts"]
    assert {
        "required_path_not_covered",
        "host_capacity_blocked",
        "release_receipt_missing",
        "container_startup_failed",
        "test_harness_unavailable",
        "connector_auth_failed",
        "webhook_correlation_missing",
    } <= codes


def test_build_operation_diagnosis_prefers_structured_evidence_and_incidents() -> None:
    diagnosis = build_operation_diagnosis(
        operation={
            "operation_id": "op-1",
            "repo_id": "zetherion-ai",
            "app_id": "zetherion-ai",
            "status": "failed",
        },
        evidence=[
            {
                "evidence_type": "coverage_summary",
                "payload": {
                    "passed": False,
                    "metrics": {"branches": {"passed": False, "actual": 88.3}},
                },
            },
            {
                "evidence_type": "coverage_gaps",
                "payload": {"gaps": [{"identifier": "foo", "metric": "branches"}]},
            },
            {
                "evidence_type": "diagnostic_summary",
                "payload": {
                    "status": "failed",
                    "blocking": True,
                    "confidence": 0.94,
                    "recommended_next_actions": ["Fix the shard contract."],
                    "diagnostic_artifacts": [{"kind": "coverage_gate_failed", "path": "a.json"}],
                },
            },
            {
                "evidence_type": "diagnostic_findings",
                "payload": {
                    "findings": [
                        {
                            "code": "coverage_gate_failed",
                            "blocking": True,
                            "recommended_next_actions": ["Add targeted tests."],
                        }
                    ]
                },
            },
        ],
        incidents=[{"incident_id": "inc-1", "blocking": True}],
    )

    assert diagnosis["blocking"] is True
    assert diagnosis["coverage_summary"]["passed"] is False
    assert diagnosis["diagnostic_summary"]["confidence"] == 0.94
    assert diagnosis["diagnostic_findings"][0]["code"] == "coverage_gate_failed"
    assert diagnosis["diagnostic_artifacts"][0]["path"] == "a.json"
    assert diagnosis["incident_count"] == 1
    assert "coverage_summary" in diagnosis["evidence_types_present"]


def test_build_operation_diagnosis_synthesizes_summary_from_coverage_when_missing() -> None:
    diagnosis = build_operation_diagnosis(
        operation={
            "operation_id": "op-2",
            "repo_id": "zetherion-ai",
            "status": "failed",
        },
        evidence=[
            {
                "evidence_type": "coverage_summary",
                "payload": {
                    "passed": False,
                    "metrics": {"functions": {"passed": False, "actual": 89.1}},
                    "artifacts": {
                        "coverage_json": ".artifacts/coverage/coverage.json",
                        "coverage_report": ".artifacts/coverage/coverage-report.txt",
                    },
                },
            },
            {
                "evidence_type": "coverage_gaps",
                "payload": {"gaps": [{"identifier": "missing_fn", "metric": "functions"}]},
            },
        ],
        incidents=[],
    )

    assert diagnosis["blocking"] is True
    assert diagnosis["diagnostic_summary"]["blocking"] is True
    assert diagnosis["diagnostic_findings"][0]["code"] == "coverage_gate_failed"
    assert diagnosis["recommended_next_actions"]
