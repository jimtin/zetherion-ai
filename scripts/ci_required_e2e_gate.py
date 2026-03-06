#!/usr/bin/env python3
"""CI gate for required E2E: validate local receipt contract, never run E2E on GitHub."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class GateConfig:
    required: bool
    decision_reason_code: str
    decision_reason: str
    expected_sha: str
    local_receipt_path: Path
    output_path: Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_config() -> GateConfig:
    expected_sha = _env("E2E_EXPECTED_SHA") or _env("GITHUB_HEAD_SHA") or _env("GITHUB_SHA")
    local_receipt = _env("LOCAL_E2E_RECEIPT_PATH", ".ci/e2e-receipt.json")
    output_path = _env("RECEIPT_PATH", "e2e-contract-receipt.json")
    return GateConfig(
        required=_as_bool(_env("E2E_REQUIRED", "true")),
        decision_reason_code=_env("E2E_DECISION_REASON_CODE", "unknown"),
        decision_reason=_env("E2E_DECISION_REASON", "No reason provided."),
        expected_sha=expected_sha,
        local_receipt_path=Path(local_receipt),
        output_path=Path(output_path),
    )


def _load_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "local_receipt_missing"
    except json.JSONDecodeError:
        return None, "local_receipt_invalid_json"

    if not isinstance(payload, dict):
        return None, "local_receipt_not_object"
    return payload, None


def _sha_matches(receipt_sha: str, expected_sha: str) -> bool:
    left = receipt_sha.strip()
    right = expected_sha.strip()
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


def validate_local_receipt(payload: dict[str, object], *, expected_sha: str) -> list[str]:
    errors: list[str] = []

    if str(payload.get("status", "")).strip().lower() != "success":
        errors.append("status_must_be_success")

    if str(payload.get("run_context", "")).strip().lower() != "local":
        errors.append("run_context_must_be_local")

    receipt_sha = str(payload.get("head_sha", "")).strip()
    if not _sha_matches(receipt_sha, expected_sha):
        errors.append("head_sha_mismatch")

    suites_obj = payload.get("suites")
    if not isinstance(suites_obj, dict):
        errors.append("suites_missing")
        return errors

    docker_obj = suites_obj.get("docker_e2e")
    if not isinstance(docker_obj, dict) or str(docker_obj.get("status", "")).strip().lower() != "passed":
        errors.append("docker_e2e_not_passed")

    discord_obj = suites_obj.get("discord_required_e2e")
    if not isinstance(discord_obj, dict) or str(discord_obj.get("status", "")).strip().lower() != "passed":
        errors.append("discord_required_e2e_not_passed")

    return errors


def _write_receipt(config: GateConfig, payload: dict[str, object]) -> None:
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _base_receipt(config: GateConfig) -> dict[str, object]:
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "required": config.required,
        "classifier": {
            "reason_code": config.decision_reason_code,
            "reason": config.decision_reason,
        },
        "expected_sha": config.expected_sha,
        "local_receipt_path": str(config.local_receipt_path),
    }


def run_gate(config: GateConfig) -> tuple[int, dict[str, object]]:
    base = _base_receipt(config)

    if not config.required:
        return 0, {
            **base,
            "status": "not_required",
            "reason_code": "not_required_by_risk_classifier",
            "reason": "Risk classifier marked this change set as low risk.",
            "validation_errors": [],
        }

    if not config.expected_sha:
        return 1, {
            **base,
            "status": "failed",
            "reason_code": "missing_expected_sha",
            "reason": "Expected commit SHA is missing; cannot validate local E2E receipt.",
            "validation_errors": ["missing_expected_sha"],
        }

    local_payload, load_error = _load_json(config.local_receipt_path)
    if load_error:
        return 1, {
            **base,
            "status": "failed",
            "reason_code": load_error,
            "reason": "Required local E2E receipt is missing or invalid.",
            "validation_errors": [load_error],
        }

    assert local_payload is not None
    errors = validate_local_receipt(local_payload, expected_sha=config.expected_sha)
    if errors:
        return 1, {
            **base,
            "status": "failed",
            "reason_code": "local_receipt_contract_failed",
            "reason": "Local required-E2E receipt failed contract validation.",
            "validation_errors": errors,
            "local_receipt": local_payload,
        }

    return 0, {
        **base,
        "status": "success",
        "reason_code": "local_receipt_validated",
        "reason": "Required local E2E receipt validated for this commit.",
        "validation_errors": [],
        "local_receipt": local_payload,
    }


def main() -> int:
    config = build_config()
    exit_code, receipt = run_gate(config)
    _write_receipt(config, receipt)

    if exit_code != 0:
        errors = receipt.get("validation_errors", [])
        print(f"ERROR: required E2E gate failed: {receipt.get('reason_code')} errors={errors}")
    else:
        print(f"Required E2E gate result: {receipt.get('status')}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
