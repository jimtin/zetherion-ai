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
    [string]$OutputPath = "resilience-verify.json"
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

function Get-TriggerFacts {
    param([object]$Task)

    $triggerTypes = @()
    $startup = $false
    $repetition = $false
    $triggers = @($Task.Triggers)

    foreach ($trigger in $triggers) {
        if (-not $trigger) {
            continue
        }

        $typeValue = ""
        try {
            $typeValue = [string]$trigger.TriggerType
        } catch {
            $typeValue = ""
        }

        if (-not $typeValue) {
            try {
                $typeValue = [string]$trigger.CimClass.CimClassName
            } catch {
                $typeValue = ""
            }
        }

        if ($typeValue) {
            $triggerTypes += $typeValue
        }

        if ($typeValue -match "Boot|Startup") {
            $startup = $true
        }

        $interval = ""
        try {
            if ($trigger.Repetition -and $trigger.Repetition.Interval) {
                $interval = [string]$trigger.Repetition.Interval
            } elseif ($trigger.RepetitionInterval) {
                $interval = [string]$trigger.RepetitionInterval
            }
        } catch {
            $interval = ""
        }

        if ($interval -and $interval -ne "PT0S") {
            $repetition = $true
        }
    }

    return [ordered]@{
        trigger_startup = [bool]$startup
        trigger_repetition = [bool]$repetition
        trigger_types = @($triggerTypes)
        trigger_count = $triggers.Count
    }
}

function Get-TaskVerification {
    param(
        [string]$TaskName,
        [string]$ScriptNeedle,
        [bool]$RequireStartupTrigger,
        [bool]$RequireRepetitionTrigger
    )

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        return [ordered]@{
            task_name = $TaskName
            exists = $false
            enabled = $false
            principal_user = ""
            system_principal = $false
            action_matches = $false
            trigger_startup = $false
            trigger_repetition = $false
            trigger_types = @()
            trigger_count = 0
            required_startup_trigger = [bool]$RequireStartupTrigger
            required_repetition_trigger = [bool]$RequireRepetitionTrigger
            source = "missing"
            failures = @("task_missing")
            passes = $false
        }
    }

    $triggerFacts = Get-TriggerFacts -Task $task
    $enabled = [bool]$task.Settings.Enabled
    $principalUser = [string]$task.Principal.UserId
    $systemPrincipal = Is-SystemPrincipal -UserId $principalUser
    $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle

    $failures = @()
    if (-not $enabled) {
        $failures += "task_disabled"
    }
    if (-not $systemPrincipal) {
        $failures += "principal_not_system"
    }
    if (-not $actionMatches) {
        $failures += "action_mismatch"
    }
    if ($RequireStartupTrigger -and -not $triggerFacts.trigger_startup) {
        $failures += "missing_startup_trigger"
    }
    if ($RequireRepetitionTrigger -and -not $triggerFacts.trigger_repetition) {
        $failures += "missing_repetition_trigger"
    }

    return [ordered]@{
        task_name = $TaskName
        exists = $true
        enabled = [bool]$enabled
        principal_user = $principalUser
        system_principal = [bool]$systemPrincipal
        action_matches = [bool]$actionMatches
        trigger_startup = [bool]$triggerFacts.trigger_startup
        trigger_repetition = [bool]$triggerFacts.trigger_repetition
        trigger_types = @($triggerFacts.trigger_types)
        trigger_count = [int]$triggerFacts.trigger_count
        required_startup_trigger = [bool]$RequireStartupTrigger
        required_repetition_trigger = [bool]$RequireRepetitionTrigger
        source = "scheduled_task_api"
        failures = @($failures)
        passes = [bool]($failures.Count -eq 0)
    }
}

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        startup_task_registered = $false
        watchdog_task_registered = $false
        promotions_task_registered = $false
        all_tasks_registered = $false
    }
    details = [ordered]@{
        deploy_path = $DeployPath
        expected = [ordered]@{
            startup = [ordered]@{
                task_name = $StartupTaskName
                script = "startup-recover.ps1"
                principal = "SYSTEM"
                requires_startup_trigger = $true
                requires_repetition_trigger = $false
            }
            watchdog = [ordered]@{
                task_name = $WatchdogTaskName
                script = "runtime-watchdog.ps1"
                principal = "SYSTEM"
                requires_startup_trigger = $false
                requires_repetition_trigger = $true
            }
            promotions = [ordered]@{
                task_name = $PromotionsTaskName
                script = "promotions-watch.ps1"
                principal = "SYSTEM"
                requires_startup_trigger = $true
                requires_repetition_trigger = $true
            }
        }
        startup_task = $null
        watchdog_task = $null
        promotions_task = $null
    }
    status = "failed"
    error = ""
}

try {
    $startup = Get-TaskVerification `
        -TaskName $StartupTaskName `
        -ScriptNeedle "startup-recover.ps1" `
        -RequireStartupTrigger $true `
        -RequireRepetitionTrigger $false

    $watchdog = Get-TaskVerification `
        -TaskName $WatchdogTaskName `
        -ScriptNeedle "runtime-watchdog.ps1" `
        -RequireStartupTrigger $false `
        -RequireRepetitionTrigger $true

    $promotions = Get-TaskVerification `
        -TaskName $PromotionsTaskName `
        -ScriptNeedle "promotions-watch.ps1" `
        -RequireStartupTrigger $true `
        -RequireRepetitionTrigger $true

    $result.details.startup_task = $startup
    $result.details.watchdog_task = $watchdog
    $result.details.promotions_task = $promotions

    $result.checks.startup_task_registered = [bool]$startup.passes
    $result.checks.watchdog_task_registered = [bool]$watchdog.passes
    $result.checks.promotions_task_registered = [bool]$promotions.passes
    $result.checks.all_tasks_registered = [bool](
        $result.checks.startup_task_registered -and
        $result.checks.watchdog_task_registered -and
        $result.checks.promotions_task_registered
    )

    $result.status = if ($result.checks.all_tasks_registered) { "success" } else { "failed" }
}
catch {
    $result.error = $_.Exception.Message
    $result.status = "failed"
}

Ensure-ParentDir -Path $OutputPath
$result | ConvertTo-Json -Depth 10 | Out-File $OutputPath -Encoding utf8

if ($result.status -eq "success") {
    exit 0
}

exit 1
