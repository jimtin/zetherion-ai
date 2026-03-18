param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [string]$StartupTaskName = "ZetherionStartupRecover",
    [Parameter(Mandatory = $false)]
    [string]$WatchdogTaskName = "ZetherionRuntimeWatchdog",
    [Parameter(Mandatory = $false)]
    [string]$PromotionsTaskName = "ZetherionPostDeployPromotions",
    [Parameter(Mandatory = $false)]
    [string]$CanaryTaskName = "ZetherionDiscordCanary",
    [Parameter(Mandatory = $false)]
    [string]$CleanupTaskName = "ZetherionDiskCleanup",
    [Parameter(Mandatory = $false)]
    [string]$DockerDesktopTaskName = "ZetherionDockerAutoStart",
    [Parameter(Mandatory = $false)]
    [string]$TaskUser = "",
    [Parameter(Mandatory = $false)]
    [int]$WatchdogIntervalMinutes = 5,
    [Parameter(Mandatory = $false)]
    [int]$PromotionsIntervalMinutes = 10,
    [Parameter(Mandatory = $false)]
    [int]$CanaryIntervalMinutes = 360,
    [Parameter(Mandatory = $false)]
    [int]$CleanupIntervalMinutes = 180,
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
if ($CleanupIntervalMinutes -lt 1) {
    throw "CleanupIntervalMinutes must be >= 1."
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

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            $enabled = [bool]$task.Settings.Enabled
            $principalUser = [string]$task.Principal.UserId
            $wslCompatiblePrincipal = Test-WslCompatibleTaskPrincipal -UserId $principalUser
            $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle
            return [ordered]@{
                exists = $true
                enabled = $enabled
                principal_user = $principalUser
                system_principal = (Is-ServiceAccountPrincipal -UserId $principalUser)
                wsl_compatible_principal = $wslCompatiblePrincipal
                action_matches = $actionMatches
                task_state = $task.State.ToString()
                source = "scheduled_task_api"
                passes = ($enabled -and $actionMatches -and $wslCompatiblePrincipal)
                degraded_pass = $false
            }
        }
    }
    catch {
        return [ordered]@{
            exists = $false
            enabled = $false
            principal_user = ""
            system_principal = $false
            wsl_compatible_principal = $false
            action_matches = $false
            task_state = "probe_error"
            source = "scheduled_task_api_error"
            passes = $false
            degraded_pass = $false
            error = $_.Exception.Message
        }
    }

    return [ordered]@{
        exists = $false
        enabled = $false
        principal_user = ""
        system_principal = $false
        wsl_compatible_principal = $false
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

function Test-WslCompatibleTaskPrincipal {
    param([string]$UserId)

    if (-not $UserId) {
        return $false
    }

    return -not (Is-ServiceAccountPrincipal -UserId $UserId)
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

function Resolve-TaskUser {
    param([string]$RequestedUser)

    if ($RequestedUser) {
        if (-not (Test-WslCompatibleTaskPrincipal -UserId $RequestedUser)) {
            throw "TaskUser must be a non-service Windows user principal."
        }
        return $RequestedUser
    }

    $candidate = Get-RegistrationActor
    if (Test-WslCompatibleTaskPrincipal -UserId $candidate) {
        return $candidate
    }

    $fallback = if ($env:USERDOMAIN -and $env:USERNAME) {
        "$env:USERDOMAIN\$env:USERNAME"
    } else {
        [string]$env:USERNAME
    }
    if (Test-WslCompatibleTaskPrincipal -UserId $fallback) {
        return $fallback
    }

    throw "TaskUser must resolve to a non-service Windows user principal."
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

function Resolve-PowerShellExecutable {
    foreach ($candidate in @("pwsh.exe", "powershell.exe")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            return [string]$command.Source
        }
    }

    throw "Unable to locate pwsh.exe or powershell.exe for scheduled task registration."
}

function Resolve-DockerDesktopExecutable {
    foreach ($candidate in @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return [string]$candidate
        }
    }

    throw "Unable to locate Docker Desktop.exe for scheduled task registration."
}

$registrationActor = Get-RegistrationActor
$isElevated = Test-IsElevated
$taskUser = Resolve-TaskUser -RequestedUser $TaskUser
$powerShellExecutable = Resolve-PowerShellExecutable
$dockerDesktopExecutable = Resolve-DockerDesktopExecutable

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        startup_task_registered = $false
        watchdog_task_registered = $false
        promotions_task_registered = $false
        canary_task_registered = $false
        cleanup_task_registered = $false
        docker_desktop_task_registered = $false
        recovery_tasks_registered = $false
    }
    details = [ordered]@{
        startup_task = $StartupTaskName
        watchdog_task = $WatchdogTaskName
        promotions_task = $PromotionsTaskName
        canary_task = $CanaryTaskName
        cleanup_task = $CleanupTaskName
        docker_desktop_task = $DockerDesktopTaskName
        wsl_distribution = $WslDistribution
        task_user = $taskUser
        deploy_path = $DeployPath
        watchdog_interval_minutes = $WatchdogIntervalMinutes
        promotions_interval_minutes = $PromotionsIntervalMinutes
        canary_interval_minutes = $CanaryIntervalMinutes
        cleanup_interval_minutes = $CleanupIntervalMinutes
        startup_task_probe = $null
        watchdog_task_probe = $null
        promotions_task_probe = $null
        canary_task_probe = $null
        cleanup_task_probe = $null
        docker_desktop_task_probe = $null
        bootstrap_required = $false
        failure_code = ""
        registration_actor = $registrationActor
        is_elevated = [bool]$isElevated
        powershell_executable = $powerShellExecutable
        docker_desktop_executable = $dockerDesktopExecutable
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
    $cleanupScriptPath = Join-Path $DeployPath "scripts\windows\disk-cleanup.ps1"

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
    if (-not (Test-Path $cleanupScriptPath)) {
        $sourceCleanupScriptPath = Join-Path $PSScriptRoot "disk-cleanup.ps1"
        if (-not (Test-Path $sourceCleanupScriptPath)) {
            throw "Disk cleanup script not found at $cleanupScriptPath or $sourceCleanupScriptPath"
        }
        $cleanupParent = Split-Path -Parent $cleanupScriptPath
        if ($cleanupParent -and -not (Test-Path $cleanupParent)) {
            New-Item -ItemType Directory -Path $cleanupParent -Force | Out-Null
        }
        Copy-Item -Path $sourceCleanupScriptPath -Destination $cleanupScriptPath -Force
        $result.details.actions_taken += "bootstrapped_recovery_script:disk-cleanup.ps1"
    }

    $dockerDesktopProbe = Get-RecoveryTaskRecord -TaskName $DockerDesktopTaskName -ScriptNeedle "Docker Desktop.exe"
    $startupProbe = Get-RecoveryTaskRecord -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
    $watchdogProbe = Get-RecoveryTaskRecord -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"
    $promotionsProbe = Get-RecoveryTaskRecord -TaskName $PromotionsTaskName -ScriptNeedle "promotions-watch.ps1"
    $canaryProbe = Get-RecoveryTaskRecord -TaskName $CanaryTaskName -ScriptNeedle "discord-canary-runner.ps1"
    $cleanupProbe = Get-RecoveryTaskRecord -TaskName $CleanupTaskName -ScriptNeedle "disk-cleanup.ps1"
    $result.details.docker_desktop_task_probe = $dockerDesktopProbe
    $result.details.startup_task_probe = $startupProbe
    $result.details.watchdog_task_probe = $watchdogProbe
    $result.details.promotions_task_probe = $promotionsProbe
    $result.details.canary_task_probe = $canaryProbe
    $result.details.cleanup_task_probe = $cleanupProbe

    if ($dockerDesktopProbe.passes -and $startupProbe.passes -and $watchdogProbe.passes -and $promotionsProbe.passes -and $canaryProbe.passes -and $cleanupProbe.passes) {
        $result.details.actions_taken += "docker_desktop_task_already_registered:$DockerDesktopTaskName"
        $result.details.actions_taken += "resilience_tasks_already_registered"
        $result.checks.docker_desktop_task_registered = $true
        $result.checks.startup_task_registered = $true
        $result.checks.watchdog_task_registered = $true
        $result.checks.promotions_task_registered = $true
        $result.checks.canary_task_registered = $true
        $result.checks.cleanup_task_registered = $true
    } else {
        $registrationAccessDenied = $false
        try {
            $principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType S4U -RunLevel Highest
            $interactivePrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType InteractiveToken -RunLevel Highest

            $dockerDesktopNeedsRegistration = -not ($dockerDesktopProbe.passes -or $dockerDesktopProbe.degraded_pass)
            $startupNeedsRegistration = -not ($startupProbe.passes -or $startupProbe.degraded_pass)
            $watchdogNeedsRegistration = -not ($watchdogProbe.passes -or $watchdogProbe.degraded_pass)
            $promotionsNeedsRegistration = -not ($promotionsProbe.passes -or $promotionsProbe.degraded_pass)
            $canaryNeedsRegistration = -not ($canaryProbe.passes -or $canaryProbe.degraded_pass)
            $cleanupNeedsRegistration = -not ($cleanupProbe.passes -or $cleanupProbe.degraded_pass)

            if ($dockerDesktopNeedsRegistration) {
                $dockerDesktopAction = New-ScheduledTaskAction -Execute $dockerDesktopExecutable
                $dockerDesktopTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
                $dockerDesktopSettings = New-ScheduledTaskSettingsSet `
                    -StartWhenAvailable `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 12)
                $dockerDesktopTask = New-ScheduledTask `
                    -Action $dockerDesktopAction `
                    -Trigger $dockerDesktopTrigger `
                    -Principal $interactivePrincipal `
                    -Settings $dockerDesktopSettings `
                    -Description "Start Docker Desktop in the interactive user session for Zetherion recovery."
                Register-ScheduledTask -TaskName $DockerDesktopTaskName -InputObject $dockerDesktopTask -Force | Out-Null
                Enable-ScheduledTask -TaskName $DockerDesktopTaskName | Out-Null
                $result.details.actions_taken += "registered_docker_desktop_task:$DockerDesktopTaskName"
            } else {
                $result.details.actions_taken += "docker_desktop_task_already_registered:$DockerDesktopTaskName"
            }

            if ($startupNeedsRegistration) {
                $startupAction = New-ScheduledTaskAction `
                    -Execute $powerShellExecutable `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startupScriptPath`" -DeployPath `"$DeployPath`" -WslDistribution `"$WslDistribution`""
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
            } else {
                $result.details.actions_taken += "startup_task_already_registered:$StartupTaskName"
            }

            if ($watchdogNeedsRegistration) {
                $watchdogAction = New-ScheduledTaskAction `
                    -Execute $powerShellExecutable `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScriptPath`" -DeployPath `"$DeployPath`" -WslDistribution `"$WslDistribution`""
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
            } else {
                $result.details.actions_taken += "watchdog_task_already_registered:$WatchdogTaskName"
            }

            if ($promotionsNeedsRegistration) {
                $promotionsAction = New-ScheduledTaskAction `
                    -Execute $powerShellExecutable `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$promotionsWatchScriptPath`" -DeployPath `"$DeployPath`" -WslDistribution `"$WslDistribution`""
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
            } else {
                $result.details.actions_taken += "promotions_task_already_registered:$PromotionsTaskName"
            }

            if ($canaryNeedsRegistration) {
                $canaryAction = New-ScheduledTaskAction `
                    -Execute $powerShellExecutable `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$canaryScriptPath`" -DeployPath `"$DeployPath`" -WslDistribution `"$WslDistribution`""
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
            } else {
                $result.details.actions_taken += "canary_task_already_registered:$CanaryTaskName"
            }

            if ($cleanupNeedsRegistration) {
                $cleanupAction = New-ScheduledTaskAction `
                    -Execute $powerShellExecutable `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$cleanupScriptPath`" -CiRoot `"C:\ZetherionCI`" -WslDistribution `"$WslDistribution`""
                $cleanupStartupTrigger = New-ScheduledTaskTrigger -AtStartup
                $cleanupRecurringTrigger = New-ScheduledTaskTrigger `
                    -Once `
                    -At ((Get-Date).AddMinutes(4)) `
                    -RepetitionInterval (New-TimeSpan -Minutes $CleanupIntervalMinutes) `
                    -RepetitionDuration (New-TimeSpan -Days 3650)
                $cleanupSettings = New-ScheduledTaskSettingsSet `
                    -StartWhenAvailable `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
                $cleanupTask = New-ScheduledTask `
                    -Action $cleanupAction `
                    -Trigger @($cleanupStartupTrigger, $cleanupRecurringTrigger) `
                    -Principal $principal `
                    -Settings $cleanupSettings `
                    -Description "Prune stale CI artifacts and Docker caches on startup and periodic schedule."
                Register-ScheduledTask -TaskName $CleanupTaskName -InputObject $cleanupTask -Force | Out-Null
                $result.details.actions_taken += "registered_cleanup_task:$CleanupTaskName"
            } else {
                $result.details.actions_taken += "cleanup_task_already_registered:$CleanupTaskName"
            }
            $result.details.actions_taken += "registered_task_user:$taskUser"
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

        $dockerDesktopProbe = Get-RecoveryTaskRecord -TaskName $DockerDesktopTaskName -ScriptNeedle "Docker Desktop.exe"
        $startupProbe = Get-RecoveryTaskRecord -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
        $watchdogProbe = Get-RecoveryTaskRecord -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"
        $promotionsProbe = Get-RecoveryTaskRecord -TaskName $PromotionsTaskName -ScriptNeedle "promotions-watch.ps1"
        $canaryProbe = Get-RecoveryTaskRecord -TaskName $CanaryTaskName -ScriptNeedle "discord-canary-runner.ps1"
        $cleanupProbe = Get-RecoveryTaskRecord -TaskName $CleanupTaskName -ScriptNeedle "disk-cleanup.ps1"
        $result.details.docker_desktop_task_probe = $dockerDesktopProbe
        $result.details.startup_task_probe = $startupProbe
        $result.details.watchdog_task_probe = $watchdogProbe
        $result.details.promotions_task_probe = $promotionsProbe
        $result.details.canary_task_probe = $canaryProbe
        $result.details.cleanup_task_probe = $cleanupProbe

        $result.checks.docker_desktop_task_registered = [bool](
            $dockerDesktopProbe.passes -or $dockerDesktopProbe.degraded_pass
        )
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
        $result.checks.cleanup_task_registered = [bool](
            $cleanupProbe.passes -or $cleanupProbe.degraded_pass
        )

        if ($registrationAccessDenied) {
            $missingTasks = @()
            if (-not $dockerDesktopProbe.exists) { $missingTasks += $DockerDesktopTaskName }
            if (-not $startupProbe.exists) { $missingTasks += $StartupTaskName }
            if (-not $watchdogProbe.exists) { $missingTasks += $WatchdogTaskName }
            if (-not $promotionsProbe.exists) { $missingTasks += $PromotionsTaskName }
            if (-not $canaryProbe.exists) { $missingTasks += $CanaryTaskName }
            if (-not $cleanupProbe.exists) { $missingTasks += $CleanupTaskName }

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
        $result.checks.docker_desktop_task_registered -and
        $result.checks.recovery_tasks_registered -and
        $result.checks.promotions_task_registered -and
        $result.checks.canary_task_registered -and
        $result.checks.cleanup_task_registered
    ) {
        "success"
    } else {
        "failed"
    }

    if ($result.status -ne "success" -and -not $result.details.failure_code) {
        if (-not $result.checks.docker_desktop_task_registered) {
            $result.details.failure_code = "DOCKER_DESKTOP_TASK_REGISTRATION_FAILED"
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
