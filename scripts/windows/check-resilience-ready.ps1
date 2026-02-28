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
    param([string]$TaskName, [string]$ScriptNeedle)

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $record = [ordered]@{
        exists = $false
        enabled = $false
        system_principal = $false
        action_matches = $false
        task_state = "missing"
        source = "not_found"
        passes = $false
        degraded_pass = $false
    }

    if ($task) {
        $enabled = [bool]$task.Settings.Enabled
        $systemPrincipal = Is-SystemPrincipal -UserId $task.Principal.UserId
        $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle

        $record = [ordered]@{
            exists = $true
            enabled = [bool]$enabled
            system_principal = [bool]$systemPrincipal
            action_matches = [bool]$actionMatches
            task_state = $task.State.ToString()
            source = "scheduled_task_api"
            passes = ($enabled -and $systemPrincipal -and $actionMatches)
            degraded_pass = ($enabled -and $actionMatches)
        }
    }

    if ($record.passes -or $record.degraded_pass) {
        return $record
    }

    $query = @(& schtasks /Query /TN $TaskName /FO LIST 2>$null)
    if ($LASTEXITCODE -eq 0 -and $query.Count -gt 0) {
        $parsed = Parse-SchtasksListOutput -Lines $query
        $status = [string]($parsed["Status"] ?? "")
        $enabledFromStatus = -not ($status -match "Disabled")
        $stateLooksActive = [bool]($status -match "Ready|Running|Queued")

        return [ordered]@{
            exists = $true
            enabled = [bool]$enabledFromStatus
            system_principal = [bool]$record.system_principal
            action_matches = [bool]$record.action_matches
            task_state = $status
            source = "schtasks_query_fallback"
            passes = [bool]($record.passes -or ($enabledFromStatus -and $stateLooksActive))
            degraded_pass = [bool]($record.degraded_pass -or ($enabledFromStatus -and $stateLooksActive))
        }
    }

    return $record
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
        ($details.startup_task.passes -or $details.startup_task.degraded_pass) -and
        ($details.watchdog_task.passes -or $details.watchdog_task.degraded_pass)
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
