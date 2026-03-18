param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$CandidatePath = "C:\ZetherionAI-cutover",
    [Parameter(Mandatory = $false)]
    [string]$RetiredLivePath = "",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionCI\artifacts\windows-cutover-promotion-receipt.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

function Ensure-ParentDir {
    param([string]$Path)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

function Get-RepositoryHeadSha {
    param([string]$RepositoryPath)

    if (-not (Test-Path -LiteralPath $RepositoryPath)) {
        return ""
    }

    Push-Location $RepositoryPath
    try {
        return ((git rev-parse HEAD 2>$null) | Out-String).Trim()
    }
    finally {
        Pop-Location
    }
}

function Write-Receipt {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Payload,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Ensure-ParentDir -Path $Path
    $Payload | ConvertTo-Json -Depth 10 | Out-File -FilePath $Path -Encoding utf8
}

if (-not $RetiredLivePath) {
    $RetiredLivePath = "$DeployPath-prepromotion-$(Get-ZetherionIsoTimestampForPath)"
}

$receipt = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    deploy_path = $DeployPath
    candidate_path = $CandidatePath
    retired_live_path = $RetiredLivePath
    previous_live_sha = ""
    promoted_sha = ""
    actions = @()
    status = "failed"
    error = ""
}

try {
    if (-not (Test-Path -LiteralPath $CandidatePath)) {
        throw "Candidate path not found: $CandidatePath"
    }
    if (Test-Path -LiteralPath $RetiredLivePath) {
        throw "Retired live path already exists: $RetiredLivePath"
    }

    if (Test-Path -LiteralPath $DeployPath) {
        $receipt.previous_live_sha = Get-RepositoryHeadSha -RepositoryPath $DeployPath
        Push-Location $DeployPath
        try {
            if (Test-Path -LiteralPath (Join-Path $DeployPath "docker-compose.yml")) {
                docker compose down
                $receipt.actions += "compose_down"
            }
        }
        finally {
            Pop-Location
        }

        Move-Item -LiteralPath $DeployPath -Destination $RetiredLivePath
        $receipt.actions += "archived_previous_live_tree"
    }

    Move-Item -LiteralPath $CandidatePath -Destination $DeployPath
    $receipt.actions += "promoted_clean_candidate"
    $receipt.promoted_sha = Get-RepositoryHeadSha -RepositoryPath $DeployPath
    $receipt.status = "promoted"
}
catch {
    $receipt.error = $_.Exception.Message
}

Write-Receipt -Payload $receipt -Path $OutputPath

if ($receipt.status -eq "promoted") {
    exit 0
}

exit 1
