"""Unit tests for CI cost reporting helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "ci_cost_report.py"
    spec = importlib.util.spec_from_file_location("ci_cost_report_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_job_name_mapping_handles_matrix_jobs() -> None:
    module = _load_module()

    assert module._job_contract_id("Tests (Python 3.12)") == "unit-test"
    assert module._job_contract_id("Integration (core)") == "integration-test"
    assert module._job_contract_id("Required E2E Gate") == "required-e2e-gate"


def test_build_job_records_classifies_receipt_and_github_jobs() -> None:
    module = _load_module()
    contract = {
        "jobs": {
            "lint": {"local_equivalent": True, "local_stage": "static-analysis"},
            "required-e2e-gate": {"local_equivalent": True, "local_stage": "e2e"},
            "risk-classifier": {"local_equivalent": False, "local_stage": "ci-policy"},
        }
    }
    jobs = [
        {
            "name": "Linting & Formatting",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-03-08T05:00:00Z",
            "completed_at": "2026-03-08T05:00:30Z",
            "html_url": "https://example.test/lint",
        },
        {
            "name": "Required E2E Gate",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-03-08T05:00:30Z",
            "completed_at": "2026-03-08T05:00:35Z",
            "html_url": "https://example.test/e2e",
        },
        {
            "name": "Risk Classifier",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-03-08T05:00:35Z",
            "completed_at": "2026-03-08T05:00:40Z",
            "html_url": "https://example.test/risk",
        },
    ]

    records = module.build_job_records(jobs, contract)
    summary = module.summarize_job_records(records)

    assert records[0].execution_class == "github_executed_local_equivalent"
    assert records[1].execution_class == "local_receipt_validation"
    assert records[2].execution_class == "github_policy_or_heavy_only"
    assert summary["total_duration_seconds"] == 40
    assert summary["by_classification"]["local_receipt_validation"]["job_count"] == 1


def test_summarize_runs_filters_and_aggregates() -> None:
    module = _load_module()
    runs = [
        {
            "id": 1,
            "name": "CI/CD Pipeline",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-03-08T05:00:00Z",
            "updated_at": "2026-03-08T05:10:00Z",
            "head_branch": "codex/test",
            "html_url": "https://example.test/1",
        },
        {
            "id": 2,
            "name": "Deploy Windows",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-03-08T06:00:00Z",
            "updated_at": "2026-03-08T06:05:00Z",
            "head_branch": "main",
            "html_url": "https://example.test/2",
        },
        {
            "id": 3,
            "name": "Unrelated Workflow",
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-03-08T07:00:00Z",
            "updated_at": "2026-03-08T07:03:00Z",
            "head_branch": "main",
            "html_url": "https://example.test/3",
        },
    ]

    summary = module.summarize_runs(
        runs,
        days=7,
        workflow_names={"CI/CD Pipeline", "Deploy Windows"},
    )

    assert summary["total_runs"] == 2
    assert summary["total_duration_seconds"] == 900
    assert summary["by_workflow"]["CI/CD Pipeline"]["run_count"] == 1
    assert summary["by_event"]["push"]["duration_seconds"] == 300


def test_run_command_writes_json_report(tmp_path: Path) -> None:
    module = _load_module()
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "jobs": {
                    "lint": {"local_equivalent": True, "local_stage": "static-analysis"},
                    "required-e2e-gate": {"local_equivalent": True, "local_stage": "e2e"},
                }
            }
        ),
        encoding="utf-8",
    )
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(
        json.dumps(
            [
                {
                    "name": "Linting & Formatting",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-03-08T05:00:00Z",
                    "completed_at": "2026-03-08T05:00:30Z",
                    "html_url": "https://example.test/lint",
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"

    rc = module.main.__wrapped__ if hasattr(module.main, "__wrapped__") else None
    assert rc is None

    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "ci_cost_report.py",
            "run",
            "--workflow-name",
            "CI/CD Pipeline",
            "--event-name",
            "pull_request",
            "--contract",
            str(contract_path),
            "--jobs-file",
            str(jobs_path),
            "--output",
            str(output_path),
        ]
        assert module.main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total_jobs"] == 1
    assert payload["jobs"][0]["execution_class"] == "github_executed_local_equivalent"
