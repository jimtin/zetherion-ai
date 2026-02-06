# Zetherion AI Stop Script for Windows
# Run as: powershell -ExecutionPolicy Bypass -File stop.ps1

#Requires -Version 7.0

$ErrorActionPreference = "Stop"

# Helper functions
function Write-Success { param([string]$message) Write-Host "[OK] $message" -ForegroundColor Green }
function Write-Warning-Message { param([string]$message) Write-Host "[WARNING] $message" -ForegroundColor Yellow }
function Write-Info { param([string]$message) Write-Host "[INFO] $message" -ForegroundColor Cyan }

Write-Host ""
Write-Host "=================================================" -ForegroundColor Blue
Write-Host "  Stopping Zetherion AI" -ForegroundColor Blue
Write-Host "=================================================" -ForegroundColor Blue
Write-Host ""

# Stop Ollama container
Write-Info "Stopping Ollama container..."
$ErrorActionPreference = "SilentlyContinue"
$ollamaRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-ollama$" -Quiet
$ErrorActionPreference = "Stop"

if ($ollamaRunning) {
    docker stop zetherion_ai-ollama
    Write-Success "Ollama container stopped"
} else {
    Write-Warning-Message "Ollama container not running"
}

# Stop Qdrant container
Write-Info "Stopping Qdrant container..."
$ErrorActionPreference = "SilentlyContinue"
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet
$ErrorActionPreference = "Stop"

if ($qdrantRunning) {
    docker stop zetherion_ai-qdrant
    Write-Success "Qdrant container stopped"
} else {
    Write-Warning-Message "Qdrant container not running"
}

# Kill any running bot processes
Write-Info "Checking for running bot processes..."
$botProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*zetherion_ai*"
}

if ($botProcesses) {
    $botProcesses | Stop-Process -Force
    Write-Success "Bot processes stopped"
} else {
    Write-Warning-Message "No bot processes found"
}

Write-Host ""
Write-Success "Zetherion AI stopped successfully"
Write-Host ""
