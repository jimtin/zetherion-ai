"""Unit tests for CI required-E2E local-receipt gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "ci_required_e2e_gate.py"
    spec = importlib.util.spec_from_file_location("ci_required_e2e_gate_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_not_required_succeeds_without_local_receipt(tmp_path: Path) -> None:
    module = _load_module()

    config = module.GateConfig(
        required=False,
        decision_reason_code="low_risk_paths_only",
        decision_reason="Only low-risk paths changed.",
        expected_sha="abc123",
        local_receipt_path=tmp_path / ".ci" / "e2e-receipt.json",
        output_path=tmp_path / "e2e-contract-receipt.json",
    )

    exit_code, payload = module.run_gate(config)
    assert exit_code == 0
    assert payload["status"] == "not_required"


def test_required_missing_local_receipt_fails(tmp_path: Path) -> None:
    module = _load_module()

    config = module.GateConfig(
        required=True,
        decision_reason_code="substantial_path_match",
        decision_reason="Substantial runtime paths changed.",
        expected_sha="abc123",
        local_receipt_path=tmp_path / ".ci" / "e2e-receipt.json",
        output_path=tmp_path / "e2e-contract-receipt.json",
    )

    exit_code, payload = module.run_gate(config)
    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["reason_code"] == "local_receipt_missing"


def test_required_receipt_sha_mismatch_fails(tmp_path: Path) -> None:
    module = _load_module()

    receipt_path = tmp_path / ".ci" / "e2e-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "status": "success",
                "run_context": "local",
                "head_sha": "deadbeef",
                "suites": {
                    "docker_e2e": {"status": "passed"},
                    "discord_required_e2e": {"status": "passed"},
                },
            }
        ),
        encoding="utf-8",
    )

    config = module.GateConfig(
        required=True,
        decision_reason_code="substantial_path_match",
        decision_reason="Substantial runtime paths changed.",
        expected_sha="abc123",
        local_receipt_path=receipt_path,
        output_path=tmp_path / "e2e-contract-receipt.json",
    )

    exit_code, payload = module.run_gate(config)
    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["reason_code"] == "local_receipt_contract_failed"
    assert "head_sha_mismatch" in payload["validation_errors"]


def test_required_valid_local_receipt_succeeds(tmp_path: Path) -> None:
    module = _load_module()

    expected_sha = "a" * 40
    receipt_path = tmp_path / ".ci" / "e2e-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "status": "success",
                "run_context": "local",
                "head_sha": expected_sha,
                "suites": {
                    "docker_e2e": {"status": "passed"},
                    "discord_required_e2e": {"status": "passed"},
                },
            }
        ),
        encoding="utf-8",
    )

    config = module.GateConfig(
        required=True,
        decision_reason_code="substantial_path_match",
        decision_reason="Substantial runtime paths changed.",
        expected_sha=expected_sha,
        local_receipt_path=receipt_path,
        output_path=tmp_path / "e2e-contract-receipt.json",
    )

    exit_code, payload = module.run_gate(config)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["reason_code"] == "local_receipt_validated"


def test_required_local_mode_receipt_succeeds_without_sha_coupling(tmp_path: Path) -> None:
    module = _load_module()

    expected_sha = "b" * 40
    receipt_path = tmp_path / ".ci" / "e2e-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "status": "success",
                "run_context": "local",
                "head_sha": "local",
                "source_head_sha": expected_sha,
                "suites": {
                    "docker_e2e": {"status": "passed"},
                    "discord_required_e2e": {"status": "passed"},
                },
            }
        ),
        encoding="utf-8",
    )

    config = module.GateConfig(
        required=True,
        decision_reason_code="substantial_path_match",
        decision_reason="Substantial runtime paths changed.",
        expected_sha=expected_sha,
        local_receipt_path=receipt_path,
        output_path=tmp_path / "e2e-contract-receipt.json",
    )

    exit_code, payload = module.run_gate(config)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["reason_code"] == "local_receipt_validated"
