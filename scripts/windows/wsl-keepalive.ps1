param(
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

try {
    Invoke-ZetherionWslCommand -Command "systemctl start docker >/dev/null 2>&1 || true" | Out-Null
}
catch {
    Write-Warning "Unable to proactively start docker inside WSL: $($_.Exception.Message)"
}

$keepaliveCommand = "trap 'exit 0' TERM INT; while true; do sleep 3600; done"
& wsl.exe -d $WslDistribution -- bash -lc $keepaliveCommand
exit $LASTEXITCODE
