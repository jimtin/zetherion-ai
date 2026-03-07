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
    [int]$WatchdogIntervalMinutes = 5,
    [Parameter(Mandatory = $false)]
    [int]$PromotionsIntervalMinutes = 10,
    [Parameter(Mandatory = $false)]
    [int]$CanaryIntervalMinutes = 360,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "resilience-bootstrap.json"
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

function Get-Actor {
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        if ($identity -and $identity.Name) {
            return [string]$identity.Name
        }
    } catch {
        # Ignore and use environment fallback.
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

$actor = Get-Actor
$isElevated = Test-IsElevated
$tempDir = Join-Path $PWD "resilience-bootstrap-artifacts"
$registrationPath = Join-Path $tempDir "registration.json"
$verificationPath = Join-Path $tempDir "verification.json"

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    checks = [ordered]@{
        is_elevated = [bool]$isElevated
        registration_success = $false
        verification_success = $false
    }
    details = [ordered]@{
        actor = $actor
        deploy_path = $DeployPath
        startup_task = $StartupTaskName
        watchdog_task = $WatchdogTaskName
        promotions_task = $PromotionsTaskName
        canary_task = $CanaryTaskName
        registration_output_path = $registrationPath
        verification_output_path = $verificationPath
        registration_exit_code = $null
        verification_exit_code = $null
        registration_result = $null
        verification_result = $null
        failure_code = ""
    }
    status = "failed"
    error = ""
}

try {
    Ensure-ParentDir -Path $registrationPath
    Ensure-ParentDir -Path $verificationPath

    if (-not $isElevated) {
        $result.details.failure_code = "ELEVATION_REQUIRED"
        $result.error = "bootstrap-resilience-tasks.ps1 must run elevated (Administrator)."
        throw $result.error
    }

    & (Join-Path $PSScriptRoot "register-resilience-tasks.ps1") `
        -DeployPath $DeployPath `
        -StartupTaskName $StartupTaskName `
        -WatchdogTaskName $WatchdogTaskName `
        -PromotionsTaskName $PromotionsTaskName `
        -CanaryTaskName $CanaryTaskName `
        -WatchdogIntervalMinutes $WatchdogIntervalMinutes `
        -PromotionsIntervalMinutes $PromotionsIntervalMinutes `
        -CanaryIntervalMinutes $CanaryIntervalMinutes `
        -OutputPath $registrationPath
    $registrationExitCode = $LASTEXITCODE
    $result.details.registration_exit_code = $registrationExitCode
    $result.checks.registration_success = ($registrationExitCode -eq 0)

    if (Test-Path $registrationPath) {
        $result.details.registration_result = Get-Content $registrationPath -Raw | ConvertFrom-Json
    }

    & (Join-Path $PSScriptRoot "verify-resilience-tasks.ps1") `
        -DeployPath $DeployPath `
        -StartupTaskName $StartupTaskName `
        -WatchdogTaskName $WatchdogTaskName `
        -PromotionsTaskName $PromotionsTaskName `
        -CanaryTaskName $CanaryTaskName `
        -OutputPath $verificationPath
    $verificationExitCode = $LASTEXITCODE
    $result.details.verification_exit_code = $verificationExitCode
    $result.checks.verification_success = ($verificationExitCode -eq 0)

    if (Test-Path $verificationPath) {
        $result.details.verification_result = Get-Content $verificationPath -Raw | ConvertFrom-Json
    }

    if ($result.checks.verification_success) {
        $result.status = "success"
    } else {
        $result.status = "failed"
        if (-not $result.details.failure_code) {
            $result.details.failure_code = "VERIFICATION_FAILED"
        }
    }
}
catch {
    if (-not $result.error) {
        $result.error = $_.Exception.Message
    }
    if (-not $result.details.failure_code) {
        $result.details.failure_code = "BOOTSTRAP_FAILED"
    }
    $result.status = "failed"
}

Ensure-ParentDir -Path $OutputPath
$result | ConvertTo-Json -Depth 10 | Out-File $OutputPath -Encoding utf8

if ($result.status -eq "success") {
    exit 0
}

exit 1
