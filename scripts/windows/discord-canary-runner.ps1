param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
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

function Resolve-PythonExecutable {
    param([string]$RepoPath)

    $candidates = @(
        (Join-Path $RepoPath ".venv\Scripts\python.exe"),
        (Join-Path $RepoPath "venv\Scripts\python.exe"),
        "python.exe",
        "py.exe"
    )

    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        try {
            $command = Get-Command $candidate -ErrorAction Stop
            return $command.Source
        }
        catch {
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }

    throw "Python executable not found for Discord canary runner."
}

$pythonExe = Resolve-PythonExecutable -RepoPath $DeployPath
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
