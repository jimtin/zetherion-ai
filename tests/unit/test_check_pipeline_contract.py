"""Unit tests for pipeline contract and workflow-trigger coverage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "check_pipeline_contract.py"
    spec = importlib.util.spec_from_file_location("check_pipeline_contract_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_required_jobs_include_fast_path_and_deferred_jobs() -> None:
    module = _load_module()

    expected = {
        "detect-changes",
        "risk-classifier",
        "required-e2e-gate",
        "docker-build-test",
    }

    assert expected.issubset(module.REQUIRED_JOBS)


def test_workflow_contracts_match_segment_4_offload_rules() -> None:
    module = _load_module()

    errors = module.validate_workflow_contracts(REPO_ROOT)

    assert errors == []


def test_pipeline_contract_file_is_complete() -> None:
    module = _load_module()

    errors = module.validate_pipeline_contract(REPO_ROOT / ".ci/pipeline_contract.json")

    assert errors == []
