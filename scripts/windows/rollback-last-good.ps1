param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $true)]
    [string]$LastGoodShaPath,
    [Parameter(Mandatory = $false)]
    [string]$LockPath = "C:\ZetherionAI\data\deploy.lock",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "rollback-result.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-RollbackResult {
    param(
        [object]$Result,
        [string]$Path
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Result | ConvertTo-Json -Depth 8 | Out-File $Path -Encoding utf8
}

$result = [ordered]@{
    rolled_back_sha = ""
    rolled_back_at = [DateTime]::UtcNow.ToString("o")
    status = "failed"
    error = ""
}

$lockCreated = $false

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    if (-not (Test-Path $LastGoodShaPath)) {
        throw "Last good SHA file not found: $LastGoodShaPath"
    }

    if (Test-Path $LockPath) {
        throw "Deployment lock exists: $LockPath"
    }

    $lastGoodSha = (Get-Content $LastGoodShaPath -Raw).Trim()
    if (-not $lastGoodSha) {
        throw "Last good SHA file is empty: $LastGoodShaPath"
    }

    $lockParent = Split-Path -Parent $LockPath
    if ($lockParent -and -not (Test-Path $lockParent)) {
        New-Item -ItemType Directory -Path $lockParent -Force | Out-Null
    }
    Set-Content -Path $LockPath -Value "pid=$PID started=$([DateTime]::UtcNow.ToString('o'))`n" -Encoding utf8
    $lockCreated = $true

    Push-Location $DeployPath
    try {
        git fetch origin --tags --prune
        git checkout --detach $lastGoodSha
        docker compose up -d --build
    } finally {
        Pop-Location
    }

    $result.rolled_back_sha = $lastGoodSha
    $result.status = "rolled_back"
} catch {
    $result.error = $_.Exception.Message
    Write-RollbackResult -Result $result -Path $OutputPath
    throw
} finally {
    if ($lockCreated -and (Test-Path $LockPath)) {
        Remove-Item -Path $LockPath -Force
    }
}

Write-RollbackResult -Result $result -Path $OutputPath
