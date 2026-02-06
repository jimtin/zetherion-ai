#Requires -Version 5.1

<#
.SYNOPSIS
    Stop Zetherion AI Docker containers

.DESCRIPTION
    This script stops all Zetherion AI Docker containers gracefully.

.EXAMPLE
    .\stop.ps1
    Stop all containers
#>

$ErrorActionPreference = "Stop"

# Helper functions
function Write-Success { param([string]$Text) Write-Host "[OK] $Text" -ForegroundColor Green }
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

Write-Header "Stopping Zetherion AI"

Write-Info-Message "Stopping Docker containers..."
docker-compose down --timeout 30

if ($LASTEXITCODE -eq 0) {
    Write-Success "All containers stopped"
    Write-Host ""
    Write-Info-Message "To start again:  .\start.ps1"
    Write-Info-Message "To view status:  .\status.ps1"
    Write-Host ""
}
else {
    Write-Warning-Message "Failed to stop some containers"
    Write-Info-Message "Check status with: docker-compose ps"
}
