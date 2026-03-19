"""Tests for canonical coverage and diagnostic artifacts."""

from __future__ import annotations

import ast
from pathlib import Path

from zetherion_ai.owner_ci.coverage_artifacts import (
    CoverageMetric,
    _ExecutableLineCollector,
    _function_targets_for_file,
    _load_coverage_json,
    _relative_path,
    build_coverage_artifacts,
    build_function_coverage,
)
from zetherion_ai.owner_ci.diagnostics import (
    build_coverage_diagnostics,
    build_operation_diagnosis,
    build_run_diagnostics,
    load_json_artifact,
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


def test_function_coverage_skips_stub_empty_outside_root_and_relative_paths(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    source_root = repo_root / "src" / "zetherion_ai"
    module_path = source_root / "helpers.py"
    outside_path = repo_root / "scripts" / "outside.py"
    source_root.mkdir(parents=True)
    outside_path.parent.mkdir(parents=True)
    module_path.write_text(
        "\n".join(
            [
                "def doc_only():",
                '    """Only docs."""',
                "",
                "def pass_only():",
                "    pass",
                "",
                "def ellipsis_only():",
                "    ...",
                "",
                "def with_docstring(flag: bool):",
                '    """Helpful docs."""',
                "    if flag:",
                "        return 1",
                "    return 0",
                "",
                "def no_candidate():",
                "    return 5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outside_path.write_text("def ignored():\n    return 1\n", encoding="utf-8")

    metric, gaps = build_function_coverage(
        coverage_payload={
            "files": {
                "src/zetherion_ai/helpers.py": {
                    "executed_lines": [12, 13],
                    "missing_lines": [14],
                },
                str(outside_path): {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                },
            }
        },
        repo_root=repo_root,
        source_root=source_root,
    )

    assert metric.total == 1
    assert metric.covered == 1
    assert gaps == []


def test_build_coverage_artifacts_handles_relative_paths_and_zero_totals(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    source_root = repo_root / "src" / "zetherion_ai"
    module_path = source_root / "relative_example.py"
    source_root.mkdir(parents=True)
    module_path.write_text("def noop():\n    return 1\n", encoding="utf-8")

    zero_metric = CoverageMetric(
        name="branches",
        covered=0,
        total=0,
        threshold=90,
    )
    assert zero_metric.actual == 100.0
    assert zero_metric.passed is True

    summary, gaps, exit_code = build_coverage_artifacts(
        coverage_payload={
            "files": {
                "src/zetherion_ai/relative_example.py": {
                    "executed_lines": [],
                    "missing_lines": [1, 2],
                    "missing_branches": [[1, 0]],
                }
            },
            "totals": {
                "covered_lines": 0,
                "num_statements": 2,
                "covered_branches": 0,
                "num_branches": 1,
            },
        },
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
        repo_sha="ghi789",
        run_id="run-2",
        lane_id="unit-relative",
    )

    assert exit_code == 1
    assert summary["artifacts"]["coverage_json"] == ".artifacts/coverage/coverage.json"
    identifiers = {gap["identifier"] for gap in gaps["gaps"]}
    assert "src/zetherion_ai/relative_example.py:missing_lines" in identifiers
    assert "src/zetherion_ai/relative_example.py:missing_branches" in identifiers


def test_coverage_artifact_helpers_cover_relative_fallbacks_and_invalid_inputs(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_root = repo_root / "src" / "zetherion_ai"
    source_root.mkdir(parents=True)
    outside_path = tmp_path / "outside.py"
    outside_path.write_text("value = 1\n", encoding="utf-8")
    assert _relative_path(outside_path, repo_root) == str(outside_path.resolve())

    coverage_json_path = repo_root / ".artifacts" / "coverage" / "coverage.json"
    coverage_json_path.parent.mkdir(parents=True)
    coverage_json_path.write_text('{"files": {}}', encoding="utf-8")
    assert _load_coverage_json(coverage_json_path) == {"files": {}}

    collector = _ExecutableLineCollector()
    odd_node = ast.Pass()
    odd_node.lineno = 5
    odd_node.end_lineno = "invalid"
    odd_node._fields = ()
    collector.generic_visit(odd_node)
    assert collector.lines == set()

    invalid_module = source_root / "broken.py"
    invalid_module.write_text("def broken(:\n", encoding="utf-8")
    assert (
        _function_targets_for_file(
            file_path=invalid_module,
            repo_root=repo_root,
            coverage_entry={"executed_lines": [1], "missing_lines": [2]},
        )
        == []
    )


def test_build_coverage_artifacts_skips_files_without_missing_lines_or_branches(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    source_root = repo_root / "src" / "zetherion_ai"
    module_path = source_root / "clean.py"
    source_root.mkdir(parents=True)
    module_path.write_text("def covered():\n    return 1\n", encoding="utf-8")

    summary, gaps, exit_code = build_coverage_artifacts(
        coverage_payload={
            "files": {
                "src/zetherion_ai/clean.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                    "missing_branches": [],
                }
            },
            "totals": {
                "covered_lines": 2,
                "num_statements": 2,
                "covered_branches": 0,
                "num_branches": 0,
            },
        },
        repo_root=repo_root,
        source_root=source_root,
        thresholds={
            "statements": 90,
            "lines": 90,
            "branches": 90,
            "functions": 90,
        },
        coverage_json_path=".artifacts/coverage/coverage.json",
    )

    assert exit_code == 0
    assert summary["passed"] is True
    assert gaps["gaps"] == []


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


def test_build_coverage_diagnostics_filters_blank_artifact_paths() -> None:
    summary, findings = build_coverage_diagnostics(
        coverage_summary={
            "metrics": {"branches": {"passed": False, "actual": 88.4}},
            "artifacts": {
                "coverage_json": " ",
                "coverage_report": "",
            },
        },
        coverage_gaps={"gaps": []},
        run_id="run-blank",
        repo_id="zetherion-ai",
    )

    assert summary["artifact_paths"] == []
    assert findings[0]["artifact_paths"] == ["", ""]


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


def test_build_run_diagnostics_classifies_required_paths_capacity_release_and_provider() -> None:
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


def test_build_operation_diagnosis_uses_coverage_artifacts_when_findings_or_artifacts_missing() -> (
    None
):
    coverage_summary_payload = {
        "passed": False,
        "metrics": {"branches": {"passed": False, "actual": 88.0}},
        "artifacts": {
            "coverage_json": ".artifacts/coverage/coverage.json",
            "coverage_report": ".artifacts/coverage/coverage-report.txt",
        },
    }
    coverage_gaps_payload = {"gaps": [{"identifier": "gap-1", "metric": "branches"}]}

    diagnosis_with_findings_only = build_operation_diagnosis(
        operation={
            "operation_id": "op-coverage-1",
            "repo_id": "zetherion-ai",
            "status": "failed",
        },
        evidence=[
            {"evidence_type": "coverage_summary", "payload": coverage_summary_payload},
            {"evidence_type": "coverage_gaps", "payload": coverage_gaps_payload},
            {
                "evidence_type": "diagnostic_findings",
                "payload": {
                    "findings": [
                        {
                            "code": "existing_finding",
                            "blocking": True,
                            "recommended_next_actions": ["Fix once."],
                        }
                    ]
                },
            },
        ],
        incidents=[],
    )
    assert diagnosis_with_findings_only["diagnostic_findings"][0]["code"] == "existing_finding"
    assert diagnosis_with_findings_only["diagnostic_artifacts"][0]["kind"] == (
        "coverage_gate_failed"
    )

    diagnosis_with_artifacts_only = build_operation_diagnosis(
        operation={
            "operation_id": "op-coverage-2",
            "repo_id": "zetherion-ai",
            "status": "failed",
        },
        evidence=[
            {"evidence_type": "coverage_summary", "payload": coverage_summary_payload},
            {"evidence_type": "coverage_gaps", "payload": coverage_gaps_payload},
            {
                "evidence_type": "diagnostic_artifacts",
                "payload": {"artifacts": [{"kind": "saved", "path": "artifact.json"}]},
            },
        ],
        incidents=[],
    )
    assert diagnosis_with_artifacts_only["diagnostic_findings"][0]["code"] == (
        "coverage_gate_failed"
    )
    assert diagnosis_with_artifacts_only["diagnostic_artifacts"] == [
        {"kind": "saved", "path": "artifact.json"}
    ]


def test_build_operation_diagnosis_dedupes_string_actions_and_ignores_non_dict_payloads() -> None:
    diagnosis = build_operation_diagnosis(
        operation={
            "operation_id": "op-actions",
            "repo_id": "zetherion-ai",
            "status": "failed",
        },
        evidence=[
            {
                "evidence_type": "diagnostic_summary",
                "payload": "skip-me",
            },
            {
                "evidence_type": "diagnostic_findings",
                "payload": {
                    "findings": [
                        {
                            "code": "artifact_contract_failed",
                            "blocking": True,
                            "artifact_paths": "artifact-a.json",
                            "recommended_next_actions": "Fix shared issue.",
                        },
                        {
                            "code": "runtime_dependency_missing",
                            "blocking": False,
                            "artifact_paths": ["artifact-a.json", "artifact-b.json"],
                            "recommended_next_actions": [
                                "Fix shared issue.",
                                "Retry the run.",
                            ],
                        },
                    ]
                },
            },
        ],
        incidents=[],
    )

    assert diagnosis["diagnostic_summary"]["recommended_next_actions"] == [
        "Fix shared issue.",
        "Retry the run.",
    ]
    assert diagnosis["diagnostic_summary"]["diagnostic_artifacts"] == [
        {"kind": "artifact_contract_failed", "path": "artifact-a.json"},
        {"kind": "runtime_dependency_missing", "path": "artifact-a.json"},
        {"kind": "runtime_dependency_missing", "path": "artifact-b.json"},
    ]


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


def test_load_json_artifact_handles_blank_invalid_and_non_mapping_payloads(
    tmp_path: Path,
) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{not-json", encoding="utf-8")
    list_path = tmp_path / "list.json"
    list_path.write_text('["not", "a", "dict"]', encoding="utf-8")

    assert load_json_artifact(None) == {}
    assert load_json_artifact(" ") == {}
    assert load_json_artifact(invalid_path) == {}
    assert load_json_artifact(list_path) == {}
