param(
    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\ZetherionAI",
    [Parameter(Mandatory = $false)]
    [string]$SecretsPath = "C:\ZetherionAI\data\secrets\promotions.bin",
    [Parameter(Mandatory = $false)]
    [string]$StatePath = "C:\ZetherionAI\data\announcements\notifications-state.json",
    [Parameter(Mandatory = $false)]
    [string]$OutboxDir = "C:\ZetherionAI\data\announcements\outbox",
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "C:\ZetherionAI\data\announcements\flush-result.json",
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

function Write-JsonFile {
    param([object]$Payload, [string]$Path)
    Ensure-ParentDir -Path $Path
    $Payload | ConvertTo-Json -Depth 12 | Out-File -FilePath $Path -Encoding utf8
}

function Write-AnnouncementEvent {
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
        # Event log writes are non-blocking.
    }
}

$result = [ordered]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    status = "skipped"
    flushed = 0
    pending = 0
    detail = ""
}

try {
    $emitScript = Join-Path $DeployPath "scripts\windows\announcement-emit.py"
    if (-not (Test-Path $emitScript)) {
        $fallbackScript = Join-Path $PSScriptRoot "announcement-emit.py"
        if (Test-Path $fallbackScript) {
            $emitScript = $fallbackScript
        }
    }

    if (-not (Test-Path $emitScript)) {
        $result.status = "skipped_missing_script"
        $result.detail = "announcement-emit.py not found."
        Write-JsonFile -Payload $result -Path $OutputPath
        $result | ConvertTo-Json -Depth 8
        exit 0
    }

    $pythonExe = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
    $args = @(
        $emitScript,
        "--flush-outbox",
        "--secrets-path", $SecretsPath,
        "--state-path", $StatePath,
        "--outbox-dir", $OutboxDir
    )

    $rawOutput = & $pythonExe @args
    $exitCode = $LASTEXITCODE
    $outputText = ($rawOutput -join "`n").Trim()
    $statusPayload = $null
    if ($outputText) {
        $lines = $outputText -split "`r?`n"
        $lastLine = $lines[-1]
        try {
            $statusPayload = $lastLine | ConvertFrom-Json
        }
        catch {
            $statusPayload = $null
        }
    }

    if ($statusPayload) {
        $result.status = [string]$statusPayload.status
        if ($null -ne $statusPayload.flushed) {
            $result.flushed = [int]$statusPayload.flushed
        }
        if ($null -ne $statusPayload.pending) {
            $result.pending = [int]$statusPayload.pending
        }
    } elseif ($exitCode -eq 0) {
        $result.status = "flush_completed"
    } else {
        $result.status = "flush_failed"
        $result.detail = "announcement-emit exited with code $exitCode."
    }

    if ($result.pending -gt 0) {
        Write-AnnouncementEvent -Source $EventSource -EntryType "Warning" -EventId 7103 -Message "Announcement flush pending=$($result.pending) flushed=$($result.flushed)."
    } elseif ($result.status -eq "flush_failed") {
        Write-AnnouncementEvent -Source $EventSource -EntryType "Warning" -EventId 7104 -Message "Announcement flush failed: $($result.detail)"
    } elseif ($result.flushed -gt 0) {
        Write-AnnouncementEvent -Source $EventSource -EntryType "Information" -EventId 7105 -Message "Announcement flush replayed $($result.flushed) queued event(s)."
    }
}
catch {
    $result.status = "flush_exception"
    $result.detail = $_.Exception.Message
}

Write-JsonFile -Payload $result -Path $OutputPath
$result | ConvertTo-Json -Depth 8
exit 0
