# Zetherion AI Startup Script for Windows
# Run as: powershell -ExecutionPolicy Bypass -File start.ps1

#Requires -Version 7.0

$ErrorActionPreference = "Stop"

# Helper functions
function Write-Success { param([string]$message) Write-Host "[OK] $message" -ForegroundColor Green }
function Write-Error-Message { param([string]$message) Write-Host "[ERROR] $message" -ForegroundColor Red }
function Write-Warning-Message { param([string]$message) Write-Host "[WARNING] $message" -ForegroundColor Yellow }
function Write-Info { param([string]$message) Write-Host "[INFO] $message" -ForegroundColor Cyan }
function Write-Header {
    param([string]$message)
    Write-Host ""
    Write-Host "=================================================" -ForegroundColor Cyan
    Write-Host "  $message" -ForegroundColor Cyan
    Write-Host "=================================================" -ForegroundColor Cyan
    Write-Host ""
}

Write-Header "Zetherion AI Startup Script"

# 1. Check Python 3.12+
Write-Info "Checking Python version..."
$pythonCmd = $null

foreach ($cmd in @("python3.13", "python3.12", "python")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python (\d+)\.(\d+)") {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            if ($major -eq 3 -and $minor -ge 12) {
                $pythonCmd = $cmd
                Write-Success "Python $major.$minor found"
                break
            }
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Error-Message "Python 3.12+ not found"
    Write-Info "Install from: https://www.python.org/downloads/"
    exit 1
}

# 2. Check Docker
Write-Info "Checking Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error-Message "Docker not found"
    Write-Info "Install Docker Desktop from: https://www.docker.com/products/docker-desktop"
    exit 1
}

# Check if Docker daemon is ready
try {
    docker info 2>&1 | Out-Null
    Write-Success "Docker is running"
} catch {
    Write-Warning-Message "Docker Desktop is not running, starting it..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Info "Waiting for Docker to start (30 seconds)..."
    Start-Sleep -Seconds 30

    $maxRetries = 30
    $retryCount = 0
    while ($retryCount -lt $maxRetries) {
        try {
            docker info 2>&1 | Out-Null
            Write-Success "Docker started successfully"
            break
        } catch {
            $retryCount++
            if ($retryCount -eq $maxRetries) {
                Write-Error-Message "Docker failed to start after 30 seconds"
                Write-Info "Please start Docker Desktop manually and try again"
                exit 1
            }
            Start-Sleep -Seconds 1
        }
    }
}

# 3. Check .env file
Write-Info "Checking .env configuration..."
if (-not (Test-Path .env)) {
    Write-Error-Message ".env file not found"
    Write-Info "Copy .env.example to .env and add your API keys"
    exit 1
}

# Load .env and check required variables
$envContent = Get-Content .env
$envVars = @{}
foreach ($line in $envContent) {
    if ($line -match "^([^#][^=]+)=(.*)$") {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$missingVars = @()
if (-not $envVars["DISCORD_TOKEN"]) { $missingVars += "DISCORD_TOKEN" }
if (-not $envVars["GEMINI_API_KEY"]) { $missingVars += "GEMINI_API_KEY" }

if ($missingVars.Count -gt 0) {
    Write-Error-Message "Missing required environment variables: $($missingVars -join ', ')"
    Write-Info "Please add them to your .env file"
    exit 1
}
Write-Success ".env file configured"

# 3.5 Router Backend Selection
$routerBackend = $envVars["ROUTER_BACKEND"]
if (-not $routerBackend) {
    Write-Host ""
    Write-Host "=================================================" -ForegroundColor Cyan
    Write-Host "  Router Backend Selection" -ForegroundColor Cyan
    Write-Host "=================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Zetherion AI can use two different backends for message routing:"
    Write-Host ""
    Write-Host "  1. " -NoNewline; Write-Host "Gemini" -ForegroundColor Green -NoNewline; Write-Host " (Google) - Cloud-based, fast, minimal setup"
    Write-Host "     • Uses your existing Gemini API key"
    Write-Host "     • No additional downloads"
    Write-Host "     • Recommended for cloud-based workflows"
    Write-Host ""
    Write-Host "  2. " -NoNewline; Write-Host "Ollama" -ForegroundColor Green -NoNewline; Write-Host " (Local) - Privacy-focused, runs on your machine"
    Write-Host "     • No data sent to external APIs for routing"
    Write-Host "     • ~5GB model download (first time only)"
    Write-Host "     • Recommended for privacy-conscious users"
    Write-Host ""

    $choice = Read-Host "Which backend would you like to use? (1=Gemini, 2=Ollama) [1]"
    Write-Host ""

    if ($choice -eq "2") {
        $routerBackend = "ollama"
        Write-Success "Selected: Ollama (local routing)"
    } else {
        $routerBackend = "gemini"
        Write-Success "Selected: Gemini (cloud routing)"
    }

    # Save to .env
    Add-Content -Path .env -Value "ROUTER_BACKEND=$routerBackend"
    Write-Info "Saved preference to .env"
    Write-Host ""
}

# 4. Set up virtual environment
Write-Info "Checking virtual environment..."
if (-not (Test-Path .venv)) {
    Write-Warning-Message "Virtual environment not found, creating..."
    & $pythonCmd -m venv .venv
    Write-Success "Virtual environment created"
}

# Activate virtual environment
.\.venv\Scripts\Activate.ps1
Write-Success "Virtual environment activated"

# 5. Check/install dependencies
Write-Info "Checking dependencies..."
try {
    python -c "import discord" 2>$null
    Write-Success "Dependencies already installed"
} catch {
    Write-Warning-Message "Dependencies not installed, installing..."
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install -e .
    Write-Success "Dependencies installed"
}

# 6. Check/start Qdrant container
Write-Info "Checking Qdrant vector database..."
$qdrantExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet
$qdrantRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-qdrant$" -Quiet

if ($qdrantExists) {
    if ($qdrantRunning) {
        Write-Success "Qdrant container already running"
    } else {
        Write-Warning-Message "Qdrant container exists but not running, starting..."
        docker start zetherion_ai-qdrant
        Write-Success "Qdrant container started"
    }
} else {
    Write-Warning-Message "Qdrant container not found, creating..."
    docker run -d `
        --name zetherion_ai-qdrant `
        -p 6333:6333 `
        -v "${PWD}/qdrant_storage:/qdrant/storage" `
        qdrant/qdrant:latest
    Write-Success "Qdrant container created and started"
}

# Wait for Qdrant to be ready
Write-Info "Waiting for Qdrant to be ready..."
$maxRetries = 30
$retryCount = 0
while ($retryCount -lt $maxRetries) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Success "Qdrant is ready"
            break
        }
    } catch { }

    $retryCount++
    if ($retryCount -eq $maxRetries) {
        Write-Error-Message "Qdrant failed to start"
        exit 1
    }
    Start-Sleep -Seconds 1
}

# 7. Check/start Ollama container (if using Ollama backend)
if ($routerBackend -eq "ollama") {
    Write-Info "Starting Ollama container..."

    $ollamaExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-ollama$" -Quiet
    $ollamaRunning = docker ps --format "{{.Names}}" | Select-String -Pattern "^zetherion_ai-ollama$" -Quiet

    if ($ollamaExists) {
        if ($ollamaRunning) {
            Write-Success "Ollama container already running"
        } else {
            Write-Warning-Message "Ollama container exists but not running, starting..."
            docker start zetherion_ai-ollama
            Write-Success "Ollama container started"
        }
    } else {
        Write-Warning-Message "Ollama container not found, creating..."
        docker run -d `
            --name zetherion_ai-ollama `
            --memory="8g" `
            --memory-swap="8g" `
            -p 11434:11434 `
            -v "${PWD}/ollama_models:/root/.ollama" `
            ollama/ollama:latest
        Write-Success "Ollama container created and started"
    }

    # Wait for Ollama to be ready
    Write-Info "Waiting for Ollama to be ready..."
    $maxRetries = 30
    $retryCount = 0
    while ($retryCount -lt $maxRetries) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) {
                Write-Success "Ollama is ready"
                break
            }
        } catch { }

        $retryCount++
        if ($retryCount -eq $maxRetries) {
            Write-Error-Message "Ollama failed to start"
            exit 1
        }
        Start-Sleep -Seconds 1
    }

    # Pull model if not already available
    $ollamaModel = if ($envVars["OLLAMA_ROUTER_MODEL"]) { $envVars["OLLAMA_ROUTER_MODEL"] } else { "llama3.1:8b" }
    Write-Info "Checking if model '$ollamaModel' is available..."

    $modelList = docker exec zetherion_ai-ollama ollama list
    if ($modelList -match $ollamaModel) {
        Write-Success "Model '$ollamaModel' already available"
    } else {
        Write-Warning-Message "Model '$ollamaModel' not found, downloading (this may take several minutes)..."
        Write-Info "Model size: ~4.7GB - please be patient..."

        docker exec zetherion_ai-ollama ollama pull $ollamaModel
        Write-Success "Model '$ollamaModel' downloaded successfully"
    }
} else {
    Write-Info "Using Gemini backend (ROUTER_BACKEND=${routerBackend})"
}

# 8. Final checks
Write-Header "Starting Zetherion AI Bot"

Write-Info "Configuration Summary:"
Write-Host "  • Python: $(& $pythonCmd --version)"
Write-Host "  • Discord Token: $($envVars['DISCORD_TOKEN'].Substring(0, [Math]::Min(20, $envVars['DISCORD_TOKEN'].Length)))..."
Write-Host "  • Gemini API: $($envVars['GEMINI_API_KEY'].Substring(0, [Math]::Min(20, $envVars['GEMINI_API_KEY'].Length)))..."
if ($envVars['ANTHROPIC_API_KEY']) {
    Write-Host "  • Anthropic API: $($envVars['ANTHROPIC_API_KEY'].Substring(0, [Math]::Min(20, $envVars['ANTHROPIC_API_KEY'].Length)))..."
}
if ($envVars['OPENAI_API_KEY']) {
    Write-Host "  • OpenAI API: $($envVars['OPENAI_API_KEY'].Substring(0, [Math]::Min(20, $envVars['OPENAI_API_KEY'].Length)))..."
}
Write-Host "  • Qdrant: http://localhost:6333"
Write-Host "  • Router Backend: $routerBackend"
if ($routerBackend -eq "ollama") {
    $ollamaModel = if ($envVars["OLLAMA_ROUTER_MODEL"]) { $envVars["OLLAMA_ROUTER_MODEL"] } else { "llama3.1:8b" }
    Write-Host "  • Ollama: http://localhost:11434 (Model: $ollamaModel)"
}
$logToFile = if ($envVars["LOG_TO_FILE"]) { $envVars["LOG_TO_FILE"] } else { "true" }
$logDir = if ($envVars["LOG_DIRECTORY"]) { $envVars["LOG_DIRECTORY"] } else { "logs" }
Write-Host "  • File Logging: $logToFile (Directory: $logDir)"
if ($envVars['ALLOWED_USER_IDS']) {
    Write-Host "  • Allowed Users: $($envVars['ALLOWED_USER_IDS'])"
} else {
    Write-Host "  • Allowed Users: All users (WARNING: not recommended for production)"
}
Write-Host ""

# 9. Start the bot
Write-Success "All checks passed! Starting bot..."
Write-Host ""
Write-Host "Press Ctrl+C to stop the bot" -ForegroundColor Green
Write-Host ""

# Run the bot (set PYTHONPATH to include src directory)
$env:PYTHONPATH = "$PWD/src;$env:PYTHONPATH"
python -m zetherion_ai
