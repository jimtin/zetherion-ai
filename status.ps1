#Requires -Version 5.1

<#
.SYNOPSIS
    Check Zetherion AI container status

.DESCRIPTION
    This script checks the status of all Zetherion AI Docker containers
    and services.

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

Write-Header "Zetherion AI Status"

# Check Qdrant
Write-Info-Message "Checking Qdrant..."
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-qdrant$" -Quiet

if ($qdrantRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Success "Qdrant is running and healthy"

            # Get collection count
            try {
                $collections = Invoke-RestMethod -Uri "http://localhost:6333/collections" -UseBasicParsing -TimeoutSec 2
                $collectionCount = $collections.result.collections.Count
                Write-Host "    Collections: $collectionCount"
            }
            catch {
                Write-Host "    Collections: Unable to retrieve"
            }
        }
        else {
            Write-Warning-Message "Qdrant container is running but not responding"
        }
    }
    catch {
        Write-Warning-Message "Qdrant container is running but not responding"
    }
}
else {
    $qdrantExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-qdrant$" -Quiet

    if ($qdrantExists) {
        Write-Warning-Message "Qdrant container exists but is not running"
    }
    else {
        Write-Failure "Qdrant container not found"
    }
}

Write-Host ""

# Check Ollama Router Container (for fast routing)
Write-Info-Message "Checking Ollama Router Container..."
$ollamaRouterRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-ollama-router$" -Quiet

if ($ollamaRouterRunning) {
    try {
        # Router container is internal only (no port exposed to host), check via docker exec
        $routerHealth = docker exec zetherion-ai-ollama-router curl -s http://localhost:11434/api/tags 2>&1
        if ($routerHealth -match "models") {
            Write-Success "Ollama Router is running and healthy"

            # Get model list
            try {
                $models = $routerHealth | ConvertFrom-Json
                $modelCount = $models.models.Count
                Write-Host "    Models: $modelCount (router models)"
                if ($modelCount -gt 0) {
                    foreach ($model in $models.models) {
                        Write-Host "      - $($model.name)"
                    }
                }
            }
            catch {
                Write-Host "    Models: Unable to retrieve"
            }
        }
        else {
            Write-Warning-Message "Ollama Router container is running but not responding"
        }
    }
    catch {
        Write-Warning-Message "Ollama Router container is running but not responding"
    }
}
else {
    $ollamaRouterExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-ollama-router$" -Quiet

    if ($ollamaRouterExists) {
        Write-Warning-Message "Ollama Router container exists but is not running"
    }
    else {
        Write-Info-Message "Ollama Router container not found (optional, used with ROUTER_BACKEND=ollama)"
    }
}

Write-Host ""

# Check Ollama Generation Container (for complex queries + embeddings)
Write-Info-Message "Checking Ollama Generation Container..."
$ollamaRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-ollama$" -Quiet

if ($ollamaRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Success "Ollama Generation is running and healthy"

            # Get model list
            try {
                $models = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2
                $modelCount = $models.models.Count
                Write-Host "    Models: $modelCount (generation + embedding models)"
                if ($modelCount -gt 0) {
                    foreach ($model in $models.models) {
                        Write-Host "      - $($model.name)"
                    }
                }
            }
            catch {
                Write-Host "    Models: Unable to retrieve"
            }
        }
        else {
            Write-Warning-Message "Ollama Generation container is running but not responding"
        }
    }
    catch {
        Write-Warning-Message "Ollama Generation container is running but not responding"
    }
}
else {
    $ollamaExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-ollama$" -Quiet

    if ($ollamaExists) {
        Write-Warning-Message "Ollama Generation container exists but is not running"
    }
    else {
        Write-Info-Message "Ollama Generation container not found (optional, used with ROUTER_BACKEND=ollama)"
    }
}

Write-Host ""

# Check Skills Service
Write-Info-Message "Checking Skills service..."
$skillsRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-skills$" -Quiet

if ($skillsRunning) {
    $skillsHealth = docker inspect --format='{{.State.Health.Status}}' zetherion-ai-skills 2>&1
    if ($skillsHealth -eq "healthy") {
        Write-Success "Skills service is running and healthy"
    }
    elseif ($skillsHealth -eq "starting") {
        Write-Info-Message "Skills service is starting..."
    }
    else {
        Write-Warning-Message "Skills service is running but unhealthy"
    }
}
else {
    $skillsExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-skills$" -Quiet

    if ($skillsExists) {
        Write-Warning-Message "Skills container exists but is not running"
    }
    else {
        Write-Failure "Skills container not found"
    }
}

Write-Host ""

# Check Bot
Write-Info-Message "Checking bot..."
$botRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-bot$" -Quiet

if ($botRunning) {
    $botHealth = docker inspect --format='{{.State.Health.Status}}' zetherion-ai-bot 2>&1
    if ($botHealth -eq "healthy") {
        Write-Success "Bot is running and healthy"

        # Get uptime
        $startTime = docker inspect --format='{{.State.StartedAt}}' zetherion-ai-bot 2>&1
        if ($startTime) {
            try {
                $start = [DateTime]::Parse($startTime)
                $uptime = (Get-Date) - $start
                $uptimeString = "{0:dd}d {0:hh}h {0:mm}m {0:ss}s" -f $uptime
                Write-Host "    Uptime: $uptimeString"
            }
            catch {}
        }
    }
    elseif ($botHealth -eq "starting") {
        Write-Info-Message "Bot is starting..."
    }
    else {
        Write-Warning-Message "Bot is running but unhealthy"
    }
}
else {
    $botExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-bot$" -Quiet

    if ($botExists) {
        Write-Warning-Message "Bot container exists but is not running"
    }
    else {
        Write-Failure "Bot container not found"
    }
}

Write-Host ""

# Overall status
Write-Info-Message "Overall Status:"
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-qdrant$" -Quiet
$botRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-bot$" -Quiet
$skillsRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion-ai-skills$" -Quiet

if ($qdrantRunning -and $botRunning -and $skillsRunning) {
    Write-Success "Zetherion AI is fully operational"
}
else {
    Write-Warning-Message "Zetherion AI is not fully running"
    Write-Host ""
    Write-Info-Message "To start Zetherion AI, run: .\start.ps1"
}

Write-Host ""

# Show container list
Write-Info-Message "Container Summary:"
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | Select-String "zetherion|NAMES"

Write-Host ""
