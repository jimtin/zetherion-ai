param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$CandidatePath = "C:\ZetherionAI-cutover",
    [Parameter(Mandatory = $true)]
    [string]$TargetSha,
    [Parameter(Mandatory = $false)]
    [string]$TargetRef = "",
    [Parameter(Mandatory = $false)]
    [string]$RepositoryUrl = "https://github.com/jimtin/zetherion-ai.git",
    [Parameter(Mandatory = $false)]
    [string]$RescuePath = "",
    [Parameter(Mandatory = $false)]
    [string[]]$FileStatePaths = @(".env"),
    [Parameter(Mandatory = $false)]
    [string[]]$PersistentDirectories = @(),
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionCI\artifacts\windows-cutover-prepare-receipt.json"
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

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-RobocopyTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Ensure-ParentDir -Path $Destination
    & robocopy $Source $Destination /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP *> $null
    $exitCode = $LASTEXITCODE
    if ($exitCode -gt 7) {
        throw "robocopy $Source $Destination failed with exit code $exitCode"
    }

    return $exitCode
}

function Get-RepositoryForensics {
    param([string]$RepositoryPath)

    if (-not (Test-Path -LiteralPath $RepositoryPath)) {
        return [ordered]@{
            exists = $false
            head_sha = ""
            status = @()
            diff_stat = @()
            modified_tracked = @()
            untracked = @()
            key_script_hashes = @()
        }
    }

    Push-Location $RepositoryPath
    try {
        $headSha = ((git rev-parse HEAD 2>$null) | Out-String).Trim()
        $statusLines = @((git status --short --branch 2>$null) | ForEach-Object { [string]$_ })
        $diffStat = @((git diff --stat 2>$null) | ForEach-Object { [string]$_ })
        $trackedModified = @(
            git diff --name-only 2>$null | ForEach-Object { [string]$_ }
        )
        $untracked = @(
            git ls-files --others --exclude-standard 2>$null | ForEach-Object { [string]$_ }
        )
    }
    finally {
        Pop-Location
    }

    $keyScriptHashes = @()
    foreach ($relativePath in @(
        "scripts\windows\deploy-runner.ps1",
        "scripts\windows\startup-recover.ps1",
        "scripts\windows\runtime-watchdog.ps1",
        "scripts\windows\verify-runtime.ps1",
        "scripts\verify-windows-host.ps1"
    )) {
        $fullPath = Join-Path $RepositoryPath $relativePath
        if (-not (Test-Path -LiteralPath $fullPath)) {
            continue
        }

        $hash = Get-FileHash -LiteralPath $fullPath -Algorithm SHA256
        $keyScriptHashes += [ordered]@{
            path = $relativePath
            sha256 = [string]$hash.Hash
        }
    }

    return [ordered]@{
        exists = $true
        head_sha = $headSha
        status = $statusLines
        diff_stat = $diffStat
        modified_tracked = $trackedModified
        untracked = $untracked
        key_script_hashes = $keyScriptHashes
    }
}

function Copy-AllowlistedRuntimeState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceRoot,
        [Parameter(Mandatory = $true)]
        [string]$DestinationRoot,
        [string[]]$FilePaths = @(".env"),
        [string[]]$DirectoryPaths = @()
    )

    $copied = New-Object 'System.Collections.Generic.List[object]'

    foreach ($relativeFile in @($FilePaths)) {
        if (-not $relativeFile) {
            continue
        }
        $sourcePath = Join-Path $SourceRoot $relativeFile
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            continue
        }
        $destinationPath = Join-Path $DestinationRoot $relativeFile
        Ensure-ParentDir -Path $destinationPath
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
        $copied.Add([ordered]@{
            kind = "file"
            relative_path = $relativeFile
            source = $sourcePath
            destination = $destinationPath
        }) | Out-Null
    }

    foreach ($relativeDir in @($DirectoryPaths)) {
        if (-not $relativeDir) {
            continue
        }
        $sourcePath = Join-Path $SourceRoot $relativeDir
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            continue
        }
        $destinationPath = Join-Path $DestinationRoot $relativeDir
        if (Test-Path -LiteralPath $destinationPath) {
            Remove-Item -LiteralPath $destinationPath -Recurse -Force
        }
        Ensure-ParentDir -Path $destinationPath
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse -Force
        $copied.Add([ordered]@{
            kind = "directory"
            relative_path = $relativeDir
            source = $sourcePath
            destination = $destinationPath
        }) | Out-Null
    }

    return @($copied.ToArray())
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

if (-not $RescuePath) {
    $RescuePath = "$DeployPath-precutover-$(Get-ZetherionIsoTimestampForPath)"
}

$receipt = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    deploy_path = $DeployPath
    candidate_path = $CandidatePath
    rescue_path = $RescuePath
    repository_url = $RepositoryUrl
    target_sha = $TargetSha
    target_ref = $TargetRef
    status = "failed"
    forensic_inventory = $null
    candidate_inventory = $null
    carried_forward_state = @()
    rescue_copy_exit_code = $null
    error = ""
}

try {
    if (-not (Test-Path -LiteralPath $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }
    if (Test-Path -LiteralPath $RescuePath) {
        throw "Rescue path already exists: $RescuePath"
    }

    $receipt.forensic_inventory = Get-RepositoryForensics -RepositoryPath $DeployPath
    $receipt.rescue_copy_exit_code = Invoke-RobocopyTree -Source $DeployPath -Destination $RescuePath

    if (Test-Path -LiteralPath $CandidatePath) {
        Remove-Item -LiteralPath $CandidatePath -Recurse -Force
    }

    Invoke-GitCommand @("clone", "--no-checkout", $RepositoryUrl, $CandidatePath)
    Push-Location $CandidatePath
    try {
        Invoke-GitCommand @("fetch", "--prune", "--force", "origin")
        Invoke-GitCommand @("fetch", "--depth=1", "--force", "origin", $TargetSha)
        Invoke-GitCommand @("checkout", "--detach", "--force", $TargetSha)
    }
    finally {
        Pop-Location
    }

    $receipt.carried_forward_state = Copy-AllowlistedRuntimeState `
        -SourceRoot $DeployPath `
        -DestinationRoot $CandidatePath `
        -FilePaths $FileStatePaths `
        -DirectoryPaths $PersistentDirectories
    $receipt.candidate_inventory = Get-RepositoryForensics -RepositoryPath $CandidatePath
    $receipt.status = "prepared"
}
catch {
    $receipt.error = $_.Exception.Message
}

Write-Receipt -Payload $receipt -Path $OutputPath

if ($receipt.status -eq "prepared") {
    exit 0
}

exit 1
