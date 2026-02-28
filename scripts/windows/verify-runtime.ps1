param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "verify-result.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$checks = [ordered]@{
    containers_healthy = $false
    bot_startup_markers = $false
    postgres_model_keys = $false
    fallback_probe = $false
}

$details = [ordered]@{
    container_health = ""
    bot_marker_check = ""
    postgres_keys = @()
    fallback_probe_output = ""
}

function Write-VerifyResult {
    param(
        [object]$Checks,
        [object]$Details,
        [string]$Path
    )
    $payload = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        checks = $Checks
        details = $Details
    }

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $payload | ConvertTo-Json -Depth 8 | Out-File $Path -Encoding utf8
}

try {
    if (-not (Test-Path $DeployPath)) {
        throw "Deploy path not found: $DeployPath"
    }

    Push-Location $DeployPath
    try {
        $psRaw = docker compose ps --format json
        $services = $psRaw | ConvertFrom-Json
        if ($services -isnot [System.Array]) {
            $services = @($services)
        }

        $badServices = @(
            $services | Where-Object {
                ($_.Status -match "Exited|Restarting|Dead|Created|unhealthy")
            }
        )
        if ($services.Count -gt 0 -and $badServices.Count -eq 0) {
            $checks.containers_healthy = $true
            $details.container_health = "All services reported non-failing status."
        } else {
            $details.container_health = "One or more services were unhealthy or not running."
        }

        $botLogs = docker compose logs zetherion-ai-bot --tail 400
        $requiredMarkers = @(
            "settings_manager_initialized",
            "provider_issue_alerts_wired",
            "provider_probe_task_started"
        )
        $allMarkersPresent = $true
        foreach ($marker in $requiredMarkers) {
            if ($botLogs -notmatch [Regex]::Escape($marker)) {
                $allMarkersPresent = $false
                break
            }
        }
        $checks.bot_startup_markers = $allMarkersPresent
        if ($allMarkersPresent) {
            $details.bot_marker_check = "All required startup markers were found in bot logs."
        } else {
            $details.bot_marker_check = "Missing one or more startup markers in bot logs."
        }

        $pgRaw = docker exec zetherion-ai-postgres psql -U zetherion -d zetherion -t -A -c "SELECT key FROM settings WHERE namespace='models' ORDER BY key;"
        $keys = @(
            $pgRaw -split "`r?`n" | Where-Object { $_ -and $_.Trim() -ne "" } | ForEach-Object { $_.Trim() }
        )
        $details.postgres_keys = $keys
        $requiredKeys = @(
            "claude_model",
            "groq_model",
            "ollama_generation_model",
            "openai_model",
            "router_model"
        )
        $missing = @(
            $requiredKeys | Where-Object { $keys -notcontains $_ }
        )
        $checks.postgres_model_keys = ($missing.Count -eq 0)

        $probeScript = @"
import asyncio
from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.providers import TaskType

broker = InferenceBroker()
try:
    result = asyncio.run(
        broker.infer(
            prompt="Return OK only.",
            task_type=TaskType.CODE_GENERATION,
            max_tokens=32,
        )
    )
    print(f"provider={result.provider.value} model={result.model} content={result.content[:80]!r}")
finally:
    asyncio.run(broker.close())
"@
        $probeOutput = docker exec zetherion-ai-bot python -c $probeScript 2>&1
        $details.fallback_probe_output = ($probeOutput | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $probeOutput -match "provider=") {
            $checks.fallback_probe = $true
        }
    } finally {
        Pop-Location
    }
} catch {
    $details.container_health = "Verification raised exception: $($_.Exception.Message)"
}

Write-VerifyResult -Checks $checks -Details $details -Path $OutputPath

if ($checks.containers_healthy -and $checks.bot_startup_markers -and $checks.postgres_model_keys -and $checks.fallback_probe) {
    exit 0
}

exit 1
