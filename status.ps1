# Zetherion AI Status Script for Windows
# Run as: powershell -ExecutionPolicy Bypass -File status.ps1

#Requires -Version 7.0

$ErrorActionPreference = "Stop"

# Helper functions
function Write-Success { param([string]$message) Write-Host "[OK] $message" -ForegroundColor Green }
function Write-Error-Message { param([string]$message) Write-Host "[ERROR] $message" -ForegroundColor Red }
function Write-Warning-Message { param([string]$message) Write-Host "[WARNING] $message" -ForegroundColor Yellow }
function Write-Info { param([string]$message) Write-Host "[INFO] $message" -ForegroundColor Cyan }

Write-Host ""
Write-Host "=================================================" -ForegroundColor Blue
Write-Host "  Zetherion AI Status" -ForegroundColor Blue
Write-Host "=================================================" -ForegroundColor Blue
Write-Host ""

# Check Qdrant
Write-Info "Checking Qdrant..."
$ErrorActionPreference = "SilentlyContinue"
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet
$ErrorActionPreference = "Stop"

if ($qdrantRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Success "Qdrant is running and healthy"

            # Get collection count
            try {
                $collections = Invoke-RestMethod -Uri "http://localhost:6333/collections" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
                $collectionCount = $collections.result.collections.Count
                Write-Host "    Collections: $collectionCount"
            } catch {
                Write-Host "    Collections: Unable to retrieve"
            }
        } else {
            Write-Warning-Message "Qdrant container is running but not responding"
        }
    } catch {
        Write-Warning-Message "Qdrant container is running but not responding"
    }
} else {
    $ErrorActionPreference = "SilentlyContinue"
    $qdrantExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet
    $ErrorActionPreference = "Stop"

    if ($qdrantExists) {
        Write-Warning-Message "Qdrant container exists but is not running"
    } else {
        Write-Error-Message "Qdrant container not found"
    }
}

Write-Host ""

# Check Ollama
Write-Info "Checking Ollama..."
$ErrorActionPreference = "SilentlyContinue"
$ollamaRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-ollama$" -Quiet
$ErrorActionPreference = "Stop"

if ($ollamaRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Success "Ollama is running and healthy"

            # Get model list
            try {
                $models = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
                $modelCount = $models.models.Count
                Write-Host "    Models: $modelCount"
            } catch {
                Write-Host "    Models: Unable to retrieve"
            }
        } else {
            Write-Warning-Message "Ollama container is running but not responding"
        }
    } catch {
        Write-Warning-Message "Ollama container is running but not responding"
    }
} else {
    $ErrorActionPreference = "SilentlyContinue"
    $ollamaExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-ollama$" -Quiet
    $ErrorActionPreference = "Stop"

    if ($ollamaExists) {
        Write-Warning-Message "Ollama container exists but is not running"
    } else {
        Write-Info "Ollama container not found (optional)"
    }
}

Write-Host ""

# Check bot process
Write-Info "Checking bot process..."
$botProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*zetherion_ai*"
}

if ($botProcesses) {
    $botProcess = $botProcesses[0]
    $pid = $botProcess.Id
    Write-Success "Bot is running (PID: $pid)"

    # Calculate uptime
    $uptime = (Get-Date) - $botProcess.StartTime
    $uptimeString = "{0:dd}d {0:hh}h {0:mm}m {0:ss}s" -f $uptime
    Write-Host "    Uptime: $uptimeString"
} else {
    Write-Error-Message "Bot is not running"
}

Write-Host ""

# Check virtual environment
Write-Info "Checking virtual environment..."
if (Test-Path .venv) {
    Write-Success "Virtual environment exists"
} else {
    Write-Error-Message "Virtual environment not found"
}

Write-Host ""

# Check .env file
Write-Info "Checking configuration..."
if (Test-Path .env) {
    Write-Success ".env file exists"

    # Load .env and check required variables
    $envContent = Get-Content .env
    $envVars = @{}
    foreach ($line in $envContent) {
        if ($line -match "^([^#][^=]+)=(.*)$") {
            $envVars[$matches[1].Trim()] = $matches[2].Trim()
        }
    }

    # Check required variables
    if ($envVars["DISCORD_TOKEN"]) {
        Write-Success "Discord token configured"
    } else {
        Write-Error-Message "Discord token missing"
    }

    if ($envVars["GEMINI_API_KEY"]) {
        Write-Success "Gemini API key configured"
    } else {
        Write-Error-Message "Gemini API key missing"
    }

    # Check optional variables
    if ($envVars["ANTHROPIC_API_KEY"]) {
        Write-Success "Anthropic API key configured (optional)"
    } else {
        Write-Warning-Message "Anthropic API key not configured (optional)"
    }

    if ($envVars["OPENAI_API_KEY"]) {
        Write-Success "OpenAI API key configured (optional)"
    } else {
        Write-Warning-Message "OpenAI API key not configured (optional)"
    }

    # Show router backend
    $routerBackend = if ($envVars["ROUTER_BACKEND"]) { $envVars["ROUTER_BACKEND"] } else { "gemini" }
    Write-Info "Router backend: $routerBackend"

} else {
    Write-Error-Message ".env file not found"
}

Write-Host ""

# Overall status
Write-Info "Overall Status:"
$ErrorActionPreference = "SilentlyContinue"
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet
$ErrorActionPreference = "Stop"

$botProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*zetherion_ai*"
}

if ($qdrantRunning -and $botProcesses) {
    Write-Success "Zetherion AI is fully operational"
} else {
    Write-Warning-Message "Zetherion AI is not fully running"
    Write-Host ""
    Write-Info "To start Zetherion AI, run: .\start.ps1"
}

Write-Host ""
