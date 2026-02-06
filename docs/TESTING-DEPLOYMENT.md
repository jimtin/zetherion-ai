# Testing & Deployment Validation Guide

Complete testing guide for validating Zetherion AI deployment across all platforms.

## Table of Contents

- [Overview](#overview)
- [Pre-Testing Checklist](#pre-testing-checklist)
- [Phase-by-Phase Validation](#phase-by-phase-validation)
- [Platform-Specific Testing](#platform-specific-testing)
- [Container Validation](#container-validation)
- [Hardware Assessment Testing](#hardware-assessment-testing)
- [Integration Testing](#integration-testing)
- [Performance Benchmarking](#performance-benchmarking)
- [Security Testing](#security-testing)
- [Troubleshooting Test Failures](#troubleshooting-test-failures)

## Overview

This guide provides comprehensive testing procedures to validate the fully automated Docker deployment of Zetherion AI. All tests should pass before considering a deployment ready for production use.

**Testing Environments:**
- Windows 10/11 with PowerShell 5.1+
- macOS 10.15+ (Catalina or later)
- Linux (Ubuntu 20.04+, Debian 11+, Fedora 35+)

**Required Tools:**
- Docker Desktop (or Docker Engine on Linux)
- Git
- curl or Invoke-WebRequest
- jq (optional, for JSON parsing)

## Pre-Testing Checklist

Before running any tests, verify:

### 1. Clean Environment

**Windows:**
```powershell
# Stop any running containers
.\stop.ps1

# Remove all containers and volumes (keeps config)
.\cleanup.ps1 -KeepConfig

# Verify clean state
docker ps -a | Select-String "zetherion"  # Should return nothing
docker volume ls | Select-String "zetherion"  # Should return nothing
```

**Unix (macOS/Linux):**
```bash
# Stop any running containers
./stop.sh

# Remove all containers and volumes (keeps config)
./cleanup.sh --keep-config

# Verify clean state
docker ps -a | grep zetherion  # Should return nothing
docker volume ls | grep zetherion  # Should return nothing
```

### 2. Prerequisites Verified

- ✅ Docker Desktop installed and running
- ✅ Git installed
- ✅ Sufficient disk space (20GB+)
- ✅ Internet connection active
- ✅ API keys available (Discord, Gemini)

### 3. Test Configuration Ready

Create a test `.env` file or have API keys ready for interactive setup:

```env
# Required for testing
DISCORD_TOKEN=your_test_discord_token
GEMINI_API_KEY=your_gemini_api_key

# Optional (can be skipped in tests)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Test configuration
ROUTER_BACKEND=gemini  # Use Gemini for faster initial tests
```

## Phase-by-Phase Validation

### Phase 1: Prerequisites Check

**Test Objective:** Verify prerequisite validation and auto-install prompts work correctly.

#### Test 1.1: Docker Detection (Already Installed)

**Windows:**
```powershell
.\start.ps1
```

**Expected Output:**
```
[STEP 1/7] Checking prerequisites...
[OK] Docker Desktop is running
[OK] Git is installed
[OK] Disk space: XXX GB available
```

**Validation:**
- ✅ Docker detected automatically
- ✅ Git detected automatically
- ✅ Disk space warning if <20GB
- ✅ No errors, proceeds to Phase 2

#### Test 1.2: Docker Not Running (Manual Test)

**Setup:**
1. Stop Docker Desktop manually
2. Run start script

**Expected Behavior:**
- Script detects Docker not running
- Attempts to start Docker Desktop
- Waits up to 60 seconds for Docker daemon
- Proceeds once Docker is ready OR errors with helpful message

**Validation:**
- ✅ Docker Desktop launched automatically
- ✅ Script waits for daemon to be ready
- ✅ Proceeds after Docker starts

#### Test 1.3: Low Disk Space Warning

**Manual Test (requires machine with <20GB free):**

**Expected Output:**
```
[WARNING] Low disk space: 15GB available
[WARNING] Recommended: 20GB+ for optimal performance
[INFO] Continue anyway? (y/N):
```

**Validation:**
- ✅ Warning displayed if <20GB
- ✅ User can choose to continue or abort
- ✅ Script proceeds if user confirms

### Phase 2: Hardware Assessment

**Test Objective:** Verify hardware detection accurately identifies system resources.

#### Test 2.1: Hardware Assessment Execution

**Windows:**
```powershell
# Run start script and observe Phase 2 output
.\start.ps1
```

**Expected Output:**
```
[STEP 2/7] Assessing hardware...
[INFO] Building hardware assessment container...
[INFO] CPU: [Detected CPU Model] (X cores, Y threads)
[INFO] RAM: XGB total, YGB available
[INFO] GPU: [Detected GPU] or "None (CPU-only mode)"
[OK] Hardware assessment complete

Recommended Ollama Model:
  Model: llama3.1:8b (or appropriate for hardware)
  Size: 4.7 GB download
  Quality: High quality, comparable to cloud models
  Speed: Fast (with GPU) or Medium (CPU-only)
  Reason: [Explanation based on detected hardware]
```

**Validation:**
- ✅ CPU cores/threads detected correctly
- ✅ RAM total and available detected correctly
- ✅ GPU detected (if present) or "None" reported
- ✅ Model recommendation matches hardware capability
- ✅ No errors during assessment

#### Test 2.2: Hardware Assessment Accuracy

**Manual Verification:**

**Windows:**
```powershell
# Check actual hardware
Get-WmiObject Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
Get-WmiObject Win32_ComputerSystem | Select-Object @{Name="RAM (GB)";Expression={[math]::Round($_.TotalPhysicalMemory/1GB,2)}}
Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM
```

**macOS:**
```bash
# Check actual hardware
sysctl -n machdep.cpu.brand_string
sysctl -n hw.ncpu
sysctl -n hw.memsize | awk '{print $1/1024/1024/1024 " GB"}'
system_profiler SPDisplaysDataType | grep "Chipset Model"
```

**Linux:**
```bash
# Check actual hardware
lscpu | grep "Model name"
lscpu | grep "^CPU(s):"
free -h | grep Mem
lspci | grep -i vga
```

**Compare with script output:**
- ✅ CPU model matches
- ✅ Core/thread count matches
- ✅ RAM amount within 10% tolerance
- ✅ GPU model matches (if present)

#### Test 2.3: Model Recommendation Logic

**Expected Recommendations by Hardware:**

| Hardware | Expected Model | Size |
|----------|----------------|------|
| <8GB RAM, no GPU | gemini (cloud) | 0GB |
| 8-16GB RAM, no GPU | llama3.1:8b | 4.7GB |
| 16-32GB RAM, no GPU | qwen2.5:14b | 9.0GB |
| 8GB VRAM GPU | llama3.1:8b | 4.7GB |
| 12GB+ VRAM GPU | qwen2.5:14b | 9.0GB |
| 24GB+ VRAM GPU | qwen2.5:32b | 18GB |

**Validation:**
- ✅ Recommendation matches table above
- ✅ Reason provided explains the recommendation
- ✅ Alternative models listed

### Phase 3: Configuration Setup

**Test Objective:** Verify interactive .env generation works correctly.

#### Test 3.1: First-Run Interactive Setup

**Prerequisite:** No `.env` file exists

**Windows:**
```powershell
# Remove .env if it exists
Remove-Item .env -ErrorAction SilentlyContinue

# Run start script
.\start.ps1
```

**Expected Prompts:**
```
[STEP 3/7] Configuration setup...
[INFO] No .env file found. Let's set it up!

Discord Bot Token (required):
> [User enters token]

Gemini API Key (required for embeddings):
> [User enters key]

Anthropic API Key (optional, press Enter to skip):
> [User enters key or presses Enter]

OpenAI API Key (optional, press Enter to skip):
> [User presses Enter]

Router Backend:
  1. Gemini (cloud-based, fast, minimal resources)
  2. Ollama (local, private, ~5GB download)
Choose [1/2]: 2

[INFO] Based on your hardware (16GB RAM, RTX 3060), we recommend:
  Recommended: llama3.1:8b
Use recommended model 'llama3.1:8b'? (Y/n): Y

[OK] Configuration saved to .env
```

**Validation:**
- ✅ All required prompts displayed
- ✅ Optional prompts allow Enter to skip
- ✅ Router backend selection works
- ✅ Model recommendation shown (if Ollama selected)
- ✅ `.env` file created with correct values
- ✅ API keys not displayed in logs

#### Test 3.2: Existing Configuration (Skip Setup)

**Prerequisite:** `.env` file exists

**Windows:**
```powershell
# .env already exists from previous test
.\start.ps1
```

**Expected Output:**
```
[STEP 3/7] Configuration setup...
[OK] Configuration file found
```

**Validation:**
- ✅ No prompts displayed
- ✅ Existing `.env` used
- ✅ Script proceeds to Phase 4

#### Test 3.3: Invalid API Key Format

**Manual Test:**

1. Modify interactive setup script to enter invalid key
2. Enter malformed Discord token (e.g., "invalid_token")

**Expected Behavior:**
```
[ERROR] Invalid Discord token format
[ERROR] Discord tokens should start with "MT" or "MQ" and contain at least 50 characters
[INFO] Discord Bot Token (required):
> [Prompts again]
```

**Validation:**
- ✅ Invalid format detected
- ✅ Helpful error message shown
- ✅ Re-prompts for correct input

### Phase 4: Docker Build & Deploy

**Test Objective:** Verify distroless images build correctly and containers start.

#### Test 4.1: Distroless Image Build

**Windows:**
```powershell
# Start script initiates build
.\start.ps1
```

**Expected Output:**
```
[STEP 4/7] Building Docker images...
[INFO] Building zetherion-ai-bot...
[+] Building 45.2s (18/18) FINISHED
[INFO] Building zetherion-ai-skills...
[+] Building 38.7s (16/16) FINISHED
[OK] Images built successfully
```

**Validation:**
- ✅ Multi-stage build completes without errors
- ✅ Builder stage installs dependencies
- ✅ Runtime stage uses distroless base
- ✅ Both bot and skills images build successfully

#### Test 4.2: Container Startup

**Expected Output:**
```
[INFO] Starting Docker containers...
[INFO] Starting docker-compose...
Creating network "personalbot_default" ... done
Creating volume "personalbot_qdrant_storage" ... done
Creating volume "personalbot_ollama_models" ... done
Creating zetherion-ai-qdrant ... done
Creating zetherion-ai-ollama ... done
Creating zetherion-ai-skills ... done
Creating zetherion-ai-bot ... done
[OK] Containers started
```

**Validation:**
- ✅ All containers created
- ✅ Networks created
- ✅ Volumes created
- ✅ No error messages

#### Test 4.3: Health Check Wait

**Expected Output:**
```
[INFO] Waiting for services to become healthy...
[INFO] Waiting for zetherion-ai-qdrant... (0/120s)
[INFO] Waiting for zetherion-ai-qdrant... (5/120s)
[OK] zetherion-ai-qdrant is healthy
[INFO] Waiting for zetherion-ai-skills... (0/120s)
[OK] zetherion-ai-skills is healthy
[INFO] Waiting for zetherion-ai-bot... (0/120s)
[OK] zetherion-ai-bot is healthy
```

**Validation:**
- ✅ Health checks executed
- ✅ All services become healthy within 120 seconds
- ✅ Progress updates shown
- ✅ No timeouts

### Phase 5: Model Download (Ollama Only)

**Test Objective:** Verify Ollama model downloads correctly (if Ollama backend selected).

#### Test 5.1: First-Time Model Download

**Prerequisite:** Ollama backend selected, model not yet downloaded

**Expected Output:**
```
[STEP 5/7] Downloading Ollama model (first time only)...
[INFO] Checking if model 'llama3.1:8b' exists...
[INFO] Model not found. Downloading llama3.1:8b (4.7GB)...
[INFO] This may take 5-7 minutes depending on internet speed...
pulling manifest
pulling 8eeb52dfb3bb... 100% ▕████████████████▏ 4.7 GB
pulling 73b313b5552d... 100% ▕████████████████▏ 1.5 KB
pulling 0ba8f0e314b4... 100% ▕████████████████▏  12 KB
pulling 56bb8bd477a5... 100% ▕████████████████▏   96 B
pulling 1a4c3c319823... 100% ▕████████████████▏  485 B
verifying sha256 digest
writing manifest
success
[OK] Model 'llama3.1:8b' downloaded successfully
```

**Validation:**
- ✅ Model download initiated
- ✅ Progress bar displayed
- ✅ Download completes successfully
- ✅ Model verified

#### Test 5.2: Existing Model (Skip Download)

**Prerequisite:** Model already downloaded from previous test

**Windows:**
```powershell
# Stop and restart to test existing model check
.\stop.ps1
.\start.ps1
```

**Expected Output:**
```
[STEP 5/7] Checking Ollama model...
[INFO] Checking if model 'llama3.1:8b' exists...
[OK] Model 'llama3.1:8b' already downloaded
```

**Validation:**
- ✅ Existing model detected
- ✅ Download skipped
- ✅ Fast startup (~30 seconds)

#### Test 5.3: Model Download Failure Recovery

**Manual Test (simulate network failure):**

1. Disconnect internet during model download
2. Observe error handling

**Expected Behavior:**
```
[ERROR] Failed to download model 'llama3.1:8b'
[ERROR] Error: connection timeout
[INFO] Please check your internet connection and try again
[INFO] To retry manually:
  docker exec zetherion-ai-ollama ollama pull llama3.1:8b
```

**Validation:**
- ✅ Error caught gracefully
- ✅ Helpful error message
- ✅ Manual retry instructions provided

### Phase 6: Verification

**Test Objective:** Verify all services are running and accessible.

#### Test 6.1: Qdrant Health Check

**Expected Output:**
```
[STEP 6/7] Verifying deployment...
[INFO] Testing Qdrant connection...
[OK] Qdrant is healthy (http://localhost:6333/healthz)
```

**Manual Verification:**
```powershell
# Windows
Invoke-WebRequest http://localhost:6333/healthz

# Unix
curl http://localhost:6333/healthz
```

**Expected Response:**
```json
{
  "title": "Qdrant - Vector Search Engine",
  "version": "v1.7.4"
}
```

**Validation:**
- ✅ Qdrant responds to health check
- ✅ HTTP 200 status code
- ✅ Version info returned

#### Test 6.2: Ollama Health Check (If Enabled)

**Expected Output:**
```
[INFO] Testing Ollama connection...
[OK] Ollama is healthy (http://localhost:11434/api/tags)
[OK] Model 'llama3.1:8b' loaded
```

**Manual Verification:**
```powershell
# Windows
Invoke-WebRequest http://localhost:11434/api/tags | ConvertFrom-Json

# Unix
curl http://localhost:11434/api/tags | jq
```

**Expected Response:**
```json
{
  "models": [
    {
      "name": "llama3.1:8b",
      "modified_at": "2026-02-07T10:30:00Z",
      "size": 4661211648
    }
  ]
}
```

**Validation:**
- ✅ Ollama responds to API request
- ✅ Model listed in response
- ✅ Model size matches expected

#### Test 6.3: Container Status Display

**Expected Output:**
```
[INFO] Container status:
zetherion-ai-qdrant   Up 1 minute (healthy)
zetherion-ai-ollama   Up 1 minute (healthy)
zetherion-ai-skills   Up 1 minute (healthy)
zetherion-ai-bot      Up 1 minute (healthy)
```

**Validation:**
- ✅ All 4 containers listed
- ✅ All show "Up" status
- ✅ All show "(healthy)" status
- ✅ Uptime displayed

### Phase 7: Success

**Test Objective:** Verify success message and next steps displayed.

**Expected Output:**
```
============================================================
  ✓ Zetherion AI is now running!
============================================================

Next Steps:
  1. View logs:        docker-compose logs -f
  2. Check status:     .\status.ps1 (or ./status.sh)
  3. Stop bot:         .\stop.ps1 (or ./stop.sh)

  4. Invite bot to Discord:
     https://discord.com/developers/applications

Deployment successful!
```

**Validation:**
- ✅ Success banner displayed
- ✅ Next steps clearly listed
- ✅ Platform-appropriate commands shown
- ✅ Discord invite link provided

## Platform-Specific Testing

### Windows Testing

#### Test W1: PowerShell Execution Policy

**Test with Restricted Policy:**
```powershell
# Set restricted policy (simulates fresh Windows)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Restricted

# Try to run start script
.\start.ps1
```

**Expected Error:**
```
.\start.ps1 : File .\start.ps1 cannot be loaded because running scripts is disabled on this system.
```

**Resolution Test:**
```powershell
# Script should provide helpful message
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1
```

**Validation:**
- ✅ Error message clear and helpful
- ✅ Documentation includes resolution steps
- ✅ Script runs after policy change

#### Test W2: Administrator Privileges

**Test without Admin:**
```powershell
# Run PowerShell as regular user (not admin)
.\start.ps1
```

**Expected Error:**
```
[ERROR] This script requires Administrator privileges
[ERROR] Right-click PowerShell and select "Run as Administrator"
```

**Validation:**
- ✅ Admin check works
- ✅ Clear error message
- ✅ Script exits gracefully

#### Test W3: WSL 2 Dependency

**Test Docker Desktop Start:**

**Expected Behavior:**
- If WSL 2 not installed, Docker Desktop installation prompts for it
- Script waits for Docker Desktop to be ready
- Clear error if WSL 2 issues detected

**Validation:**
- ✅ WSL 2 requirement documented
- ✅ Helpful error messages if WSL 2 missing
- ✅ Links to WSL 2 installation guide

### macOS Testing

#### Test M1: Homebrew Auto-Install

**Test on Mac without Homebrew:**

1. Check if Homebrew installed: `which brew`
2. If not, start script should offer to install

**Expected Prompt:**
```
[INFO] Homebrew not found
[INFO] Homebrew is required to install Docker Desktop
[INFO] Install Homebrew? (Y/n):
```

**Validation:**
- ✅ Homebrew detection works
- ✅ Installation prompt clear
- ✅ Homebrew installs successfully
- ✅ Script continues after installation

#### Test M2: Docker Desktop Installation (macOS)

**Expected Command:**
```bash
brew install --cask docker
```

**Validation:**
- ✅ Docker Desktop installed via Homebrew
- ✅ Application launches automatically
- ✅ Script waits for Docker daemon
- ✅ Proceeds after Docker ready

#### Test M3: Apple Silicon vs Intel

**Test on both architectures:**

- **Apple Silicon (M1/M2/M3)**: Should use arm64 images
- **Intel**: Should use amd64 images

**Validation:**
```bash
# Check architecture in use
docker inspect zetherion-ai-bot | jq '.[0].Architecture'
```

**Expected:**
- ✅ Apple Silicon: "arm64"
- ✅ Intel: "amd64"
- ✅ Both architectures work correctly

### Linux Testing

#### Test L1: Docker Installation Detection

**Test on fresh Ubuntu/Debian:**

```bash
# Check if Docker installed
which docker

# If not, script should provide instructions
./start.sh
```

**Expected Output:**
```
[ERROR] Docker is not installed
[INFO] Please install Docker using one of these methods:
[INFO]   Ubuntu/Debian: sudo apt-get update && sudo apt-get install docker-ce docker-ce-cli containerd.io
[INFO]   Fedora: sudo dnf install docker-ce
[INFO]   See: https://docs.docker.com/engine/install/
```

**Validation:**
- ✅ Docker detection works
- ✅ Platform-specific install instructions provided
- ✅ Clear error message

#### Test L2: Docker Permission Issues

**Test without docker group membership:**

```bash
# Remove user from docker group (if added)
sudo gpasswd -d $USER docker

# Try to run start script
./start.sh
```

**Expected Error:**
```
docker: permission denied while trying to connect to the Docker daemon socket
```

**Expected Helpful Message:**
```
[ERROR] Docker permission denied
[INFO] Add your user to the docker group:
  sudo usermod -aG docker $USER
  newgrp docker
[INFO] Then try again
```

**Validation:**
- ✅ Permission error detected
- ✅ Clear resolution steps
- ✅ Documentation includes this scenario

#### Test L3: systemd Docker Service

**Test Docker service start:**

```bash
# Check Docker service status
sudo systemctl status docker

# If not running, script should detect and start
./start.sh
```

**Expected Behavior:**
- Script detects Docker service not running
- Attempts to start: `sudo systemctl start docker`
- Waits for service to be ready
- Proceeds with deployment

**Validation:**
- ✅ Service status detected
- ✅ Automatic start attempted
- ✅ Clear error if start fails

## Container Validation

### Distroless Image Verification

#### Test C1: Image Size

**Verify distroless images are smaller:**

```bash
# Check image sizes
docker images | grep zetherion-ai
```

**Expected Results:**
- `zetherion-ai-bot:latest` ≈ 180-220MB (distroless)
- `zetherion-ai-skills:latest` ≈ 180-220MB (distroless)
- Significantly smaller than python:3.11-slim (~500MB)

**Validation:**
- ✅ Images under 250MB each
- ✅ Smaller than traditional Python images

#### Test C2: No Shell Access

**Verify distroless has no shell:**

```bash
# Attempt to exec shell (should fail)
docker exec -it zetherion-ai-bot /bin/sh
```

**Expected Error:**
```
OCI runtime exec failed: exec: "/bin/sh": stat /bin/sh: no such file or directory
```

**Validation:**
- ✅ Shell access denied (security feature)
- ✅ Cannot execute arbitrary commands
- ✅ Distroless working as intended

#### Test C3: Python Execution Only

**Verify only Python works:**

```bash
# Python should work
docker exec zetherion-ai-bot python3.11 --version

# Shell commands should NOT work
docker exec zetherion-ai-bot ls
```

**Expected Results:**
- Python version displayed (e.g., "Python 3.11.7")
- `ls` command fails (command not found)

**Validation:**
- ✅ Python interpreter accessible
- ✅ System utilities not available (security)

#### Test C4: Non-Root User

**Verify container runs as non-root:**

```bash
# Check container user
docker exec zetherion-ai-bot python3.11 -c "import os; print(f'UID: {os.getuid()}, GID: {os.getgid()}')"
```

**Expected Output:**
```
UID: 65532, GID: 65532
```

**Validation:**
- ✅ UID is 65532 (nonroot user)
- ✅ Not running as root (UID 0)
- ✅ Security best practice

### Container Health Checks

#### Test C5: Health Check Execution

**Monitor health checks:**

```bash
# Watch health status change from "starting" to "healthy"
watch -n 1 'docker ps --format "table {{.Names}}\t{{.Status}}"'
```

**Expected Progression:**
```
# Initial (0-30s)
zetherion-ai-bot    Up 5 seconds (health: starting)

# After health check passes (30-60s)
zetherion-ai-bot    Up 45 seconds (healthy)
```

**Validation:**
- ✅ Health status changes from "starting" to "healthy"
- ✅ Happens within 60 seconds
- ✅ All containers show "healthy"

#### Test C6: Health Check Failure Detection

**Simulate health check failure:**

```bash
# Stop Qdrant (bot health check will fail)
docker stop zetherion-ai-qdrant

# Wait 30 seconds for health check to detect failure
sleep 30

# Check bot status
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zetherion-ai-bot
```

**Expected Status:**
```
zetherion-ai-bot    Up 2 minutes (unhealthy)
```

**Validation:**
- ✅ Unhealthy status detected
- ✅ Container still running (not crashed)
- ✅ Can be restarted to recover

## Hardware Assessment Testing

### CPU Detection Tests

#### Test H1: CPU Model Detection

**Windows:**
```powershell
# Compare script output with actual CPU
$scriptCpu = (.\start.ps1 2>&1 | Select-String "CPU:").Line
$actualCpu = (Get-WmiObject Win32_Processor).Name
Write-Host "Script: $scriptCpu"
Write-Host "Actual: $actualCpu"
```

**Unix:**
```bash
# Compare script output with actual CPU
script_cpu=$(./start.sh 2>&1 | grep "CPU:")
actual_cpu=$(lscpu | grep "Model name" | awk -F: '{print $2}' | xargs)
echo "Script: $script_cpu"
echo "Actual: $actual_cpu"
```

**Validation:**
- ✅ CPU model name matches
- ✅ Core count matches
- ✅ Thread count matches

### RAM Detection Tests

#### Test H2: RAM Amount Detection

**Validation:**
```bash
# Check detected RAM vs actual
docker run --rm zetherion-ai-assess:distroless | jq '.ram_total_gb, .ram_available_gb'

# Compare with system info
free -g  # Linux
# or
vm_stat  # macOS
```

**Expected:**
- Detected RAM within 10% of actual
- Available RAM reasonable (50-90% of total)

**Validation:**
- ✅ Total RAM detected accurately
- ✅ Available RAM reasonable
- ✅ Values in GB, not bytes

### GPU Detection Tests

#### Test H3: NVIDIA GPU Detection

**Prerequisites:** NVIDIA GPU installed

```bash
# Check detected GPU
docker run --rm --gpus all zetherion-ai-assess:distroless | jq '.gpu'

# Compare with nvidia-smi
nvidia-smi --query-gpu=name,memory.total --format=csv
```

**Expected Output:**
```json
{
  "vendor": "NVIDIA",
  "model": "GeForce RTX 3060",
  "vram_gb": 12
}
```

**Validation:**
- ✅ NVIDIA GPU detected
- ✅ Model name matches nvidia-smi
- ✅ VRAM amount accurate

#### Test H4: AMD GPU Detection

**Prerequisites:** AMD GPU installed

```bash
# Check detected GPU
docker run --rm zetherion-ai-assess:distroless | jq '.gpu'

# Compare with lspci
lspci | grep VGA
```

**Expected Output:**
```json
{
  "vendor": "AMD",
  "model": "Radeon RX 6800",
  "vram_gb": 16
}
```

**Validation:**
- ✅ AMD GPU detected
- ✅ Model name matches lspci
- ✅ VRAM amount accurate

#### Test H5: No GPU (CPU-Only) Detection

**Prerequisites:** No dedicated GPU, or integrated graphics only

```bash
# Check detected GPU
docker run --rm zetherion-ai-assess:distroless | jq '.gpu'
```

**Expected Output:**
```json
{
  "vendor": null,
  "model": null,
  "vram_gb": 0
}
```

**Validation:**
- ✅ No GPU detected (null values)
- ✅ Model recommendation adjusts to CPU-only
- ✅ Gemini or small models recommended

## Integration Testing

### End-to-End Workflow Tests

#### Test I1: Fresh Installation (Gemini Backend)

**Test Steps:**
1. Clean environment: `./cleanup.sh` or `.\cleanup.ps1`
2. Remove .env: `rm .env` or `Remove-Item .env`
3. Run start script
4. Select Gemini backend
5. Wait for completion
6. Test Discord bot

**Expected Timeline:**
- Phase 1-3: ~30 seconds
- Phase 4 (build): ~2 minutes
- Phase 6-7: ~30 seconds
- **Total: ~3 minutes**

**Validation:**
- ✅ Completes in <5 minutes
- ✅ All phases succeed
- ✅ Bot responds in Discord

#### Test I2: Fresh Installation (Ollama Backend)

**Test Steps:**
1. Clean environment
2. Remove .env
3. Run start script
4. Select Ollama backend
5. Choose llama3.1:8b model
6. Wait for model download
7. Test Discord bot

**Expected Timeline:**
- Phases 1-4: ~2.5 minutes
- Phase 5 (model download): ~5-7 minutes
- Phases 6-7: ~30 seconds
- **Total: ~8-10 minutes**

**Validation:**
- ✅ Completes in <12 minutes
- ✅ Model downloads successfully
- ✅ Bot responds in Discord

#### Test I3: Restart with Existing Configuration

**Test Steps:**
1. Stop bot: `./stop.sh` or `.\stop.ps1`
2. Start bot: `./start.sh` or `.\start.ps1`

**Expected Timeline:**
- **Total: ~30 seconds**

**Validation:**
- ✅ No configuration prompts
- ✅ Containers start quickly
- ✅ Bot operational within 30s

### Discord Bot Integration

#### Test I4: Bot Responds to Mentions

**Prerequisites:** Bot invited to Discord server

**Test Steps:**
1. Ensure bot is online (green status)
2. Send message: `@Zetherion AI hello`
3. Wait for response

**Expected Response:**
```
Hello! I'm Zetherion AI, your AI assistant. How can I help you today?
```

**Validation:**
- ✅ Bot shows online status
- ✅ Bot responds within 3 seconds
- ✅ Response is coherent

#### Test I5: Memory/Search Commands

**Test Steps:**
1. Store memory: `@Zetherion AI remember I prefer Python for coding`
2. Wait for confirmation
3. Search memory: `@Zetherion AI search for my coding preferences`

**Expected Behavior:**
- Memory stored in Qdrant
- Search returns stored information

**Validation:**
- ✅ Memory stored successfully
- ✅ Search retrieves correct information
- ✅ Qdrant database working

### Status Script Testing

#### Test I6: status.ps1 / status.sh Accuracy

**Test Steps:**
```bash
# Run status script
./status.sh  # or .\status.ps1
```

**Expected Output:**
```
============================================================
  Zetherion AI Status
============================================================

[OK] Qdrant is running and healthy
    Collections: 1
    Vectors: 42

[OK] Ollama is running and healthy
    Models: 1
      - llama3.1:8b

[OK] Skills service is running and healthy

[OK] Bot is running and healthy
    Uptime: 0d 0h 15m 32s

[OK] Zetherion AI is fully operational

Container Summary:
zetherion-ai-qdrant   Up 15 minutes (healthy)
zetherion-ai-ollama   Up 15 minutes (healthy)
zetherion-ai-skills   Up 15 minutes (healthy)
zetherion-ai-bot      Up 15 minutes (healthy)
```

**Validation:**
- ✅ All services show [OK]
- ✅ Collection and vector counts displayed
- ✅ Loaded models listed
- ✅ Uptime accurate
- ✅ Container summary matches docker ps

### Stop Script Testing

#### Test I7: Graceful Shutdown

**Test Steps:**
```bash
# Stop all services
./stop.sh  # or .\stop.ps1
```

**Expected Output:**
```
============================================================
  Stopping Zetherion AI
============================================================

[INFO] Stopping Docker containers...
Stopping zetherion-ai-bot ... done
Stopping zetherion-ai-skills ... done
Stopping zetherion-ai-ollama ... done
Stopping zetherion-ai-qdrant ... done

[OK] All containers stopped
[INFO] Data preserved in Docker volumes
[INFO] Run ./start.sh to restart
```

**Validation:**
- ✅ All containers stopped
- ✅ 30-second timeout respected
- ✅ No errors
- ✅ Volumes preserved

#### Test I8: Data Persistence After Stop

**Test Steps:**
1. Store memory in Discord bot
2. Stop bot: `./stop.sh`
3. Start bot: `./start.sh`
4. Search for stored memory

**Validation:**
- ✅ Memory persists after restart
- ✅ Qdrant data preserved
- ✅ Ollama model not re-downloaded

### Cleanup Script Testing

#### Test I9: Cleanup with Keep Data

**Test Steps:**
```bash
# Stop and cleanup, keeping data
./cleanup.sh --keep-data  # or .\cleanup.ps1 -KeepData
```

**Expected Behavior:**
- Containers removed
- Images removed
- Volumes KEPT
- .env KEPT

**Validation:**
```bash
# Check containers (should be none)
docker ps -a | grep zetherion  # Empty

# Check volumes (should exist)
docker volume ls | grep zetherion  # Shows volumes

# Check .env (should exist)
ls -la .env  # File exists
```

**Validation:**
- ✅ Containers removed
- ✅ Volumes preserved
- ✅ .env preserved

#### Test I10: Complete Cleanup

**Test Steps:**
```bash
# Complete removal (with confirmation)
./cleanup.sh  # or .\cleanup.ps1
# Type 'yes' when prompted
```

**Expected Behavior:**
- Containers removed
- Images removed
- Volumes removed
- .env removed
- Complete fresh slate

**Validation:**
```bash
# Everything should be gone
docker ps -a | grep zetherion  # Empty
docker volume ls | grep zetherion  # Empty
docker images | grep zetherion  # Empty
ls .env  # File not found
```

**Validation:**
- ✅ All containers removed
- ✅ All volumes removed
- ✅ All images removed
- ✅ .env removed
- ✅ Clean environment

## Performance Benchmarking

### Startup Time Benchmarks

#### Test P1: First-Run Performance (Gemini)

**Measurement:**
```powershell
# Windows
Measure-Command { .\start.ps1 }

# Unix
time ./start.sh
```

**Expected Times:**
| Phase | Expected Duration |
|-------|------------------|
| Phase 1: Prerequisites | 5-10s |
| Phase 2: Hardware Assessment | 10-20s |
| Phase 3: Interactive Setup | 30-60s (user input) |
| Phase 4: Build & Deploy | 90-120s |
| Phase 5: N/A (Gemini) | 0s |
| Phase 6: Verification | 10-20s |
| **Total** | **3-4 minutes** |

**Validation:**
- ✅ Completes within 5 minutes
- ✅ No phase takes excessively long
- ✅ Build time reasonable

#### Test P2: First-Run Performance (Ollama)

**Expected Times:**
| Phase | Expected Duration |
|-------|------------------|
| Phase 1: Prerequisites | 5-10s |
| Phase 2: Hardware Assessment | 10-20s |
| Phase 3: Interactive Setup | 30-60s (user input) |
| Phase 4: Build & Deploy | 90-120s |
| Phase 5: Model Download | 300-420s (5-7 min) |
| Phase 6: Verification | 10-20s |
| **Total** | **8-10 minutes** |

**Validation:**
- ✅ Completes within 12 minutes
- ✅ Model download shows progress
- ✅ Download speed reasonable for internet connection

#### Test P3: Subsequent Startup Performance

**Measurement:**
```bash
# Stop and restart
./stop.sh
time ./start.sh
```

**Expected Times:**
| Phase | Expected Duration |
|-------|------------------|
| Phase 1: Prerequisites | 5s |
| Phase 2: Hardware Assessment | 0s (skipped) |
| Phase 3: Config Check | 1s |
| Phase 4: Container Start | 15-20s |
| Phase 5: Model Check | 2s (already exists) |
| Phase 6: Verification | 5s |
| **Total** | **30-35 seconds** |

**Validation:**
- ✅ Completes within 45 seconds
- ✅ Fast startup from cached images
- ✅ No unnecessary steps

### Resource Usage Benchmarks

#### Test P4: Memory Usage

**Measurement:**
```bash
# Monitor memory usage
docker stats --no-stream zetherion-ai-bot zetherion-ai-qdrant zetherion-ai-ollama zetherion-ai-skills
```

**Expected Memory Usage:**
| Container | Expected RAM | Notes |
|-----------|--------------|-------|
| zetherion-ai-bot | 200-400MB | Varies with activity |
| zetherion-ai-qdrant | 100-200MB | Increases with vectors |
| zetherion-ai-ollama | 5-8GB | Model in memory |
| zetherion-ai-skills | 200-300MB | Varies with requests |
| **Total (Ollama)** | **6-9GB** | |
| **Total (Gemini)** | **0.5-1GB** | No Ollama |

**Validation:**
- ✅ Memory usage within expected ranges
- ✅ No memory leaks over 24 hours
- ✅ Total under hardware recommendations

#### Test P5: CPU Usage

**Measurement:**
```bash
# Monitor CPU usage
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}"
```

**Expected CPU Usage (Idle):**
| Container | Expected CPU | Notes |
|-----------|--------------|-------|
| zetherion-ai-bot | 0-2% | Idle |
| zetherion-ai-qdrant | 0-1% | Idle |
| zetherion-ai-ollama | 0% | Idle |
| zetherion-ai-skills | 0-1% | Idle |

**Expected CPU Usage (Active - Query):**
| Container | Expected CPU | Notes |
|-----------|--------------|-------|
| zetherion-ai-bot | 10-30% | Processing |
| zetherion-ai-ollama | 200-400% | Inference (multi-core) |

**Validation:**
- ✅ Low CPU usage when idle
- ✅ CPU spikes during queries expected
- ✅ Returns to idle after query

#### Test P6: Disk Usage

**Measurement:**
```bash
# Check volume sizes
docker system df -v
```

**Expected Disk Usage:**
| Component | Expected Size | Notes |
|-----------|---------------|-------|
| zetherion-ai-bot image | 180-220MB | Distroless |
| zetherion-ai-skills image | 180-220MB | Distroless |
| qdrant_storage volume | 100MB-1GB | Grows with vectors |
| ollama_models volume | 5-20GB | Model dependent |
| **Total (llama3.1:8b)** | **6-8GB** | |
| **Total (qwen2.5:32b)** | **20-25GB** | Large model |

**Validation:**
- ✅ Distroless images under 250MB each
- ✅ Volume sizes reasonable
- ✅ Total under documented requirements

### Response Time Benchmarks

#### Test P7: Bot Response Time (Gemini Backend)

**Measurement:**
```bash
# Time Discord bot response
# Send: "@Zetherion AI hello"
# Note timestamp of send and response
```

**Expected Response Times:**
| Query Type | Expected Time | Notes |
|------------|---------------|-------|
| Simple (hello) | 1-3s | Fast |
| Memory search | 1-2s | Vector search |
| Complex query | 3-7s | API call + processing |

**Validation:**
- ✅ Simple queries under 3 seconds
- ✅ Complex queries under 10 seconds
- ✅ Consistent response times

#### Test P8: Bot Response Time (Ollama Backend)

**Expected Response Times:**
| Query Type | Expected Time | Notes |
|------------|---------------|-------|
| Simple (hello) | 2-5s | Local inference |
| Memory search | 2-4s | Vector + local |
| Complex query | 5-15s | Longer inference |

**Validation:**
- ✅ Simple queries under 7 seconds
- ✅ Complex queries under 20 seconds
- ✅ Faster with GPU acceleration

## Security Testing

### Container Security Tests

#### Test S1: Distroless Base Image Verification

**Verify base image:**
```bash
# Check base image
docker inspect zetherion-ai-bot | jq '.[0].Config.Image'
```

**Expected Output:**
```
"gcr.io/distroless/python3-debian12:nonroot"
```

**Validation:**
- ✅ Using distroless base
- ✅ Using nonroot variant
- ✅ No shell present

#### Test S2: Vulnerability Scanning

**Run security scan:**
```bash
# Scan bot image for vulnerabilities
docker scan zetherion-ai-bot:latest

# Or use Trivy
trivy image zetherion-ai-bot:latest
```

**Expected Results:**
- No HIGH or CRITICAL vulnerabilities
- Significantly fewer vulnerabilities than python:3.11-slim
- Most findings in base distroless image (maintained by Google)

**Validation:**
- ✅ Zero CRITICAL vulnerabilities
- ✅ HIGH vulnerabilities under 5
- ✅ Fewer CVEs than standard Python images

#### Test S3: Non-Root User Verification

**Verify all containers run as non-root:**
```bash
# Check each container's user
for container in zetherion-ai-bot zetherion-ai-skills; do
  echo "$container:"
  docker exec $container python3.11 -c "import os; print(f'  UID: {os.getuid()}')"
done
```

**Expected Output:**
```
zetherion-ai-bot:
  UID: 65532
zetherion-ai-skills:
  UID: 65532
```

**Validation:**
- ✅ UID 65532 (nonroot)
- ✅ Not UID 0 (root)
- ✅ Security best practice

### Encryption Tests

#### Test S4: Encryption Enabled

**Prerequisites:** Enable encryption in .env:
```env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=test-passphrase-for-testing-only
```

**Test Steps:**
1. Restart bot with encryption enabled
2. Store memory: `@Zetherion AI remember my secret: test123`
3. Check Qdrant data (should be encrypted)

**Validation:**
```bash
# Query Qdrant directly (data should be encrypted)
curl http://localhost:6333/collections/memories/points/scroll | jq
```

**Expected:**
- Payload data encrypted (not plaintext "test123")
- Metadata may be visible (timestamps, user IDs)
- Bot can decrypt and retrieve correctly

**Validation:**
- ✅ Data encrypted in Qdrant
- ✅ Bot can decrypt and retrieve
- ✅ Raw data not readable in database

### API Key Security Tests

#### Test S5: API Keys Not in Logs

**Test Steps:**
1. View container logs
2. Check for exposed API keys

**Check Logs:**
```bash
# Check bot logs
docker-compose logs zetherion-ai-bot | grep -i "api" | grep -i "key"

# Check start script output
./start.sh 2>&1 | grep -E "(DISCORD_TOKEN|GEMINI_API_KEY|ANTHROPIC_API_KEY)"
```

**Expected Result:**
- No full API keys visible
- At most, first 4 characters shown (e.g., "sk-an****")
- No keys in startup output

**Validation:**
- ✅ API keys redacted in logs
- ✅ Keys not echoed during setup
- ✅ .env file not displayed

#### Test S6: .env File Permissions

**Check file permissions:**

**Unix:**
```bash
ls -la .env
```

**Expected:**
```
-rw------- 1 user user 1234 Feb 07 10:30 .env
```

**Validation:**
- ✅ Owner read/write only (600)
- ✅ Not readable by other users
- ✅ Security best practice

**Windows:**
```powershell
icacls .env
```

**Expected:**
```
.env DOMAIN\User:(R,W)
```

**Validation:**
- ✅ Only owner has access
- ✅ Other users denied

## Troubleshooting Test Failures

### Common Test Failures

#### Failure: Docker Desktop Not Starting

**Symptoms:**
- Script times out waiting for Docker
- "Docker daemon not responding"

**Resolution Steps:**
1. Check WSL 2 (Windows): `wsl --status`
2. Update WSL: `wsl --update`
3. Restart Docker Desktop manually
4. Check Docker Desktop settings (Resources)
5. Restart computer if needed

#### Failure: Health Checks Timeout

**Symptoms:**
- Containers start but never become "healthy"
- Timeout after 120 seconds

**Resolution Steps:**
1. Check Docker resource allocation (Memory)
2. View container logs: `docker-compose logs <service>`
3. Verify ports not in use: `netstat -an | grep 6333`
4. Try clean rebuild: `./cleanup.sh && ./start.sh --force-rebuild`

#### Failure: Model Download Hangs

**Symptoms:**
- Model download stuck at X%
- No progress for 5+ minutes

**Resolution Steps:**
1. Check internet connection
2. Check disk space: `df -h` or `Get-PSDrive`
3. Stop and retry:
   ```bash
   docker exec zetherion-ai-ollama pkill ollama
   docker exec zetherion-ai-ollama ollama pull llama3.1:8b
   ```

#### Failure: Bot Won't Respond in Discord

**Symptoms:**
- Bot shows online but doesn't respond
- No errors in logs

**Resolution Steps:**
1. Verify Message Content Intent enabled
2. Check bot has permissions in channel
3. Test with direct mention: `@Zetherion AI hello`
4. Check logs: `docker-compose logs -f zetherion-ai-bot`
5. Verify Discord token valid

### Debugging Commands

#### View Detailed Container Logs

```bash
# All services
docker-compose logs -f --tail 100

# Specific service
docker-compose logs -f zetherion-ai-bot

# With timestamps
docker-compose logs -f --timestamps zetherion-ai-bot

# Last 50 lines only
docker-compose logs --tail 50 zetherion-ai-bot
```

#### Inspect Container Configuration

```bash
# Full container inspect
docker inspect zetherion-ai-bot | jq

# Check environment variables
docker inspect zetherion-ai-bot | jq '.[0].Config.Env'

# Check health check config
docker inspect zetherion-ai-bot | jq '.[0].State.Health'

# Check volume mounts
docker inspect zetherion-ai-bot | jq '.[0].Mounts'
```

#### Test Service Connectivity

```bash
# Test Qdrant
curl http://localhost:6333/healthz

# Test Qdrant collections
curl http://localhost:6333/collections

# Test Ollama (if enabled)
curl http://localhost:11434/api/tags

# Test Ollama generate
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.1:8b",
  "prompt": "Hello!",
  "stream": false
}'
```

#### Check Resource Usage

```bash
# Real-time stats
docker stats

# Disk usage
docker system df -v

# Network inspection
docker network inspect personalbot_default

# Volume inspection
docker volume inspect zetherion-ai_qdrant_storage
```

## Test Report Template

Use this template to document test results:

```markdown
# Zetherion AI Deployment Test Report

**Date:** YYYY-MM-DD
**Platform:** Windows 11 / macOS 14 / Ubuntu 22.04
**Tester:** [Your Name]
**Version:** [Git commit hash or tag]

## Hardware Configuration
- CPU: [Model, cores, threads]
- RAM: [Total, available]
- GPU: [Model, VRAM] or "None"
- Disk: [Total, free]

## Test Results Summary

| Phase | Tests | Passed | Failed | Notes |
|-------|-------|--------|--------|-------|
| Phase 1: Prerequisites | 3 | 3 | 0 | |
| Phase 2: Hardware Assessment | 5 | 5 | 0 | |
| Phase 3: Configuration | 3 | 3 | 0 | |
| Phase 4: Build & Deploy | 4 | 4 | 0 | |
| Phase 5: Model Download | 3 | 3 | 0 | |
| Phase 6: Verification | 3 | 3 | 0 | |
| Platform-Specific | 9 | 9 | 0 | |
| Container Validation | 6 | 6 | 0 | |
| Integration Tests | 10 | 10 | 0 | |
| Performance Tests | 8 | 8 | 0 | |
| Security Tests | 6 | 6 | 0 | |
| **TOTAL** | **60** | **60** | **0** | |

## Detailed Results

### Phase 1: Prerequisites Check
- ✅ Test 1.1: Docker Detection - Passed
- ✅ Test 1.2: Docker Auto-Start - Passed
- ✅ Test 1.3: Disk Space Warning - Passed

[Continue for all phases...]

## Performance Metrics

- **First-Run Time (Gemini):** 3m 45s
- **First-Run Time (Ollama):** 9m 12s
- **Subsequent Startup:** 32s
- **Build Time:** 1m 58s
- **Model Download Time:** 6m 24s

## Failed Tests

[List any failures with details]

## Recommendations

[Any suggestions for improvements]

## Sign-Off

- [ ] All critical tests passed
- [ ] No security vulnerabilities found
- [ ] Performance within acceptable range
- [ ] Documentation accurate
- [ ] Ready for deployment

**Tester Signature:** _______________________
**Date:** _______________________
```

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Fully Automated Docker Deployment)
