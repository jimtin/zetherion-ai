param(
    [Parameter(Mandatory = $true)]
    [string]$DeployPath,
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "verify-result.json",
    [Parameter(Mandatory = $false)]
    [int]$StartupWaitSeconds = 180,
    [Parameter(Mandatory = $false)]
    [int]$RetryIntervalSeconds = 5
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
    optional_services_skipped = @()
    monitored_services = @()
    unhealthy_services = @()
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

function Wait-ForBotStartupMarkers {
    param(
        [int]$TimeoutSeconds,
        [int]$IntervalSeconds,
        [string[]]$RequiredMarkers
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastLogs = ""
    while ((Get-Date) -lt $deadline) {
        $botLogs = docker compose logs zetherion-ai-bot --tail 400
        $lastLogs = ($botLogs | Out-String)

        $allMarkersPresent = $true
        foreach ($marker in $RequiredMarkers) {
            if ($lastLogs -notmatch [Regex]::Escape($marker)) {
                $allMarkersPresent = $false
                break
            }
        }

        if ($allMarkersPresent) {
            return [pscustomobject]@{
                passed = $true
                logs = $lastLogs
            }
        }

        Start-Sleep -Seconds $IntervalSeconds
    }

    return [pscustomobject]@{
        passed = $false
        logs = $lastLogs
    }
}

function Wait-ForFallbackProbe {
    param(
        [int]$TimeoutSeconds,
        [int]$IntervalSeconds,
        [string]$ProbeScript
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastOutput = ""
    while ((Get-Date) -lt $deadline) {
        $probeOutput = docker exec zetherion-ai-bot python -c $ProbeScript 2>&1
        $lastOutput = ($probeOutput | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $lastOutput -match "provider=") {
            return [pscustomobject]@{
                passed = $true
                output = $lastOutput
            }
        }

        Start-Sleep -Seconds $IntervalSeconds
    }

    return [pscustomobject]@{
        passed = $false
        output = $lastOutput
    }
}

function Get-EnvValueFromFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    $lines = Get-Content -Path $Path
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        $line = $lines[$i]
        if ($line -match "^\s*#") {
            continue
        }
        if ($line -notmatch "^\s*$([Regex]::Escape($Key))\s*=") {
            continue
        }

        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 0) {
            continue
        }

        return $line.Substring($separatorIndex + 1).Trim()
    }

    return ""
}

function Test-Truthy {
    param([string]$Value)

    $normalized = [string]$Value
    $normalized = $normalized.Trim().ToLowerInvariant()
    return @("1", "true", "yes", "on") -contains $normalized
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

        $optionalServices = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $envPath = Join-Path $DeployPath ".env"
        $cloudflareToken = Get-EnvValueFromFile -Path $envPath -Key "CLOUDFLARE_TUNNEL_TOKEN"

        if (-not $cloudflareToken) {
            $optionalServices.Add("cloudflared") | Out-Null
        }

        $whatsappEnabled = Test-Truthy -Value (Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_ENABLED")
        $whatsappSigningSecret = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_SIGNING_SECRET"
        $whatsappStateKey = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_STATE_KEY"
        $whatsappTenantId = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_TENANT_ID"
        $whatsappIngestUrl = Get-EnvValueFromFile -Path $envPath -Key "WHATSAPP_BRIDGE_INGEST_URL"
        $whatsappConfigured = (
            $whatsappSigningSecret -and
            $whatsappStateKey -and
            $whatsappTenantId -and
            $whatsappIngestUrl
        )

        if (-not $whatsappEnabled -or -not $whatsappConfigured) {
            $optionalServices.Add("whatsapp-bridge") | Out-Null
            $optionalServices.Add("zetherion-ai-whatsapp-bridge") | Out-Null
        }

        $monitoredServices = @(
            $services | Where-Object {
                $serviceName = [string]($_.Service ?? "")
                $containerName = [string]($_.Name ?? "")
                -not ($optionalServices.Contains($serviceName) -or $optionalServices.Contains($containerName))
            }
        )
        $details.optional_services_skipped = @($optionalServices)
        $details.monitored_services = @($monitoredServices | ForEach-Object { [string]($_.Service ?? $_.Name) })

        $badServices = @(
            $monitoredServices | Where-Object {
                ($_.Status -match "Exited|Restarting|Dead|Created|unhealthy")
            }
        )
        $details.unhealthy_services = @(
            $badServices | ForEach-Object { [string]($_.Service ?? $_.Name) }
        )

        if ($monitoredServices.Count -gt 0 -and $badServices.Count -eq 0) {
            $checks.containers_healthy = $true
            $details.container_health = "All monitored services reported non-failing status."
        } else {
            $details.container_health = "One or more monitored services were unhealthy or not running."
        }

        $requiredMarkers = @(
            "settings_manager_initialized",
            "provider_issue_alerts_wired",
            "provider_probe_task_started"
        )
        $markerCheck = Wait-ForBotStartupMarkers `
            -TimeoutSeconds $StartupWaitSeconds `
            -IntervalSeconds $RetryIntervalSeconds `
            -RequiredMarkers $requiredMarkers
        $checks.bot_startup_markers = [bool]$markerCheck.passed
        if ($checks.bot_startup_markers) {
            $details.bot_marker_check = "All required startup markers were found in bot logs."
        } else {
            $details.bot_marker_check = "Missing one or more startup markers in bot logs after waiting for readiness."
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
        $probeCheck = Wait-ForFallbackProbe `
            -TimeoutSeconds $StartupWaitSeconds `
            -IntervalSeconds $RetryIntervalSeconds `
            -ProbeScript $probeScript
        $details.fallback_probe_output = [string]$probeCheck.output
        $checks.fallback_probe = [bool]$probeCheck.passed
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
