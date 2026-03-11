param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$StartupTaskName = "ZetherionStartupRecover",
    [Parameter(Mandatory = $false)]
    [string]$WatchdogTaskName = "ZetherionRuntimeWatchdog",
    [Parameter(Mandatory = $false)]
    [string]$PromotionsTaskName = "ZetherionPostDeployPromotions",
    [Parameter(Mandatory = $false)]
    [string]$CanaryTaskName = "ZetherionDiscordCanary",
    [Parameter(Mandatory = $false)]
    [string]$LegacyTaskName = "ZetherionDockerAutoStart",
    [Parameter(Mandatory = $false)]
    [int]$WatchdogIntervalMinutes = 5,
    [Parameter(Mandatory = $false)]
    [int]$PromotionsIntervalMinutes = 10,
    [Parameter(Mandatory = $false)]
    [int]$CanaryIntervalMinutes = 360,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "resilience-registration.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($WatchdogIntervalMinutes -lt 1) {
    throw "WatchdogIntervalMinutes must be >= 1."
}
if ($PromotionsIntervalMinutes -lt 1) {
    throw "PromotionsIntervalMinutes must be >= 1."
}
if ($CanaryIntervalMinutes -lt 1) {
    throw "CanaryIntervalMinutes must be >= 1."
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
        $systemPrincipal = Is-ServiceAccountPrincipal -UserId $task.Principal.UserId
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
        $status = ""
        if ($parsed.ContainsKey("Status")) {
            $status = [string]$parsed["Status"]
        }
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

function Is-ServiceAccountPrincipal {
    param([string]$UserId)

    if (-not $UserId) {
        return $false
    }

    return $UserId -in @(
        "SYSTEM",
        "NT AUTHORITY\SYSTEM",
        "NETWORK SERVICE",
        "NT AUTHORITY\NETWORK SERVICE"
    )
}

function Get-RegistrationActor {
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        if ($identity -and $identity.Name) {
            return [string]$identity.Name
        }
    } catch {
        # Ignore and fall back to environment-derived actor.
    }

    $userDomain = [string]$env:USERDOMAIN
    $userName = [string]$env:USERNAME
    if ($userDomain -and $userName) {
        return "$userDomain\$userName"
    }
    if ($userName) {
        return $userName
    }
    return "unknown"
}

function Test-IsElevated {
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        if (-not $identity) {
            return $false
        }
        $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

$registrationActor = Get-RegistrationActor
$isElevated = Test-IsElevated

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        startup_task_registered = $false
        watchdog_task_registered = $false
        promotions_task_registered = $false
        canary_task_registered = $false
        legacy_task_disabled = $false
        recovery_tasks_registered = $false
    }
    details = [ordered]@{
        startup_task = $StartupTaskName
        watchdog_task = $WatchdogTaskName
        promotions_task = $PromotionsTaskName
        canary_task = $CanaryTaskName
        legacy_task = $LegacyTaskName
        deploy_path = $DeployPath
        watchdog_interval_minutes = $WatchdogIntervalMinutes
        promotions_interval_minutes = $PromotionsIntervalMinutes
        canary_interval_minutes = $CanaryIntervalMinutes
        startup_task_probe = $null
        watchdog_task_probe = $null
        promotions_task_probe = $null
        canary_task_probe = $null
        bootstrap_required = $false
        failure_code = ""
        registration_actor = $registrationActor
        is_elevated = [bool]$isElevated
        actions_taken = @()
        warnings = @()
    }
    status = "failed"
    error = ""
}

try {
    $startupScriptPath = Join-Path $DeployPath "scripts\windows\startup-recover.ps1"
    $watchdogScriptPath = Join-Path $DeployPath "scripts\windows\runtime-watchdog.ps1"
    $promotionsWatchScriptPath = Join-Path $DeployPath "scripts\windows\promotions-watch.ps1"
    $canaryScriptPath = Join-Path $DeployPath "scripts\windows\discord-canary-runner.ps1"
    $canaryPythonScriptPath = Join-Path $DeployPath "scripts\windows\discord-canary.py"

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
    if (-not (Test-Path $promotionsWatchScriptPath)) {
        $sourcePromotionsWatchScriptPath = Join-Path $PSScriptRoot "promotions-watch.ps1"
        if (-not (Test-Path $sourcePromotionsWatchScriptPath)) {
            throw "Promotions watch script not found at $promotionsWatchScriptPath or $sourcePromotionsWatchScriptPath"
        }
        $promotionsParent = Split-Path -Parent $promotionsWatchScriptPath
        if ($promotionsParent -and -not (Test-Path $promotionsParent)) {
            New-Item -ItemType Directory -Path $promotionsParent -Force | Out-Null
        }
        Copy-Item -Path $sourcePromotionsWatchScriptPath -Destination $promotionsWatchScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:promotions-watch.ps1"
    }
    if (-not (Test-Path $canaryScriptPath)) {
        $sourceCanaryScriptPath = Join-Path $PSScriptRoot "discord-canary-runner.ps1"
        if (-not (Test-Path $sourceCanaryScriptPath)) {
            throw "Discord canary runner script not found at $canaryScriptPath or $sourceCanaryScriptPath"
        }
        $canaryParent = Split-Path -Parent $canaryScriptPath
        if ($canaryParent -and -not (Test-Path $canaryParent)) {
            New-Item -ItemType Directory -Path $canaryParent -Force | Out-Null
        }
        Copy-Item -Path $sourceCanaryScriptPath -Destination $canaryScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:discord-canary-runner.ps1"
    }
    if (-not (Test-Path $canaryPythonScriptPath)) {
        $sourceCanaryPythonScriptPath = Join-Path $PSScriptRoot "discord-canary.py"
        if (-not (Test-Path $sourceCanaryPythonScriptPath)) {
            throw "Discord canary Python script not found at $canaryPythonScriptPath or $sourceCanaryPythonScriptPath"
        }
        $canaryPythonParent = Split-Path -Parent $canaryPythonScriptPath
        if ($canaryPythonParent -and -not (Test-Path $canaryPythonParent)) {
            New-Item -ItemType Directory -Path $canaryPythonParent -Force | Out-Null
        }
        Copy-Item -Path $sourceCanaryPythonScriptPath -Destination $canaryPythonScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:discord-canary.py"
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
    $promotionsProbe = Get-RecoveryTaskRecord -TaskName $PromotionsTaskName -ScriptNeedle "promotions-watch.ps1"
    $canaryProbe = Get-RecoveryTaskRecord -TaskName $CanaryTaskName -ScriptNeedle "discord-canary-runner.ps1"
    $result.details.startup_task_probe = $startupProbe
    $result.details.watchdog_task_probe = $watchdogProbe
    $result.details.promotions_task_probe = $promotionsProbe
    $result.details.canary_task_probe = $canaryProbe

    if ($startupProbe.passes -and $watchdogProbe.passes -and $promotionsProbe.passes -and $canaryProbe.passes) {
        $result.details.actions_taken += "resilience_tasks_already_registered"
        $result.checks.startup_task_registered = $true
        $result.checks.watchdog_task_registered = $true
        $result.checks.promotions_task_registered = $true
        $result.checks.canary_task_registered = $true
    } else {
        $registrationAccessDenied = $false
        try {
            $principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\NETWORK SERVICE" -LogonType ServiceAccount -RunLevel Highest

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

            $promotionsAction = New-ScheduledTaskAction `
                -Execute "pwsh.exe" `
                -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$promotionsWatchScriptPath`" -DeployPath `"$DeployPath`""
            $promotionsStartupTrigger = New-ScheduledTaskTrigger -AtStartup
            $promotionsRecurringTrigger = New-ScheduledTaskTrigger `
                -Once `
                -At ((Get-Date).AddMinutes(2)) `
                -RepetitionInterval (New-TimeSpan -Minutes $PromotionsIntervalMinutes) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $promotionsSettings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
            $promotionsTask = New-ScheduledTask `
                -Action $promotionsAction `
                -Trigger @($promotionsStartupTrigger, $promotionsRecurringTrigger) `
                -Principal $principal `
                -Settings $promotionsSettings `
                -Description "Process post-deploy promotions (blog + release) on startup and periodic schedule."
            Register-ScheduledTask -TaskName $PromotionsTaskName -InputObject $promotionsTask -Force | Out-Null
            $result.details.actions_taken += "registered_promotions_task:$PromotionsTaskName"

            $canaryAction = New-ScheduledTaskAction `
                -Execute "pwsh.exe" `
                -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$canaryScriptPath`" -DeployPath `"$DeployPath`""
            $canaryStartupTrigger = New-ScheduledTaskTrigger -AtStartup
            $canaryRecurringTrigger = New-ScheduledTaskTrigger `
                -Once `
                -At ((Get-Date).AddMinutes(3)) `
                -RepetitionInterval (New-TimeSpan -Minutes $CanaryIntervalMinutes) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $canarySettings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
            $canaryTask = New-ScheduledTask `
                -Action $canaryAction `
                -Trigger @($canaryStartupTrigger, $canaryRecurringTrigger) `
                -Principal $principal `
                -Settings $canarySettings `
                -Description "Run the isolated Discord production canary on startup and periodic schedule."
            Register-ScheduledTask -TaskName $CanaryTaskName -InputObject $canaryTask -Force | Out-Null
            $result.details.actions_taken += "registered_canary_task:$CanaryTaskName"
        } catch {
            $message = $_.Exception.Message
            if ($message -and $message -like "*Access is denied*") {
                $registrationAccessDenied = $true
                $result.details.actions_taken += "task_registration_skipped_access_denied"
                $result.details.failure_code = "TASK_REGISTRATION_ACCESS_DENIED"
            } else {
                throw
            }
        }

        $startupProbe = Get-RecoveryTaskRecord -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
        $watchdogProbe = Get-RecoveryTaskRecord -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"
        $promotionsProbe = Get-RecoveryTaskRecord -TaskName $PromotionsTaskName -ScriptNeedle "promotions-watch.ps1"
        $canaryProbe = Get-RecoveryTaskRecord -TaskName $CanaryTaskName -ScriptNeedle "discord-canary-runner.ps1"
        $result.details.startup_task_probe = $startupProbe
        $result.details.watchdog_task_probe = $watchdogProbe
        $result.details.promotions_task_probe = $promotionsProbe
        $result.details.canary_task_probe = $canaryProbe

        $result.checks.startup_task_registered = [bool](
            $startupProbe.passes -or $startupProbe.degraded_pass
        )
        $result.checks.watchdog_task_registered = [bool](
            $watchdogProbe.passes -or $watchdogProbe.degraded_pass
        )
        $result.checks.promotions_task_registered = [bool](
            $promotionsProbe.passes -or $promotionsProbe.degraded_pass
        )
        $result.checks.canary_task_registered = [bool](
            $canaryProbe.passes -or $canaryProbe.degraded_pass
        )

        if ($registrationAccessDenied) {
            $missingTasks = @()
            if (-not $startupProbe.exists) { $missingTasks += $StartupTaskName }
            if (-not $watchdogProbe.exists) { $missingTasks += $WatchdogTaskName }
            if (-not $promotionsProbe.exists) { $missingTasks += $PromotionsTaskName }
            if (-not $canaryProbe.exists) { $missingTasks += $CanaryTaskName }

            if ($missingTasks.Count -gt 0) {
                $result.details.bootstrap_required = $true
                $result.details.failure_code = "BOOTSTRAP_REQUIRED_TASKS_MISSING_ACCESS_DENIED"
                $result.details.warnings += "task_registration_access_denied_tasks_missing_bootstrap_required"
                $result.details.warnings += ("missing_tasks:" + ($missingTasks -join ","))
            } else {
                $result.details.bootstrap_required = $false
                $result.details.failure_code = "TASK_REGISTRATION_ACCESS_DENIED_TASKS_PRESENT"
                $result.details.warnings += "task_registration_access_denied_tasks_present"
            }
        }
    }

    $result.checks.recovery_tasks_registered = [bool](
        $result.checks.startup_task_registered -and $result.checks.watchdog_task_registered
    )

    $result.status = if (
        $result.checks.recovery_tasks_registered -and
        $result.checks.promotions_task_registered -and
        $result.checks.canary_task_registered -and
        $result.checks.legacy_task_disabled
    ) {
        "success"
    } else {
        "failed"
    }

    if ($result.status -ne "success" -and -not $result.details.failure_code) {
        if (-not $result.checks.legacy_task_disabled) {
            $result.details.failure_code = "LEGACY_TASK_DISABLE_FAILED"
        } else {
            $result.details.failure_code = "TASK_REGISTRATION_INCOMPLETE"
        }
    }
}
catch {
    $result.error = $_.Exception.Message
    if (-not $result.details.failure_code) {
        $result.details.failure_code = "UNHANDLED_EXCEPTION"
    }
    $result.status = "failed"
}

Write-RegistrationResult -Result $result -Path $OutputPath

if ($result.status -eq "success") {
    exit 0
}

exit 1
