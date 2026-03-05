#Requires -Version 5.1

<#
.SYNOPSIS
    Check Zetherion AI container status

.DESCRIPTION
    This script checks the status of all Zetherion AI Docker containers
    and services for the current blue/green runtime topology.

.EXAMPLE
    .\status.ps1
    Check container status
#>

$ErrorActionPreference = "SilentlyContinue"

# Helper functions
function Write-Success { param([string]$Text) Write-Host "[OK] $Text" -ForegroundColor Green }
function Write-Failure { param([string]$Text) Write-Host "[ERROR] $Text" -ForegroundColor Red }
function Write-Warning-Message { param([string]$Text) Write-Host "[WARNING] $Text" -ForegroundColor Yellow }
function Write-Info-Message { param([string]$Text) Write-Host "[INFO] $Text" -ForegroundColor Cyan }
function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host "  $Text" -ForegroundColor Blue
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host ""
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

function Get-ContainerState {
    param([string]$ContainerName)

    $exists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^$ContainerName$" -Quiet
    if (-not $exists) {
        return [pscustomobject]@{
            Exists = $false
            Running = $false
            State = "missing"
            Health = "missing"
        }
    }

    $inspect = docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}" $ContainerName 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $inspect) {
        return [pscustomobject]@{
            Exists = $true
            Running = $false
            State = "unknown"
            Health = "unknown"
        }
    }

    $parts = $inspect -split "\|"
    $state = if ($parts.Count -gt 0) { $parts[0].Trim() } else { "unknown" }
    $health = if ($parts.Count -gt 1) { $parts[1].Trim() } else { "unknown" }

    return [pscustomobject]@{
        Exists = $true
        Running = ($state -eq "running")
        State = $state
        Health = $health
    }
}

function Test-ServiceContainer {
    param(
        [string]$ContainerName,
        [string]$Label
    )

    $state = Get-ContainerState -ContainerName $ContainerName
    if (-not $state.Exists) {
        Write-Failure "$Label container not found ($ContainerName)"
        return $false
    }

    if (-not $state.Running) {
        Write-Warning-Message "$Label is not running (state: $($state.State))"
        return $false
    }

    if ($state.Health -eq "healthy" -or $state.Health -eq "no-healthcheck") {
        Write-Success "$Label is running ($($state.Health))"
        return $true
    }

    if ($state.Health -eq "starting") {
        Write-Info-Message "$Label is starting"
        return $false
    }

    Write-Warning-Message "$Label is running but unhealthy (health: $($state.Health))"
    return $false
}

Write-Header "Zetherion AI Status"

# Qdrant host check
Write-Info-Message "Checking Qdrant endpoint..."
$qdrantContainer = Get-ContainerState -ContainerName "zetherion-ai-qdrant"
if ($qdrantContainer.Running) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Success "Qdrant endpoint is healthy"
        }
        else {
            Write-Warning-Message "Qdrant endpoint returned status $($response.StatusCode)"
        }
    }
    catch {
        Write-Warning-Message "Qdrant container is running but localhost health probe failed"
    }
}
else {
    Write-Warning-Message "Qdrant container is not running"
}

Write-Host ""

# Ollama Router check
Write-Info-Message "Checking Ollama Router endpoint..."
$routerContainer = Get-ContainerState -ContainerName "zetherion-ai-ollama-router"
if ($routerContainer.Running) {
    try {
        $routerHealth = docker exec zetherion-ai-ollama-router curl -s http://localhost:11434/api/tags 2>&1
        if ($routerHealth -match "models") {
            Write-Success "Ollama Router endpoint is healthy"
        }
        else {
            Write-Warning-Message "Ollama Router health probe did not return model list"
        }
    }
    catch {
        Write-Warning-Message "Ollama Router container is running but probe failed"
    }
}
else {
    Write-Warning-Message "Ollama Router container is not running"
}

Write-Host ""

# Ollama Generation check
Write-Info-Message "Checking Ollama Generation endpoint..."
$ollamaContainer = Get-ContainerState -ContainerName "zetherion-ai-ollama"
if ($ollamaContainer.Running) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Success "Ollama Generation endpoint is healthy"
        }
        else {
            Write-Warning-Message "Ollama Generation endpoint returned status $($response.StatusCode)"
        }
    }
    catch {
        Write-Warning-Message "Ollama Generation container is running but localhost probe failed"
    }
}
else {
    Write-Warning-Message "Ollama Generation container is not running"
}

Write-Host ""

Write-Info-Message "Checking core blue/green runtime containers..."
$coreServices = @(
    @{ Name = "zetherion-ai-postgres"; Label = "PostgreSQL" },
    @{ Name = "zetherion-ai-qdrant"; Label = "Qdrant" },
    @{ Name = "zetherion-ai-ollama"; Label = "Ollama Generation" },
    @{ Name = "zetherion-ai-ollama-router"; Label = "Ollama Router" },
    @{ Name = "zetherion-ai-traefik"; Label = "Traefik" },
    @{ Name = "zetherion-ai-skills-blue"; Label = "Skills Blue" },
    @{ Name = "zetherion-ai-skills-green"; Label = "Skills Green" },
    @{ Name = "zetherion-ai-api-blue"; Label = "API Blue" },
    @{ Name = "zetherion-ai-api-green"; Label = "API Green" },
    @{ Name = "zetherion-ai-cgs-gateway-blue"; Label = "CGS Gateway Blue" },
    @{ Name = "zetherion-ai-cgs-gateway-green"; Label = "CGS Gateway Green" },
    @{ Name = "zetherion-ai-updater"; Label = "Updater" },
    @{ Name = "zetherion-ai-dev-agent"; Label = "Dev Agent" },
    @{ Name = "zetherion-ai-bot"; Label = "Bot" }
)

$allCoreHealthy = $true
foreach ($service in $coreServices) {
    if (-not (Test-ServiceContainer -ContainerName $service.Name -Label $service.Label)) {
        $allCoreHealthy = $false
    }
}

Write-Host ""

$optionalServices = @()
$cloudflareToken = Get-EnvValueFromFile -Path ".env" -Key "CLOUDFLARE_TUNNEL_TOKEN"
if ($cloudflareToken.Trim()) {
    $optionalServices += @{ Name = "zetherion-ai-cloudflared"; Label = "Cloudflared" }
}

$whatsappSigningSecret = Get-EnvValueFromFile -Path ".env" -Key "WHATSAPP_BRIDGE_SIGNING_SECRET"
$whatsappStateKey = Get-EnvValueFromFile -Path ".env" -Key "WHATSAPP_BRIDGE_STATE_KEY"
$whatsappTenantId = Get-EnvValueFromFile -Path ".env" -Key "WHATSAPP_BRIDGE_TENANT_ID"
$whatsappIngestUrl = Get-EnvValueFromFile -Path ".env" -Key "WHATSAPP_BRIDGE_INGEST_URL"
if (
    $whatsappSigningSecret.Trim() -and
    $whatsappStateKey.Trim() -and
    $whatsappTenantId.Trim() -and
    $whatsappIngestUrl.Trim()
) {
    $optionalServices += @{ Name = "zetherion-ai-whatsapp-bridge"; Label = "WhatsApp Bridge" }
}

$optionalFailures = 0
if ($optionalServices.Count -gt 0) {
    Write-Info-Message "Checking configured optional runtime containers..."
    foreach ($service in $optionalServices) {
        if (-not (Test-ServiceContainer -ContainerName $service.Name -Label $service.Label)) {
            $optionalFailures += 1
        }
    }
    Write-Host ""
}
else {
    Write-Info-Message "No optional services configured for strict checks (cloudflared/whatsapp bridge)."
    Write-Host ""
}

# Overall status
Write-Info-Message "Overall Status:"
if ($allCoreHealthy) {
    Write-Success "Core runtime is operational"
    if ($optionalFailures -gt 0) {
        Write-Warning-Message "Optional configured services have failures: $optionalFailures"
    }
}
else {
    Write-Warning-Message "Core runtime is not fully healthy"
    Write-Host ""
    Write-Info-Message "To start Zetherion AI, run: .\start.ps1"
}

Write-Host ""

# Show container list
Write-Info-Message "Container Summary:"
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | Select-String "zetherion|NAMES"

Write-Host ""
