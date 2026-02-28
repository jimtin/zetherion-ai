param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$LastGoodShaPath = "C:\ZetherionAI\data\last-good-sha.txt",
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [int]$LockStaleMinutes = 45,
    [Parameter(Mandatory = $false)]
    [int]$MaxNetworkWaitSeconds = 300,
    [Parameter(Mandatory = $false)]
    [int]$MaxDockerWaitSeconds = 300,
    [Parameter(Mandatory = $false)]
    [string]$ReceiptPath = "C:\ZetherionAI\data\boot-recovery-receipt.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Write-BootReceipt {
    param([object]$Receipt, [string]$Path)
    Ensure-ParentDir -Path $Path
    $Receipt | ConvertTo-Json -Depth 10 | Out-File $Path -Encoding utf8
}

function Get-CurrentRepoSha {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return ""
    }
    Push-Location $Path
    try {
        $sha = (git rev-parse HEAD 2>$null).Trim()
        return $sha
    }
    catch {
        return ""
    }
    finally {
        Pop-Location
    }
}

function Wait-ForNetwork {
    param([int]$TimeoutSeconds)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $ipConfig = Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object {
                $_.NetAdapter.Status -eq "Up" -and $_.IPv4DefaultGateway -ne $null
            }
            if ($ipConfig) {
                return $true
            }
        }
        catch {
            # Ignore transient probe errors while waiting.
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Wait-ForDocker {
    param([int]$TimeoutSeconds, [ref]$ActionsTaken)

    $dockerService = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
    if ($dockerService -and $dockerService.Status -ne "Running") {
        Start-Service -Name "com.docker.service"
        $ActionsTaken.Value += "started_docker_service"
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            docker info *> $null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        }
        catch {
            # Wait and retry.
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

$bootId = [guid]::NewGuid().ToString()
$bootStartedAt = [DateTime]::UtcNow
$actionsTaken = @()
$activeChecks = [ordered]@{
    containers_healthy = $false
    bot_startup_markers = $false
    postgres_model_keys = $false
    fallback_probe = $false
}
$status = "failed"
$errorText = ""

$preBootSha = Get-CurrentRepoSha -Path $DeployPath
$postRecoverySha = $preBootSha

$verifyScriptPath = Join-Path $DeployPath "scripts\windows\verify-runtime.ps1"
$rollbackScriptPath = Join-Path $DeployPath "scripts\windows\rollback-last-good.ps1"
$tempVerifyPath = Join-Path $env:TEMP "startup-verify-result.json"
$tempRollbackPath = Join-Path $env:TEMP "startup-rollback-result.json"
$tempPostRollbackVerifyPath = Join-Path $env:TEMP "startup-post-rollback-verify-result.json"

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }
    if (-not (Test-Path $verifyScriptPath)) {
        throw "Verification script not found: $verifyScriptPath"
    }
    if (-not (Test-Path $rollbackScriptPath)) {
        throw "Rollback script not found: $rollbackScriptPath"
    }

    if (-not (Wait-ForNetwork -TimeoutSeconds $MaxNetworkWaitSeconds)) {
        throw "Network readiness check timed out after $MaxNetworkWaitSeconds seconds."
    }
    $actionsTaken += "network_ready"

    if (-not (Wait-ForDocker -TimeoutSeconds $MaxDockerWaitSeconds -ActionsTaken ([ref]$actionsTaken))) {
        throw "Docker readiness check timed out after $MaxDockerWaitSeconds seconds."
    }
    $actionsTaken += "docker_ready"

    if (Test-Path $LockPath) {
        $lockItem = Get-Item $LockPath -ErrorAction SilentlyContinue
        if ($lockItem) {
            $ageMinutes = ((Get-Date) - $lockItem.LastWriteTime).TotalMinutes
            if ($ageMinutes -ge $LockStaleMinutes) {
                Remove-Item -Path $LockPath -Force
                $actionsTaken += "removed_stale_lock"
            } else {
                throw "Active deployment lock present at $LockPath (age=$([Math]::Round($ageMinutes, 1)) minutes)."
            }
        }
    }

    Push-Location $DeployPath
    try {
        docker compose up -d
    }
    finally {
        Pop-Location
    }
    $actionsTaken += "compose_up"

    & $verifyScriptPath -DeployPath $DeployPath -OutputPath $tempVerifyPath
    if ($LASTEXITCODE -eq 0) {
        if (Test-Path $tempVerifyPath) {
            $verifyResult = Get-Content $tempVerifyPath -Raw | ConvertFrom-Json
            $activeChecks = [ordered]@{
                containers_healthy = [bool]$verifyResult.checks.containers_healthy
                bot_startup_markers = [bool]$verifyResult.checks.bot_startup_markers
                postgres_model_keys = [bool]$verifyResult.checks.postgres_model_keys
                fallback_probe = [bool]$verifyResult.checks.fallback_probe
            }
        }
        $status = "success"
        $actionsTaken += "verify_success"
    } else {
        $actionsTaken += "verify_failed_initial"

        & $rollbackScriptPath `
            -DeployPath $DeployPath `
            -LastGoodShaPath $LastGoodShaPath `
            -LockPath $LockPath `
            -OutputPath $tempRollbackPath

        if ($LASTEXITCODE -ne 0) {
            throw "Rollback failed during startup recovery."
        }

        $actionsTaken += "rollback_executed"

        & $verifyScriptPath -DeployPath $DeployPath -OutputPath $tempPostRollbackVerifyPath
        if ($LASTEXITCODE -eq 0) {
            if (Test-Path $tempPostRollbackVerifyPath) {
                $postRollbackVerify = Get-Content $tempPostRollbackVerifyPath -Raw | ConvertFrom-Json
                $activeChecks = [ordered]@{
                    containers_healthy = [bool]$postRollbackVerify.checks.containers_healthy
                    bot_startup_markers = [bool]$postRollbackVerify.checks.bot_startup_markers
                    postgres_model_keys = [bool]$postRollbackVerify.checks.postgres_model_keys
                    fallback_probe = [bool]$postRollbackVerify.checks.fallback_probe
                }
            }
            $status = "rolled_back"
            $actionsTaken += "verify_success_after_rollback"
        } else {
            if (Test-Path $tempPostRollbackVerifyPath) {
                $postRollbackVerify = Get-Content $tempPostRollbackVerifyPath -Raw | ConvertFrom-Json
                $activeChecks = [ordered]@{
                    containers_healthy = [bool]$postRollbackVerify.checks.containers_healthy
                    bot_startup_markers = [bool]$postRollbackVerify.checks.bot_startup_markers
                    postgres_model_keys = [bool]$postRollbackVerify.checks.postgres_model_keys
                    fallback_probe = [bool]$postRollbackVerify.checks.fallback_probe
                }
            }
            throw "Runtime verification still failing after rollback."
        }
    }
}
catch {
    $errorText = $_.Exception.Message
    if ($status -ne "rolled_back") {
        $status = "failed"
    }
    $actionsTaken += "startup_recovery_error"
}
finally {
    $postRecoverySha = Get-CurrentRepoSha -Path $DeployPath

    $receipt = [ordered]@{
        boot_id = $bootId
        boot_started_at = $bootStartedAt.ToString("o")
        boot_completed_at = [DateTime]::UtcNow.ToString("o")
        pre_boot_sha = $preBootSha
        post_recovery_sha = $postRecoverySha
        checks = $activeChecks
        actions = $actionsTaken
        status = $status
        error = $errorText
    }

    Write-BootReceipt -Receipt $receipt -Path $ReceiptPath
}

if ($status -eq "success" -or $status -eq "rolled_back") {
    exit 0
}

exit 1
