from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTER_PATH = REPO_ROOT / "scripts" / "windows" / "register-resilience-tasks.ps1"
VERIFY_PATH = REPO_ROOT / "scripts" / "windows" / "verify-resilience-tasks.ps1"
READY_PATH = REPO_ROOT / "scripts" / "windows" / "check-resilience-ready.ps1"
BOOTSTRAP_PATH = REPO_ROOT / "scripts" / "windows" / "bootstrap-resilience-tasks.ps1"
SECRETS_PATH = REPO_ROOT / "scripts" / "windows" / "set-promotions-secrets.ps1"


def test_register_resilience_tasks_uses_wsl_compatible_user_principal() -> None:
    script = REGISTER_PATH.read_text(encoding="utf-8")

    assert '[string]$TaskUser = ""' in script
    assert '[string]$WslDistribution = "Ubuntu"' in script
    assert "function Resolve-TaskUser" in script
    assert "New-ScheduledTaskPrincipal -UserId $taskUser -LogonType S4U -RunLevel Highest" in script
    assert '-WslDistribution `"$WslDistribution`"' in script
    assert 'New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\\NETWORK SERVICE"' not in script


def test_verify_resilience_tasks_requires_expected_user_principal() -> None:
    script = VERIFY_PATH.read_text(encoding="utf-8")

    assert '[string]$TaskUser = ""' in script
    assert '[string]$WslDistribution = "Ubuntu"' in script
    assert "principal_mismatch" in script
    assert "principal_not_wsl_compatible" in script
    assert "expected_principal_user = $ExpectedPrincipalUser" in script


def test_resilience_ready_requires_matching_task_user() -> None:
    script = READY_PATH.read_text(encoding="utf-8")
    readiness_condition = (
        "passes = ($enabled -and $wslCompatiblePrincipal -and "
        "$actionMatches -and ($principalUser -ieq $ExpectedPrincipalUser))"
    )

    assert '[string]$TaskUser = ""' in script
    assert '[string]$WslDistribution = "Ubuntu"' in script
    assert "function Resolve-TaskUser" in script
    assert readiness_condition in script


def test_bootstrap_resilience_tasks_threads_task_user_through() -> None:
    script = BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert '[string]$TaskUser = ""' in script
    assert '[string]$WslDistribution = "Ubuntu"' in script
    assert "-TaskUser $TaskUser" in script
    assert "-WslDistribution $WslDistribution" in script


def test_promotions_secrets_default_to_current_user_account() -> None:
    script = SECRETS_PATH.read_text(encoding="utf-8")
    runner_resolution = (
        "$RunnerServiceAccount = Resolve-RunnerAccount "
        "-RequestedAccount $RunnerServiceAccount"
    )

    assert '[string]$RunnerServiceAccount = ""' in script
    assert "function Resolve-RunnerAccount" in script
    assert runner_resolution in script
