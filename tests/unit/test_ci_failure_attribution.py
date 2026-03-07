"""Unit tests for CI failure attribution script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "ci_failure_attribution.py"
    spec = importlib.util.spec_from_file_location("ci_failure_attribution_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_required_e2e_gate_uses_policy_breach_reason() -> None:
    module = _load_module()
    contract: dict[str, object] = {"jobs": {}}

    failure = module.classify_failure("required-e2e-gate", "failure", contract)
    assert failure.reason_code == "AGENTS_POLICY_BREACH_REQUIRED_E2E"
    assert "required e2e gate failed" in failure.explanation.lower()


def test_missing_job_mapping_reports_contract_gap() -> None:
    module = _load_module()
    contract: dict[str, object] = {"jobs": {}}

    failure = module.classify_failure("unknown-job", "failure", contract)
    assert failure.reason_code == "PIPELINE_CONTRACT_GAP"


def test_local_stage_maps_to_specific_local_gate_breach_reason() -> None:
    module = _load_module()
    contract = {
        "jobs": {
            "unit-test": {
                "local_equivalent": True,
                "local_stage": "unit+mypy",
                "note": "unit tests with coverage and mypy run locally",
            }
        }
    }

    failure = module.classify_failure("unit-test", "failure", contract)
    assert failure.reason_code == "LOCAL_GATE_BREACH_UNIT_AND_MYPY"
    assert "Local gate stage: unit+mypy" in failure.explanation


def test_local_equivalent_failure_maps_to_local_breach() -> None:
    module = _load_module()
    contract = {
        "jobs": {
            "lint": {
                "local_equivalent": True,
                "note": "ruff runs locally",
            }
        }
    }

    failure = module.classify_failure("lint", "failure", contract)
    assert failure.reason_code == "SHOULD_HAVE_BEEN_CAUGHT_LOCALLY"
    assert "ruff runs locally" in failure.explanation
