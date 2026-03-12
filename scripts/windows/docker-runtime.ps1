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
        [string]$Command,
        [string]$User = ""
    )

    $distro = Get-ZetherionWslDistribution
    $wslArgs = New-Object 'System.Collections.Generic.List[string]'
    $wslArgs.Add("-d")
    $wslArgs.Add($distro)
    if ($User) {
        $wslArgs.Add("-u")
        $wslArgs.Add($User)
    }
    $wslArgs.Add("--")
    $wslArgs.Add("bash")
    $wslArgs.Add("-lc")
    $wslArgs.Add($Command)

    $output = & wsl.exe @wslArgs 2>&1
    $exitCode = $LASTEXITCODE
    $global:LASTEXITCODE = $exitCode

    foreach ($entry in @($output)) {
        Write-Output $entry
    }

    if ($exitCode -ne 0) {
        $message = ($output | Out-String).Trim()
        if (-not $message) {
            $message = "WSL command failed with exit code $exitCode."
        }
        throw $message
    }
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

function Ensure-ZetherionWslRuntimePaths {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployPath,
        [string[]]$RelativePaths = @("data", "logs")
    )

    $repoWslPath = ConvertTo-ZetherionWslPath -WindowsPath $DeployPath
    $driveMatch = [regex]::Match($repoWslPath, "^(/mnt/[a-z])(?:/|$)")
    if (-not $driveMatch.Success) {
        throw "Deploy path '$DeployPath' is not on a supported WSL drvfs mount."
    }

    $driveRoot = $driveMatch.Groups[1].Value
    $escapedRelativePaths = New-Object 'System.Collections.Generic.List[string]'
    foreach ($relativePath in $RelativePaths) {
        $escapedRelativePaths.Add((ConvertTo-ZetherionBashLiteral -Value $relativePath))
    }

    $relativePathsLiteral = $escapedRelativePaths -join " "
    $command = @'
set -euo pipefail
repo_path=__REPO_PATH__
drive_root=__DRIVE_ROOT__
mount_line=\$(mount | grep " on \$drive_root " | head -n 1 || true)
if [ -z "\$mount_line" ]; then
  echo "Unable to resolve WSL mount metadata for \$drive_root." >&2
  exit 1
fi
case "\$mount_line" in
  *metadata*) ;;
  *)
    echo "WSL automount for \$drive_root must include the metadata option to support writable runtime bind mounts." >&2
    exit 1
    ;;
esac
owner_user=\$(stat -c '%U' "\$repo_path")
owner_group=\$(stat -c '%G' "\$repo_path")
if [ -z "\$owner_user" ] || [ "\$owner_user" = "UNKNOWN" ]; then
  echo "Unable to resolve a writable WSL owner for \$repo_path." >&2
  exit 1
fi
for rel in __RELATIVE_PATHS__; do
  target="\$repo_path/\$rel"
  mkdir -p "\$target"
  chown -R "\$owner_user:\$owner_group" "\$target"
  chmod -R u+rwX,g+rwX "\$target"
  runuser -u "\$owner_user" -- touch "\$target/.wsl-write-check"
  rm -f "\$target/.wsl-write-check"
done
'@
    $command = $command.Replace("__REPO_PATH__", (ConvertTo-ZetherionBashLiteral -Value $repoWslPath))
    $command = $command.Replace("__DRIVE_ROOT__", (ConvertTo-ZetherionBashLiteral -Value $driveRoot))
    $command = $command.Replace("__RELATIVE_PATHS__", $relativePathsLiteral)

    try {
        Invoke-ZetherionWslCommand -User "root" -Command $command | Out-Null
    } catch {
        $reason = $_.Exception.Message
        if ($reason) {
            throw "Failed to prepare WSL runtime paths for Docker bind mounts under '$DeployPath': $reason"
        }
        throw "Failed to prepare WSL runtime paths for Docker bind mounts under '$DeployPath'."
    }
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
