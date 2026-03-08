"""Regression tests for scripts/validate-deployment-receipt.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate-deployment-receipt.py"


def _write_receipt(tmp_path: Path, payload: dict[str, object]) -> Path:
    receipt_path = tmp_path / "deployment-receipt.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    return receipt_path


def _run_validator(
    *, receipt_path: Path, expected_sha: str = ""
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT_PATH), "--receipt", str(receipt_path)]
    if expected_sha:
        command.extend(["--expected-sha", expected_sha])
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=REPO_ROOT)


def _valid_receipt(sha: str) -> dict[str, object]:
    return {
        "status": "success",
        "target_sha": sha,
        "deployed_sha": sha,
        "core_status": "healthy",
        "aux_status": "healthy",
        "checks": {
            "containers_healthy": True,
            "auxiliary_services_healthy": True,
            "bot_startup_markers": True,
            "postgres_model_keys": True,
            "fallback_probe": True,
            "recovery_tasks_registered": True,
            "runner_service_persistent": True,
            "docker_service_persistent": True,
        },
    }


def test_validate_deployment_receipt_accepts_matching_sha_prefix(tmp_path: Path) -> None:
    sha = "c" * 40
    receipt_path = _write_receipt(tmp_path, _valid_receipt(sha))

    result = _run_validator(receipt_path=receipt_path, expected_sha=sha[:12])

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Deployment receipt validation passed." in result.stdout
    assert f"target_sha={sha}" in result.stdout


def test_validate_deployment_receipt_rejects_failed_health_check(tmp_path: Path) -> None:
    sha = "d" * 40
    payload = _valid_receipt(sha)
    payload["checks"]["bot_startup_markers"] = False  # type: ignore[index]
    receipt_path = _write_receipt(tmp_path, payload)

    result = _run_validator(receipt_path=receipt_path)

    assert result.returncode == 1
    assert "ERROR: receipt health checks failed:" in result.stdout
    assert "bot_startup_markers" in result.stdout


def test_validate_deployment_receipt_accepts_degraded_auxiliary_services(tmp_path: Path) -> None:
    sha = "e" * 40
    payload = _valid_receipt(sha)
    payload["aux_status"] = "degraded"
    payload["checks"]["auxiliary_services_healthy"] = False  # type: ignore[index]
    receipt_path = _write_receipt(tmp_path, payload)

    result = _run_validator(receipt_path=receipt_path)

    assert result.returncode == 0, result.stderr + result.stdout


def test_validate_deployment_receipt_rejects_failed_core_status(tmp_path: Path) -> None:
    sha = "f" * 40
    payload = _valid_receipt(sha)
    payload["core_status"] = "failed"
    receipt_path = _write_receipt(tmp_path, payload)

    result = _run_validator(receipt_path=receipt_path)

    assert result.returncode == 1
    assert "core_status" in result.stdout


def test_validate_deployment_receipt_rejects_inconsistent_auxiliary_contract(
    tmp_path: Path,
) -> None:
    sha = "1" * 40
    payload = _valid_receipt(sha)
    payload["aux_status"] = "degraded"
    receipt_path = _write_receipt(tmp_path, payload)

    result = _run_validator(receipt_path=receipt_path)

    assert result.returncode == 1
    assert "aux_status degraded" in result.stdout
