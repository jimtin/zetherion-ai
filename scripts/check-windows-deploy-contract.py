#!/usr/bin/env python3
"""Guardrail: Windows deploy workflow must preserve core-vs-aux receipt semantics."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path.cwd()
VERIFY_RUNTIME_PATH = REPO_ROOT / "scripts" / "windows" / "verify-runtime.ps1"
VERIFY_HOST_PATH = REPO_ROOT / "scripts" / "verify-windows-host.ps1"
DEPLOY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-windows.yml"
CORE_ONLY_EXIT_GATE = (
    "if ($checks.containers_healthy -and $checks.bot_startup_markers "
    "-and $checks.postgres_model_keys -and $checks.fallback_probe)"
)


def collect_violations(
    *,
    verify_runtime_path: Path = VERIFY_RUNTIME_PATH,
    verify_host_path: Path = VERIFY_HOST_PATH,
    deploy_workflow_path: Path = DEPLOY_WORKFLOW_PATH,
) -> list[str]:
    violations: list[str] = []
    verify_runtime = verify_runtime_path.read_text(encoding="utf-8")
    verify_host = verify_host_path.read_text(encoding="utf-8")
    deploy_workflow = deploy_workflow_path.read_text(encoding="utf-8")

    for token in (
        "auxiliary_services_healthy",
        'core_status = "healthy"',
        'aux_status = "degraded"',
    ):
        if token not in verify_runtime:
            violations.append(f"verify-runtime.ps1: missing token {token!r}")

    if CORE_ONLY_EXIT_GATE not in verify_runtime:
        violations.append("verify-runtime.ps1: missing core-only success exit gate")

    if 'Add-Check -Name "containers_auxiliary"' not in verify_host:
        violations.append("verify-windows-host.ps1: missing containers_auxiliary check")

    for token in (
        'WINDOWS_REQUIRE_HEALTHY_AUXILIARY_SERVICES: "false"',
        '"auxiliary_services_healthy=$($checks.auxiliary_services_healthy)"',
        '"core_status=$($details.core_status)"',
        '"aux_status=$($details.aux_status)"',
        "core_status = $coreStatus",
        "aux_status = $auxStatus",
        'if ($receipt.core_status -ne "healthy")',
        'if ($requireHealthyAuxiliaryServices -and $receipt.aux_status -eq "degraded")',
    ):
        if token not in deploy_workflow:
            violations.append(f"deploy-windows.yml: missing token {token!r}")

    return violations


def main() -> int:
    violations = collect_violations()
    if not violations:
        print("Windows deploy contract check passed.")
        return 0

    print("Windows deploy contract check failed.")
    print()
    print("Violations:")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
