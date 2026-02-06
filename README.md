# Zetherion AI

[![CI Pipeline](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml)
[![Test Coverage](https://img.shields.io/badge/coverage-78%25-brightgreen)](https://github.com/jimtin/zetherion-ai/actions)
[![Unit Tests](https://img.shields.io/badge/tests-885-blue)](https://github.com/jimtin/zetherion-ai/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A secure, simplified personal AI assistant. Discord-based with vector memory.

## ✨ Key Features

- 🔒 **Security-First**: AES-256-GCM encryption, pre-commit secret scanning, prompt injection defense
- 🧠 **Smart Routing**: InferenceBroker routes tasks to optimal provider (Claude/OpenAI/Gemini/Ollama)
- 💾 **Encrypted Vector Memory**: Long-term semantic memory powered by Qdrant with field-level encryption
- 📋 **Skills Framework**: Task management, calendar awareness, profile management via natural language
- 👤 **User Profiling**: Builds understanding of you over time with tiered inference
- ⏰ **Proactive Scheduler**: Morning briefings, deadline reminders, end-of-day summaries
- 🎯 **885 Unit Tests**: Comprehensive test coverage (78%)
- 🐳 **Fully Containerized**: 100% Docker deployment with distroless security (no local Python required)
- 🛡️ **Distroless Containers**: Minimal attack surface with Google's distroless base images (~70% smaller)
- 🌐 **Dual Router Options**: Cloud (Gemini) or Local (Ollama) for privacy
- 🤖 **Hardware-Aware**: Automatic hardware assessment and optimal model recommendations
- 🚀 **Fast Deployment**: ~3 minutes (Gemini) or ~9 minutes (Ollama with model download)

## Quick Start

**One command deployment - fully containerized, no local Python required:**

```bash
# 1. Clone repository
git clone https://github.com/youruser/zetherion-ai.git
cd zetherion-ai

# 2. Start (handles everything automatically)
./start.sh      # Mac/Linux
.\start.ps1     # Windows
```

That's it! The script will guide you through interactive setup on first run.

**What the script does automatically (7 phases):**

**Phase 1: Prerequisites**
- ✅ Checks Docker Desktop installed (offers to install if missing)
- ✅ Checks Git installed (offers to install if missing)
- ✅ Validates Docker daemon is running (auto-launches if needed)
- ✅ Checks disk space (warns if <20GB free)

**Phase 2: Hardware Assessment**
- ✅ Detects CPU, RAM, and GPU capabilities
- ✅ Recommends optimal Ollama model for your system
- ✅ Displays hardware summary and model recommendations

**Phase 3: Configuration Setup**
- ✅ Interactive .env generation (first run only)
- ✅ API key validation and format checking
- ✅ Router backend selection (Gemini or Ollama)

**Phase 4: Docker Build & Deploy**
- ✅ Builds distroless container images (secure, minimal attack surface)
- ✅ Starts all services via docker-compose
- ✅ Waits for health checks (Qdrant, Skills, Bot)

**Phase 5: Model Download** (if Ollama selected)
- ✅ Checks if model already downloaded
- ✅ Downloads recommended model (~5-10GB, first time only)
- ✅ Progress indicators during download

**Phase 6: Verification**
- ✅ Tests Qdrant connection
- ✅ Tests Ollama connection (if enabled)
- ✅ Displays container status

**Phase 7: Success**
- ✅ Deployment summary with next steps
- ✅ Troubleshooting commands

**Required**: Discord Bot Token + Gemini API Key (both free tier available)
**Optional**: Anthropic (Claude) or OpenAI (GPT-4) for complex tasks

**Additional Commands:**
```bash
./status.sh     # Check status of all containers
./stop.sh       # Stop all containers (data preserved)
./cleanup.sh    # Complete removal and reset
```

## 📚 Documentation

- **[Command Reference](docs/COMMANDS.md)** - Complete list of Discord commands for testing
- **[Testing Guide](docs/TESTING.md)** - Unit tests, integration tests, and CI/CD
- **[Troubleshooting Guide](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[Docker Architecture](docs/DOCKER_ARCHITECTURE.md)** - Understanding Docker Desktop vs containers, memory management
- **[Startup Script Walkthrough](docs/STARTUP_WALKTHROUGH.md)** - Complete guide to start.sh execution flow
- **[FAQ](docs/FAQ.md)** - Frequently asked questions
- **[Setup Guide](#setup-guide)** - Detailed setup instructions below
- **[GitHub Wiki](../../wiki)** - Community-maintained documentation

**Quick Links:**
- [All Discord Commands](docs/COMMANDS.md) - Testing checklist & examples
- [Docker Memory Management](docs/DOCKER_ARCHITECTURE.md#automated-memory-management) - Automated Ollama memory allocation
- [Startup Script Phases](docs/STARTUP_WALKTHROUGH.md#phase-by-phase-breakdown) - Detailed execution flow
- [Discord Privileged Intents Error](docs/TROUBLESHOOTING.md#error-privilegedintentsrequired)
- [Qdrant Connection Issues](docs/TROUBLESHOOTING.md#qdrant-connection-issues)
- [Configuration Problems](docs/TROUBLESHOOTING.md#configuration-issues)

## Hardware Requirements

### Minimum Requirements
- **OS**: Windows 10/11, macOS 10.15+, or Linux (Ubuntu 20.04+)
- **Docker**: Docker Desktop 4.0+ with at least 4GB RAM allocated
- **Disk**: 20GB free space (10GB for Docker images, 5-10GB for Ollama models if used)
- **RAM**: 8GB system RAM (4GB minimum)
- **Network**: Internet connection for API calls and initial setup

### Recommended for Ollama (Local AI)
- **RAM**: 16GB+ system RAM
- **Docker Memory**: 8-12GB allocated to Docker Desktop
- **GPU** (optional but improves performance):
  - NVIDIA GPU with 8GB+ VRAM (RTX 3060 or better)
  - AMD GPU with ROCm support
  - Apple Silicon (M1/M2/M3) with Metal support

The `start.sh`/`start.ps1` script automatically detects your hardware and recommends the optimal model configuration.

## Management Scripts

Zetherion AI includes four management scripts for complete lifecycle management:

### `./start.sh` (or `start.ps1` on Windows)
**Fully automated 7-phase deployment** - handles everything from prerequisites to final verification.

**First run timing:**
- Gemini backend: ~3 minutes (Docker build, startup)
- Ollama backend: ~9 minutes (includes ~5GB model download)

**Subsequent runs:** Quick startup (containers cached, ~30 seconds)

**Options:**
- `--skip-hardware-assessment`: Skip hardware detection and use default model
- `--force-rebuild`: Force rebuild Docker images even if cached

**See also:** [Startup Script Walkthrough](docs/STARTUP_WALKTHROUGH.md) for detailed execution flow.

### `./status.sh` (or `status.ps1` on Windows)
Shows real-time status of all components:
- ✅ Qdrant vector database health and collection count
- ✅ Ollama service health and loaded models (if enabled)
- ✅ Skills service health check
- ✅ Bot container health and uptime
- ✅ Overall operational status

Use this to verify everything is running correctly.

### `./stop.sh` (or `stop.ps1` on Windows)
Gracefully stops all Docker containers:
- Stops bot, skills service, Qdrant, and Ollama (if running)
- **Data is preserved**: All volumes (database, models) are kept
- Containers stopped but not removed for quick restart

### `./cleanup.sh` (or `cleanup.ps1` on Windows)
**Complete removal and reset** - useful for fresh reinstalls:

**Options:**
- `--keep-data`: Preserve Qdrant database and Ollama models
- `--keep-config`: Preserve .env configuration file
- `--remove-old-version`: Also remove old local Python artifacts (.venv, __pycache__)

**Default behavior** (with confirmations):
- Removes all Docker containers, images, and volumes
- Removes .env configuration
- Removes build artifacts
- Shows summary of remaining resources

**Examples:**
```bash
./cleanup.sh                      # Complete cleanup (prompts for confirmation)
./cleanup.sh --keep-data          # Remove containers but keep database/models
./cleanup.sh --keep-config        # Remove everything except .env
./cleanup.sh --remove-old-version # Also clean old local Python installation
```

## Setup Guide

### 1. Create Discord Bot
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and name it (e.g., "Zetherion AI").
3. Go to the **Bot** tab and click **Reset Token**.
   - Copy this token immediately (you won't see it again).
   - Uncheck "Public Bot" if you want it private.
   - **Note**: If you get a "Private application cannot have a default authorization link" error:
     1. Go to the **Installation** tab.
     2. Ensure **Install Link** is set to **None**.
     3. Save changes, then uncheck "Public Bot" again.
   - **Privileged Gateway Intents**:
     - **Message Content Intent**: Toggle this **ON** (Required to read messages).
     - *Presence Intent* and *Server Members Intent* can be left **OFF**.
   - **Bot Permissions**: You can ignore the permissions calculator here (we will set them in Step 2).

### 2. Invite to Server
1. Go to the **OAuth2** tab -> **URL Generator**.
2. Select Scopes:
   - `bot`
   - `applications.commands` (for slash commands like /ask)
3. Select Bot Permissions:
   - `Send Messages`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
   - `View Channels`
4. Copy the generated URL at the bottom. **This is your bot's OAuth2 Invite URL.**
5. Paste this URL into your browser to initiate the OAuth flow.
6. Select the server you want to add the bot to and click **Authorize**. This adds the bot user to your server.

### 3. Get Required API Keys

#### A. Discord Bot Token (Required)
You already have this from Step 1 above.

#### B. Gemini API Key (Required)
Gemini is used for embeddings, routing, and simple queries. This is **required** for Zetherion AI to function.

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in with your Google account
3. Click **"Get API key"** or **"Create API key"**
4. Select or create a Google Cloud project
5. Click **"Create API key in existing project"** or **"Create API key in new project"**
6. Copy the API key (starts with `AIza...`)
7. **Important**: This key gives access to your Google AI services, keep it secure

**Pricing**: Gemini has a generous free tier:
- Free tier: 15 requests per minute, 1,500 requests per day
- See current pricing: https://ai.google.dev/pricing

#### C. Anthropic API Key (Optional)
Claude is used for complex tasks requiring advanced reasoning. **Optional** but recommended for best quality.

1. Go to [Anthropic Console](https://console.anthropic.com/)
2. Sign up or sign in
3. Go to **Settings** → **API Keys**
4. Click **"Create Key"**
5. Give it a name (e.g., "Zetherion AI")
6. Copy the API key (starts with `sk-ant-...`)
7. **Add credits**: You'll need to add payment method and credits to use Claude
   - Go to **Settings** → **Billing**
   - Add payment method
   - Purchase credits ($5 minimum)

**Pricing**:
- Claude 3.5 Sonnet: ~$3 per million input tokens, ~$15 per million output tokens
- See current pricing: https://www.anthropic.com/pricing

#### D. OpenAI API Key (Optional)
GPT-4o is used as an alternative to Claude for complex tasks if Claude isn't available. **Optional**.

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Sign up or sign in
3. Click your profile icon → **"View API keys"** or go to https://platform.openai.com/api-keys
4. Click **"Create new secret key"**
5. Give it a name (e.g., "Zetherion AI")
6. Copy the API key (starts with `sk-...`) - **you won't see it again!**
7. **Add credits**:
   - Go to **Settings** → **Billing**
   - Add payment method
   - Add credits ($5 minimum recommended)

**Pricing**:
- GPT-4o: ~$2.50 per million input tokens, ~$10 per million output tokens
- See current pricing: https://openai.com/api/pricing/

#### E. Discord User IDs (Optional - for allowlist)
Restrict bot access to specific users only.

1. Enable Developer Mode in Discord:
   - Open Discord → **User Settings** (⚙️)
   - Go to **App Settings** → **Advanced**
   - Toggle **"Developer Mode"** ON
2. Get User IDs:
   - Right-click on your username (or any user)
   - Click **"Copy User ID"**
   - This gives you a numeric ID (e.g., `123456789012345678`)
3. Add multiple IDs separated by commas in `.env`:
   ```
   ALLOWED_USER_IDS=123456789012345678,987654321098765432
   ```
4. Leave empty to allow all users (not recommended for production)

### 4. Configure Environment
1. Clone the repository:
   ```bash
   git clone https://github.com/youruser/zetherion-ai.git
   cd zetherion-ai
   ```
2. Create your config file:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` with your API keys from above:
   ```bash
   nano .env  # or use your preferred editor
   ```

   **Minimum required configuration**:
   ```env
   DISCORD_TOKEN=your_discord_token_here
   GEMINI_API_KEY=your_gemini_key_here
   ALLOWED_USER_IDS=your_user_id_here
   ```

   **Full configuration** (with Claude and OpenAI):
   ```env
   DISCORD_TOKEN=your_discord_token_here
   GEMINI_API_KEY=your_gemini_key_here
   ANTHROPIC_API_KEY=your_anthropic_key_here
   OPENAI_API_KEY=your_openai_key_here
   ALLOWED_USER_IDS=123456789012345678,987654321098765432
   LOG_LEVEL=INFO
   ```

**Note**: At minimum, you need `DISCORD_TOKEN` and `GEMINI_API_KEY`. Without Claude or OpenAI, all queries will use Gemini Flash (fast and cheap, but less capable for complex tasks).

## Testing & Quality Assurance

Zetherion AI maintains **87.58% test coverage** with a comprehensive three-tier testing approach:

### Test Coverage by Module

| Module | Coverage | Tests | Status |
|--------|----------|-------|--------|
| **Router Factory** | 100% | 12 tests | ✅ Comprehensive async/sync factory, health checks, fallback logic |
| **Discord Bot** | 89.92% | 30 tests | ✅ Commands, edge cases, message splitting, authorization |
| **Agent Core** | 94.76% | 41 tests | ✅ Retry logic, context building, dual generators |
| **Security** | 94.12% | 37 tests | ✅ Rate limiting, allowlist, 24+ prompt injection patterns |
| **Config** | 96.88% | 49 tests | ✅ Settings validation, SecretStr, environment isolation |
| **Qdrant Memory** | 88.73% | 7 tests | ✅ Vector operations, embeddings, async client |
| **Overall** | **87.58%** | **255 tests** | ✅ All passing |

### Three-Tier Testing Pyramid

1. **Unit Tests** (255 tests, ~24s)
   - Fast, isolated tests with mocked dependencies
   - Run automatically on every push via pre-commit hooks
   - 100% async/await support

2. **Integration Tests** (14 tests, ~2min)
   - Full stack with Docker services (Qdrant + Ollama)
   - MockDiscordBot bypasses Discord API
   - Parametrized: all tests run against both Gemini and Ollama backends

3. **Discord E2E Tests** (4 tests, ~1min)
   - Real Discord API integration
   - Tests bot responses, memory operations, slash commands
   - Optional: requires test bot credentials

### CI/CD Pipeline

Every push triggers a comprehensive 7-job pipeline:

1. ✅ **Lint & Format** (Ruff) - Code style and formatting
2. ✅ **Type Check** (Mypy strict mode) - Static type analysis
3. ✅ **Security Scan** (Bandit + Gitleaks) - Vulnerability and secret detection
4. ✅ **Unit Tests** (Python 3.12 & 3.13) - Cross-version compatibility
5. ✅ **Docker Build** - Container image validation
6. ✅ **Integration Tests** - Full stack testing with services
7. ✅ **Discord E2E Tests** - Real Discord API (if secrets configured)

**See**: [Testing Guide](docs/TESTING.md) for detailed documentation

### Pre-Commit Hooks

Automated checks on every `git commit`:
- 🔍 **Gitleaks** - Prevents secrets from entering version control (12 rules, zero false positives)
- 🎨 **Ruff** - Fast linting and auto-formatting
- 🔧 **Mypy** - Strict type checking
- 🛡️ **Bandit** - Security issue detection
- 🐳 **Hadolint** - Dockerfile best practices

**Setup**: Run `./scripts/setup-git-hooks.sh` (automatic on first `./start.sh`)

## Development

Zetherion AI uses **100% Docker-based development** with distroless containers for security:

### Running in Development Mode
```bash
# Run with hot reload (development mode)
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# View logs
docker-compose logs -f zetherion-ai-bot

# Run tests inside container
docker-compose exec zetherion-ai-bot pytest

# Run specific test file
docker-compose exec zetherion-ai-bot pytest tests/test_agent.py -v

# Check test coverage
docker-compose exec zetherion-ai-bot pytest --cov=zetherion_ai --cov-report=html
```

### Distroless Container Architecture

Zetherion AI uses **Google's distroless base images** for enhanced security:

**Benefits:**
- ✅ **70% smaller attack surface** - No shell, no package managers, no OS utilities
- ✅ **Fewer vulnerabilities** - Only Python runtime, no extraneous binaries
- ✅ **50MB runtime images** vs ~150MB with standard python:slim
- ✅ **Passes GitHub security scans** - Zero critical/high CVEs

**Multi-stage builds:**
1. **Builder stage** (`python:3.11-slim`): Installs dependencies with pip
2. **Runtime stage** (`gcr.io/distroless/python3-debian12:nonroot`): Minimal execution environment
3. **Import verification**: Ensures all imports work before creating final image

**See:** [docs/SECURITY.md](docs/SECURITY.md) for detailed security documentation.

## Troubleshooting

### "Invalid Discord Token" Error
- Ensure token is correct in `.env` (no quotes, no spaces)
- Regenerate token in Discord Developer Portal if compromised
- Check bot has "Message Content Intent" enabled

### "Gemini API Error" or "Rate Limit"
- Verify API key is correct: https://aistudio.google.com/app/apikey
- Check you haven't exceeded free tier (15 req/min, 1500 req/day)
- Wait a few minutes and try again if rate limited

### "Claude/OpenAI Not Available"
- These are optional - bot works without them (uses Gemini for all queries)
- Check API key is correct and has credits
- View logs with `docker compose logs zetherion-ai` for details

### Bot Not Responding
- Check bot is online in Discord server members list
- Verify you've added your User ID to `ALLOWED_USER_IDS` (or left it empty)
- Check logs: `docker compose logs -f zetherion-ai`
- Ensure "Message Content Intent" is enabled in Discord Developer Portal

### Memory/Vector Search Not Working
- Ensure Qdrant is running: `docker compose ps`
- Check Qdrant UI: http://localhost:6333/dashboard
- View collections at: http://localhost:6333/dashboard#/collections

## Security

Zetherion AI implements defense-in-depth with multiple security layers:

### Distroless Containers
- **Base Images**: Google's `gcr.io/distroless/python3-debian12:nonroot`
- **No Shell**: Cannot execute arbitrary commands even if compromised
- **No Package Managers**: Cannot install malware or additional packages
- **Minimal Surface**: Only Python 3.11 runtime and application code
- **Non-Root User**: Containers run as UID 65532 (nonroot) by default
- **Size**: ~50MB runtime vs ~150MB standard images (70% reduction)

### Application Security
- **AES-256-GCM Encryption**: Field-level encryption for sensitive vector data
- **Prompt Injection Defense**: 24+ detection patterns for malicious prompts
- **Rate Limiting**: 10 messages/minute per user (configurable)
- **User Allowlist**: Restrict access to specific Discord users
- **Secret Scanning**: Pre-commit hooks prevent API keys from entering git
- **API Key Validation**: Format checking for all provider keys

### CI/CD Security
- **Bandit**: Python security linting on every commit
- **Gitleaks**: Secret detection in git history and commits
- **Hadolint**: Dockerfile security best practices
- **GitHub Security Scanning**: Automatic vulnerability detection
- **Dependency Scanning**: Automated updates for vulnerable packages

**See:** [docs/SECURITY.md](docs/SECURITY.md) for comprehensive security documentation.

## Architecture

- **Discord Bot**: Main interface (discord.py)
- **Qdrant**: Vector database for semantic memory
- **Router Backend** (choose one):
  - **Gemini Flash**: Cloud-based routing and simple queries (default, free tier)
  - **Ollama**: Local AI models for routing (privacy-focused, runs on your machine)
- **Gemini Embeddings**: High-quality text embeddings (free tier)
- **Claude Sonnet 4.5**: Complex tasks requiring reasoning (optional, paid)
- **GPT-4o**: Alternative for complex tasks (optional, paid)

### Intelligent Routing
Zetherion AI automatically routes messages to the most cost-effective model:
- **Simple queries** (greetings, quick facts) → Router backend (Gemini or Ollama)
- **Complex tasks** (analysis, code, reasoning) → Claude/GPT-4 (paid, if available)

### Router Backend Options

**Gemini (Cloud)**
- ✅ Fast startup (~3 minutes first time)
- ✅ No additional downloads
- ✅ Generous free tier (15 req/min, 1,500 req/day)
- ⚠️ Sends routing data to Google's cloud
- Best for: Cloud-based workflows, minimal setup

**Ollama (Local)**
- ✅ Privacy-focused (no data sent to cloud for routing)
- ✅ Free inference (runs on your machine)
- ✅ Automated memory management (script handles Docker configuration)
- ⚠️ Longer startup (~9 minutes first time due to ~5GB model download)
- ⚠️ Requires sufficient RAM (8-16GB recommended)
- Best for: Privacy-conscious users, offline capability

**Model Recommendations (Ollama):**
The startup script automatically detects your hardware (CPU, RAM, GPU) and recommends:
- **phi3:mini** (5GB Docker RAM): For systems with 4-8GB RAM
- **llama3.1:8b** (8GB Docker RAM): Balanced quality/performance
- **qwen2.5:7b** (10GB Docker RAM): Best quality (recommended for 16GB+ RAM or GPU)
- **mistral:7b** (7GB Docker RAM): Fastest inference

**See also:** [Docker Architecture](docs/DOCKER_ARCHITECTURE.md) for details on memory management.

## Cost Optimization

### Free Tier (Gemini Only)
- **Cost**: Free (within quotas)
- **Setup**: Only requires `DISCORD_TOKEN` + `GEMINI_API_KEY`
- **Capabilities**: Great for simple queries, basic conversations
- **Limitations**: Less capable for complex reasoning, code generation

### Hybrid (Recommended)
- **Cost**: ~$0.01-0.10 per day for moderate use
- **Setup**: Add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
- **Capabilities**: Full power - simple queries free, complex tasks paid
- **Routing**: Automatically uses cheap Gemini for simple tasks, Claude/GPT for complex

### Cost-Saving Tips
1. **Use allowlist**: Set `ALLOWED_USER_IDS` to prevent unauthorized usage
2. **Rate limiting**: Built-in (10 messages/minute per user)
3. **Smart routing**: The router minimizes expensive model usage
4. **Monitor usage**:
   - Anthropic: https://console.anthropic.com/settings/usage
   - OpenAI: https://platform.openai.com/usage
   - Google: https://console.cloud.google.com/apis/dashboard

## License

MIT
