param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$StartupTaskName = "ZetherionStartupRecover",
    [Parameter(Mandatory = $false)]
    [string]$WatchdogTaskName = "ZetherionRuntimeWatchdog",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "resilience-ready.json"
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

function Is-SystemPrincipal {
    param([string]$UserId)
    if (-not $UserId) {
        return $false
    }
    return $UserId -eq "SYSTEM" -or $UserId -eq "NT AUTHORITY\SYSTEM"
}

function Task-ActionContains {
    param([object]$Task, [string]$Needle)
    foreach ($action in @($Task.Actions)) {
        if ($action.Arguments -and $action.Arguments -like "*$Needle*") {
            return $true
        }
    }
    return $false
}

function Test-RecoveryTask {
    param([string]$TaskName, [string]$ScriptNeedle)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return [ordered]@{
            exists = $false
            enabled = $false
            system_principal = $false
            action_matches = $false
            passes = $false
        }
    }

    $enabled = [bool]$task.Settings.Enabled
    $systemPrincipal = Is-SystemPrincipal -UserId $task.Principal.UserId
    $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle

    return [ordered]@{
        exists = $true
        enabled = $enabled
        system_principal = $systemPrincipal
        action_matches = $actionMatches
        passes = ($enabled -and $systemPrincipal -and $actionMatches)
    }
}

$checks = [ordered]@{
    recovery_tasks_registered = $false
    runner_service_persistent = $false
    docker_service_persistent = $false
}

$details = [ordered]@{
    deploy_path = $DeployPath
    startup_task_name = $StartupTaskName
    watchdog_task_name = $WatchdogTaskName
    startup_task = $null
    watchdog_task = $null
    runner_services = @()
    docker_service = $null
    network_service_in_docker_users = $false
}

try {
    $details.startup_task = Test-RecoveryTask -TaskName $StartupTaskName -ScriptNeedle "startup-recover.ps1"
    $details.watchdog_task = Test-RecoveryTask -TaskName $WatchdogTaskName -ScriptNeedle "runtime-watchdog.ps1"

    $checks.recovery_tasks_registered = [bool](
        $details.startup_task.passes -and $details.watchdog_task.passes
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
    } else {
        $checks.runner_service_persistent = $false
    }

    $dockerService = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
    if ($dockerService) {
        $details.docker_service = [ordered]@{
            name = $dockerService.Name
            status = $dockerService.Status.ToString()
            start_type = $dockerService.StartType.ToString()
        }
    } else {
        $details.docker_service = [ordered]@{
            name = "com.docker.service"
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

    $checks.docker_service_persistent = [bool](
        $dockerService `
        -and $dockerService.StartType.ToString() -ne "Disabled" `
        -and $dockerService.Status.ToString() -eq "Running" `
        -and $details.network_service_in_docker_users
    )
}
catch {
    # Keep defaults false and include error string in payload.
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
