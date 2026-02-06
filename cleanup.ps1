#Requires -Version 5.1

<#
.SYNOPSIS
    Complete cleanup and reset of Zetherion AI

.DESCRIPTION
    This script removes all Docker containers, volumes, images, and optionally
    configuration files and old local Python artifacts. Use this for a complete
    fresh start.

.PARAMETER KeepData
    Keep Qdrant database and Ollama models (preserve data volumes)

.PARAMETER KeepConfig
    Keep .env configuration file

.PARAMETER RemoveOldVersion
    Also remove old local Python installation artifacts (.venv, __pycache__, etc.)

.EXAMPLE
    .\cleanup.ps1
    Complete cleanup (will prompt for confirmation)

.EXAMPLE
    .\cleanup.ps1 -KeepData
    Remove containers but keep data volumes

.EXAMPLE
    .\cleanup.ps1 -KeepConfig
    Remove everything but keep .env file

.EXAMPLE
    .\cleanup.ps1 -RemoveOldVersion
    Also clean up old local Python artifacts
#>

param(
    [switch]$KeepData = $false,
    [switch]$KeepConfig = $false,
    [switch]$RemoveOldVersion = $false
)

$ErrorActionPreference = "Stop"

# Helper functions
function Write-Success { param([string]$Text) Write-Host "[OK] $Text" -ForegroundColor Green }
function Write-Failure { param([string]$Text) Write-Host "[ERROR] $Text" -ForegroundColor Red }
function Write-Warning-Message { param([string]$Text) Write-Host "[WARNING] $Text" -ForegroundColor Yellow }
function Write-Info-Message { param([string]$Text) Write-Host "[INFO] $Text" -ForegroundColor Cyan }
function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "  $Text" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host ""
}

Write-Header "Zetherion AI - Complete Cleanup"

Write-Warning-Message "This will remove all Docker containers, images, and optionally data!"
Write-Host ""

# Summary of what will be removed
Write-Host "The following will be removed:" -ForegroundColor Yellow
Write-Host "  - All Zetherion AI Docker containers" -ForegroundColor Gray
Write-Host "  - All Zetherion AI Docker images" -ForegroundColor Gray

if (-not $KeepData) {
    Write-Host "  - Qdrant database (all stored memories)" -ForegroundColor Gray
    Write-Host "  - Ollama models (will need to re-download)" -ForegroundColor Gray
}
else {
    Write-Host "  - Data volumes will be KEPT" -ForegroundColor Green
}

if (-not $KeepConfig) {
    Write-Host "  - .env configuration file" -ForegroundColor Gray
}
else {
    Write-Host "  - .env file will be KEPT" -ForegroundColor Green
}

if ($RemoveOldVersion) {
    Write-Host "  - Old local Python artifacts (.venv, __pycache__)" -ForegroundColor Gray
}

Write-Host ""
$confirm = Read-Host "Are you sure you want to continue? (yes/no)"
if ($confirm -ne "yes") {
    Write-Info-Message "Cleanup cancelled"
    exit 0
}

Write-Host ""
Write-Info-Message "Starting cleanup..."
Write-Host ""

# ============================================================
# STEP 1: STOP AND REMOVE CONTAINERS
# ============================================================

Write-Info-Message "Step 1: Stopping and removing containers..."

try {
    # Stop all containers
    $containers = docker ps -a --format "{{.Names}}" | Select-String "zetherion-ai"

    if ($containers) {
        Write-Info-Message "Stopping containers..."
        docker-compose down --timeout 30 2>&1 | Out-Null
        Write-Success "Containers stopped and removed"
    }
    else {
        Write-Info-Message "No containers found"
    }
}
catch {
    Write-Warning-Message "Error stopping containers: $_"
}

# ============================================================
# STEP 2: REMOVE VOLUMES
# ============================================================

if (-not $KeepData) {
    Write-Info-Message "Step 2: Removing data volumes..."

    Write-Warning-Message "This will delete all stored data (memories, models)"
    $confirmData = Read-Host "Confirm data deletion? (yes/no)"

    if ($confirmData -eq "yes") {
        try {
            # Remove named volumes
            $volumes = docker volume ls --format "{{.Name}}" | Select-String "zetherion"

            if ($volumes) {
                foreach ($volume in $volumes) {
                    Write-Info-Message "Removing volume: $volume"
                    docker volume rm $volume 2>&1 | Out-Null
                }
                Write-Success "Data volumes removed"
            }
            else {
                Write-Info-Message "No volumes found"
            }
        }
        catch {
            Write-Warning-Message "Error removing volumes: $_"
        }
    }
    else {
        Write-Info-Message "Skipping data volume removal"
    }
}
else {
    Write-Info-Message "Step 2: Keeping data volumes (--KeepData)"
}

# ============================================================
# STEP 3: REMOVE DOCKER IMAGES
# ============================================================

Write-Info-Message "Step 3: Removing Docker images..."

try {
    $images = docker images --format "{{.Repository}}:{{.Tag}}" | Select-String "zetherion-ai"

    if ($images) {
        foreach ($image in $images) {
            Write-Info-Message "Removing image: $image"
            docker rmi $image -f 2>&1 | Out-Null
        }
        Write-Success "Docker images removed"
    }
    else {
        Write-Info-Message "No images found"
    }
}
catch {
    Write-Warning-Message "Error removing images: $_"
}

# ============================================================
# STEP 4: REMOVE CONFIGURATION
# ============================================================

if (-not $KeepConfig) {
    Write-Info-Message "Step 4: Removing configuration..."

    if (Test-Path ".env") {
        Write-Warning-Message "This will delete your .env configuration (API keys, etc.)"
        $confirmConfig = Read-Host "Confirm .env deletion? (yes/no)"

        if ($confirmConfig -eq "yes") {
            Remove-Item ".env" -Force
            Write-Success ".env file removed"
        }
        else {
            Write-Info-Message "Keeping .env file"
        }
    }
    else {
        Write-Info-Message "No .env file found"
    }
}
else {
    Write-Info-Message "Step 4: Keeping .env file (--KeepConfig)"
}

# ============================================================
# STEP 5: CLEAN UP LOCAL ARTIFACTS
# ============================================================

Write-Info-Message "Step 5: Cleaning up local build artifacts..."

try {
    # Remove Python cache
    if (Test-Path "__pycache__") {
        Remove-Item -Recurse -Force "__pycache__"
        Write-Success "Removed __pycache__"
    }

    # Remove pytest cache
    if (Test-Path ".pytest_cache") {
        Remove-Item -Recurse -Force ".pytest_cache"
        Write-Success "Removed .pytest_cache"
    }

    # Remove local logs (unless in Docker volume)
    if (Test-Path "logs" -and (Test-Path "logs/*.log")) {
        $confirmLogs = Read-Host "Remove local log files? (yes/no)"
        if ($confirmLogs -eq "yes") {
            Remove-Item "logs/*.log" -Force
            Write-Success "Removed log files"
        }
    }
}
catch {
    Write-Warning-Message "Error cleaning artifacts: $_"
}

# ============================================================
# STEP 6: REMOVE OLD LOCAL PYTHON VERSION (Optional)
# ============================================================

if ($RemoveOldVersion) {
    Write-Info-Message "Step 6: Removing old local Python artifacts..."

    try {
        # Remove virtual environment
        if (Test-Path ".venv") {
            Write-Info-Message "Removing .venv directory..."
            Remove-Item -Recurse -Force ".venv"
            Write-Success "Removed .venv"
        }

        # Remove Python cache in src/
        $pycacheDirs = Get-ChildItem -Path "src" -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
        if ($pycacheDirs) {
            foreach ($dir in $pycacheDirs) {
                Remove-Item -Recurse -Force $dir.FullName
            }
            Write-Success "Removed Python cache directories"
        }

        # Remove .pyc files
        $pycFiles = Get-ChildItem -Path "src" -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue
        if ($pycFiles) {
            foreach ($file in $pycFiles) {
                Remove-Item -Force $file.FullName
            }
            Write-Success "Removed .pyc files"
        }

        Write-Success "Old local Python artifacts removed"
    }
    catch {
        Write-Warning-Message "Error removing old artifacts: $_"
    }
}
else {
    Write-Info-Message "Step 6: Skipping old version cleanup (use -RemoveOldVersion to enable)"
}

# ============================================================
# SUMMARY
# ============================================================

Write-Host ""
Write-Header "Cleanup Complete"

Write-Success "Zetherion AI has been cleaned up"
Write-Host ""

Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. To reinstall: .\start.ps1" -ForegroundColor Cyan
if (-not $KeepConfig) {
    Write-Host "  2. You'll need to reconfigure API keys" -ForegroundColor Cyan
}
if (-not $KeepData) {
    Write-Host "  3. Ollama models will need to be re-downloaded" -ForegroundColor Cyan
}

Write-Host ""

# Show what remains
Write-Info-Message "Remaining Docker resources:"
$remainingContainers = docker ps -a --format "{{.Names}}" | Select-String "zetherion" | Measure-Object
$remainingVolumes = docker volume ls --format "{{.Name}}" | Select-String "zetherion" | Measure-Object
$remainingImages = docker images --format "{{.Repository}}" | Select-String "zetherion-ai" | Measure-Object

Write-Host "  Containers: $($remainingContainers.Count)"
Write-Host "  Volumes: $($remainingVolumes.Count)"
Write-Host "  Images: $($remainingImages.Count)"

if ($KeepConfig -and (Test-Path ".env")) {
    Write-Host "  Config: .env file preserved" -ForegroundColor Green
}

Write-Host ""
