param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$StatePath = "C:\ZetherionAI\data\watchdog-state.json",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\watchdog-result.json",
    [Parameter(Mandatory = $false)]
    [string]$LastGoodShaPath = "C:\ZetherionAI\data\last-good-sha.txt",
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [int]$FailureThreshold = 2,
    [Parameter(Mandatory = $false)]
    [string]$EventSource = "ZetherionRuntimeWatchdog"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

if ($FailureThreshold -lt 1) {
    throw "FailureThreshold must be >= 1."
}

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Read-State {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [ordered]@{
            consecutive_failures = 0
            last_status = "unknown"
            last_checked_at = ""
            last_error = ""
        }
    }

    try {
        return (Get-Content $Path -Raw | ConvertFrom-Json)
    }
    catch {
        return [ordered]@{
            consecutive_failures = 0
            last_status = "unknown"
            last_checked_at = ""
            last_error = "state_parse_error"
        }
    }
}

function Write-JsonFile {
    param([object]$Payload, [string]$Path)
    Ensure-ParentDir -Path $Path
    $Payload | ConvertTo-Json -Depth 10 | Out-File $Path -Encoding utf8
}

function Write-WatchdogEvent {
    param(
        [string]$Source,
        [string]$EntryType,
        [int]$EventId,
        [string]$Message
    )

    try {
        if (-not [System.Diagnostics.EventLog]::SourceExists($Source)) {
            New-EventLog -LogName "Application" -Source $Source
        }
        Write-EventLog -LogName "Application" -Source $Source -EntryType $EntryType -EventId $EventId -Message $Message
    }
    catch {
        # Event logging should never block remediation logic.
    }
}

function Extract-Checks {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [ordered]@{
            containers_healthy = $false
            bot_startup_markers = $false
            postgres_model_keys = $false
            fallback_probe = $false
        }
    }
    $payload = Get-Content $Path -Raw | ConvertFrom-Json
    return [ordered]@{
        containers_healthy = [bool]$payload.checks.containers_healthy
        bot_startup_markers = [bool]$payload.checks.bot_startup_markers
        postgres_model_keys = [bool]$payload.checks.postgres_model_keys
        fallback_probe = [bool]$payload.checks.fallback_probe
    }
}

$state = Read-State -Path $StatePath
$actions = @()
$status = "failed"
$errorText = ""
$verifyPath = Join-Path $env:TEMP "watchdog-verify-result.json"
$verifyAfterRestartPath = Join-Path $env:TEMP "watchdog-verify-after-restart.json"
$verifyAfterRollbackPath = Join-Path $env:TEMP "watchdog-verify-after-rollback.json"
$rollbackPath = Join-Path $env:TEMP "watchdog-rollback-result.json"
$checks = [ordered]@{
    containers_healthy = $false
    bot_startup_markers = $false
    postgres_model_keys = $false
    fallback_probe = $false
}

$verifyScriptPath = Join-Path $DeployPath "scripts\windows\verify-runtime.ps1"
$rollbackScriptPath = Join-Path $DeployPath "scripts\windows\rollback-last-good.ps1"

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

    & $verifyScriptPath -DeployPath $DeployPath -OutputPath $verifyPath
    if ($LASTEXITCODE -eq 0) {
        $checks = Extract-Checks -Path $verifyPath
        $state.consecutive_failures = 0
        $status = "healthy"
        $actions += "verify_success"
    } else {
        $checks = Extract-Checks -Path $verifyPath
        $state.consecutive_failures = [int]$state.consecutive_failures + 1
        $actions += "verify_failed"

        Push-Location $DeployPath
        try {
            docker compose restart
        }
        finally {
            Pop-Location
        }
        $actions += "compose_restart"

        & $verifyScriptPath -DeployPath $DeployPath -OutputPath $verifyAfterRestartPath
        if ($LASTEXITCODE -eq 0) {
            $checks = Extract-Checks -Path $verifyAfterRestartPath
            $state.consecutive_failures = 0
            $status = "recovered_after_restart"
            $actions += "verify_success_after_restart"
        } elseif ([int]$state.consecutive_failures -ge $FailureThreshold) {
            & $rollbackScriptPath `
                -DeployPath $DeployPath `
                -LastGoodShaPath $LastGoodShaPath `
                -LockPath $LockPath `
                -OutputPath $rollbackPath
            if ($LASTEXITCODE -ne 0) {
                throw "Rollback failed after repeated watchdog failures."
            }

            $actions += "rollback_executed"

            & $verifyScriptPath -DeployPath $DeployPath -OutputPath $verifyAfterRollbackPath
            if ($LASTEXITCODE -eq 0) {
                $checks = Extract-Checks -Path $verifyAfterRollbackPath
                $state.consecutive_failures = 0
                $status = "rolled_back"
                $actions += "verify_success_after_rollback"
            } else {
                $checks = Extract-Checks -Path $verifyAfterRollbackPath
                $status = "failed"
                $actions += "verify_failed_after_rollback"
            }
        } else {
            $checks = Extract-Checks -Path $verifyAfterRestartPath
            $status = "degraded"
            $actions += "awaiting_failure_threshold"
        }
    }
}
catch {
    $errorText = $_.Exception.Message
    if ($status -eq "healthy" -or $status -eq "recovered_after_restart" -or $status -eq "rolled_back") {
        $status = "failed"
    }
}
finally {
    $state.last_status = $status
    $state.last_checked_at = [DateTime]::UtcNow.ToString("o")
    $state.last_error = $errorText

    $result = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        checks = $checks
        status = $status
        actions = $actions
        consecutive_failures = [int]$state.consecutive_failures
        error = $errorText
    }

    Write-JsonFile -Payload $state -Path $StatePath
    Write-JsonFile -Payload $result -Path $OutputPath

    switch ($status) {
        "healthy" {
            Write-WatchdogEvent -Source $EventSource -EntryType "Information" -EventId 7000 -Message "Watchdog check healthy."
        }
        "recovered_after_restart" {
            Write-WatchdogEvent -Source $EventSource -EntryType "Warning" -EventId 7001 -Message "Watchdog recovered service by container restart."
        }
        "rolled_back" {
            Write-WatchdogEvent -Source $EventSource -EntryType "Warning" -EventId 7002 -Message "Watchdog rolled back to last known good SHA."
        }
        "degraded" {
            Write-WatchdogEvent -Source $EventSource -EntryType "Warning" -EventId 7003 -Message "Watchdog detected degradation and is waiting for threshold."
        }
        default {
            $message = if ($errorText) { "Watchdog failure: $errorText" } else { "Watchdog failure without explicit error." }
            Write-WatchdogEvent -Source $EventSource -EntryType "Error" -EventId 7099 -Message $message
        }
    }
}

if ($status -eq "healthy" -or $status -eq "recovered_after_restart" -or $status -eq "rolled_back") {
    exit 0
}

exit 1
