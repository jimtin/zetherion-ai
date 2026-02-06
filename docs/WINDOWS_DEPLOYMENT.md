# Windows Deployment Guide

Simple, fully automated Docker deployment for Zetherion AI on Windows 10/11.

## 🚀 Quick Start

**One command deployment - no Python installation required:**

### Prerequisites

1. **Administrator PowerShell** (required for installation)
   - Press `Win + X`
   - Select **"Windows PowerShell (Admin)"** or **"Terminal (Admin)"**

2. **That's it!** Script handles Docker and Git installation if needed.

### Installation

```powershell
# 1. Clone repository (or download ZIP from GitHub)
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai

# 2. Run automated deployment
.\start.ps1
```

**First run:** ~3-9 minutes (depending on Ollama vs Gemini)
**Subsequent runs:** ~30 seconds (containers cached)

## What Happens Automatically

### Phase 1: Prerequisites Check
- ✅ Checks if Docker Desktop installed
  - If not found: **Prompts to install via winget**
  - Auto-downloads and installs Docker Desktop
- ✅ Checks if Git installed
  - If not found: **Prompts to install via winget**
- ✅ Checks Docker daemon running
  - If not: **Auto-launches Docker Desktop**
  - Waits up to 60 seconds for Docker to start
- ✅ Validates disk space (warns if <20GB)

### Phase 2: Hardware Assessment
- ✅ Detects CPU model, core count, thread count
- ✅ Checks system RAM and available memory
- ✅ Detects GPU (NVIDIA, AMD, integrated)
- ✅ Recommends optimal Ollama model for your hardware
- ✅ Displays hardware summary

Example output:
```
System Hardware:
  CPU: Intel Core i7-12700K (12 cores, 20 threads)
  RAM: 32 GB total, 24 GB available
  GPU: NVIDIA GeForce RTX 3060 (12GB)

Recommended Ollama Model:
  Model: qwen2.5:14b
  Size: 9.0 GB download
  Quality: High quality, comparable to cloud models
  Speed: Fast (with GPU acceleration)
  Reason: Powerful GPU detected, high-quality model recommended
```

### Phase 3: Configuration Setup
- ✅ Checks if `.env` exists
- ✅ If not found: **Interactive setup wizard**
  - Prompts for Discord Bot Token (required)
  - Prompts for Gemini API Key (required)
  - Prompts for Anthropic API Key (optional)
  - Prompts for OpenAI API Key (optional)
  - Asks router backend choice (Gemini or Ollama)
  - If Ollama: Shows hardware-recommended model, allows override
- ✅ Validates API key formats
- ✅ Generates `.env` file

### Phase 4: Docker Build & Deploy
- ✅ Builds distroless Docker images (~2 minutes)
  - Bot container (~50MB runtime)
  - Skills service container
  - Hardware assessment container
- ✅ Starts all services via docker-compose
- ✅ Waits for health checks (up to 2 minutes)
  - Qdrant vector database
  - Skills service
  - Bot container
  - Ollama (if selected)

### Phase 5: Model Download (if Ollama selected)
- ✅ Checks if model already downloaded
- ✅ If not: Downloads recommended model
  - `llama3.1:8b`: ~4.7GB (~5-7 minutes)
  - `qwen2.5:14b`: ~9.0GB (~7-10 minutes)
  - `qwen2.5:32b`: ~18GB (~15-20 minutes)
- ✅ Shows download progress
- ✅ Verifies model loaded successfully

### Phase 6: Verification
- ✅ Tests Qdrant connection (http://localhost:6333/healthz)
- ✅ Tests Ollama connection (http://localhost:11434/api/tags)
- ✅ Displays container status
- ✅ Shows running containers with health status

### Phase 7: Success
```
============================================================
  Zetherion AI is now running!
============================================================

Next Steps:
  1. View logs:        docker-compose logs -f
  2. Check status:     .\status.ps1
  3. Stop bot:         .\stop.ps1

  4. Invite bot to Discord:
     https://discord.com/developers/applications

Deployment successful!
```

## Management Commands

### Check Status
```powershell
.\status.ps1
```

Shows:
- ✅ Qdrant health and collection count
- ✅ Ollama health and loaded models (if enabled)
- ✅ Skills service health
- ✅ Bot health and uptime
- ✅ Overall operational status
- ✅ Container summary table

### Stop Bot
```powershell
.\stop.ps1
```

- Gracefully stops all containers (30-second timeout)
- **Data preserved**: Volumes (database, models) kept
- Quick restart possible

### Complete Cleanup
```powershell
# Complete removal (prompts for confirmation)
.\cleanup.ps1

# Keep data but remove containers
.\cleanup.ps1 -KeepData

# Keep config but remove everything else
.\cleanup.ps1 -KeepConfig

# Also remove old local Python artifacts
.\cleanup.ps1 -RemoveOldVersion
```

Cleanup options:
- `KeepData`: Preserve Qdrant database and Ollama models
- `KeepConfig`: Preserve `.env` file
- `RemoveOldVersion`: Clean up old local Python installation (if exists)

### View Logs
```powershell
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f zetherion-ai-bot

# Last 50 lines
docker-compose logs --tail 50 zetherion-ai-bot
```

### Restart Services
```powershell
# Quick restart (no rebuild)
docker-compose restart

# Full restart with rebuild
.\stop.ps1
.\start.ps1 --force-rebuild
```

## Hardware Requirements

### Minimum (Gemini Backend)
- Windows 10/11 (64-bit)
- 8GB RAM
- 20GB free disk space
- Any modern CPU (2+ cores)

### Recommended (Ollama Backend)
- Windows 11 (64-bit)
- 16GB RAM (32GB for larger models)
- 30GB free SSD space
- 8+ core CPU or NVIDIA GPU

See [Hardware Recommendations](docs/HARDWARE-RECOMMENDATIONS.md) for detailed specs.

## Configuration

### Edit Configuration
```powershell
# Open .env in Notepad
notepad .env

# Or use your preferred editor
code .env  # VS Code
```

**Required Settings:**
```env
DISCORD_TOKEN=your_discord_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

**Router Backend:**
```env
# Cloud-based (default, fastest setup)
ROUTER_BACKEND=gemini

# Local AI (privacy-focused)
ROUTER_BACKEND=ollama
OLLAMA_ROUTER_MODEL=llama3.1:8b
```

**Optional Settings:**
```env
ANTHROPIC_API_KEY=your_anthropic_key  # For Claude
OPENAI_API_KEY=your_openai_key        # For GPT-4
ALLOWED_USER_IDS=123456789,987654321  # User allowlist
RATE_LIMIT_MESSAGES=10                 # Messages per minute
```

See [Configuration Guide](docs/CONFIGURATION.md) for complete reference.

### Restart After Config Changes
```powershell
.\stop.ps1
.\start.ps1
```

## Troubleshooting

### Docker Desktop Won't Start

**Issue**: Docker daemon not responding after 60 seconds

**Solutions**:
1. Check WSL 2 is installed:
   ```powershell
   wsl --status
   ```
2. Update WSL:
   ```powershell
   wsl --update
   ```
3. Restart computer and try again

### Script Requires Administrator

**Issue**: "This script requires Administrator privileges"

**Solution**: Right-click PowerShell → "Run as Administrator"

### Execution Policy Error

**Issue**: "Cannot be loaded because running scripts is disabled"

**Solution**:
```powershell
# Allow scripts for current session
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process

# Then run start.ps1
.\start.ps1
```

### Out of Memory Errors

**Issue**: Container crashes with "Out of memory"

**Solutions**:
1. Open Docker Desktop
2. Settings → Resources → Memory
3. Increase memory allocation:
   - Gemini: 4GB minimum
   - Ollama (`llama3.1:8b`): 8GB
   - Ollama (`qwen2.5:14b`): 12GB
4. Click "Apply & Restart"

### Model Download Fails

**Issue**: Ollama model download times out or fails

**Solutions**:
1. Check internet connection
2. Verify disk space:
   ```powershell
   Get-PSDrive C | Select-Object Used,Free
   ```
3. Retry download manually:
   ```powershell
   docker exec zetherion-ai-ollama ollama pull llama3.1:8b
   ```

### Port Already in Use

**Issue**: "port is already allocated"

**Solutions**:
1. Find process using port:
   ```powershell
   netstat -ano | findstr :6333
   netstat -ano | findstr :8080
   netstat -ano | findstr :11434
   ```
2. Stop conflicting process or change ports in `docker-compose.yml`

### Container Health Check Failing

**Issue**: Services don't become healthy within 2 minutes

**Solutions**:
1. Check Docker resource allocation (see "Out of Memory" above)
2. View container logs:
   ```powershell
   docker-compose logs zetherion-ai-bot
   ```
3. Check for errors in logs
4. Try clean rebuild:
   ```powershell
   .\cleanup.ps1
   .\start.ps1 --force-rebuild
   ```

## Updating Zetherion AI

### Update to Latest Version
```powershell
# Stop bot
.\stop.ps1

# Pull latest code
git pull origin main

# Rebuild and restart
.\start.ps1 --force-rebuild
```

### Update Specific Components

**Update Docker images only:**
```powershell
docker-compose pull
docker-compose up -d
```

**Update Ollama model:**
```powershell
docker exec zetherion-ai-ollama ollama pull llama3.1:8b
```

## Security Best Practices

### Secure .env File
```powershell
# Set file permissions (owner only)
icacls .env /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

### Enable User Allowlist
```env
# In .env file
ALLOWED_USER_IDS=your_discord_id_here
```

Get your Discord ID:
1. Enable Developer Mode (Discord Settings → Advanced)
2. Right-click your username → Copy User ID

### Rotate API Keys
- **Discord**: Every 6 months
- **AI Providers**: Every 3 months
- **Encryption**: Every 6-12 months

### Enable Encryption
```env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=<strong-random-passphrase>
```

Generate passphrase:
```powershell
# Generate secure random passphrase
-join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | % {[char]$_})
```

See [Security Guide](docs/SECURITY.md) for comprehensive security documentation.

## Performance Optimization

### For Faster Startup
```env
ROUTER_BACKEND=gemini  # Cloud-based, no model download
```

### For Better Quality
```env
ROUTER_BACKEND=ollama
OLLAMA_ROUTER_MODEL=qwen2.5:14b  # Requires 16GB+ RAM
```

### For Lower Costs
```env
ROUTER_BACKEND=gemini  # Free tier (1,500 requests/day)
ANTHROPIC_API_KEY=     # Leave empty (use Gemini for all queries)
```

### GPU Acceleration
- Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- Ollama automatically detects and uses GPU
- 5-10x faster inference vs CPU

## Advanced Features

### Custom Model Selection
```env
# List available models
docker exec zetherion-ai-ollama ollama list

# Pull specific model
docker exec zetherion-ai-ollama ollama pull mistral:7b

# Update .env
OLLAMA_ROUTER_MODEL=mistral:7b
```

### Remote Qdrant Instance
```env
QDRANT_HOST=qdrant.example.com
QDRANT_PORT=6333
QDRANT_USE_TLS=true
```

### Custom Docker Compose
```powershell
# Use custom compose file
docker-compose -f docker-compose.yml -f docker-compose.custom.yml up -d
```

## Monitoring

### Resource Usage
```powershell
# View Docker resource usage
docker stats

# Specific container
docker stats zetherion-ai-bot
```

### Log Monitoring
```powershell
# Continuous log monitoring
docker-compose logs -f --tail 100
```

### Health Endpoints
- Qdrant: http://localhost:6333/dashboard
- Ollama: http://localhost:11434/api/tags

## Getting Help

**Before asking for help:**
1. Check [Troubleshooting](#troubleshooting) section
2. View logs: `docker-compose logs`
3. Check status: `.\status.ps1`

**Where to get help:**
- **[Installation Guide](docs/INSTALLATION.md)** - Detailed setup instructions
- **[Configuration Guide](docs/CONFIGURATION.md)** - All environment variables
- **[Hardware Guide](docs/HARDWARE-RECOMMENDATIONS.md)** - Optimize for your system
- **[GitHub Issues](https://github.com/jimtin/zetherion-ai/issues)** - Bug reports
- **[GitHub Discussions](https://github.com/jimtin/zetherion-ai/discussions)** - Questions

**When reporting issues, include:**
- Windows version (`winver`)
- Docker Desktop version (`docker --version`)
- Output of `.\status.ps1`
- Relevant error messages from `docker-compose logs`

## Additional Resources

- **[Security Guide](docs/SECURITY.md)** - Distroless containers and encryption
- **[Testing Guide](docs/TESTING.md)** - Running tests and CI/CD
- **[Architecture](docs/ARCHITECTURE.md)** - System architecture overview
- **[FAQ](docs/FAQ.md)** - Frequently asked questions

---

**Last Updated:** 2026-02-07
**Version:** 3.0.0 (Fully Automated Docker Deployment)
