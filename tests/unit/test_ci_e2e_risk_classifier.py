"""Unit tests for CI required-E2E risk classifier script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "ci_e2e_risk_classifier.py"
    spec = importlib.util.spec_from_file_location("ci_e2e_risk_classifier_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_schedule_is_always_required() -> None:
    module = _load_module()

    result = module.classify_changed_paths(
        event_name="schedule",
        changed_paths=["docs/development/ci-cd.md"],
    )
    assert result.e2e_required is True
    assert result.reason_code == "scheduled_or_manual_run"


def test_substantial_runtime_path_requires_e2e() -> None:
    module = _load_module()

    result = module.classify_changed_paths(
        event_name="pull_request",
        changed_paths=["src/zetherion_ai/skills/server.py"],
    )
    assert result.e2e_required is True
    assert result.reason_code == "substantial_path_match"


def test_low_risk_paths_can_skip_required_e2e() -> None:
    module = _load_module()

    result = module.classify_changed_paths(
        event_name="pull_request",
        changed_paths=[
            "docs/development/ci-cd.md",
            "tests/unit/test_ci_e2e_risk_classifier.py",
            ".ci/pipeline_contract.json",
            "scripts/ci_e2e_risk_classifier.py",
            "scripts/ci_required_e2e_gate.py",
        ],
    )
    assert result.e2e_required is False
    assert result.reason_code == "low_risk_paths_only"


def test_unclassified_paths_fail_safe_to_required() -> None:
    module = _load_module()

    result = module.classify_changed_paths(
        event_name="pull_request",
        changed_paths=["scripts/random-helper.sh"],
    )
    assert result.e2e_required is True
    assert result.reason_code == "ambiguous_unclassified_path"


def test_missing_changed_paths_fail_safe_to_required() -> None:
    module = _load_module()

    result = module.classify_changed_paths(event_name="pull_request", changed_paths=[])
    assert result.e2e_required is True
    assert result.reason_code == "ambiguous_no_changed_files"
