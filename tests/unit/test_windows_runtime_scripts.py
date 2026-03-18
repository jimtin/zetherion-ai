"""Regression checks for Windows runtime and worker scripts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_RUNTIME_PATH = REPO_ROOT / "scripts" / "windows" / "verify-runtime.ps1"
RUNTIME_WATCHDOG_PATH = REPO_ROOT / "scripts" / "windows" / "runtime-watchdog.ps1"
DOCKER_RUNTIME_PATH = REPO_ROOT / "scripts" / "windows" / "docker-runtime.ps1"
INSTALL_CI_WORKER_PATH = REPO_ROOT / "scripts" / "windows" / "install-ci-worker.ps1"
VERIFY_CI_WORKER_CONNECTIVITY_PATH = (
    REPO_ROOT / "scripts" / "windows" / "verify-ci-worker-connectivity.ps1"
)
WSL_KEEPALIVE_PATH = REPO_ROOT / "scripts" / "windows" / "wsl-keepalive.ps1"
DISK_CLEANUP_PATH = REPO_ROOT / "scripts" / "windows" / "disk-cleanup.ps1"
VERIFY_WINDOWS_HOST_PATH = REPO_ROOT / "scripts" / "verify-windows-host.ps1"
DOCKER_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"


def test_verify_runtime_uses_shell_safe_postgres_probe() -> None:
    verify_runtime = VERIFY_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function Invoke-PostgresSettingsQuery" in verify_runtime
    assert "docker exec -i zetherion-ai-postgres psql" in verify_runtime
    assert "base64 -d | docker exec -i zetherion-ai-postgres psql" in verify_runtime
    assert "WHERE namespace = 'models'" in verify_runtime


def test_verify_runtime_uses_non_throwing_probe_helpers() -> None:
    verify_runtime = VERIFY_RUNTIME_PATH.read_text(encoding="utf-8")

    assert (
        "Invoke-ZetherionWslDockerResult compose logs zetherion-ai-bot --tail 400"
        in verify_runtime
    )
    assert "function Invoke-FallbackProbe" in verify_runtime
    assert "base64 -d | docker exec -i zetherion-ai-bot python -" in verify_runtime


def test_runtime_watchdog_skips_restarts_for_non_restartable_failures() -> None:
    runtime_watchdog = RUNTIME_WATCHDOG_PATH.read_text(encoding="utf-8")

    assert "function Test-RestartableRuntimeFailure" in runtime_watchdog
    assert "Repair-ZetherionDockerDesktopRuntime -TimeoutSeconds 180 -RepairSettings -DisableAutoPause" in runtime_watchdog
    assert 'throw "Docker Desktop recovery did not restore the desktop-linux engine."' in runtime_watchdog
    assert "docker_recovery = $dockerRecovery" in runtime_watchdog
    assert "restart_skipped_nonrestartable_failure" in runtime_watchdog
    assert "$state.consecutive_failures = 0" in runtime_watchdog
    assert "Get-ZetherionDiskStatus" in runtime_watchdog
    assert "Invoke-ZetherionDiskCleanup" in runtime_watchdog
    assert '"disk_cleanup_low_headroom"' in runtime_watchdog
    assert (
        "return (-not [bool]$Checks.containers_healthy) -or "
        "(-not [bool]$Checks.bot_startup_markers)"
    ) in runtime_watchdog


def test_docker_runtime_exposes_non_throwing_wsl_helpers() -> None:
    docker_runtime = DOCKER_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "function Invoke-ZetherionWslCommandResult" in docker_runtime
    assert "function Invoke-ZetherionWslDockerResult" in docker_runtime
    assert "function Get-ZetherionWslHostConfigPath" in docker_runtime
    assert "function Get-ZetherionWslHostConfig" in docker_runtime
    assert "function Set-ZetherionWslHostVmIdleTimeout" in docker_runtime
    assert "function Get-ZetherionWslDockerConfigStatus" in docker_runtime
    assert "function Ensure-ZetherionWslDockerHeadlessConfig" in docker_runtime
    assert "function Get-ZetherionDiskStatus" in docker_runtime
    assert "function Invoke-ZetherionDiskCleanup" in docker_runtime
    assert "image_prune_unused" in docker_runtime
    assert "volume_prune_unused" in docker_runtime
    assert "workspace_artifact_cleanup" in docker_runtime
    assert "runtime_artifact_cleanup" in docker_runtime
    assert "function Get-ZetherionTrackedComposeProjectManifests" in docker_runtime
    assert "function Get-ZetherionComposeProjects" in docker_runtime
    assert "function Get-ZetherionComposeProjectCreatedAtUtc" in docker_runtime
    assert "function Remove-ZetherionDockerResourcesByLabel" in docker_runtime
    assert "function Remove-ZetherionStaleComposeProjects" in docker_runtime
    assert 'if ($projectName -notlike "zetherion-ai-test-run-*"' in docker_runtime
    assert '$projectName -notlike "owner-ci-*"' in docker_runtime
    assert "stale_compose_project_detected" in docker_runtime
    assert 'action = "stale_compose_project_$($resource.suffix)_remove"' in docker_runtime
    assert '-LabelFilter "label=com.docker.compose.project=$projectName"' in docker_runtime
    assert "stale_compose_project_minutes" in docker_runtime
    assert '"credsStore"' in docker_runtime
    assert '"headless_ready"' in docker_runtime
    assert "vmIdleTimeout=" in docker_runtime
    assert "Text = ($output | Out-String).Trim()" in docker_runtime
    assert "foreach ($root in @($artifactsRoot, $logsRoot))" in docker_runtime
    assert 'if ($child.Name -eq "home")' in docker_runtime
    assert 'if ($child.Name -eq "venv")' in docker_runtime
    assert "function Get-ZetherionDockerDesktopSettingsPath" in docker_runtime
    assert "function Get-ZetherionDockerDesktopSettings" in docker_runtime
    assert "function Set-ZetherionDockerDesktopDesiredConfiguration" in docker_runtime
    assert "function Get-ZetherionDockerDesktopStatus" in docker_runtime
    assert "function Wait-ZetherionDockerDesktopEngine" in docker_runtime
    assert "function Ensure-ZetherionWslDockerService" in docker_runtime
    assert "function Repair-ZetherionDockerDesktopRuntime" in docker_runtime
    assert '$script:ZetherionRequiredDockerMemoryMiB = 98304' in docker_runtime
    assert '$script:ZetherionRequiredDockerSwapMiB = 0' in docker_runtime
    assert '$script:ZetherionDockerDesktopContextName = "desktop-linux"' in docker_runtime
    assert '$script:ZetherionDockerDesktopServiceName = "com.docker.service"' in docker_runtime
    assert 'Set-ZetherionObjectPropertyValue -Object $settings -Name "autoStart" -Value $true' in docker_runtime
    assert 'Set-ZetherionObjectPropertyValue -Object $settings -Name "memoryMiB" -Value $MemoryMiB' in docker_runtime
    assert 'Set-ZetherionObjectPropertyValue -Object $settings -Name "swapMiB" -Value $SwapMiB' in docker_runtime
    assert '& $dockerCli.Source --context $contextName info *> $null' in docker_runtime


def test_docker_runtime_supports_native_windows_backend() -> None:
    docker_runtime = DOCKER_RUNTIME_PATH.read_text(encoding="utf-8")

    assert '$script:ZetherionExecutionBackend' in docker_runtime
    assert '$script:ZetherionWslDistribution' in docker_runtime
    assert "function Get-ZetherionNativeDockerRuntimeStatus" in docker_runtime
    assert 'backend = "native_windows_docker"' in docker_runtime
    assert 'backend = "wsl_docker"' in docker_runtime
    assert 'Get-Command "docker.exe"' in docker_runtime
    assert "function Invoke-ZetherionNativeDockerResult" in docker_runtime
    assert "function Invoke-ZetherionDockerResult" in docker_runtime
    assert 'if ((Get-ZetherionDockerBackend) -eq "native_windows_docker")' in docker_runtime


def test_install_ci_worker_writes_backend_contract_and_blocks_live_runtime_path() -> None:
    install_script = INSTALL_CI_WORKER_PATH.read_text(encoding="utf-8")

    assert '[ValidateSet("native_windows_docker", "wsl_docker")]' in install_script
    assert '[string]$ExecutionBackend = "wsl_docker"' in install_script
    assert '[int64]$RecommendedWslVmIdleTimeoutMs = 604800000' in install_script
    assert "Get-ZetherionWslHostConfig" in install_script
    assert "Set-ZetherionWslHostVmIdleTimeout" in install_script
    assert "function Register-WslKeepaliveTask" in install_script
    assert 'Start-ScheduledTask -TaskName $WslKeepaliveTaskName' in install_script
    assert 'wsl_keepalive_task_name = $WslKeepaliveTaskName' in install_script
    assert "recommended_vm_idle_timeout_ms" in install_script
    assert "restart_required = [bool]$wslConfigChanged" in install_script
    assert 'worker_execution_backend = ' in install_script
    assert 'worker_workspace_root = ' in install_script
    assert 'worker_runtime_root = ' in install_script
    assert 'worker_docker_backend = ' in install_script
    assert 'worker_wsl_distribution = ' in install_script
    assert "Ensure-ZetherionWslDockerHeadlessConfig" in install_script
    assert "wsl_docker_config = if ($wslDockerConfig)" in install_script
    assert "WSL Docker config still depends on a desktop credential helper." in install_script
    assert '$env:ZETHERION_WSL_DISTRIBUTION = "' in install_script
    assert "WorkspaceRoot must not point to C:\\ZetherionAI" in install_script
    assert 'cleanup_enabled = true' in install_script
    assert 'worker_cleanup_enabled = true' in install_script
    assert 'worker_cleanup_low_disk_free_bytes = 21474836480' in install_script
    assert 'worker_cleanup_target_free_bytes = 42949672960' in install_script
    assert 'worker_cleanup_artifact_retention_hours = 24' in install_script
    assert 'worker_cleanup_log_retention_days = 7' in install_script
    assert '$mutexName = "Global\\ZetherionOwnerCiWorker"' in install_script
    assert 'Owner-CI worker already running; exiting duplicate launcher.' in install_script
    assert '$hasHandle = $mutex.WaitOne(0, $false)' in install_script
    assert (
        'if ($ExecutionBackend -eq "wsl_docker" -and -not ($AllowedCommands -contains "wsl"))'
        in install_script
    )


def test_verify_ci_worker_connectivity_emits_worker_certification_receipt() -> None:
    verify_connectivity = VERIFY_CI_WORKER_CONNECTIVITY_PATH.read_text(encoding="utf-8")

    assert '"worker_execution_backend": cfg.worker_execution_backend' in verify_connectivity
    assert '"worker_workspace_root": cfg.worker_workspace_root' in verify_connectivity
    assert '"worker_runtime_root": cfg.worker_runtime_root' in verify_connectivity
    assert '"worker_docker_backend": cfg.worker_docker_backend' in verify_connectivity
    assert '"worker_wsl_distribution": cfg.worker_wsl_distribution' in verify_connectivity
    assert "$code = @'" in verify_connectivity
    assert '$output = $code | & $pythonExe -' in verify_connectivity
    assert '$dns.PSObject.Properties.Name -contains "IPAddress"' in verify_connectivity
    assert 'receipt_kind = "WorkerCertificationReceipt"' in verify_connectivity
    assert 'execution_backend = $executionBackend' in verify_connectivity
    assert 'docker_backend = $dockerBackend' in verify_connectivity
    assert 'wsl_distribution = $wslDistribution' in verify_connectivity
    assert 'key = "wsl_keepalive_task_running"' in verify_connectivity
    assert 'Get-ScheduledTaskSummary' in verify_connectivity
    assert 'wsl_keepalive_task = $wslKeepaliveTask' in verify_connectivity
    assert 'last_task_result = if ($info) { [int]$info.LastTaskResult } else { $null }' in verify_connectivity
    assert '$wslKeepaliveHealthy = [bool](' in verify_connectivity
    assert 'key = "wsl_idle_timeout_configured"' in verify_connectivity
    assert 'key = "wsl_docker_config_ready"' in verify_connectivity
    assert 'wsl_host_config = if ($wslHostConfig)' in verify_connectivity
    assert 'wsl_docker_config = if ($wslDockerConfig)' in verify_connectivity
    assert "Ensure-ZetherionWslDockerHeadlessConfig" in verify_connectivity
    assert "Get-ZetherionWslHostConfig" in verify_connectivity
    assert "function Resolve-WorkspaceEvidencePath" in verify_connectivity
    assert "function ConvertFrom-ZetherionJson" in verify_connectivity
    assert "function Get-LatestWorkspaceReadiness" in verify_connectivity
    assert '$payload.PSObject.Properties.Name -contains "shard_receipts"' in verify_connectivity
    assert '$shardReceipts = @($payload.shard_receipts)' in verify_connectivity
    assert (
        '$workspaceReadiness = Get-LatestWorkspaceReadiness -WorkspaceRoot $workspaceRoot'
        in verify_connectivity
    )
    assert "ConvertFrom-ZetherionJson" in verify_connectivity
    assert 'path = [string]$workspaceReadiness.path' in verify_connectivity
    assert 'cleanup_statuses = @($workspaceReadiness.cleanup_statuses)' in verify_connectivity
    assert 'key = "ci_test_run_succeeded"' in verify_connectivity
    assert 'key = "status_publication_succeeded"' in verify_connectivity


def test_wsl_keepalive_script_starts_docker_and_holds_wsl_open() -> None:
    keepalive_script = WSL_KEEPALIVE_PATH.read_text(encoding="utf-8")

    assert '[string]$WslDistribution = "Ubuntu"' in keepalive_script
    assert '$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution' in keepalive_script
    assert (
        'Invoke-ZetherionWslCommand -Command "systemctl start docker >/dev/null '
        '2>&1 || true"' in keepalive_script
    )
    assert "while true; do sleep 3600; done" in keepalive_script


def test_disk_cleanup_script_writes_receipt_and_uses_shared_cleanup_helper() -> None:
    cleanup_script = DISK_CLEANUP_PATH.read_text(encoding="utf-8")
    expected_output_path = (
        '[string]$OutputPath = '
        '"C:\\ZetherionCI\\artifacts\\disk-cleanup-receipt.json"'
    )

    assert '[string]$CiRoot = "C:\\ZetherionCI"' in cleanup_script
    assert expected_output_path in cleanup_script
    assert ". (Join-Path $PSScriptRoot \"docker-runtime.ps1\")" in cleanup_script
    assert "Invoke-ZetherionDiskCleanup" in cleanup_script
    assert "Write-CleanupResult" in cleanup_script


def test_deploy_runner_triggers_disk_cleanup_after_rebuild() -> None:
    deploy_runner = (REPO_ROOT / "scripts" / "windows" / "deploy-runner.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string]$CleanupReceiptPath = "deploy-cleanup-receipt.json"' in deploy_runner
    assert "Invoke-ZetherionDiskCleanup -CiRoot \"C:\\ZetherionCI\" -Aggressive" in deploy_runner
    assert '$result.cleanup_status = [string]$cleanupReceipt.status' in deploy_runner
    assert '$result.cleanup_status = "cleanup_failed"' in deploy_runner


def test_default_runtime_disables_ollama_unless_explicitly_enabled() -> None:
    deploy_runner = (REPO_ROOT / "scripts" / "windows" / "deploy-runner.ps1").read_text(
        encoding="utf-8"
    )
    startup_recover = (REPO_ROOT / "scripts" / "windows" / "startup-recover.ps1").read_text(
        encoding="utf-8"
    )
    rollback_script = (REPO_ROOT / "scripts" / "windows" / "rollback-last-good.ps1").read_text(
        encoding="utf-8"
    )

    for script in (deploy_runner, startup_recover, rollback_script):
        assert 'Keys @("ENABLE_OLLAMA_RUNTIME")' in script
        assert 'Set-OrAddEnvLine -Lines $lines -Key "ROUTER_BACKEND" -Value "gemini"' in script
        assert 'Set-OrAddEnvLine -Lines $lines -Key "EMBEDDINGS_BACKEND" -Value "openai"' in script
        assert '$profiles.Add("ollama")' in script
        assert '--remove-orphans' in script


def test_default_compose_marks_ollama_services_as_optional() -> None:
    compose_text = DOCKER_COMPOSE_PATH.read_text(encoding="utf-8")

    assert "container_name: zetherion-ai-ollama\n    profiles:\n      - ollama" in compose_text
    assert (
        "container_name: zetherion-ai-ollama-router\n    profiles:\n      - ollama"
        in compose_text
    )


def test_verify_windows_host_treats_ollama_as_optional_runtime() -> None:
    verify_windows_host = VERIFY_WINDOWS_HOST_PATH.read_text(encoding="utf-8")

    assert '. (Join-Path $PSScriptRoot "windows\\docker-runtime.ps1")' in verify_windows_host
    assert '[string]$WslDistribution = "Ubuntu"' in verify_windows_host
    assert '$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution' in verify_windows_host
    assert 'Get-ZetherionDockerDesktopStatus' in verify_windows_host
    assert 'Add-Check -Name "docker_resources"' in verify_windows_host
    assert 'Add-Check -Name "docker_service"' in verify_windows_host
    assert 'Add-Check -Name "wsl_docker_service"' in verify_windows_host
    assert 'Add-Check -Name "docker_unattended_recovery"' in verify_windows_host
    assert 'Get-EnvValueFromFile -Path $envPath -Key "ENABLE_OLLAMA_RUNTIME"' in verify_windows_host
    assert '$auxiliaryContainers += "zetherion-ai-ollama"' in verify_windows_host
    assert '$auxiliaryContainers += "zetherion-ai-ollama-router"' in verify_windows_host
    assert (
        'Add-Check -Name "ollama_models" -Status "pass" -Message '
        '"Ollama runtime is disabled by default"'
        in verify_windows_host
    )


def test_register_resilience_tasks_registers_disk_cleanup_task() -> None:
    register_script_path = REPO_ROOT / "scripts" / "windows" / "register-resilience-tasks.ps1"
    register_script = register_script_path.read_text(encoding="utf-8")

    assert '[string]$CleanupTaskName = "ZetherionDiskCleanup"' in register_script
    assert '[int]$CleanupIntervalMinutes = 180' in register_script
    assert "disk-cleanup.ps1" in register_script
    assert "registered_cleanup_task:$CleanupTaskName" in register_script
    assert "cleanup_task_registered" in register_script


def test_startup_recover_uses_shared_docker_desktop_repair_flow() -> None:
    startup_recover = (REPO_ROOT / "scripts" / "windows" / "startup-recover.ps1").read_text(
        encoding="utf-8"
    )

    assert "Repair-ZetherionDockerDesktopRuntime -TimeoutSeconds $TimeoutSeconds -RepairSettings -DisableAutoPause" in startup_recover
    assert '$ActionsTaken.Value += "docker_settings_repaired"' in startup_recover
    assert 'return [bool]$repair.success' in startup_recover
