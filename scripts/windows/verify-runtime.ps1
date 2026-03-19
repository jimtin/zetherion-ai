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
. (Join-Path $PSScriptRoot "docker-runtime.ps1")

$checks = [ordered]@{
    containers_healthy = $false
    auxiliary_services_healthy = $true
    bot_startup_markers = $false
    postgres_model_keys = $false
    fallback_probe = $false
}

$details = [ordered]@{
    container_health = ""
    auxiliary_container_health = ""
    bot_marker_check = ""
    postgres_keys = @()
    postgres_probe_error = ""
    fallback_probe_output = ""
    defined_services = @()
    running_service_count = 0
    runtime_start_hint = ""
    optional_services_skipped = @()
    monitored_services = @()
    unhealthy_services = @()
    core_services = @()
    unhealthy_core_services = @()
    auxiliary_services = @()
    unhealthy_auxiliary_services = @()
    core_status = "failed"
    aux_status = "healthy"
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
        $botLogs = Invoke-ZetherionWslDockerResult compose logs zetherion-ai-bot --tail 400
        $lastLogs = $botLogs.Text

        if ($botLogs.ExitCode -ne 0) {
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

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
        [string]$DeployPath,
        [string]$ProbeScript
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastOutput = ""
    while ((Get-Date) -lt $deadline) {
        $probeOutput = Invoke-FallbackProbe -DeployPath $DeployPath -ProbeScript $ProbeScript
        $lastOutput = $probeOutput.Text
        if ($probeOutput.ExitCode -eq 0 -and $lastOutput -match "provider=") {
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

function Invoke-PostgresSettingsQuery {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployPath,
        [Parameter(Mandatory = $true)]
        [string]$Query
    )

    $repoWslPath = ConvertTo-ZetherionWslPath -WindowsPath $DeployPath
    $encodedQuery = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Query.Trim()))
    $command = "cd $(ConvertTo-ZetherionBashLiteral -Value $repoWslPath) && printf '%s' $(ConvertTo-ZetherionBashLiteral -Value $encodedQuery) | base64 -d | docker exec -i zetherion-ai-postgres psql -U zetherion -d zetherion -t -A"

    return (Invoke-ZetherionWslCommandResult -Command $command)
}

function Invoke-FallbackProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployPath,
        [Parameter(Mandatory = $true)]
        [string]$ProbeScript
    )

    $repoWslPath = ConvertTo-ZetherionWslPath -WindowsPath $DeployPath
    $encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($ProbeScript.Trim()))
    $command = "cd $(ConvertTo-ZetherionBashLiteral -Value $repoWslPath) && printf '%s' $(ConvertTo-ZetherionBashLiteral -Value $encodedScript) | base64 -d | docker exec -i zetherion-ai-bot python -"

    return (Invoke-ZetherionWslCommandResult -Command $command)
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

function Get-ComposeServiceField {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Service,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string]$Default = ""
    )

    $property = $Service.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }

    $value = $property.Value
    if ($null -eq $value) {
        return $Default
    }

    return [string]$value
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
        $definedServices = @(
            docker compose config --services |
                ForEach-Object { [string]$_ } |
                Where-Object { $_ -and $_.Trim() -ne "" }
        )
        $details.defined_services = $definedServices
        $details.running_service_count = @($services | Where-Object { $_ }).Count

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

        $auxiliaryServiceNames = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        foreach ($name in @("cloudflared", "zetherion-ai-cloudflared", "whatsapp-bridge", "zetherion-ai-whatsapp-bridge")) {
            $auxiliaryServiceNames.Add($name) | Out-Null
        }

        $monitoredServices = @(
            $services | Where-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                $containerName = Get-ComposeServiceField -Service $_ -Name "Name"
                -not ($optionalServices.Contains($serviceName) -or $optionalServices.Contains($containerName))
            }
        )
        $details.optional_services_skipped = @($optionalServices)
        $details.monitored_services = @(
            $monitoredServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )

        $coreServices = @(
            $monitoredServices | Where-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                $containerName = Get-ComposeServiceField -Service $_ -Name "Name"
                -not ($auxiliaryServiceNames.Contains($serviceName) -or $auxiliaryServiceNames.Contains($containerName))
            }
        )
        $auxiliaryServices = @(
            $monitoredServices | Where-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                $containerName = Get-ComposeServiceField -Service $_ -Name "Name"
                $auxiliaryServiceNames.Contains($serviceName) -or $auxiliaryServiceNames.Contains($containerName)
            }
        )

        $details.core_services = @(
            $coreServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )
        $details.auxiliary_services = @(
            $auxiliaryServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )

        $badServices = @(
            $monitoredServices | Where-Object {
                ($_.Status -match "Exited|Restarting|Dead|Created|unhealthy")
            }
        )
        $badCoreServices = @(
            $coreServices | Where-Object {
                ($_.Status -match "Exited|Restarting|Dead|Created|unhealthy")
            }
        )
        $badAuxiliaryServices = @(
            $auxiliaryServices | Where-Object {
                ($_.Status -match "Exited|Restarting|Dead|Created|unhealthy")
            }
        )
        $details.unhealthy_services = @(
            $badServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )
        $details.unhealthy_core_services = @(
            $badCoreServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )
        $details.unhealthy_auxiliary_services = @(
            $badAuxiliaryServices | ForEach-Object {
                $serviceName = Get-ComposeServiceField -Service $_ -Name "Service"
                if ($serviceName) {
                    return $serviceName
                }
                return (Get-ComposeServiceField -Service $_ -Name "Name")
            }
        )

        if ($definedServices.Count -gt 0 -and @($services | Where-Object { $_ }).Count -eq 0) {
            $details.container_health = "No containers are running for this deployment path. This usually means the compose project at this path has not been started yet. If this is a clean cutover candidate, promote it first or stop the live project and bring this path up before running verify-runtime."
            $details.runtime_start_hint = "compose_project_not_started_for_target_path"
            $details.core_status = "failed"
            $details.aux_status = "not_enabled"
        }
        elseif ($coreServices.Count -gt 0 -and $badCoreServices.Count -eq 0) {
            $checks.containers_healthy = $true
            $details.container_health = "All monitored core services reported non-failing status."
            $details.core_status = "healthy"
        } else {
            $details.container_health = "One or more monitored core services were unhealthy or not running."
            $details.core_status = "failed"
        }

        if ($auxiliaryServices.Count -eq 0) {
            $checks.auxiliary_services_healthy = $true
            $details.auxiliary_container_health = "No auxiliary services are enabled for this deployment."
            $details.aux_status = "not_enabled"
        }
        elseif ($badAuxiliaryServices.Count -eq 0) {
            $checks.auxiliary_services_healthy = $true
            $details.auxiliary_container_health = "All monitored auxiliary services reported non-failing status."
            $details.aux_status = "healthy"
        }
        else {
            $checks.auxiliary_services_healthy = $false
            $details.auxiliary_container_health = "One or more monitored auxiliary services were unhealthy or not running."
            $details.aux_status = "degraded"
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

        $pgResult = Invoke-PostgresSettingsQuery `
            -DeployPath $DeployPath `
            -Query @"
SELECT key
FROM settings
WHERE namespace = 'models'
ORDER BY key;
"@
        if ($pgResult.ExitCode -eq 0) {
            $keys = @(
                $pgResult.Text -split "`r?`n" | Where-Object { $_ -and $_.Trim() -ne "" } | ForEach-Object { $_.Trim() }
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
        } else {
            $details.postgres_probe_error = $pgResult.Text
            $checks.postgres_model_keys = $false
        }

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
            -DeployPath $DeployPath `
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
