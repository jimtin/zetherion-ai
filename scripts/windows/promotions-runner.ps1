param(
    [Parameter(Mandatory = $false)]
    [string]$Sha = "",
    [Parameter(Mandatory = $false)]
    [string]$ReceiptPath = "",
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$SecretsPath = "C:\ZetherionAI\data\secrets\promotions.bin",
    [Parameter(Mandatory = $false)]
    [string]$DataRoot = "C:\ZetherionAI\data\promotions",
    [Parameter(Mandatory = $false)]
    [string]$QueuePath = "C:\ZetherionAI\data\promotions\queue.json",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\promotions\last-run.json",
    [Parameter(Mandatory = $false)]
    [string]$EventSource = "ZetherionPromotions",
    [Parameter(Mandatory = $false)]
    [string]$Repo = ""
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
        # Event log failure should not block promotions.
    }
}

function Decode-PromotionsSecrets {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Promotions secret blob not found: $Path"
    }

    $cipherBytes = [System.IO.File]::ReadAllBytes($Path)
    if (-not $cipherBytes -or $cipherBytes.Length -eq 0) {
        throw "Promotions secret blob is empty: $Path"
    }

    try {
        $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $cipherBytes,
            $null,
            [System.Security.Cryptography.DataProtectionScope]::LocalMachine
        )
    }
    catch {
        throw "Unable to decrypt promotions secret blob via DPAPI LocalMachine."
    }

    $plainText = [System.Text.Encoding]::UTF8.GetString($plainBytes)
    if (-not $plainText) {
        throw "Promotions secret blob decrypted to empty payload."
    }

    try {
        $secretPayload = $plainText | ConvertFrom-Json
    }
    catch {
        throw "Promotions secret blob contains invalid JSON."
    }

    if (-not $secretPayload.secrets) {
        throw "Promotions secret payload missing 'secrets' object."
    }

    return $secretPayload.secrets
}

function Require-SecretValue {
    param([object]$Secrets, [string]$Name)
    $value = [string]($Secrets.$Name)
    if (-not $value) {
        throw "Missing required promotions secret: $Name"
    }
    return $value
}

function Ensure-QueueEntry {
    param(
        [string]$Path,
        [string]$Sha,
        [string]$Reason
    )

    if (-not $Sha) {
        return
    }

    $queue = Read-JsonFile -Path $Path -Default ([pscustomobject]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        pending = @()
    })

    $pending = @()
    if ($queue.pending) {
        $pending = @($queue.pending)
    }

    $existing = $pending | Where-Object { [string]$_.sha -eq $Sha } | Select-Object -First 1
    if ($existing) {
        $attempts = [int]($existing.attempts ?? 0)
        $existing.attempts = $attempts + 1
        $existing.last_error = $Reason
        $existing.updated_at = [DateTime]::UtcNow.ToString("o")
    } else {
        $pending += [pscustomobject]@{
            sha = $Sha
            attempts = 1
            first_seen_at = [DateTime]::UtcNow.ToString("o")
            updated_at = [DateTime]::UtcNow.ToString("o")
            last_error = $Reason
        }
    }

    $queue.pending = $pending
    $queue.generated_at = [DateTime]::UtcNow.ToString("o")
    Write-JsonFile -Payload $queue -Path $Path
}

function Resolve-ShaFromReceipt {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return ""
    }
    try {
        $receipt = Get-Content -Path $Path -Raw | ConvertFrom-Json
        $candidate = [string]($receipt.target_sha)
        if (-not $candidate) {
            $candidate = [string]($receipt.deployed_sha)
        }
        return $candidate.Trim().ToLowerInvariant()
    }
    catch {
        return ""
    }
}

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    sha = ""
    receipt_path = ""
    status = "failed"
    retryable = $true
    command = ""
    exit_code = -1
    error = ""
}

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    if (-not $Sha -and $ReceiptPath) {
        $Sha = Resolve-ShaFromReceipt -Path $ReceiptPath
    }
    if (-not $Sha) {
        throw "SHA is required (either -Sha or a receipt containing target_sha/deployed_sha)."
    }
    $Sha = $Sha.Trim().ToLowerInvariant()
    $result.sha = $Sha

    if (-not $ReceiptPath) {
        $ReceiptPath = Join-Path $DeployPath "data\deployment-receipts\$Sha.json"
    }
    $result.receipt_path = $ReceiptPath

    $existingPromotionReceiptPath = Join-Path $DataRoot "receipts\$Sha.json"
    if (Test-Path $existingPromotionReceiptPath) {
        try {
            $existingPromotionReceipt = Get-Content -Path $existingPromotionReceiptPath -Raw | ConvertFrom-Json
            if ([string]$existingPromotionReceipt.status -eq "success") {
                $result.status = "skipped_existing_success"
                $result.retryable = $false
                $result.exit_code = 0
                Write-JsonFile -Payload $result -Path $OutputPath
                exit 0
            }
        }
        catch {
            # Continue when receipt parse fails; pipeline will rebuild receipt.
        }
    }

    $secrets = Decode-PromotionsSecrets -Path $SecretsPath

    $env:CGS_BLOG_PUBLISH_URL = Require-SecretValue -Secrets $secrets -Name "CGS_BLOG_PUBLISH_URL"
    $env:CGS_BLOG_PUBLISH_TOKEN = Require-SecretValue -Secrets $secrets -Name "CGS_BLOG_PUBLISH_TOKEN"
    $env:OPENAI_API_KEY = Require-SecretValue -Secrets $secrets -Name "OPENAI_API_KEY"
    $env:ANTHROPIC_API_KEY = Require-SecretValue -Secrets $secrets -Name "ANTHROPIC_API_KEY"
    $githubToken = Require-SecretValue -Secrets $secrets -Name "GITHUB_PROMOTION_TOKEN"
    $env:GITHUB_TOKEN = $githubToken
    $env:GH_TOKEN = $githubToken

    $primaryModel = [string]($secrets.BLOG_MODEL_PRIMARY)
    if (-not $primaryModel) {
        $primaryModel = "gpt-5.2"
    }
    $secondaryModel = [string]($secrets.BLOG_MODEL_SECONDARY)
    if (-not $secondaryModel) {
        $secondaryModel = "claude-sonnet-4-6"
    }
    if ($primaryModel -ne "gpt-5.2") {
        throw "BLOG_MODEL_PRIMARY secret must be set to gpt-5.2"
    }
    if ($secondaryModel -ne "claude-sonnet-4-6") {
        throw "BLOG_MODEL_SECONDARY secret must be set to claude-sonnet-4-6"
    }

    $env:BLOG_MODEL_PRIMARY = $primaryModel
    $env:BLOG_MODEL_SECONDARY = $secondaryModel
    $env:BLOG_PUBLISH_ENABLED = [string]($secrets.BLOG_PUBLISH_ENABLED ?? "true")
    $env:RELEASE_AUTO_INCREMENT_ENABLED = [string]($secrets.RELEASE_AUTO_INCREMENT_ENABLED ?? "true")

    $resolvedRepo = $Repo
    if (-not $resolvedRepo) {
        $resolvedRepo = [string]($secrets.GITHUB_REPOSITORY)
    }

    $pythonExe = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
    $pipelineScript = Join-Path $DeployPath "scripts\windows\promotions-pipeline.py"
    if (-not (Test-Path $pipelineScript)) {
        throw "Promotions pipeline script not found: $pipelineScript"
    }

    $commandArgs = @(
        $pipelineScript,
        "--sha", $Sha,
        "--deploy-path", $DeployPath,
        "--deployment-receipt", $ReceiptPath,
        "--data-root", $DataRoot
    )
    if ($resolvedRepo) {
        $commandArgs += @("--repo", $resolvedRepo)
    }

    $result.command = "$pythonExe $($commandArgs -join ' ')"
    & $pythonExe @commandArgs
    $exitCode = $LASTEXITCODE
    $result.exit_code = $exitCode

    if ($exitCode -eq 0) {
        $result.status = "success"
        $result.retryable = $false
        Write-PromotionEvent -Source $EventSource -EntryType "Information" -EventId 7100 -Message "Post-deploy promotion succeeded for SHA $Sha."
        Write-JsonFile -Payload $result -Path $OutputPath
        exit 0
    }

    if ($exitCode -eq 2) {
        $result.status = "retryable_failure"
        $result.retryable = $true
        $result.error = "Promotions pipeline returned retryable exit code."
        Ensure-QueueEntry -Path $QueuePath -Sha $Sha -Reason $result.error
        Write-PromotionEvent -Source $EventSource -EntryType "Warning" -EventId 7101 -Message "Post-deploy promotion deferred for SHA $Sha (retry queued)."
        Write-JsonFile -Payload $result -Path $OutputPath
        exit 2
    }

    $result.status = "failed"
    $result.retryable = $true
    $result.error = "Promotions pipeline exited with code $exitCode."
    Ensure-QueueEntry -Path $QueuePath -Sha $Sha -Reason $result.error
    Write-PromotionEvent -Source $EventSource -EntryType "Error" -EventId 7199 -Message "Post-deploy promotion failed for SHA $Sha (exit=$exitCode)."
    Write-JsonFile -Payload $result -Path $OutputPath
    exit $exitCode
}
catch {
    $result.status = "failed"
    $result.retryable = $true
    $result.error = $_.Exception.Message
    if ($result.sha) {
        Ensure-QueueEntry -Path $QueuePath -Sha $result.sha -Reason $result.error
    }
    Write-PromotionEvent -Source $EventSource -EntryType "Error" -EventId 7198 -Message "Post-deploy promotion runner error: $($result.error)"
    Write-JsonFile -Payload $result -Path $OutputPath
    exit 2
}
