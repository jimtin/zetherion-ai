param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\discord-canary\last-run.json",
    [Parameter(Mandatory = $false)]
    [string]$StatePath = "C:\ZetherionAI\data\discord-canary\state.json",
    [Parameter(Mandatory = $false)]
    [string]$LogPath = "C:\ZetherionAI\data\discord-canary\last-run.log",
    [Parameter(Mandatory = $false)]
    [string]$ResultPath = "C:\ZetherionAI\data\discord-canary\discord-e2e-result.json",
    [Parameter(Mandatory = $false)]
    [string]$AnnouncementScript = "C:\ZetherionAI\scripts\windows\announcement-emit.py",
    [Parameter(Mandatory = $false)]
    [int]$TimeoutSeconds = 1200
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-BootstrapPythonExecutable {
    $candidates = @(
        "python.exe",
        "py.exe"
    )

    foreach ($candidate in $candidates) {
        try {
            $command = Get-Command $candidate -ErrorAction Stop
            return $command.Source
        }
        catch {
            continue
        }
    }

    throw "Python executable not found for Discord canary runner bootstrap."
}

function Test-RepoPythonReady {
    param(
        [string]$PythonExecutable,
        [string]$RepoPath
    )

    if (-not (Test-Path $PythonExecutable)) {
        return $false
    }

    $args = @(
        "-c",
        "import importlib.util, sys; required=('httpx','pytest','discord','zetherion_ai'); missing=[name for name in required if importlib.util.find_spec(name) is None]; sys.exit(0 if not missing else 1)"
    )
    & $PythonExecutable @args *> $null
    return $LASTEXITCODE -eq 0
}

function Install-RepoPythonDependencies {
    param(
        [string]$PythonExecutable,
        [string]$RepoPath
    )

    $requirementsPath = Join-Path $RepoPath "requirements-dev.txt"
    if (-not (Test-Path $requirementsPath)) {
        throw "requirements-dev.txt not found at $requirementsPath"
    }

    & $PythonExecutable -m pip install --disable-pip-version-check -r $requirementsPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Discord canary Python requirements into repo venv."
    }

    & $PythonExecutable -m pip install --disable-pip-version-check -e $RepoPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install editable repo package into Discord canary venv."
    }
}

function Ensure-RepoPythonExecutable {
    param([string]$RepoPath)

    $repoCandidates = @(
        (Join-Path $RepoPath ".venv\Scripts\python.exe"),
        (Join-Path $RepoPath "venv\Scripts\python.exe")
    )

    foreach ($candidate in $repoCandidates) {
        if (-not (Test-Path $candidate)) {
            continue
        }
        if (Test-RepoPythonReady -PythonExecutable $candidate -RepoPath $RepoPath) {
            return $candidate
        }
        Install-RepoPythonDependencies -PythonExecutable $candidate -RepoPath $RepoPath
        if (Test-RepoPythonReady -PythonExecutable $candidate -RepoPath $RepoPath) {
            return $candidate
        }
    }

    $bootstrapPython = Resolve-BootstrapPythonExecutable
    $venvRoot = Join-Path $RepoPath ".venv"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        & $bootstrapPython -m venv $venvRoot | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
            throw "Failed to create repo-local venv for Discord canary runner."
        }
    }

    Install-RepoPythonDependencies -PythonExecutable $venvPython -RepoPath $RepoPath
    if (-not (Test-RepoPythonReady -PythonExecutable $venvPython -RepoPath $RepoPath)) {
        throw "Repo-local Discord canary venv is not ready after dependency installation."
    }
    return $venvPython
}

function Resolve-RepoCaBundle {
    param([string]$PythonExecutable)

    if (-not (Test-Path $PythonExecutable)) {
        return $null
    }

    $args = @(
        "-c",
        "import os, ssl; from pathlib import Path; verify=ssl.get_default_verify_paths(); cafile=verify.cafile or ''; certifi_path=''; readable=lambda value: bool(value) and Path(value).is_file() and os.access(value, os.R_OK);
if readable(cafile): print(cafile)
else:
 import certifi; certifi_path=certifi.where(); print(certifi_path if readable(certifi_path) else '')"
    )
    $bundle = & $PythonExecutable @args
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $bundle = ($bundle | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($bundle)) {
        return $null
    }
    return $bundle
}

$pythonExe = Ensure-RepoPythonExecutable -RepoPath $DeployPath
$repoCaBundle = Resolve-RepoCaBundle -PythonExecutable $pythonExe
if ($repoCaBundle) {
    $env:SSL_CERT_FILE = $repoCaBundle
}
$scriptPath = Join-Path $DeployPath "scripts\windows\discord-canary.py"
if (-not (Test-Path $scriptPath)) {
    throw "Discord canary script not found: $scriptPath"
}

$args = @(
    $scriptPath,
    "--deploy-path", $DeployPath,
    "--output-path", $OutputPath,
    "--state-path", $StatePath,
    "--log-path", $LogPath,
    "--result-path", $ResultPath,
    "--announcement-script", $AnnouncementScript,
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $pythonExe @args
exit $LASTEXITCODE
