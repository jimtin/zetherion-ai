param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$QueuePath = "C:\ZetherionAI\data\promotions\queue.json",
    [Parameter(Mandatory = $false)]
    [string]$StatePath = "C:\ZetherionAI\data\promotions\watch-state.json",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\promotions\watch-result.json",
    [Parameter(Mandatory = $false)]
    [string]$EventSource = "ZetherionPromotions"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-ParentDir {
    param([string]$Path)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Read-JsonFile {
    param([string]$Path, [object]$Default)
    if (-not (Test-Path $Path)) {
        return $Default
    }
    try {
        return Get-Content -Path $Path -Raw | ConvertFrom-Json
    }
    catch {
        return $Default
    }
}

function Write-JsonFile {
    param([object]$Payload, [string]$Path)
    Ensure-ParentDir -Path $Path
    $Payload | ConvertTo-Json -Depth 12 | Out-File -FilePath $Path -Encoding utf8
}

function Write-PromotionEvent {
    param(
        [string]$Source,
        [string]$EntryType,
        [int]$EventId,
        [string]$Message
    )

    try {
        if (-not [System.Diagnostics.EventLog]::SourceExists($Source)) {
            New-EventLog -LogName "Application" -Source $Source
        }
        Write-EventLog -LogName "Application" -Source $Source -EntryType $EntryType -EventId $EventId -Message $Message
    }
    catch {
        # Non-blocking by design.
    }
}

function Read-PromotionsQueue {
    param([string]$Path)
    $default = [pscustomobject]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        pending = @()
    }
    return Read-JsonFile -Path $Path -Default $default
}

function Get-SuccessfulPromotionShas {
    param([string]$ReceiptsDirectory)
    $successful = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    if (-not (Test-Path $ReceiptsDirectory)) {
        return $successful
    }

    $receiptFiles = Get-ChildItem -Path $ReceiptsDirectory -Filter "*.json" -File -ErrorAction SilentlyContinue
    foreach ($file in $receiptFiles) {
        try {
            $receipt = Get-Content -Path $file.FullName -Raw | ConvertFrom-Json
            if ([string]$receipt.status -eq "success") {
                $sha = [string]$receipt.sha
                if ($sha) {
                    [void]$successful.Add($sha.Trim().ToLowerInvariant())
                }
            }
        }
        catch {
            # Ignore unreadable receipts.
        }
    }

    return $successful
}

function Get-DeploymentCandidates {
    param(
        [string]$DeploymentReceiptsDirectory,
        [System.Collections.Generic.HashSet[string]]$AlreadySuccessful
    )

    $candidates = New-Object 'System.Collections.Generic.List[object]'
    if (-not (Test-Path $DeploymentReceiptsDirectory)) {
        return $candidates
    }

    $deploymentFiles = Get-ChildItem -Path $DeploymentReceiptsDirectory -Filter "*.json" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
    foreach ($file in $deploymentFiles) {
        try {
            $receipt = Get-Content -Path $file.FullName -Raw | ConvertFrom-Json
            if ([string]$receipt.status -ne "success") {
                continue
            }
            $sha = [string]$receipt.target_sha
            if (-not $sha) {
                $sha = [string]$receipt.deployed_sha
            }
            if (-not $sha) {
                continue
            }

            $normalized = $sha.Trim().ToLowerInvariant()
            if ($AlreadySuccessful.Contains($normalized)) {
                continue
            }

            $candidates.Add([pscustomobject]@{
                sha = $normalized
                receipt_path = $file.FullName
                source = "deployment-receipt"
            })
        }
        catch {
            # Ignore invalid receipt payloads.
        }
    }

    return $candidates
}

function Merge-Candidates {
    param(
        [object[]]$QueueCandidates,
        [object[]]$DeploymentCandidates
    )

    $map = @{}
    foreach ($candidate in @($QueueCandidates) + @($DeploymentCandidates)) {
        if (-not $candidate) {
            continue
        }
        $sha = [string]$candidate.sha
        if (-not $sha) {
            continue
        }
        $normalized = $sha.Trim().ToLowerInvariant()
        if (-not $map.ContainsKey($normalized)) {
            $map[$normalized] = [ordered]@{
                sha = $normalized
                receipt_path = [string]$candidate.receipt_path
                source = [string]$candidate.source
            }
            continue
        }

        if (-not $map[$normalized].receipt_path -and $candidate.receipt_path) {
            $map[$normalized].receipt_path = [string]$candidate.receipt_path
        }
        if ($candidate.source -and -not $map[$normalized].source) {
            $map[$normalized].source = [string]$candidate.source
        }
    }

    return @($map.Values)
}

if (-not (Test-Path $DeployPath)) {
    throw "Deploy path not found: $DeployPath"
}

$deployReceiptsDir = Join-Path $DeployPath "data\deployment-receipts"
$promotionsDir = Join-Path $DeployPath "data\promotions"
$promotionReceiptsDir = Join-Path $promotionsDir "receipts"
$runnerScript = Join-Path $DeployPath "scripts\windows\promotions-runner.ps1"
if (-not (Test-Path $runnerScript)) {
    throw "Promotions runner script not found: $runnerScript"
}

$queue = Read-PromotionsQueue -Path $QueuePath
$pendingQueue = @()
if ($queue.pending) {
    $pendingQueue = @($queue.pending)
}

$successfulShas = Get-SuccessfulPromotionShas -ReceiptsDirectory $promotionReceiptsDir
$queueCandidates = @()
foreach ($entry in $pendingQueue) {
    $sha = [string]$entry.sha
    if (-not $sha) {
        continue
    }
    $normalized = $sha.Trim().ToLowerInvariant()
    if ($successfulShas.Contains($normalized)) {
        continue
    }
    $queueCandidates += [pscustomobject]@{
        sha = $normalized
        receipt_path = ""
        source = "retry-queue"
    }
}

$deploymentCandidates = Get-DeploymentCandidates -DeploymentReceiptsDirectory $deployReceiptsDir -AlreadySuccessful $successfulShas
$candidates = Merge-Candidates -QueueCandidates $queueCandidates -DeploymentCandidates $deploymentCandidates

$actions = @()
$processed = @()
$remainingShas = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

foreach ($entry in $pendingQueue) {
    $existingSha = [string]$entry.sha
    if ($existingSha) {
        [void]$remainingShas.Add($existingSha.Trim().ToLowerInvariant())
    }
}
foreach ($entry in $deploymentCandidates) {
    $candidateSha = [string]$entry.sha
    if ($candidateSha) {
        [void]$remainingShas.Add($candidateSha.Trim().ToLowerInvariant())
    }
}

foreach ($candidate in $candidates) {
    $sha = [string]$candidate.sha
    if (-not $sha) {
        continue
    }

    $runOutputPath = Join-Path $promotionsDir "watch-run-$sha.json"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $runnerScript,
        "-Sha", $sha,
        "-DeployPath", $DeployPath,
        "-QueuePath", $QueuePath,
        "-OutputPath", $runOutputPath,
        "-EventSource", $EventSource
    )
    if ($candidate.receipt_path) {
        $args += @("-ReceiptPath", [string]$candidate.receipt_path)
    }

    & pwsh.exe @args
    $exitCode = $LASTEXITCODE
    $processed += [pscustomobject]@{
        sha = $sha
        source = [string]$candidate.source
        exit_code = $exitCode
        output_path = $runOutputPath
    }

    if ($exitCode -eq 0) {
        [void]$remainingShas.Remove($sha)
        $actions += "promoted:$sha"
    } else {
        [void]$remainingShas.Add($sha)
        $actions += "deferred:$sha"
    }
}

$newQueue = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    pending = @()
}
foreach ($sha in $remainingShas) {
    $newQueue.pending += [ordered]@{
        sha = $sha
        updated_at = [DateTime]::UtcNow.ToString("o")
        last_error = "pending_or_retry"
    }
}
Write-JsonFile -Payload $newQueue -Path $QueuePath

$state = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    candidates = @($candidates | ForEach-Object { [string]$_.sha })
    processed_count = $processed.Count
    queue_count = $newQueue.pending.Count
}
Write-JsonFile -Payload $state -Path $StatePath

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    status = "completed"
    actions = $actions
    processed = $processed
    queue_count = $newQueue.pending.Count
}
Write-JsonFile -Payload $result -Path $OutputPath

if ($processed.Count -gt 0) {
    Write-PromotionEvent -Source $EventSource -EntryType "Information" -EventId 7102 -Message "Promotions watch processed $($processed.Count) candidate(s); pending queue size $($newQueue.pending.Count)."
}

exit 0
