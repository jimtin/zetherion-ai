# Zetherion AI

[![CI Pipeline](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/jimtin/zetherion-ai/actions/workflows/ci.yml)
[![Test Coverage](https://img.shields.io/badge/coverage-87.58%25-brightgreen)](https://github.com/jimtin/zetherion-ai/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A secure, simplified personal AI assistant. Discord-based with vector memory.

## ✨ Key Features

- 🔒 **Security-First**: Pre-commit secret scanning, prompt injection defense, allowlist-based access control
- 🧠 **Smart Routing**: Automatically routes queries to the most cost-effective AI model
- 💾 **Vector Memory**: Long-term semantic memory powered by Qdrant
- 🎯 **87.58% Test Coverage**: Comprehensive unit, integration, and E2E tests
- 🐳 **Docker-Based**: Fully containerized with automated setup and management
- 🌐 **Dual Router Options**: Cloud (Gemini) or Local (Ollama) for privacy
- 🚀 **18-Second Startup**: After initial setup, starts in under 20 seconds

## Quick Start

Zetherion AI includes automated startup scripts that check all prerequisites and start the bot:

```bash
# 1. Clone and configure
git clone https://github.com/youruser/zetherion-ai.git
cd zetherion-ai
cp .env.example .env  # Edit with your API keys (see Setup Guide below)

# 2. Start everything automatically
./start.sh

# Check status
./status.sh

# Stop when done
./stop.sh
```

**The startup script automatically:**
- ✅ Checks Python 3.12+ is installed
- ✅ Checks Docker is running (auto-launches if needed)
- ✅ Validates your .env configuration
- ✅ Prompts router backend choice (Gemini cloud or Ollama local)
- ✅ Detects hardware and recommends optimal Ollama model
- ✅ Manages Docker Desktop memory allocation
- ✅ Creates virtual environment if needed
- ✅ Installs dependencies
- ✅ Starts Qdrant vector database
- ✅ Downloads and starts Ollama (if selected)
- ✅ Launches the bot

**Required**: Discord Bot Token + Gemini API Key (both free tier available)
**Optional**: Anthropic (Claude) or OpenAI (GPT-4) for complex tasks

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

## Management Scripts

Zetherion AI includes three management scripts for easy operation:

### `./start.sh` - Start Zetherion AI
Performs comprehensive checks and starts all services:
- Validates Python 3.12+ installation
- Confirms Docker is running (auto-launches Docker Desktop if needed)
- Checks .env configuration
- **Prompts router backend selection** (first run only):
  - **Gemini (Cloud)**: Fast, cloud-based routing using Google's API
  - **Ollama (Local)**: Privacy-focused, runs AI models on your machine
- **Detects hardware** and recommends optimal Ollama model (if Ollama selected)
- **Manages Docker Desktop memory** automatically for Ollama models
- Sets up Python virtual environment (if needed)
- Installs/updates dependencies
- Starts Qdrant vector database
- Downloads and starts Ollama container and model (if Ollama selected)
- Launches the Discord bot

**First run timing:**
- Gemini backend: ~3 minutes (Docker startup, dependencies)
- Ollama backend: ~9 minutes (includes ~5GB model download)

**Subsequent runs:** ~18 seconds (all containers already exist)

If any prerequisite is missing, it provides clear instructions on how to fix it.

**See also:** [Startup Script Walkthrough](docs/STARTUP_WALKTHROUGH.md) for detailed execution flow.

### `./status.sh` - Check Status
Shows the current state of all components:
- Qdrant container status and health
- Bot process status and uptime
- Virtual environment status
- Configuration validation
- Number of vector collections

Use this to verify everything is running correctly.

### `./stop.sh` - Stop Zetherion AI
Gracefully stops all services:
- Stops the Discord bot process
- Stops the Qdrant container (data is preserved)
- Stops the Ollama container (if running, downloaded models are preserved)

All containers are stopped but not removed, so your data and downloaded models persist.

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

### With Docker (Recommended)
```bash
# Run in development mode with hot reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Run tests
docker compose exec zetherion-ai pytest
```

### Without Docker (Local Development)
```bash
# Install dependencies
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start Qdrant (required for vector memory)
docker run -p 6333:6333 qdrant/qdrant

# Run the bot
python -m zetherion-ai

# Run tests
pytest
```

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
