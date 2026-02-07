#Requires -Version 5.1
#Requires -RunAsAdministrator

<#
.SYNOPSIS
    Zetherion AI - Fully Automated Docker Deployment for Windows

.DESCRIPTION
    This script sets up and runs Zetherion AI entirely in Docker containers.
    It handles all prerequisites, configuration, and deployment automatically.

.PARAMETER SkipHardwareAssessment
    Skip hardware assessment and use default Ollama model

.PARAMETER ForceRebuild
    Force rebuild of Docker images even if they exist

.EXAMPLE
    .\start.ps1
    Standard deployment with hardware assessment

.EXAMPLE
    .\start.ps1 -SkipHardwareAssessment
    Deploy without hardware assessment

.EXAMPLE
    .\start.ps1 -ForceRebuild
    Force rebuild all Docker images
#>

param(
    [switch]$SkipHardwareAssessment = $false,
    [switch]$ForceRebuild = $false
)

$ErrorActionPreference = "Stop"

# ============================================================
# HELPER FUNCTIONS
# ============================================================

function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host "  $Text" -ForegroundColor Blue
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host ""
}

function Write-Phase {
    param([string]$Text)
    Write-Host ""
    Write-Host "[PHASE] $Text" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success {
    param([string]$Text)
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-Failure {
    param([string]$Text)
    Write-Host "[ERROR] $Text" -ForegroundColor Red
}

function Write-Warning-Message {
    param([string]$Text)
    Write-Host "[WARNING] $Text" -ForegroundColor Yellow
}

function Write-Info-Message {
    param([string]$Text)
    Write-Host "[INFO] $Text" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Command)
    try {
        if (Get-Command $Command -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    catch {
        return $false
    }
    return $false
}

function Get-DiskFreeSpaceGB {
    $drive = (Get-Location).Drive
    $freeSpace = (Get-PSDrive $drive.Name).Free
    return [math]::Round($freeSpace / 1GB, 1)
}

# ============================================================
# PHASE 1: PREREQUISITES CHECK & AUTO-INSTALL
# ============================================================

Write-Header "Zetherion AI - Automated Docker Deployment"

Write-Phase "Phase 1/7: Checking Prerequisites"

# Check Docker Desktop
Write-Info-Message "Checking Docker Desktop..."
if (-not (Test-Command "docker")) {
    Write-Warning-Message "Docker Desktop not found"

    $install = Read-Host "Install Docker Desktop? (Y/n)"
    if ($install -eq "" -or $install -eq "y" -or $install -eq "Y") {
        Write-Info-Message "Installing Docker Desktop via winget..."
        try {
            winget install Docker.DockerDesktop --silent --accept-source-agreements --accept-package-agreements
            Write-Success "Docker Desktop installed"
            Write-Warning-Message "Please restart this script after Docker Desktop starts"
            exit 0
        }
        catch {
            Write-Failure "Failed to install Docker Desktop: $_"
            Write-Info-Message "Please install manually from: https://www.docker.com/products/docker-desktop"
            exit 1
        }
    }
    else {
        Write-Failure "Docker Desktop is required"
        Write-Info-Message "Install from: https://www.docker.com/products/docker-desktop"
        exit 1
    }
}

Write-Success "Docker Desktop is installed"

# Check if Docker daemon is running
Write-Info-Message "Checking Docker daemon..."
try {
    docker ps | Out-Null
    Write-Success "Docker daemon is running"
}
catch {
    Write-Warning-Message "Docker daemon is not running"
    Write-Info-Message "Starting Docker Desktop..."

    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    Write-Info-Message "Waiting for Docker to start (max 60 seconds)..."
    $maxWait = 60
    $waited = 0
    while ($waited -lt $maxWait) {
        Start-Sleep -Seconds 2
        $waited += 2
        try {
            docker ps | Out-Null
            Write-Success "Docker daemon is now running"
            break
        }
        catch {
            Write-Host "." -NoNewline
        }
    }

    if ($waited -ge $maxWait) {
        Write-Failure "Docker failed to start within $maxWait seconds"
        Write-Info-Message "Please start Docker Desktop manually and try again"
        exit 1
    }
}

# Check Git
Write-Info-Message "Checking Git..."
if (-not (Test-Command "git")) {
    Write-Warning-Message "Git not found"

    $install = Read-Host "Install Git? (Y/n)"
    if ($install -eq "" -or $install -eq "y" -or $install -eq "Y") {
        Write-Info-Message "Installing Git via winget..."
        try {
            winget install Git.Git --silent --accept-source-agreements --accept-package-agreements
            Write-Success "Git installed"
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        }
        catch {
            Write-Failure "Failed to install Git: $_"
            Write-Info-Message "Please install manually from: https://git-scm.com/download/win"
            exit 1
        }
    }
    else {
        Write-Warning-Message "Git not installed (optional for now)"
    }
}
else {
    Write-Success "Git is installed"
}

# Check disk space
$freeSpace = Get-DiskFreeSpaceGB
Write-Info-Message "Disk space: $freeSpace GB free"
if ($freeSpace -lt 20) {
    Write-Warning-Message "Low disk space (less than 20GB free)"
    Write-Warning-Message "Ollama models require 5-10GB of space"
}
else {
    Write-Success "Sufficient disk space available"
}

Write-Success "Prerequisites check complete"

# ============================================================
# PHASE 2: HARDWARE ASSESSMENT
# ============================================================

$hardwareAssessment = $null

if (-not $SkipHardwareAssessment) {
    Write-Phase "Phase 2/7: Hardware Assessment"

    Write-Info-Message "Building hardware assessment container..."
    try {
        docker build -t zetherion-ai-assess:distroless -f Dockerfile.assess . 2>&1 | Out-Null
        Write-Success "Assessment container built"
    }
    catch {
        Write-Warning-Message "Failed to build assessment container: $_"
        Write-Warning-Message "Skipping hardware assessment"
    }

    if ($?) {
        Write-Info-Message "Assessing system hardware..."
        try {
            $assessOutput = docker run --rm --entrypoint /usr/bin/python3.11 `
                zetherion-ai-assess:distroless /app/assess-system.py --json 2>&1

            if ($LASTEXITCODE -eq 0) {
                $hardwareAssessment = $assessOutput | ConvertFrom-Json

                # Display hardware info
                $hw = $hardwareAssessment.hardware
                $rec = $hardwareAssessment.recommendation

                Write-Host ""
                Write-Host "System Hardware:" -ForegroundColor White
                Write-Host "  CPU: $($hw.cpu_model)"
                if ($hw.cpu_count) {
                    Write-Host "  Cores: $($hw.cpu_count) ($($hw.cpu_threads) threads)"
                }
                if ($hw.ram_gb) {
                    Write-Host "  RAM: $($hw.ram_gb) GB total, $($hw.available_ram_gb) GB available"
                }
                Write-Host "  GPU: $($hw.gpu.name)"

                Write-Host ""
                Write-Host "Recommended Ollama Model:" -ForegroundColor White
                Write-Host "  Model: " -NoNewline
                Write-Host "$($rec.model)" -ForegroundColor Green
                Write-Host "  Size: $($rec.size_gb) GB download"
                Write-Host "  Quality: $($rec.quality)"
                Write-Host "  Speed: $($rec.inference_time)"
                Write-Host "  Reason: $($rec.reason)"

                if ($hardwareAssessment.warnings.Count -gt 0) {
                    Write-Host ""
                    Write-Host "Warnings:" -ForegroundColor Yellow
                    foreach ($warning in $hardwareAssessment.warnings) {
                        Write-Host "  ⚠ $warning" -ForegroundColor Yellow
                    }
                }

                Write-Success "Hardware assessment complete"
            }
            else {
                Write-Warning-Message "Hardware assessment failed, using defaults"
            }
        }
        catch {
            Write-Warning-Message "Failed to run hardware assessment: $_"
        }
    }
}
else {
    Write-Info-Message "Skipping hardware assessment (--SkipHardwareAssessment)"
}

# ============================================================
# PHASE 3: CONFIGURATION SETUP
# ============================================================

Write-Phase "Phase 3/7: Configuration Setup"

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Info-Message ".env file not found"
    Write-Info-Message "Starting interactive setup..."

    try {
        python scripts/interactive-setup.py
        if ($LASTEXITCODE -ne 0) {
            Write-Failure "Interactive setup failed"
            exit 1
        }
        Write-Success "Configuration created"
    }
    catch {
        Write-Failure "Failed to run interactive setup: $_"
        Write-Info-Message "Please create .env manually from .env.example"
        exit 1
    }
}
else {
    Write-Success ".env file exists"

    # Verify required keys
    $envContent = Get-Content .env
    $hasDiscordToken = $envContent | Select-String -Pattern "^DISCORD_TOKEN=.+"
    $hasGeminiKey = $envContent | Select-String -Pattern "^GEMINI_API_KEY=.+"

    if (-not $hasDiscordToken) {
        Write-Failure "DISCORD_TOKEN not set in .env"
        Write-Info-Message "Please configure .env or delete it to run setup again"
        exit 1
    }

    if (-not $hasGeminiKey) {
        Write-Failure "GEMINI_API_KEY not set in .env"
        Write-Info-Message "Please configure .env or delete it to run setup again"
        exit 1
    }

    Write-Success "Required configuration present"
}

# Get router backend from .env
$routerBackend = "gemini"  # default
$envContent = Get-Content .env
$routerLine = $envContent | Select-String -Pattern "^ROUTER_BACKEND="
if ($routerLine) {
    $routerBackend = ($routerLine -split "=")[1].Trim()
}

Write-Info-Message "Router backend: $routerBackend"

# ============================================================
# PHASE 4: DOCKER BUILD & DEPLOY
# ============================================================

Write-Phase "Phase 4/7: Docker Build & Deploy"

# Build images
if ($ForceRebuild) {
    Write-Info-Message "Force rebuild requested"
    docker-compose build --no-cache
}
else {
    Write-Info-Message "Building Docker images (if needed)..."
    docker-compose build
}

if ($LASTEXITCODE -ne 0) {
    Write-Failure "Docker build failed"
    exit 1
}

Write-Success "Docker images built"

# Start containers
Write-Info-Message "Starting containers..."
docker-compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Failure "Failed to start containers"
    exit 1
}

Write-Success "Containers started"

# Wait for health checks
Write-Info-Message "Waiting for services to become healthy..."
$maxWait = 120  # 2 minutes
$waited = 0
$services = @("zetherion-ai-qdrant", "zetherion-ai-skills", "zetherion-ai-bot")

while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 5
    $waited += 5

    $allHealthy = $true
    foreach ($service in $services) {
        $health = docker inspect --format='{{.State.Health.Status}}' $service 2>&1
        if ($health -ne "healthy") {
            $allHealthy = $false
            Write-Host "." -NoNewline
            break
        }
    }

    if ($allHealthy) {
        Write-Host ""
        Write-Success "All services are healthy"
        break
    }
}

if ($waited -ge $maxWait) {
    Write-Warning-Message "Services did not become healthy within $maxWait seconds"
    Write-Info-Message "Check logs with: docker-compose logs"
}

# ============================================================
# PHASE 5: MODEL DOWNLOAD (if Ollama)
# ============================================================

if ($routerBackend -eq "ollama") {
    Write-Phase "Phase 5/7: Ollama Model Download"
    Write-Info-Message "Dual-container architecture: router + generation containers"

    # Get router model from .env (small, fast model)
    $routerModel = "qwen2.5:0.5b"  # default
    $routerModelLine = $envContent | Select-String -Pattern "^OLLAMA_ROUTER_MODEL="
    if ($routerModelLine) {
        $routerModel = ($routerModelLine -split "=")[1].Trim()
    }

    # Get generation model from .env (larger, capable model)
    $generationModel = "qwen2.5:7b"  # default
    $genModelLine = $envContent | Select-String -Pattern "^OLLAMA_GENERATION_MODEL="
    if ($genModelLine) {
        $generationModel = ($genModelLine -split "=")[1].Trim()
    }

    # Get embedding model from .env
    $embeddingModel = "nomic-embed-text"  # default
    $embedModelLine = $envContent | Select-String -Pattern "^OLLAMA_EMBEDDING_MODEL="
    if ($embedModelLine) {
        $embeddingModel = ($embedModelLine -split "=")[1].Trim()
    }

    Write-Host ""
    Write-Info-Message "Router container model: $routerModel (fast classification)"
    Write-Info-Message "Generation container model: $generationModel (complex queries)"
    Write-Info-Message "Embedding model: $embeddingModel (vector embeddings)"
    Write-Host ""

    # Download router model to ollama-router container
    Write-Info-Message "Checking router model '$routerModel' on ollama-router container..."
    $routerModelExists = docker exec zetherion-ai-ollama-router ollama list 2>&1 | Select-String -Pattern $routerModel

    if ($routerModelExists) {
        Write-Success "Router model '$routerModel' already downloaded"
    }
    else {
        Write-Info-Message "Downloading router model '$routerModel'..."
        Write-Warning-Message "This is a small model (~500MB), should be quick"
        docker exec zetherion-ai-ollama-router ollama pull $routerModel

        if ($LASTEXITCODE -eq 0) {
            Write-Success "Router model downloaded successfully"
        }
        else {
            Write-Failure "Router model download failed"
            Write-Warning-Message "You can download it later with: docker exec zetherion-ai-ollama-router ollama pull $routerModel"
        }
    }

    # Download generation model to ollama container
    Write-Info-Message "Checking generation model '$generationModel' on ollama container..."
    $genModelExists = docker exec zetherion-ai-ollama ollama list 2>&1 | Select-String -Pattern $generationModel

    if ($genModelExists) {
        Write-Success "Generation model '$generationModel' already downloaded"
    }
    else {
        Write-Info-Message "Downloading generation model '$generationModel'..."
        Write-Warning-Message "This may take several minutes (4-10GB download)"
        docker exec zetherion-ai-ollama ollama pull $generationModel

        if ($LASTEXITCODE -eq 0) {
            Write-Success "Generation model downloaded successfully"
        }
        else {
            Write-Failure "Generation model download failed"
            Write-Warning-Message "You can download it later with: docker exec zetherion-ai-ollama ollama pull $generationModel"
        }
    }

    # Download embedding model to ollama container
    Write-Info-Message "Checking embedding model '$embeddingModel' on ollama container..."
    $embedModelExists = docker exec zetherion-ai-ollama ollama list 2>&1 | Select-String -Pattern $embeddingModel

    if ($embedModelExists) {
        Write-Success "Embedding model '$embeddingModel' already downloaded"
    }
    else {
        Write-Info-Message "Downloading embedding model '$embeddingModel'..."
        docker exec zetherion-ai-ollama ollama pull $embeddingModel

        if ($LASTEXITCODE -eq 0) {
            Write-Success "Embedding model downloaded successfully"
        }
        else {
            Write-Failure "Embedding model download failed"
            Write-Warning-Message "You can download it later with: docker exec zetherion-ai-ollama ollama pull $embeddingModel"
        }
    }
}
else {
    Write-Info-Message "Skipping model download (using Gemini for routing)"
}

# ============================================================
# PHASE 6: VERIFICATION
# ============================================================

Write-Phase "Phase 6/7: Verification"

# Check all containers
Write-Info-Message "Checking container status..."
$containers = docker ps --format "table {{.Names}}\t{{.Status}}" | Select-String "zetherion"

if ($containers) {
    Write-Host ""
    Write-Host "Running Containers:" -ForegroundColor White
    foreach ($container in $containers) {
        Write-Host "  $container"
    }
    Write-Host ""
}

# Test Qdrant
Write-Info-Message "Testing Qdrant connection..."
try {
    $qdrantHealth = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($qdrantHealth.StatusCode -eq 200) {
        Write-Success "Qdrant is healthy"
    }
}
catch {
    Write-Warning-Message "Qdrant health check failed: $_"
}

# Test Ollama containers (if enabled)
if ($routerBackend -eq "ollama") {
    Write-Info-Message "Testing Ollama router container..."
    try {
        # Router container is internal only (no port exposed to host)
        $routerHealth = docker exec zetherion-ai-ollama-router curl -s http://localhost:11434/api/tags 2>&1
        if ($routerHealth -match "models") {
            Write-Success "Ollama router container is healthy"
        }
        else {
            Write-Warning-Message "Ollama router container health check failed"
        }
    }
    catch {
        Write-Warning-Message "Ollama router container health check failed: $_"
    }

    Write-Info-Message "Testing Ollama generation container..."
    try {
        $ollamaHealth = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($ollamaHealth.StatusCode -eq 200) {
            Write-Success "Ollama generation container is healthy"
        }
    }
    catch {
        Write-Warning-Message "Ollama generation container health check failed: $_"
    }
}

# ============================================================
# PHASE 7: SUCCESS & NEXT STEPS
# ============================================================

Write-Phase "Phase 7/7: Deployment Complete"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Zetherion AI is now running!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor White
Write-Host "  1. View logs:        docker-compose logs -f" -ForegroundColor Cyan
Write-Host "  2. Check status:     .\status.ps1" -ForegroundColor Cyan
Write-Host "  3. Stop bot:         .\stop.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  4. Invite bot to Discord:" -ForegroundColor Cyan
Write-Host "     https://discord.com/developers/applications" -ForegroundColor Gray
Write-Host ""

Write-Host "Troubleshooting:" -ForegroundColor White
Write-Host "  - Check container logs: docker-compose logs <service-name>" -ForegroundColor Gray
Write-Host "  - Restart services:     docker-compose restart" -ForegroundColor Gray
Write-Host "  - Full reset:           docker-compose down && .\start.ps1" -ForegroundColor Gray
Write-Host ""

Write-Success "Deployment successful!"
Write-Host ""
