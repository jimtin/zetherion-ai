#!/usr/bin/env python3
"""Validate deployment-receipt.json against the main deploy success contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_REQUIRED_CHECKS = {
    "containers_healthy",
    "bot_startup_markers",
    "postgres_model_keys",
    "fallback_probe",
    "recovery_tasks_registered",
    "runner_service_persistent",
    "docker_service_persistent",
}
_ALLOWED_AUX_STATUSES = {"healthy", "degraded", "not_enabled"}


def _normalize_sha(value: str | None) -> str:
    return (value or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, help="Path to deployment-receipt.json")
    parser.add_argument(
        "--expected-sha",
        default="",
        help="Expected deployed/target SHA (full or short prefix)",
    )
    args = parser.parse_args()

    receipt_path = Path(args.receipt)
    if not receipt_path.exists():
        print(f"ERROR: receipt file not found: {receipt_path}")
        return 1

    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON receipt: {exc}")
        return 1

    if not isinstance(payload, dict):
        print("ERROR: receipt payload must be a JSON object")
        return 1

    status = str(payload.get("status", ""))
    if status != "success":
        print(f"ERROR: receipt status must be 'success' (got {status!r})")
        return 1

    target_sha = _normalize_sha(str(payload.get("target_sha", "")))
    deployed_sha = _normalize_sha(str(payload.get("deployed_sha", "")))
    if not target_sha or not deployed_sha:
        print("ERROR: receipt missing target_sha/deployed_sha")
        return 1
    if target_sha != deployed_sha:
        print(
            "ERROR: receipt SHA mismatch " f"(target_sha={target_sha}, deployed_sha={deployed_sha})"
        )
        return 1

    expected_sha = _normalize_sha(args.expected_sha)
    if expected_sha and not (
        target_sha == expected_sha
        or target_sha.startswith(expected_sha)
        or expected_sha.startswith(target_sha)
    ):
        print(
            "ERROR: receipt SHA does not match expected SHA "
            f"(expected={expected_sha}, got={target_sha})"
        )
        return 1

    core_status = str(payload.get("core_status", ""))
    aux_status = str(payload.get("aux_status", ""))
    if core_status != "healthy":
        print(f"ERROR: receipt core_status must be 'healthy' (got {core_status!r})")
        return 1
    if aux_status not in _ALLOWED_AUX_STATUSES:
        print(
            "ERROR: receipt aux_status must be one of "
            f"{sorted(_ALLOWED_AUX_STATUSES)!r} (got {aux_status!r})"
        )
        return 1

    checks = payload.get("checks")
    if not isinstance(checks, dict):
        print("ERROR: receipt checks must be an object")
        return 1

    auxiliary_services_healthy = checks.get("auxiliary_services_healthy")
    if aux_status == "degraded" and auxiliary_services_healthy is not False:
        print("ERROR: receipt aux_status degraded requires checks.auxiliary_services_healthy=false")
        return 1
    if aux_status in {"healthy", "not_enabled"} and auxiliary_services_healthy is not True:
        print(
            "ERROR: receipt aux_status healthy/not_enabled requires "
            "checks.auxiliary_services_healthy=true"
        )
        return 1

    failed: list[str] = []
    for key in sorted(_REQUIRED_CHECKS):
        if checks.get(key) is not True:
            failed.append(key)

    if failed:
        print("ERROR: receipt health checks failed:")
        for key in failed:
            print(f"  - {key}")
        return 1

    print("Deployment receipt validation passed.")
    print(f"target_sha={target_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
