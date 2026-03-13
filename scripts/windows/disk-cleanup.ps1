param(
    [Parameter(Mandatory = $false)]
    [string]$CiRoot = "C:\ZetherionCI",
    [Parameter(Mandatory = $false)]
    [string]$WslDistribution = "Ubuntu",
    [Parameter(Mandatory = $false)]
    [int64]$LowDiskFreeBytes = 21474836480,
    [Parameter(Mandatory = $false)]
    [int64]$TargetFreeBytes = 42949672960,
    [Parameter(Mandatory = $false)]
    [int]$ArtifactRetentionHours = 24,
    [Parameter(Mandatory = $false)]
    [int]$LogRetentionDays = 7,
    [Parameter(Mandatory = $false)]
    [switch]$Aggressive,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionCI\artifacts\disk-cleanup-receipt.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:ZETHERION_WSL_DISTRIBUTION = $WslDistribution
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Write-CleanupResult {
    param(
        [object]$Result,
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $Result | ConvertTo-Json -Depth 10 | Out-File $Path -Encoding utf8
}

$cleanupResult = Invoke-ZetherionDiskCleanup `
    -CiRoot $CiRoot `
    -LowDiskFreeBytes $LowDiskFreeBytes `
    -TargetFreeBytes $TargetFreeBytes `
    -ArtifactRetentionHours $ArtifactRetentionHours `
    -LogRetentionDays $LogRetentionDays `
    -Aggressive:$Aggressive

Write-CleanupResult -Result $cleanupResult -Path $OutputPath

if ($cleanupResult.status -eq "cleaned") {
    exit 0
}

exit 1
