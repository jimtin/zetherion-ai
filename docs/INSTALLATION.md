# Installation Guide

Complete step-by-step installation guide for Zetherion AI on Windows, macOS, and Linux.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Platform-Specific Installation](#platform-specific-installation)
  - [Windows Installation](#windows-installation)
  - [macOS Installation](#macos-installation)
  - [Linux Installation](#linux-installation)
- [Getting API Keys](#getting-api-keys)
- [First-Time Setup](#first-time-setup)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Overview

Zetherion AI is **100% containerized** using Docker. No local Python installation is required. The installation process is automated through platform-specific startup scripts:

- **Windows**: `start.ps1` (PowerShell)
- **macOS/Linux**: `start.sh` (Bash)

**Estimated Time:**
- First run: 3-9 minutes (depending on backend choice)
- Subsequent runs: ~30 seconds (containers cached)

## Prerequisites

### Required

1. **Operating System**
   - Windows 10/11 (64-bit)
   - macOS 10.15+ (Catalina or later)
   - Linux: Ubuntu 20.04+, Debian 11+, Fedora 35+, or compatible

2. **Hardware**
   - **Minimum**: 8GB RAM, 20GB free disk space
   - **Recommended**: 16GB RAM, 30GB free SSD space
   - See [Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md) for detailed specs

3. **Network**
   - Internet connection (for initial setup and API calls)
   - Broadband recommended for Ollama model downloads

### Automatically Installed (if missing)

The startup script will **offer to install** these if not present:

1. **Docker Desktop** (required)
   - Windows/Mac: Installed via package manager
   - Linux: Manual installation required

2. **Git** (recommended)
   - Used for cloning repository
   - Optional but recommended

## Platform-Specific Installation

### Windows Installation

#### Step 1: Open PowerShell as Administrator

**Why Administrator?** Required to install Docker Desktop and Git if missing.

1. Press `Win + X`
2. Select **"Windows PowerShell (Admin)"** or **"Terminal (Admin)"**
3. Click **"Yes"** on the UAC prompt

#### Step 2: Clone Repository

```powershell
# Navigate to desired location
cd $HOME\Documents

# Clone repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
```

**Don't have Git?** Download ZIP from GitHub:
1. Go to https://github.com/jimtin/zetherion-ai
2. Click **"Code"** → **"Download ZIP"**
3. Extract to desired location
4. Open PowerShell in that folder

#### Step 3: Run Startup Script

```powershell
# Run the automated deployment script
.\start.ps1
```

**What happens next:**
- Script checks for Docker Desktop (offers to install if missing)
- Script checks for Git (offers to install if missing)
- Launches Docker Desktop if not running
- Guides you through interactive configuration
- Builds and starts all containers

#### Step 4: Follow Interactive Prompts

The script will ask for:
1. **Discord Bot Token** (required)
2. **Gemini API Key** (required)
3. **Router Backend** (Gemini or Ollama)
4. **Ollama Model** (if Ollama selected, shows hardware recommendation)

**First run timing:**
- **Gemini backend**: ~3 minutes
- **Ollama backend**: ~9 minutes (includes model download)

### macOS Installation

#### Step 1: Open Terminal

1. Press `Cmd + Space`
2. Type **"Terminal"**
3. Press `Enter`

#### Step 2: Clone Repository

```bash
# Navigate to desired location
cd ~/Documents

# Clone repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
```

**Don't have Git?** Homebrew will prompt to install Command Line Tools.

#### Step 3: Run Startup Script

```bash
# Make script executable
chmod +x start.sh

# Run the automated deployment script
./start.sh
```

**What happens next:**
- Script checks for Docker Desktop (offers to install via Homebrew)
- Script checks for Git (offers to install via Homebrew)
- Launches Docker Desktop if not running
- Guides you through interactive configuration
- Builds and starts all containers

#### Step 4: Grant Permissions

Docker Desktop may request permissions:
- **File Access**: Allow (needed for volumes)
- **Network**: Allow (needed for containers)

Click **"OK"** on all prompts.

### Linux Installation

#### Step 1: Install Docker (if needed)

**Ubuntu/Debian:**
```bash
# Update package index
sudo apt-get update

# Install prerequisites
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Set up repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add user to docker group (avoid sudo)
sudo usermod -aG docker $USER
newgrp docker
```

**Fedora:**
```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
newgrp docker
```

#### Step 2: Clone Repository

```bash
# Navigate to desired location
cd ~/Documents

# Clone repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai
```

#### Step 3: Run Startup Script

```bash
# Make script executable
chmod +x start.sh

# Run the automated deployment script
./start.sh
```

**Note**: On Linux, the script will **not** auto-install Docker. You must install it manually (see Step 1).

## Getting API Keys

Before running the setup, gather these API keys:

### 1. Discord Bot Token (Required)

**Steps:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"**
3. Name it (e.g., "Zetherion AI")
4. Go to **"Bot"** tab → Click **"Reset Token"**
5. **Copy token immediately** (you won't see it again)
6. Enable **"Message Content Intent"** (required)

**Invite Bot to Server:**
1. Go to **"OAuth2"** → **"URL Generator"**
2. Select scopes: `bot`, `applications.commands`
3. Select permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `View Channels`
4. Copy generated URL and open in browser
5. Select server and authorize

### 2. Gemini API Key (Required)

**Steps:**
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in with Google account
3. Click **"Create API key"**
4. Select or create Google Cloud project
5. **Copy API key** (starts with `AIzaSy...`)

**Pricing**: Free tier (1,500 requests/day) - sufficient for most users

### 3. Anthropic API Key (Optional)

For Claude Sonnet 4.5 (complex reasoning tasks):

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Sign up or sign in
3. Go to **Settings** → **API Keys**
4. Click **"Create Key"**
5. **Copy key** (starts with `sk-ant-...`)
6. Add payment method and credits ($5 minimum)

**Pricing**: ~$3 per million input tokens

### 4. OpenAI API Key (Optional)

For GPT-4o (alternative to Claude):

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Sign in or create account
3. Click profile → **"View API keys"**
4. Click **"Create new secret key"**
5. **Copy key** (starts with `sk-...`)
6. Add payment method and credits

**Pricing**: ~$2.50 per million input tokens

## First-Time Setup

### Interactive Configuration

When you run `start.ps1` or `start.sh` for the first time, you'll be guided through setup:

#### 1. Prerequisites Check

The script checks:
- ✅ Docker Desktop installed and running
- ✅ Git installed (optional)
- ✅ Sufficient disk space (20GB+)

**Auto-Install Prompts:**
```
Docker Desktop not found
Install Docker Desktop? (Y/n):
```

Type `Y` and press Enter to auto-install.

#### 2. Hardware Assessment

Script detects your system:
```
System Hardware:
  CPU: Intel Core i7-12700K (12 cores, 20 threads)
  RAM: 32 GB total, 24 GB available
  GPU: NVIDIA GeForce RTX 3060 (12GB)

Recommended Ollama Model:
  Model: qwen2.5:14b
  Size: 9.0 GB download
  Quality: High
  Speed: Fast (with GPU)
  Reason: Powerful GPU detected, high-quality model recommended
```

#### 3. Configuration Setup

Prompts for API keys:

```
Discord Bot Token (required):
> MTQ2ODc4MDQxODY1MTI2MzEyOQ.GGFum2.lsf_abc123...

Gemini API Key (required):
> AIzaSyCO9WodgUFJfW-7qK4Vtbnc...

Anthropic API Key (optional, press Enter to skip):
> sk-ant-api03-OEKnlIipBFzx...

OpenAI API Key (optional, press Enter to skip):
> [Enter]
```

#### 4. Router Backend Selection

```
Router Backend:
  1. Gemini (cloud-based, fast, minimal resources)
  2. Ollama (local, private, ~5GB download)

Choose [1/2]: 2
```

**If you choose Ollama:**
```
Recommended model for your hardware: qwen2.5:14b
Use recommended model? (Y/n): Y
```

#### 5. Deployment

Script automatically:
1. Builds distroless Docker images (~2 minutes)
2. Starts all containers (Qdrant, Skills, Bot, Ollama if selected)
3. Waits for health checks
4. Downloads Ollama model if selected (~5-7 minutes)
5. Verifies all services running

#### 6. Success

```
============================================================
  Zetherion AI is now running!
============================================================

Next Steps:
  1. View logs:        docker-compose logs -f
  2. Check status:     ./status.sh
  3. Stop bot:         ./stop.sh

  4. Invite bot to Discord:
     https://discord.com/developers/applications

Deployment successful!
```

## Verification

### Check Container Status

**Windows:**
```powershell
.\status.ps1
```

**macOS/Linux:**
```bash
./status.sh
```

**Expected Output:**
```
============================================================
  Zetherion AI Status
============================================================

[OK] Qdrant is running and healthy
    Collections: 0

[OK] Ollama is running and healthy
    Models: 1
      - qwen2.5:14b

[OK] Skills service is running and healthy

[OK] Bot is running and healthy
    Uptime: 0d 0h 2m 15s

[OK] Zetherion AI is fully operational

Container Summary:
zetherion-ai-qdrant   Up 2 minutes (healthy)
zetherion-ai-ollama   Up 2 minutes (healthy)
zetherion-ai-skills   Up 2 minutes (healthy)
zetherion-ai-bot      Up 2 minutes (healthy)
```

### Test Discord Bot

1. Open Discord
2. Go to server where bot was invited
3. Type: `@Zetherion AI hello`
4. Bot should respond within 1-2 seconds

**If bot doesn't respond:**
- Check bot is online in member list
- Verify "Message Content Intent" is enabled
- Check logs: `docker-compose logs -f zetherion-ai-bot`

### Check Qdrant Dashboard

Open browser: http://localhost:6333/dashboard

You should see:
- Qdrant UI loads
- Collections tab (may be empty initially)
- Cluster info shows healthy

### Check Ollama (if enabled)

Open browser: http://localhost:11434/api/tags

You should see JSON with loaded models:
```json
{
  "models": [
    {
      "name": "qwen2.5:14b",
      "modified_at": "2026-02-07T10:30:00Z",
      "size": 8900000000
    }
  ]
}
```

## Troubleshooting

### Docker Desktop Won't Start

**Windows:**
1. Check WSL 2 is installed: `wsl --status`
2. Update WSL: `wsl --update`
3. Restart computer
4. Try starting Docker Desktop manually

**macOS:**
1. Check System Preferences → Security & Privacy
2. Allow Docker Desktop if blocked
3. Restart Docker Desktop from menu bar

**Linux:**
1. Check Docker service: `sudo systemctl status docker`
2. Start Docker: `sudo systemctl start docker`
3. Enable auto-start: `sudo systemctl enable docker`

### Script Says "Docker Not Found" After Installing

**Issue**: PATH not refreshed after installation

**Solution:**
1. Close and reopen terminal/PowerShell
2. Run script again
3. If still not working, restart computer

### "Permission Denied" Errors (Linux)

**Issue**: User not in docker group

**Solution:**
```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Activate group membership
newgrp docker

# Or logout and login again
```

### Out of Memory Errors

**Issue**: Docker memory allocation too low

**Solution:**
1. Open Docker Desktop
2. Settings → Resources → Memory
3. Increase to recommended amount:
   - Gemini: 4GB minimum
   - Ollama (llama3.1:8b): 8GB
   - Ollama (qwen2.5:14b): 12GB
4. Click "Apply & Restart"

### Model Download Fails

**Issue**: Network timeout or disk space

**Solutions:**
1. Check internet connection
2. Verify disk space: `df -h` (Linux/Mac) or `Get-PSDrive` (Windows)
3. Retry download:
   ```bash
   docker exec zetherion-ai-ollama ollama pull llama3.1:8b
   ```

### "Port Already in Use"

**Issue**: Another service using ports 6333, 8080, or 11434

**Solutions:**
1. Find process using port:
   ```bash
   # Linux/Mac
   lsof -i :6333

   # Windows
   netstat -ano | findstr :6333
   ```
2. Stop conflicting service or change Zetherion AI ports in `docker-compose.yml`

### Interactive Setup Fails

**Issue**: Python error during setup

**Solution:**
```bash
# Manually create .env from template
cp .env.example .env

# Edit with your favorite editor
nano .env  # or vim, code, notepad, etc.

# Fill in at minimum:
# DISCORD_TOKEN=your_token_here
# GEMINI_API_KEY=your_key_here
# ROUTER_BACKEND=gemini

# Save and run start script again
./start.sh  # or start.ps1 on Windows
```

## Next Steps

After successful installation:

### 1. Explore Commands

Try these Discord commands:
```
@Zetherion AI what can you do?
@Zetherion AI remember I prefer Python for coding
@Zetherion AI search for what I told you about coding
```

See [Command Reference](COMMANDS.md) for full list.

### 2. Configure Advanced Features

Edit `.env` to customize:
- Rate limiting
- User allowlist
- Logging level
- Model selection

See [Configuration Guide](CONFIGURATION.md) for details.

### 3. Set Up Encryption (Optional)

Enable AES-256-GCM encryption for vector storage:

```bash
# Generate encryption passphrase
openssl rand -base64 32

# Add to .env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE="your-generated-passphrase"

# Restart bot
./stop.sh && ./start.sh
```

### 4. Monitor Performance

Check system resources:
```bash
# View container resource usage
docker stats

# View bot logs
docker-compose logs -f zetherion-ai-bot

# Check Qdrant metrics
curl http://localhost:6333/metrics
```

### 5. Set Up Backups

Backup important data:

```bash
# Backup Qdrant data
docker run --rm -v zetherion-ai_qdrant_storage:/data \
  -v $(pwd)/backups:/backup alpine \
  tar czf /backup/qdrant-backup-$(date +%Y%m%d).tar.gz /data

# Backup .env (careful - contains secrets!)
cp .env .env.backup
chmod 600 .env.backup
```

### 6. Update Regularly

Keep Zetherion AI up to date:

```bash
# Pull latest code
git pull origin main

# Rebuild containers
./stop.sh
./start.sh --force-rebuild
```

## Additional Resources

- **[Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md)** - Optimize for your system
- **[Configuration Guide](CONFIGURATION.md)** - Customize settings
- **[Security Guide](SECURITY.md)** - Distroless containers and encryption
- **[Troubleshooting](TROUBLESHOOTING.md)** - Common issues and solutions
- **[GitHub Discussions](https://github.com/jimtin/zetherion-ai/discussions)** - Community help

## Getting Help

**Before asking for help:**
1. Check [Troubleshooting Guide](TROUBLESHOOTING.md)
2. Review logs: `docker-compose logs`
3. Check [GitHub Issues](https://github.com/jimtin/zetherion-ai/issues)

**Where to get help:**
- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: General questions and community support
- **Discord Server**: (link if available)

**When reporting issues, include:**
- Operating system and version
- Docker Desktop version
- Output of `./status.sh` or `status.ps1`
- Relevant error messages from logs
- Steps to reproduce

---

**Congratulations!** You've successfully installed Zetherion AI. Enjoy your new AI assistant! 🎉
