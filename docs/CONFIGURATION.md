# Configuration Guide

Complete reference for configuring Zetherion AI via environment variables in `.env` file.

## Table of Contents

- [Overview](#overview)
- [Required Configuration](#required-configuration)
- [Router Backend Configuration](#router-backend-configuration)
- [Optional API Keys](#optional-api-keys)
- [Security Configuration](#security-configuration)
- [Performance Tuning](#performance-tuning)
- [Logging Configuration](#logging-configuration)
- [Advanced Settings](#advanced-settings)
- [Environment-Specific Configs](#environment-specific-configs)
- [Configuration Examples](#configuration-examples)

## Overview

Zetherion AI is configured via a `.env` file in the project root directory. This file contains:
- API keys (Discord, AI providers)
- Router backend selection
- Security settings
- Performance tuning
- Logging configuration

**Configuration File:**
- **Template**: `.env.example` (checked into git)
- **Active Config**: `.env` (gitignored, contains your secrets)

**Creating Your Config:**
```bash
# Copy template
cp .env.example .env

# Or let start.sh/start.ps1 create it interactively
./start.sh  # Runs interactive setup if .env missing
```

## Required Configuration

These settings are **mandatory** for Zetherion AI to function.

### DISCORD_TOKEN

**Discord Bot Token** - Authenticates your bot with Discord API.

```env
DISCORD_TOKEN=MTQ2ODc4MDQxODY1MTI2MzEyOQ.GGFum2.lsf_abc123def456ghi789
```

**How to get:**
1. [Discord Developer Portal](https://discord.com/developers/applications)
2. Create Application → Bot tab → Reset Token
3. Copy token immediately (won't be shown again)
4. Enable "Message Content Intent" in Bot settings

**Format:**
- 59+ characters
- Pattern: `[MN][A-Za-z0-9]{23}.[A-Za-z0-9_-]{6}.[A-Za-z0-9_-]{27}`
- Example: `MTQz...GGF...lsf...`

**Security:**
- ⚠️ **Never commit to git**
- ⚠️ **Never share publicly**
- ⚠️ **Regenerate if compromised**

### GEMINI_API_KEY

**Google Gemini API Key** - Used for embeddings (required) and routing (if `ROUTER_BACKEND=gemini`).

```env
GEMINI_API_KEY=AIzaSyCO9WodgUFJfW-7qK4Vtbnc1234567890ABC
```

**How to get:**
1. [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in → Create API key
3. Select or create Google Cloud project
4. Copy key

**Format:**
- Starts with `AIzaSy`
- 39 characters total
- Pattern: `AIzaSy[A-Za-z0-9_-]{33}`

**Pricing:**
- **Free tier**: 15 requests/minute, 1,500 requests/day
- **Paid tier**: $0.00007 per 1K characters (input)
- See: https://ai.google.dev/pricing

**Usage:**
- **Always used**: Generating embeddings for vector storage
- **Router backend**: Message classification and simple responses (if `ROUTER_BACKEND=gemini`)

## Router Backend Configuration

Choose how Zetherion AI routes and processes messages.

### ROUTER_BACKEND

**Router Backend Selection** - Determines which AI processes routing decisions.

```env
ROUTER_BACKEND=gemini  # or ollama
```

**Options:**

#### 1. `gemini` (Default - Cloud-Based)

**Pros:**
- ✅ Fast startup (~3 minutes first run)
- ✅ No model downloads
- ✅ Lower resource requirements (4GB Docker RAM)
- ✅ Free tier available (1,500 requests/day)

**Cons:**
- ❌ Sends routing data to Google's cloud
- ❌ Requires internet for every request

**Best for:**
- Quick deployments
- Cloud-based workflows
- Limited hardware

#### 2. `ollama` (Local/Privacy-Focused)

**Pros:**
- ✅ Privacy-focused (no routing data sent to cloud)
- ✅ Offline capability
- ✅ No per-request API costs
- ✅ GPU acceleration support

**Cons:**
- ❌ Longer startup (~9 minutes first run, includes model download)
- ❌ Higher resource requirements (8-12GB Docker RAM)
- ❌ 5-10GB model downloads

**Best for:**
- Privacy-conscious users
- Offline deployments
- Powerful hardware (16GB+ RAM or GPU)

### Dual Ollama Container Architecture

When using `ROUTER_BACKEND=ollama`, Zetherion AI uses **two separate Ollama containers** to eliminate model-swapping delays:

```
┌────────────────────────┐    ┌────────────────────────┐
│  ollama-router         │    │  ollama                │
│  ┌──────────────────┐  │    │  ┌──────────────────┐  │
│  │  qwen2.5:0.5b    │  │    │  │   qwen2.5:7b     │  │
│  │  Always loaded   │  │    │  │  Always loaded   │  │
│  └──────────────────┘  │    │  └──────────────────┘  │
│  Memory: 1GB           │    │  ┌──────────────────┐  │
│  Purpose: Routing      │    │  │ nomic-embed-text │  │
└────────────────────────┘    │  └──────────────────┘  │
                              │  Memory: 8GB           │
                              │  Purpose: Generation   │
                              └────────────────────────┘
```

**Why two containers?**
- Ollama can only keep ONE model loaded at a time per container
- Single container = 2-10 second delay when swapping between routing and generation models
- Dual containers = both models always loaded, instant responses

### OLLAMA_ROUTER_HOST

**Router Container Host** - Dedicated container for fast message classification.

```env
OLLAMA_ROUTER_HOST=ollama-router  # Docker service name (default)
# or
OLLAMA_ROUTER_HOST=localhost  # For local development
```

**Default**: `ollama-router` (Docker Compose service name)

### OLLAMA_ROUTER_PORT

**Router Container Port** - API port for the router container.

```env
OLLAMA_ROUTER_PORT=11434
```

**Default**: `11434` (standard Ollama port)

### OLLAMA_ROUTER_MODEL

**Router Model Selection** - Small, fast model for message classification.

```env
OLLAMA_ROUTER_MODEL=qwen2.5:0.5b
```

**Recommended Router Models:**

| Model | Size | Speed | RAM | Best For |
|-------|------|-------|-----|----------|
| `qwen2.5:0.5b` | 500MB | Fastest | 1GB | Default, good quality |
| `llama3.2:1b` | 1.3GB | Fast | 1.5GB | Better quality |
| `phi3:mini` | 2.3GB | Moderate | 2GB | Best classification |

**Note:** Router models should be small and fast - quality matters less for classification.

### OLLAMA_HOST

**Generation Container Host** - Main container for complex queries and embeddings.

```env
OLLAMA_HOST=ollama  # Docker service name (default)
# or
OLLAMA_HOST=localhost  # For local development
```

**Default**: `ollama` (Docker Compose service name)

### OLLAMA_PORT

**Generation Container Port** - API port for the generation container.

```env
OLLAMA_PORT=11434
```

**Default**: `11434` (standard Ollama port)

### OLLAMA_GENERATION_MODEL

**Generation Model Selection** - Larger, more capable model for complex queries.

```env
OLLAMA_GENERATION_MODEL=qwen2.5:7b
```

**Recommended Generation Models** (by hardware):

| Hardware | Model | RAM | Quality | Speed |
|----------|-------|-----|---------|-------|
| 8GB RAM, CPU | `qwen2.5:3b` | 4GB | Good | Moderate |
| 16GB RAM, CPU | `qwen2.5:7b` | 8GB | Excellent | Moderate |
| 16GB+ RAM, GPU | `qwen2.5:14b` | 12GB | Best | Fast (GPU) |
| 32GB+ RAM, GPU | `qwen2.5:32b` | 24GB | Maximum | Fast (GPU) |

**Hardware assessment** in `start.sh`/`start.ps1` automatically recommends optimal model.

**See:** [Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md) for detailed comparison.

### OLLAMA_EMBEDDING_MODEL

**Embedding Model Selection** - Model for generating vector embeddings (runs on generation container).

```env
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

**Default**: `nomic-embed-text` (768-dimensional vectors, same as Gemini)

**Note:** Embeddings run on the generation container alongside the generation model.

### OLLAMA_DOCKER_MEMORY

**Docker Memory Allocation** - RAM limit for the generation container (GB).

```env
OLLAMA_DOCKER_MEMORY=8
```

**Recommended by Generation Model:**
- `qwen2.5:3b`: 4GB
- `qwen2.5:7b`: 8GB
- `qwen2.5:14b`: 12GB
- `qwen2.5:32b`: 24GB

**Note:** The router container uses 1GB fixed allocation (small model only).

**Total Ollama Memory:**
- Router container: 1GB (fixed)
- Generation container: `OLLAMA_DOCKER_MEMORY` value
- Example: 8GB + 1GB = 9GB total for Ollama

**Auto-set** by startup script based on model selection.

### OLLAMA_TIMEOUT

**API Timeout** - Request timeout in seconds for Ollama API calls.

```env
OLLAMA_TIMEOUT=30
```

**Default**: `30` seconds

**Adjust for:**
- Slow hardware: Increase to 60-120
- Fast GPU: Can decrease to 15-20

## Optional API Keys

These enhance capabilities but are not required.

### ANTHROPIC_API_KEY

**Anthropic Claude API Key** - For complex reasoning tasks.

```env
ANTHROPIC_API_KEY=sk-ant-api03-OEKnlIipBFzxRV1234567890...
```

**How to get:**
1. [Anthropic Console](https://console.anthropic.com/)
2. Settings → API Keys → Create Key
3. Add payment method ($5 minimum credit)

**Format:**
- Starts with `sk-ant-api03-`
- ~100 characters total

**Pricing:**
- **Claude Sonnet 4.5**: $3/million input tokens, $15/million output tokens
- See: https://www.anthropic.com/pricing

**Usage:**
- Complex queries requiring advanced reasoning
- Code generation and analysis
- Multi-step problem solving
- Fallback if OpenAI unavailable

**Can be omitted** - Gemini Flash handles all queries (lower quality for complex tasks).

### OPENAI_API_KEY

**OpenAI API Key** - Alternative for complex tasks (GPT-4o).

```env
OPENAI_API_KEY=sk-proj-1234567890abcdefghijklmnopqrstuvwxyz...
```

**How to get:**
1. [OpenAI Platform](https://platform.openai.com/)
2. Profile → API Keys → Create new secret key
3. Add payment method ($5+ credits recommended)

**Format:**
- Starts with `sk-` or `sk-proj-`
- 48+ characters

**Pricing:**
- **GPT-4o**: $2.50/million input tokens, $10/million output tokens
- See: https://openai.com/api/pricing/

**Usage:**
- Alternative to Claude for complex tasks
- Used if Claude unavailable or rate limited

**Can be omitted** - Not used if Anthropic key present.

## Security Configuration

### ALLOWED_USER_IDS

**User Allowlist** - Restrict bot access to specific Discord users (comma-separated user IDs).

```env
ALLOWED_USER_IDS=123456789012345678,987654321098765432
```

**Getting User IDs:**
1. Enable Discord Developer Mode (Settings → Advanced)
2. Right-click user → Copy User ID
3. Add to comma-separated list

**Examples:**
```env
# Single user
ALLOWED_USER_IDS=123456789012345678

# Multiple users
ALLOWED_USER_IDS=123456789012345678,987654321098765432,456789012345678901

# All users (not recommended for production)
ALLOWED_USER_IDS=
```

**Security:**
- ✅ **Recommended**: Set to your user ID(s)
- ⚠️ **Warning**: Empty = anyone can use bot (costs, spam, abuse)
- 🔒 **Production**: Always set allowlist

### ENCRYPTION_ENABLED

**Data Encryption** - Enable AES-256-GCM encryption for vector storage.

```env
ENCRYPTION_ENABLED=true
```

**Default**: `false` (disabled)

**When enabled:**
- All sensitive data in Qdrant encrypted
- Encryption key derived from `ENCRYPTION_PASSPHRASE`
- Provides defense-in-depth if database compromised

**See:** [Security Guide](SECURITY.md#data-encryption-phase-5a) for details.

### ENCRYPTION_PASSPHRASE

**Encryption Key** - Passphrase for AES-256-GCM encryption (required if `ENCRYPTION_ENABLED=true`).

```env
ENCRYPTION_PASSPHRASE=your-secure-passphrase-here-min-16-chars
```

**Requirements:**
- Minimum 16 characters
- Use strong, random passphrase
- Never commit to git

**Generating secure passphrase:**
```bash
# Generate 32-byte random passphrase
openssl rand -base64 32
# Output: 8zP3kL9mN2qR5vT7wX0yA4bC6dE1fG8hI...

# Or use password manager to generate
```

**⚠️ CRITICAL:**
- **Never lose this**: Cannot decrypt data without it
- **Backup securely**: Separate from git repository
- **Rotate regularly**: Change every 6-12 months (requires data migration)

## Performance Tuning

### RATE_LIMIT_MESSAGES

**Rate Limit** - Maximum messages per user per time window.

```env
RATE_LIMIT_MESSAGES=10
```

**Default**: `10` messages

**Purpose:**
- Prevent abuse
- Control API costs
- Manage resource usage

**Adjust based on:**
- **Personal use**: 5-10 messages/minute sufficient
- **Team use**: 15-20 messages/minute
- **Production**: 10 messages/minute (monitor costs)

### RATE_LIMIT_WINDOW

**Rate Limit Window** - Time window in seconds for rate limiting.

```env
RATE_LIMIT_WINDOW=60
```

**Default**: `60` seconds (1 minute)

**Examples:**
```env
# Strict: 5 messages per 30 seconds
RATE_LIMIT_MESSAGES=5
RATE_LIMIT_WINDOW=30

# Lenient: 20 messages per 2 minutes
RATE_LIMIT_MESSAGES=20
RATE_LIMIT_WINDOW=120
```

### QDRANT_HOST

**Qdrant Service Host** - Vector database connection.

```env
QDRANT_HOST=qdrant  # Docker service name (default)
```

**Default**: `qdrant` (Docker Compose service name)

**Alternatives:**
```env
# Local development
QDRANT_HOST=localhost

# Remote Qdrant instance
QDRANT_HOST=qdrant.example.com
```

### QDRANT_PORT

**Qdrant Service Port** - Vector database API port.

```env
QDRANT_PORT=6333
```

**Default**: `6333` (Qdrant standard port)

**Change if:**
- Port conflict with other services
- Custom Qdrant deployment
- Security requirements (non-standard port)

## Logging Configuration

### LOG_LEVEL

**Logging Verbosity** - Controls detail level of logs.

```env
LOG_LEVEL=INFO
```

**Options** (from most to least verbose):
- `DEBUG`: All messages (development, troubleshooting)
- `INFO`: General information (default, recommended)
- `WARNING`: Warnings and errors only
- `ERROR`: Errors only
- `CRITICAL`: Critical failures only

**Recommendations:**
- **Development**: `DEBUG`
- **Production**: `INFO` or `WARNING`
- **Troubleshooting**: `DEBUG` (temporarily)

### LOG_TO_FILE

**File Logging** - Enable writing logs to files.

```env
LOG_TO_FILE=true
```

**Default**: `true` (enabled)

**When enabled:**
- Logs written to `logs/` directory
- Automatic rotation (10MB max, 5 backups)
- Separate files per severity (info.log, error.log)

**Disable for:**
- Containerized environments with centralized logging
- Docker log aggregation (use `docker-compose logs` instead)

### LOG_DIRECTORY

**Log File Directory** - Where to store log files.

```env
LOG_DIRECTORY=logs
```

**Default**: `logs/` (relative to project root)

**Absolute path example:**
```env
LOG_DIRECTORY=/var/log/zetherion-ai
```

**Ensure directory:**
- Exists or is created automatically
- Has write permissions for bot user (UID 65532 in distroless)

## Advanced Settings

### DISCORD_COMMAND_PREFIX

**Command Prefix** - Prefix for text-based commands (alternative to mentions).

```env
DISCORD_COMMAND_PREFIX=!
```

**Default**: `None` (mention-only)

**Examples:**
```env
# Exclamation prefix
DISCORD_COMMAND_PREFIX=!
# Usage: !ask What is the weather?

# Dot prefix
DISCORD_COMMAND_PREFIX=.
# Usage: .ask What is the weather?

# No prefix (mention only)
DISCORD_COMMAND_PREFIX=
# Usage: @Zetherion AI What is the weather?
```

**Note**: Slash commands (`/ask`) always work regardless of prefix.

### QDRANT_USE_TLS

**TLS Encryption** - Enable encrypted connection to Qdrant.

```env
QDRANT_USE_TLS=false
```

**Default**: `false` (unencrypted, OK for local Docker network)

**Enable for:**
- Remote Qdrant instances
- Production deployments
- Compliance requirements

**Requires:**
- TLS certificates (self-signed or CA-issued)
- Certificate files mounted in Docker volume

**See:** [Security Guide](SECURITY.md#tls-for-qdrant-in-transit-encryption)

### MEMORY_SEARCH_LIMIT

**Memory Search Results** - Maximum number of results from vector search.

```env
MEMORY_SEARCH_LIMIT=5
```

**Default**: `5` results

**Purpose:**
- Limit context size sent to LLM
- Control API costs (tokens)
- Balance relevance vs. context window

**Adjust based on:**
- **Complex queries**: Increase to 10-15
- **Simple queries**: Decrease to 3
- **Cost-sensitive**: Decrease to 3-5

### CONTEXT_WINDOW_SIZE

**Conversation History** - Number of recent messages to include in context.

```env
CONTEXT_WINDOW_SIZE=10
```

**Default**: `10` messages

**Higher values:**
- Better conversation continuity
- More tokens per request (higher costs)
- Risk of exceeding model context limits

**Lower values:**
- Cheaper API calls
- May lose conversation thread
- Faster responses

## Environment-Specific Configs

### Development Environment

```env
# .env.development
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
RATE_LIMIT_MESSAGES=100  # High limit for testing
ROUTER_BACKEND=gemini    # Fast iteration
ENCRYPTION_ENABLED=false # Faster for testing
```

### Production Environment

```env
# .env.production
LOG_LEVEL=INFO
LOG_TO_FILE=true
RATE_LIMIT_MESSAGES=10
ROUTER_BACKEND=ollama  # Privacy-focused
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=<strong-passphrase>
ALLOWED_USER_IDS=<specific-users>
QDRANT_USE_TLS=true
```

### Testing Environment

```env
# .env.test
LOG_LEVEL=WARNING
LOG_TO_FILE=false  # Use stdout for CI logs
ROUTER_BACKEND=gemini
ALLOWED_USER_IDS=  # Allow test users
RATE_LIMIT_MESSAGES=1000  # No limits during tests
```

## Configuration Examples

### Minimal Setup (Personal Use)

```env
# Required only
DISCORD_TOKEN=MTQz...
GEMINI_API_KEY=AIzaSy...

# Recommended
ROUTER_BACKEND=gemini
ALLOWED_USER_IDS=123456789012345678
LOG_LEVEL=INFO
```

**Good for:**
- Quick start
- Personal Discord server
- Gemini free tier

### Privacy-Focused Setup

```env
# Required
DISCORD_TOKEN=MTQz...
# GEMINI_API_KEY not needed when using local embeddings

# Ollama for routing (local, dual-container architecture)
ROUTER_BACKEND=ollama
EMBEDDINGS_BACKEND=ollama  # Use local embeddings too

# Router container (fast classification)
OLLAMA_ROUTER_HOST=ollama-router
OLLAMA_ROUTER_PORT=11434
OLLAMA_ROUTER_MODEL=qwen2.5:0.5b

# Generation container (complex queries + embeddings)
OLLAMA_HOST=ollama
OLLAMA_PORT=11434
OLLAMA_GENERATION_MODEL=qwen2.5:14b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_DOCKER_MEMORY=12

# Security
ALLOWED_USER_IDS=123456789012345678
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=<strong-passphrase>

# Logging
LOG_LEVEL=INFO
LOG_TO_FILE=true
```

**Good for:**
- Privacy requirements (no data leaves your machine)
- Offline capability
- Powerful hardware (16GB+ RAM)

### Professional Setup (Best Quality)

```env
# Required
DISCORD_TOKEN=MTQz...
GEMINI_API_KEY=AIzaSy...

# All AI providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Cloud routing for speed
ROUTER_BACKEND=gemini

# Security
ALLOWED_USER_IDS=123,456,789
RATE_LIMIT_MESSAGES=15
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE=<strong-passphrase>

# Logging
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_DIRECTORY=/var/log/zetherion-ai

# Performance
CONTEXT_WINDOW_SIZE=15
MEMORY_SEARCH_LIMIT=7
```

**Good for:**
- Professional use
- Team deployments
- Best quality responses

### Budget-Conscious Setup

```env
# Required (free tiers)
DISCORD_TOKEN=MTQz...
GEMINI_API_KEY=AIzaSy...

# Free routing
ROUTER_BACKEND=gemini

# Strict rate limiting
RATE_LIMIT_MESSAGES=5
RATE_LIMIT_WINDOW=60

# Minimize token usage
CONTEXT_WINDOW_SIZE=5
MEMORY_SEARCH_LIMIT=3

# Security
ALLOWED_USER_IDS=123456789012345678
```

**Good for:**
- Staying within free tiers
- Controlling costs
- Moderate usage

## Validation

### Check Configuration

**Verify .env file exists:**
```bash
ls -la .env
# Should show: -rw------- 1 user user 1234 .env
```

**Verify required keys set:**
```bash
grep -E "^(DISCORD_TOKEN|GEMINI_API_KEY)=" .env
# Should show both variables with values
```

**Test configuration:**
```bash
# Start and check logs
./start.sh
docker-compose logs -f zetherion-ai-bot

# Look for:
# ✓ Discord token validated
# ✓ Gemini API key validated
# ✓ Router backend: gemini (or ollama)
```

### Common Validation Errors

**"Invalid Discord token format"**
```
Fix: Ensure token is 59+ characters, no quotes/spaces
DISCORD_TOKEN=MTQz...  ✓ Correct
DISCORD_TOKEN="MTQz..."  ✗ Has quotes
```

**"Gemini API key validation failed"**
```
Fix: Ensure key starts with AIzaSy, 39 characters total
GEMINI_API_KEY=AIzaSy...  ✓ Correct
GEMINI_API_KEY=AIzaS...   ✗ Too short
```

**"No users allowed (ALLOWED_USER_IDS empty)"**
```
Fix: Set your Discord user ID or expect warning
ALLOWED_USER_IDS=123456789012345678  ✓ Restricted
ALLOWED_USER_IDS=                    ⚠️ Warning (anyone can use)
```

## Security Best Practices

### File Permissions

**Unix/Mac:**
```bash
# .env should be readable by owner only
chmod 600 .env
ls -la .env
# Expected: -rw------- (owner read/write only)
```

**Windows (PowerShell as Admin):**
```powershell
# Remove inheritance and grant only user access
icacls .env /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

### Key Rotation

**Rotate API keys periodically:**
1. **Discord**: Every 6 months or if compromised
2. **AI Providers**: Every 3-6 months
3. **Encryption**: Every 6-12 months (complex migration)

**Rotation process:**
1. Generate new key in provider console
2. Update `.env` with new key
3. Restart bot: `./stop.sh && ./start.sh`
4. Revoke old key in provider console
5. Verify bot still works

### Backup Configuration

**Backup .env securely:**
```bash
# Encrypt backup
gpg -c .env
# Creates: .env.gpg (encrypted)

# Store in secure location (not git)
mv .env.gpg ~/secure-backups/

# Restore when needed
gpg -d ~/secure-backups/.env.gpg > .env
```

### Audit Logging

**Monitor configuration changes:**
```bash
# Track who changed .env
git log -p .env.example  # Track template changes in git

# Log access to .env
audit-log ".env accessed by $USER at $(date)" >> .env.access.log
```

## Troubleshooting

### Bot Won't Start

**Check configuration:**
```bash
# View container logs
docker-compose logs zetherion-ai-bot

# Common errors:
# - "Invalid Discord token" → Check DISCORD_TOKEN format
# - "Gemini API error" → Check GEMINI_API_KEY
# - "Router backend not set" → Add ROUTER_BACKEND=gemini
```

### High API Costs

**Reduce token usage:**
```env
# Lower context window
CONTEXT_WINDOW_SIZE=5  # Was: 10

# Fewer memory results
MEMORY_SEARCH_LIMIT=3  # Was: 5

# Stricter rate limiting
RATE_LIMIT_MESSAGES=5  # Was: 10
```

### Performance Issues

**Optimize settings:**
```env
# For faster responses (cloud)
ROUTER_BACKEND=gemini

# For Ollama (if slow)
OLLAMA_ROUTER_MODEL=phi3:mini  # Faster model
OLLAMA_DOCKER_MEMORY=5  # Match model requirements
```

## Additional Resources

- **[Installation Guide](INSTALLATION.md)** - First-time setup
- **[Hardware Recommendations](HARDWARE-RECOMMENDATIONS.md)** - Optimize for your system
- **[Security Guide](SECURITY.md)** - Encryption and distroless containers
- **[.env.example](../.env.example)** - Template with all variables

---

**Need Help?** Check [GitHub Discussions](https://github.com/jimtin/zetherion-ai/discussions) or [open an issue](https://github.com/jimtin/zetherion-ai/issues).
