"""Unit tests for the Windows deploy core-vs-aux contract guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check-windows-deploy-contract.py"
CORE_ONLY_EXIT_GATE = (
    "if ($checks.containers_healthy -and $checks.bot_startup_markers "
    "-and $checks.postgres_model_keys -and $checks.fallback_probe)"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_windows_deploy_contract_module", MODULE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_violations_accepts_complete_contract(tmp_path: Path) -> None:
    module = _load_module()
    verify_runtime = tmp_path / "verify-runtime.ps1"
    verify_host = tmp_path / "verify-windows-host.ps1"
    deploy_workflow = tmp_path / "deploy-windows.yml"

    verify_runtime.write_text(
        "\n".join(
            [
                "auxiliary_services_healthy",
                'core_status = "healthy"',
                'aux_status = "degraded"',
                CORE_ONLY_EXIT_GATE,
            ]
        ),
        encoding="utf-8",
    )
    verify_host.write_text('Add-Check -Name "containers_auxiliary"', encoding="utf-8")
    deploy_workflow.write_text(
        "\n".join(
            [
                'WINDOWS_REQUIRE_HEALTHY_AUXILIARY_SERVICES: "false"',
                '"auxiliary_services_healthy=$($checks.auxiliary_services_healthy)"',
                '"core_status=$($details.core_status)"',
                '"aux_status=$($details.aux_status)"',
                "core_status = $coreStatus",
                "aux_status = $auxStatus",
                'if ($receipt.core_status -ne "healthy")',
                'if ($requireHealthyAuxiliaryServices -and $receipt.aux_status -eq "degraded")',
            ]
        ),
        encoding="utf-8",
    )

    assert (
        module.collect_violations(
            verify_runtime_path=verify_runtime,
            verify_host_path=verify_host,
            deploy_workflow_path=deploy_workflow,
        )
        == []
    )


def test_collect_violations_flags_missing_auxiliary_contract_tokens(tmp_path: Path) -> None:
    module = _load_module()
    verify_runtime = tmp_path / "verify-runtime.ps1"
    verify_host = tmp_path / "verify-windows-host.ps1"
    deploy_workflow = tmp_path / "deploy-windows.yml"

    verify_runtime.write_text("if ($checks.containers_healthy) { exit 0 }", encoding="utf-8")
    verify_host.write_text("# no auxiliary check here", encoding="utf-8")
    deploy_workflow.write_text("core_status = $coreStatus", encoding="utf-8")

    violations = module.collect_violations(
        verify_runtime_path=verify_runtime,
        verify_host_path=verify_host,
        deploy_workflow_path=deploy_workflow,
    )

    assert any("verify-runtime.ps1" in violation for violation in violations)
    assert any("verify-windows-host.ps1" in violation for violation in violations)
    assert any("deploy-windows.yml" in violation for violation in violations)
