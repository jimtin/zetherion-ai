<#
.SYNOPSIS
    Export sanitized Windows live env manifests for CGS + Zetherion cutover.

.DESCRIPTION
    Reads the live env files from the Windows host, invokes the Python exporter,
    and writes name/presence/classification manifests only. Raw secret values are
    never written to the output bundle.
#>

param(
    [string]$DeployPath = "C:\ZetherionAI",
    [string]$CgsEnvPath = "",
    [string]$OutputDir = "",
    [string]$HostLabel = "",
    [switch]$Strict
)

$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $candidates = @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot "venv\Scripts\python.exe"),
        "py.exe",
        "python.exe",
        "python"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -like "*.exe" -or $candidate -eq "python") {
            try {
                $command = Get-Command $candidate -ErrorAction Stop
                return $command.Source
            }
            catch {
                continue
            }
        }
    }

    throw "Python executable not found. Install Python or activate the repo virtualenv first."
}

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptPath "..\..")).Path
$pythonScript = Join-Path $scriptPath "export-live-env-manifest.py"
$pythonExe = Resolve-PythonExecutable -RepoRoot $repoRoot
$zetherionEnvPath = Join-Path $DeployPath ".env"

if (-not $OutputDir) {
    $OutputDir = Join-Path $DeployPath "data\windows-live-env"
}

if (-not $HostLabel) {
    $HostLabel = $env:COMPUTERNAME
}

$arguments = @(
    $pythonScript,
    "--zetherion-env-file",
    $zetherionEnvPath,
    "--out-dir",
    $OutputDir,
    "--host-label",
    $HostLabel
)

if ($CgsEnvPath) {
    $arguments += @("--cgs-env-file", $CgsEnvPath)
}

if ($Strict) {
    $arguments += "--strict"
}

& $pythonExe @arguments
exit $LASTEXITCODE
