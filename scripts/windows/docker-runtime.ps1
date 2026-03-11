Set-StrictMode -Version Latest

$script:ZetherionWslDistribution = if ($env:ZETHERION_WSL_DISTRIBUTION) {
    [string]$env:ZETHERION_WSL_DISTRIBUTION
} else {
    "Ubuntu"
}

function Get-ZetherionWslDistribution {
    return $script:ZetherionWslDistribution
}

function ConvertTo-ZetherionWslPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WindowsPath
    )

    if ($WindowsPath -match "^[A-Za-z]:\\") {
        $drive = $WindowsPath.Substring(0, 1).ToLowerInvariant()
        $rest = $WindowsPath.Substring(2).Replace("\", "/")
        return "/mnt/$drive$rest"
    }

    return $WindowsPath.Replace("\", "/")
}

function ConvertTo-ZetherionBashLiteral {
    param(
        [AllowNull()]
        [string]$Value
    )

    if ($null -eq $Value) {
        return "''"
    }

    return "'" + $Value.Replace("'", "'""'""'") + "'"
}

function Invoke-ZetherionWslCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $distro = Get-ZetherionWslDistribution
    & wsl.exe -d $distro -- bash -lc $Command
}

function Get-ZetherionDockerRuntimeStatus {
    $distro = Get-ZetherionWslDistribution

    $enabledOutput = (& wsl.exe -d $distro -- bash -lc "systemctl is-enabled docker 2>/dev/null || true" 2>&1 | Out-String).Trim()
    $enabled = ($LASTEXITCODE -eq 0 -or $enabledOutput) -and ($enabledOutput -eq "enabled")

    $activeOutput = (& wsl.exe -d $distro -- bash -lc "systemctl is-active docker 2>/dev/null || true" 2>&1 | Out-String).Trim()
    $active = ($LASTEXITCODE -eq 0 -or $activeOutput) -and ($activeOutput -eq "active")

    & wsl.exe -d $distro -- bash -lc "docker info >/dev/null 2>&1"
    $available = ($LASTEXITCODE -eq 0)

    return [pscustomobject]@{
        backend = "wsl"
        distribution = $distro
        enabled = [bool]$enabled
        active = [bool]$active
        available = [bool]$available
    }
}

function Test-ZetherionDockerAvailable {
    $status = Get-ZetherionDockerRuntimeStatus
    return [bool]($status.enabled -and $status.active -and $status.available)
}

function Invoke-ZetherionWslDocker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$DockerArguments
    )

    $currentPath = (Get-Location).Path
    $wslPath = ConvertTo-ZetherionWslPath -WindowsPath $currentPath

    $escapedDockerArgs = New-Object 'System.Collections.Generic.List[string]'
    $escapedDockerArgs.Add("docker")
    foreach ($argument in $DockerArguments) {
        $escapedDockerArgs.Add((ConvertTo-ZetherionBashLiteral -Value ([string]$argument)))
    }

    $command = "cd $(ConvertTo-ZetherionBashLiteral -Value $wslPath) && $($escapedDockerArgs -join ' ')"
    Invoke-ZetherionWslCommand -Command $command
}

function docker {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$RemainingArgs
    )

    Invoke-ZetherionWslDocker @RemainingArgs
}
