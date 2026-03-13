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
    [string]$TaskUser = "",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "resilience-ready.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
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

function Get-Actor {
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
    return ""
}

function Resolve-TaskUser {
    param([string]$RequestedUser)

    if ($RequestedUser) {
        if (-not (Test-WslCompatibleTaskPrincipal -UserId $RequestedUser)) {
            throw "TaskUser must be a non-service Windows user principal."
        }
        return $RequestedUser
    }

    $candidate = Get-Actor
    if (Test-WslCompatibleTaskPrincipal -UserId $candidate) {
        return $candidate
    }

    throw "TaskUser must resolve to a non-service Windows user principal."
}

function Task-ActionContains {
    param([object]$Task, [string]$Needle)
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

function Test-RecoveryTask {
    param([string]$TaskName, [string]$ScriptNeedle, [string]$ExpectedPrincipalUser)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $record = [ordered]@{
        exists = $false
        enabled = $false
        principal_user = ""
        system_principal = $false
        wsl_compatible_principal = $false
        action_matches = $false
        task_state = "missing"
        expected_principal_user = $ExpectedPrincipalUser
        source = "not_found"
        passes = $false
        degraded_pass = $false
    }

    if ($task) {
        $enabled = [bool]$task.Settings.Enabled
        $principalUser = [string]$task.Principal.UserId
        $systemPrincipal = Is-ServiceAccountPrincipal -UserId $principalUser
        $wslCompatiblePrincipal = Test-WslCompatibleTaskPrincipal -UserId $principalUser
        $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle

        $record = [ordered]@{
            exists = $true
            enabled = [bool]$enabled
            principal_user = $principalUser
            system_principal = [bool]$systemPrincipal
            wsl_compatible_principal = [bool]$wslCompatiblePrincipal
            action_matches = [bool]$actionMatches
            task_state = $task.State.ToString()
            expected_principal_user = $ExpectedPrincipalUser
            source = "scheduled_task_api"
            passes = ($enabled -and $wslCompatiblePrincipal -and $actionMatches -and ($principalUser -ieq $ExpectedPrincipalUser))
            degraded_pass = $false
        }
    }

    if ($record.passes -or $record.degraded_pass) {
        return $record
    }

    $query = @(& schtasks /Query /TN $TaskName /FO LIST 2>$null)
    if ($LASTEXITCODE -eq 0 -and $query.Count -gt 0) {
        $parsed = Parse-SchtasksListOutput -Lines $query
        $status = ""
        if ($parsed.ContainsKey("Status")) {
            $status = [string]$parsed["Status"]
        }
        $enabledFromStatus = -not ($status -match "Disabled")
        $stateLooksActive = [bool]($status -match "Ready|Running|Queued")

        return [ordered]@{
            exists = $true
            enabled = [bool]$enabledFromStatus
            principal_user = ""
            system_principal = [bool]$record.system_principal
            wsl_compatible_principal = [bool]$record.wsl_compatible_principal
            action_matches = [bool]$record.action_matches
            task_state = $status
            expected_principal_user = $ExpectedPrincipalUser
            source = "schtasks_query_fallback"
            passes = [bool]$record.passes
            degraded_pass = [bool]($record.degraded_pass -or ($enabledFromStatus -and $stateLooksActive))
        }
    }

    return $record
}

$taskUser = Resolve-TaskUser -RequestedUser $TaskUser

$checks = [ordered]@{
    recovery_tasks_registered = $false
    promotions_task_registered = $false
    canary_task_registered = $false
    runner_service_persistent = $false
    docker_service_persistent = $false
}

$details = [ordered]@{
    deploy_path = $DeployPath
    wsl_distribution = $WslDistribution
    startup_task_name = $StartupTaskName
    watchdog_task_name = $WatchdogTaskName
    promotions_task_name = $PromotionsTaskName
    canary_task_name = $CanaryTaskName
    task_user = $taskUser
    startup_task = $null
    watchdog_task = $null
    promotions_task = $null
    canary_task = $null
    runner_services = @()
    docker_service = $null
    network_service_in_docker_users = $false
}

try {
    $details.startup_task = Test-RecoveryTask -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1" -ExpectedPrincipalUser $taskUser
    $details.watchdog_task = Test-RecoveryTask -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1" -ExpectedPrincipalUser $taskUser
    $details.promotions_task = Test-RecoveryTask -TaskName $PromotionsTaskName -ScriptNeedle "promotions-watch.ps1" -ExpectedPrincipalUser $taskUser
    $details.canary_task = Test-RecoveryTask -TaskName $CanaryTaskName -ScriptNeedle "discord-canary-runner.ps1" -ExpectedPrincipalUser $taskUser

    $checks.recovery_tasks_registered = [bool](
        ($details.startup_task.passes -or $details.startup_task.degraded_pass) -and
        ($details.watchdog_task.passes -or $details.watchdog_task.degraded_pass)
    )
    $checks.promotions_task_registered = [bool](
        ($details.promotions_task.passes -or $details.promotions_task.degraded_pass)
    )
    $checks.canary_task_registered = [bool](
        ($details.canary_task.passes -or $details.canary_task.degraded_pass)
    )

    $runnerServices = @(
        Get-Service -Name "actions.runner*" -ErrorAction SilentlyContinue
    )
    $details.runner_services = @(
        $runnerServices | ForEach-Object {
            [ordered]@{
                name = $_.Name
                status = $_.Status.ToString()
                start_type = $_.StartType.ToString()
            }
        }
    )

    if ($runnerServices.Count -gt 0) {
        $allRunnerServicesPersistent = $true
        foreach ($svc in $runnerServices) {
            if ($svc.StartType.ToString() -ne "Automatic" -or $svc.Status.ToString() -ne "Running") {
                $allRunnerServicesPersistent = $false
                break
            }
        }
        $checks.runner_service_persistent = [bool]$allRunnerServicesPersistent
    }
    else {
        $checks.runner_service_persistent = $false
    }

    $dockerRuntime = Get-ZetherionDockerRuntimeStatus
    if ($dockerRuntime) {
        $details.docker_service = [ordered]@{
            name = "wsl:docker.service"
            status = if ($dockerRuntime.active) { "Running" } else { "Stopped" }
            start_type = if ($dockerRuntime.enabled) { "Automatic" } else { "Disabled" }
            backend = [string]$dockerRuntime.backend
            distribution = [string]$dockerRuntime.distribution
            reachable = [bool]$dockerRuntime.available
        }
    }
    else {
        $details.docker_service = [ordered]@{
            name = "wsl:docker.service"
            status = "missing"
            start_type = "missing"
        }
    }

    $dockerUsers = @()
    try {
        $dockerUsers = @(
            Get-LocalGroupMember -Group "docker-users" -ErrorAction Stop | Select-Object -ExpandProperty Name
        )
    }
    catch {
        $dockerUsers = @()
    }

    $details.network_service_in_docker_users = [bool]($dockerUsers -contains "NT AUTHORITY\NETWORK SERVICE")
    if ($dockerRuntime.backend -eq "wsl") {
        $details.network_service_in_docker_users = $true
    }

    $checks.docker_service_persistent = [bool](
        $dockerRuntime `
        -and [bool]$dockerRuntime.enabled `
        -and [bool]$dockerRuntime.active `
        -and [bool]$dockerRuntime.available
    )

    $allowServiceFallback = $false
    $fallbackRaw = [string]($env:WINDOWS_RESILIENCE_ALLOW_SERVICE_FALLBACK)
    if ($fallbackRaw) {
        $normalized = $fallbackRaw.Trim().ToLowerInvariant()
        $allowServiceFallback = @("1", "true", "yes", "on") -contains $normalized
    }

    if (
        -not $checks.recovery_tasks_registered `
        -and $allowServiceFallback `
        -and -not $details.startup_task.exists `
        -and -not $details.watchdog_task.exists `
        -and $checks.runner_service_persistent `
        -and $checks.docker_service_persistent
    ) {
        $checks.recovery_tasks_registered = $true
        $details.recovery_tasks_fallback = "service_persistence"
    }
}
catch {
    $details.error = $_.Exception.Message
}

$payload = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = $checks
    details = $details
}

Ensure-ParentDir -Path $OutputPath
$payload | ConvertTo-Json -Depth 10 | Out-File $OutputPath -Encoding utf8

if ($checks.recovery_tasks_registered -and $checks.runner_service_persistent -and $checks.docker_service_persistent) {
    exit 0
}

exit 1
