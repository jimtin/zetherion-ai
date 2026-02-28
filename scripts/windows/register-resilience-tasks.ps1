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
        if ($action.Arguments -and $action.Arguments -like "*$Needle*") {
            return $true
        }
    }
    return $false
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
        actions_taken = @()
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
        -At ((Get-Date).Date.AddMinutes(1)) `
        -RepetitionInterval (New-TimeSpan -Minutes $WatchdogIntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
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

    $registeredStartupTask = Get-ScheduledTask -TaskName $StartupTaskName -ErrorAction SilentlyContinue
    $registeredWatchdogTask = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue

    $result.checks.startup_task_registered = [bool](
        $registeredStartupTask `
        -and (Is-SystemPrincipal -UserId $registeredStartupTask.Principal.UserId) `
        -and $registeredStartupTask.Settings.Enabled `
        -and (Task-ActionContains -Task $registeredStartupTask -Needle "startup-recover.ps1")
    )

    $result.checks.watchdog_task_registered = [bool](
        $registeredWatchdogTask `
        -and (Is-SystemPrincipal -UserId $registeredWatchdogTask.Principal.UserId) `
        -and $registeredWatchdogTask.Settings.Enabled `
        -and (Task-ActionContains -Task $registeredWatchdogTask -Needle "runtime-watchdog.ps1")
    )

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
