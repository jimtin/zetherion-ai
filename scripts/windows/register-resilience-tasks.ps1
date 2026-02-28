param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$StartupTaskName = "ZetherionStartupRecover",
    [Parameter(Mandatory = $false)]
    [string]$WatchdogTaskName = "ZetherionRuntimeWatchdog",
    [Parameter(Mandatory = $false)]
    [string]$LegacyTaskName = "ZetherionDockerAutoStart",
    [Parameter(Mandatory = $false)]
    [int]$WatchdogIntervalMinutes = 5,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "resilience-registration.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($WatchdogIntervalMinutes -lt 1) {
    throw "WatchdogIntervalMinutes must be >= 1."
}

function Write-RegistrationResult {
    param(
        [object]$Result,
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $Result | ConvertTo-Json -Depth 8 | Out-File $Path -Encoding utf8
}

function Task-ActionContains {
    param(
        [object]$Task,
        [string]$Needle
    )

    foreach ($action in @($Task.Actions)) {
        if (
            ($action.Arguments -and $action.Arguments -like "*$Needle*") -or
            ($action.Execute -and $action.Execute -like "*$Needle*")
        ) {
            return $true
        }
    }
    return $false
}

function Parse-SchtasksListOutput {
    param([string[]]$Lines)

    $parsed = @{}
    foreach ($line in $Lines) {
        if (-not $line) {
            continue
        }

        $parts = $line -split ":", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not $key) {
            continue
        }
        $parsed[$key] = $value
    }

    return $parsed
}

function Get-RecoveryTaskRecord {
    param(
        [string]$TaskName,
        [string]$ScriptNeedle
    )

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $enabled = [bool]$task.Settings.Enabled
        $systemPrincipal = Is-SystemPrincipal -UserId $task.Principal.UserId
        $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle
        return [ordered]@{
            exists = $true
            enabled = $enabled
            system_principal = $systemPrincipal
            action_matches = $actionMatches
            task_state = $task.State.ToString()
            source = "scheduled_task_api"
            passes = ($enabled -and $actionMatches -and $systemPrincipal)
            degraded_pass = ($enabled -and $actionMatches)
        }
    }

    $query = @(& schtasks /Query /TN $TaskName /FO LIST 2>$null)
    if ($LASTEXITCODE -eq 0 -and $query.Count -gt 0) {
        $parsed = Parse-SchtasksListOutput -Lines $query
        $status = [string]($parsed["Status"] ?? "")
        $enabled = -not ($status -match "Disabled")
        $stateLooksActive = [bool]($status -match "Ready|Running|Queued")

        return [ordered]@{
            exists = $true
            enabled = [bool]$enabled
            system_principal = $false
            action_matches = $false
            task_state = $status
            source = "schtasks_query_fallback"
            passes = [bool]($enabled -and $stateLooksActive)
            degraded_pass = [bool]($enabled -and $stateLooksActive)
        }
    }

    return [ordered]@{
        exists = $false
        enabled = $false
        system_principal = $false
        action_matches = $false
        task_state = "missing"
        source = "not_found"
        passes = $false
        degraded_pass = $false
    }
}

function Is-SystemPrincipal {
    param([string]$UserId)

    if (-not $UserId) {
        return $false
    }

    return $UserId -eq "SYSTEM" -or $UserId -eq "NT AUTHORITY\SYSTEM"
}

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        startup_task_registered = $false
        watchdog_task_registered = $false
        legacy_task_disabled = $false
        recovery_tasks_registered = $false
    }
    details = [ordered]@{
        startup_task = $StartupTaskName
        watchdog_task = $WatchdogTaskName
        legacy_task = $LegacyTaskName
        deploy_path = $DeployPath
        watchdog_interval_minutes = $WatchdogIntervalMinutes
        startup_task_probe = $null
        watchdog_task_probe = $null
        actions_taken = @()
        warnings = @()
    }
    status = "failed"
    error = ""
}

try {
    $startupScriptPath = Join-Path $DeployPath "scripts\windows\startup-recover.ps1"
    $watchdogScriptPath = Join-Path $DeployPath "scripts\windows\runtime-watchdog.ps1"

    if (-not (Test-Path $startupScriptPath)) {
        $sourceStartupScriptPath = Join-Path $PSScriptRoot "startup-recover.ps1"
        if (-not (Test-Path $sourceStartupScriptPath)) {
            throw "Startup recovery script not found at $startupScriptPath or $sourceStartupScriptPath"
        }
        $startupParent = Split-Path -Parent $startupScriptPath
        if ($startupParent -and -not (Test-Path $startupParent)) {
            New-Item -ItemType Directory -Path $startupParent -Force | Out-Null
        }
        Copy-Item -Path $sourceStartupScriptPath -Destination $startupScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:startup-recover.ps1"
    }
    if (-not (Test-Path $watchdogScriptPath)) {
        $sourceWatchdogScriptPath = Join-Path $PSScriptRoot "runtime-watchdog.ps1"
        if (-not (Test-Path $sourceWatchdogScriptPath)) {
            throw "Runtime watchdog script not found at $watchdogScriptPath or $sourceWatchdogScriptPath"
        }
        $watchdogParent = Split-Path -Parent $watchdogScriptPath
        if ($watchdogParent -and -not (Test-Path $watchdogParent)) {
            New-Item -ItemType Directory -Path $watchdogParent -Force | Out-Null
        }
        Copy-Item -Path $sourceWatchdogScriptPath -Destination $watchdogScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:runtime-watchdog.ps1"
    }

    $legacyTask = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
    if ($legacyTask) {
        if ($legacyTask.State -ne "Disabled") {
            Disable-ScheduledTask -TaskName $LegacyTaskName | Out-Null
            $result.details.actions_taken += "disabled_legacy_task:$LegacyTaskName"
        } else {
            $result.details.actions_taken += "legacy_task_already_disabled:$LegacyTaskName"
        }
        $result.checks.legacy_task_disabled = $true
    } else {
        $result.details.actions_taken += "legacy_task_not_present:$LegacyTaskName"
        $result.checks.legacy_task_disabled = $true
    }

    $startupProbe = Get-RecoveryTaskRecord -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
    $watchdogProbe = Get-RecoveryTaskRecord -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"
    $result.details.startup_task_probe = $startupProbe
    $result.details.watchdog_task_probe = $watchdogProbe

    if ($startupProbe.passes -and $watchdogProbe.passes) {
        $result.details.actions_taken += "recovery_tasks_already_registered"
        $result.checks.startup_task_registered = $true
        $result.checks.watchdog_task_registered = $true
    } else {
        try {
            $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

            $startupAction = New-ScheduledTaskAction `
                -Execute "pwsh.exe" `
                -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startupScriptPath`" -DeployPath `"$DeployPath`""
            $startupTrigger = New-ScheduledTaskTrigger -AtStartup
            $startupSettings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Hours 2)
            $startupTask = New-ScheduledTask `
                -Action $startupAction `
                -Trigger $startupTrigger `
                -Principal $principal `
                -Settings $startupSettings `
                -Description "Recover Zetherion runtime at host startup."
            Register-ScheduledTask -TaskName $StartupTaskName -InputObject $startupTask -Force | Out-Null
            $result.details.actions_taken += "registered_startup_task:$StartupTaskName"

            $watchdogAction = New-ScheduledTaskAction `
                -Execute "pwsh.exe" `
                -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScriptPath`" -DeployPath `"$DeployPath`""
            $watchdogTrigger = New-ScheduledTaskTrigger `
                -Once `
                -At ((Get-Date).AddMinutes(1)) `
                -RepetitionInterval (New-TimeSpan -Minutes $WatchdogIntervalMinutes) `
                -RepetitionDuration (New-TimeSpan -Days 30)
            $watchdogSettings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
            $watchdogTask = New-ScheduledTask `
                -Action $watchdogAction `
                -Trigger $watchdogTrigger `
                -Principal $principal `
                -Settings $watchdogSettings `
                -Description "Periodic runtime watchdog for Zetherion."
            Register-ScheduledTask -TaskName $WatchdogTaskName -InputObject $watchdogTask -Force | Out-Null
            $result.details.actions_taken += "registered_watchdog_task:$WatchdogTaskName"
        } catch {
            $message = $_.Exception.Message
            if ($message -and $message -like "*Access is denied*") {
                $result.details.actions_taken += "task_registration_skipped_access_denied"
                $result.details.warnings += "task_registration_access_denied_existing_task_fallback_applied"
            } else {
                throw
            }
        }

        $startupProbe = Get-RecoveryTaskRecord -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
        $watchdogProbe = Get-RecoveryTaskRecord -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"
        $result.details.startup_task_probe = $startupProbe
        $result.details.watchdog_task_probe = $watchdogProbe

        $result.checks.startup_task_registered = [bool](
            $startupProbe.passes -or $startupProbe.degraded_pass
        )
        $result.checks.watchdog_task_registered = [bool](
            $watchdogProbe.passes -or $watchdogProbe.degraded_pass
        )
    }

    $result.checks.recovery_tasks_registered = [bool](
        $result.checks.startup_task_registered -and $result.checks.watchdog_task_registered
    )

    $result.status = if ($result.checks.recovery_tasks_registered -and $result.checks.legacy_task_disabled) {
        "success"
    } else {
        "failed"
    }
}
catch {
    $result.error = $_.Exception.Message
    $result.status = "failed"
}

Write-RegistrationResult -Result $result -Path $OutputPath

if ($result.status -eq "success") {
    exit 0
}

exit 1
