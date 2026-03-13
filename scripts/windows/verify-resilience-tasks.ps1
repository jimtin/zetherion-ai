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
        }
        catch {
            $typeValue = ""
        }

        if (-not $typeValue) {
            try {
                $typeValue = [string]$trigger.CimClass.CimClassName
            }
            catch {
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
            }
            elseif ($trigger.RepetitionInterval) {
                $interval = [string]$trigger.RepetitionInterval
            }
        }
        catch {
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
        [string]$ExpectedPrincipalUser,
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
            wsl_compatible_principal = $false
            action_matches = $false
            trigger_startup = $false
            trigger_repetition = $false
            trigger_types = @()
            trigger_count = 0
            expected_principal_user = $ExpectedPrincipalUser
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
    $systemPrincipal = Is-ServiceAccountPrincipal -UserId $principalUser
    $wslCompatiblePrincipal = Test-WslCompatibleTaskPrincipal -UserId $principalUser
    $actionMatches = Task-ActionContains -Task $task -Needle $ScriptNeedle

    $failures = @()
    if (-not $enabled) {
        $failures += "task_disabled"
    }
    if (-not $wslCompatiblePrincipal) {
        $failures += "principal_not_wsl_compatible"
    }
    if ($ExpectedPrincipalUser -and $principalUser -ine $ExpectedPrincipalUser) {
        $failures += "principal_mismatch"
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
        wsl_compatible_principal = [bool]$wslCompatiblePrincipal
        action_matches = [bool]$actionMatches
        trigger_startup = [bool]$triggerFacts.trigger_startup
        trigger_repetition = [bool]$triggerFacts.trigger_repetition
        trigger_types = @($triggerFacts.trigger_types)
        trigger_count = [int]$triggerFacts.trigger_count
        expected_principal_user = $ExpectedPrincipalUser
        required_startup_trigger = [bool]$RequireStartupTrigger
        required_repetition_trigger = [bool]$RequireRepetitionTrigger
        source = "scheduled_task_api"
        failures = @($failures)
        passes = [bool]($failures.Count -eq 0)
    }
}

$taskUser = Resolve-TaskUser -RequestedUser $TaskUser

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        startup_task_registered = $false
        watchdog_task_registered = $false
        promotions_task_registered = $false
        canary_task_registered = $false
        all_tasks_registered = $false
    }
    details = [ordered]@{
        deploy_path = $DeployPath
        wsl_distribution = $WslDistribution
        expected = [ordered]@{
            startup = [ordered]@{
                task_name = $StartupTaskName
                script = "startup-recover.ps1"
                principal = $taskUser
                requires_startup_trigger = $true
                requires_repetition_trigger = $false
            }
            watchdog = [ordered]@{
                task_name = $WatchdogTaskName
                script = "runtime-watchdog.ps1"
                principal = $taskUser
                requires_startup_trigger = $false
                requires_repetition_trigger = $true
            }
            promotions = [ordered]@{
                task_name = $PromotionsTaskName
                script = "promotions-watch.ps1"
                principal = $taskUser
                requires_startup_trigger = $true
                requires_repetition_trigger = $true
            }
            canary = [ordered]@{
                task_name = $CanaryTaskName
                script = "discord-canary-runner.ps1"
                principal = $taskUser
                requires_startup_trigger = $true
                requires_repetition_trigger = $true
            }
        }
        task_user = $taskUser
        startup_task = $null
        watchdog_task = $null
        promotions_task = $null
        canary_task = $null
    }
    status = "failed"
    error = ""
}

try {
    $startup = Get-TaskVerification `
        -TaskName $StartupTaskName `
        -ScriptNeedle "startup-recover.ps1" `
        -ExpectedPrincipalUser $taskUser `
        -RequireStartupTrigger $true `
        -RequireRepetitionTrigger $false

    $watchdog = Get-TaskVerification `
        -TaskName $WatchdogTaskName `
        -ScriptNeedle "runtime-watchdog.ps1" `
        -ExpectedPrincipalUser $taskUser `
        -RequireStartupTrigger $false `
        -RequireRepetitionTrigger $true

    $promotions = Get-TaskVerification `
        -TaskName $PromotionsTaskName `
        -ScriptNeedle "promotions-watch.ps1" `
        -ExpectedPrincipalUser $taskUser `
        -RequireStartupTrigger $true `
        -RequireRepetitionTrigger $true

    $canary = Get-TaskVerification `
        -TaskName $CanaryTaskName `
        -ScriptNeedle "discord-canary-runner.ps1" `
        -ExpectedPrincipalUser $taskUser `
        -RequireStartupTrigger $true `
        -RequireRepetitionTrigger $true

    $result.details.startup_task = $startup
    $result.details.watchdog_task = $watchdog
    $result.details.promotions_task = $promotions
    $result.details.canary_task = $canary

    $result.checks.startup_task_registered = [bool]$startup.passes
    $result.checks.watchdog_task_registered = [bool]$watchdog.passes
    $result.checks.promotions_task_registered = [bool]$promotions.passes
    $result.checks.canary_task_registered = [bool]$canary.passes
    $result.checks.all_tasks_registered = [bool](
        $result.checks.startup_task_registered -and
        $result.checks.watchdog_task_registered -and
        $result.checks.promotions_task_registered -and
        $result.checks.canary_task_registered
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
