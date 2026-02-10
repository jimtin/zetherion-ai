# Configuration Reference

## Overview

All configuration for Zetherion AI is managed via environment variables defined in a `.env` file at the project root. The bot uses Pydantic `BaseSettings` for validation, type coercion, and default values. Settings are loaded once at startup and cached via `@lru_cache`.

**Configuration files**:
- `.env.example` -- Template with all variables (checked into git, no real values)
- `.env` -- Active configuration (gitignored, contains your secrets)

**Creating your config**:
```bash
cp .env.example .env
# Edit .env with your values
```

## Required Variables

These three variables are mandatory. The bot will not start without them.

| Variable | Description | Example |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Discord bot authentication token | `MTQ2ODc4...` |
| `GEMINI_API_KEY` | Google Gemini API key (used for embeddings and routing) | `AIzaSy...` |
| `ENCRYPTION_PASSPHRASE` | Master passphrase for AES-256-GCM encryption (minimum 16 characters) | `your-secure-passphrase-here` |

## Discord Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | (required) | Bot authentication token from the Discord Developer Portal |
| `ALLOWED_USER_IDS` | `""` | Comma-separated Discord user IDs permitted to interact with the bot. Empty string allows all users (not recommended for production) |
| `ALLOW_ALL_USERS` | `false` | Explicit flag to allow all users when no allowlist is configured |
| `OWNER_USER_ID` | `0` | Bootstrap admin user ID for RBAC. This user is automatically assigned the `owner` role on first startup |

## LLM Provider Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | Anthropic model identifier for complex reasoning tasks |
| `OPENAI_MODEL` | `gpt-5.2` | OpenAI model identifier for complex tasks |
| `ROUTER_MODEL` | `gemini-2.5-flash` | Gemini model for routing decisions and simple query responses |
| `EMBEDDING_MODEL` | `text-embedding-004` | Gemini embedding model for vector generation |
| `ANTHROPIC_API_KEY` | (optional) | Anthropic API key for Claude. Loaded as `SecretStr` |
| `OPENAI_API_KEY` | (optional) | OpenAI API key. Loaded as `SecretStr` |

## Router Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUTER_BACKEND` | `gemini` | Router backend selection. Valid values: `gemini` (cloud-based, fast startup) or `ollama` (local, privacy-focused) |

When set to `gemini`, routing uses the Gemini API. When set to `ollama`, routing uses a dedicated local Ollama container for message classification.

## Ollama Configuration (Generation)

These settings control the main Ollama container used for text generation and local embeddings.

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `ollama` | Ollama generation container hostname (Docker service name) |
| `OLLAMA_PORT` | `11434` | Ollama API port |
| `OLLAMA_GENERATION_MODEL` | `llama3.1:8b` | Model for local text generation |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Model for local embedding generation (768 dimensions) |
| `OLLAMA_TIMEOUT` | `30` | API request timeout in seconds |

## Ollama Router Configuration

These settings control the dedicated lightweight Ollama container used for fast message classification.

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_ROUTER_HOST` | `ollama-router` | Router container hostname (Docker service name) |
| `OLLAMA_ROUTER_PORT` | `11434` | Router container API port |
| `OLLAMA_ROUTER_MODEL` | `llama3.2:1b` | Small, fast model for query classification |

## Embeddings Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDINGS_BACKEND` | `ollama` | Embeddings backend selection. Valid values: `ollama`, `gemini`, or `openai` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | OpenAI embedding model (used when backend is `openai`) |
| `OPENAI_EMBEDDING_DIMENSIONS` | `3072` | Embedding vector dimensions for OpenAI model |

## Encryption

| Variable | Default | Description |
|----------|---------|-------------|
| `ENCRYPTION_PASSPHRASE` | (required) | Master passphrase for key derivation. Minimum 16 characters. Used with PBKDF2-HMAC-SHA256 (600,000 iterations) to derive the AES-256-GCM key. Loaded as `SecretStr` |
| `ENCRYPTION_SALT_PATH` | `data/salt.bin` | Filesystem path for the persistent salt file. Auto-generated on first run. Must be backed up |
| `ENCRYPTION_STRICT` | `false` | When `true`, decryption failures raise errors instead of passing through unencrypted data. Set to `false` for backward compatibility with legacy unencrypted records |

## Database

### PostgreSQL

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DSN` | `postgresql://zetherion:changeme@postgres:5432/zetherion` | Full PostgreSQL connection string. Used for RBAC, dynamic settings, audit trail, and Gmail trust state |

### Qdrant (Vector Database)

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `qdrant` | Qdrant server hostname (Docker service name) |
| `QDRANT_PORT` | `6333` | Qdrant HTTP API port |
| `QDRANT_USE_TLS` | `false` | Enable TLS encryption for the Qdrant connection |
| `QDRANT_CERT_PATH` | (optional) | Path to TLS certificate file for Qdrant server verification |

## InferenceBroker

The InferenceBroker provides smart multi-provider routing across Anthropic, OpenAI, and Google.

| Variable | Default | Description |
|----------|---------|-------------|
| `INFERENCE_BROKER_ENABLED` | `true` | Enable the multi-provider routing system |
| `COST_TRACKING_ENABLED` | `true` | Track API costs per provider and task type |

## Model Discovery

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_DISCOVERY_ENABLED` | `true` | Enable automatic model discovery from provider APIs |
| `MODEL_REFRESH_HOURS` | `24` | Hours between model list refreshes from provider APIs |
| `ANTHROPIC_TIER` | `balanced` | Default tier for Anthropic models. Valid values: `quality`, `balanced`, `fast` |
| `OPENAI_TIER` | `balanced` | Default tier for OpenAI models. Valid values: `quality`, `balanced`, `fast` |
| `GOOGLE_TIER` | `fast` | Default tier for Google models. Valid values: `quality`, `balanced`, `fast` |

## Cost Tracking

| Variable | Default | Description |
|----------|---------|-------------|
| `COST_DB_PATH` | `data/costs.db` | Path to the SQLite database for cost tracking |
| `DAILY_BUDGET_USD` | (optional) | Daily spending limit in USD. When set, alerts are triggered at the warning threshold |
| `MONTHLY_BUDGET_USD` | (optional) | Monthly spending limit in USD. When set, alerts are triggered at the warning threshold |
| `BUDGET_WARNING_PCT` | `80.0` | Percentage of budget at which to send a warning notification (0-100) |

## Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFICATIONS_ENABLED` | `true` | Enable the notification system for cost and model alerts |
| `NOTIFY_ON_NEW_MODELS` | `true` | Send a notification when new models are discovered from provider APIs |
| `NOTIFY_ON_DEPRECATION` | `true` | Send a notification when a model is marked as deprecated |
| `NOTIFY_ON_MISSING_PRICING` | `false` | Send a notification for discovered models that lack pricing data |
| `DAILY_SUMMARY_ENABLED` | `false` | Send a daily cost summary notification via Discord |
| `DAILY_SUMMARY_HOUR` | `9` | Hour of day (0-23) at which to send the daily cost summary |

## Profile System

| Variable | Default | Description |
|----------|---------|-------------|
| `PROFILE_INFERENCE_ENABLED` | `true` | Enable automatic profile extraction from conversations |
| `PROFILE_TIER1_ONLY` | `false` | Use only Tier 1 regex-based extraction (no LLM calls). Reduces cost at the expense of extraction quality |
| `PROFILE_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence score (0.0-1.0) to auto-apply a profile update without user confirmation |
| `PROFILE_CACHE_TTL` | `300` | Profile cache time-to-live in seconds |
| `PROFILE_DB_PATH` | `data/profiles.db` | Path to the SQLite database for profile operational data |
| `PROFILE_MAX_PENDING_CONFIRMATIONS` | `5` | Maximum number of pending confirmation prompts per user |
| `PROFILE_CONFIRMATION_EXPIRY_HOURS` | `72` | Hours before a pending confirmation expires automatically |
| `DEFAULT_FORMALITY` | `0.5` | Initial response formality level (0.0 = casual, 1.0 = formal) |
| `DEFAULT_VERBOSITY` | `0.5` | Initial response detail level (0.0 = terse, 1.0 = detailed) |
| `DEFAULT_PROACTIVITY` | `0.3` | Initial proactive behavior level (0.0 = reactive only, 1.0 = fully proactive) |
| `TRUST_EVOLUTION_RATE` | `0.05` | Trust increase per positive interaction |

## Skills Service

| Variable | Default | Description |
|----------|---------|-------------|
| `SKILLS_SERVICE_URL` | `http://zetherion_ai-skills:8080` | URL of the Skills Service on the internal Docker network |
| `SKILLS_API_SECRET` | (optional) | Shared secret for `X-API-Secret` header authentication. Loaded as `SecretStr`. Required for production |
| `SKILLS_REQUEST_TIMEOUT` | `30` | Timeout in seconds for requests to the Skills Service |

## Gmail Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | (optional) | Google OAuth 2.0 client ID for Gmail integration |
| `GOOGLE_CLIENT_SECRET` | (optional) | Google OAuth 2.0 client secret. Loaded as `SecretStr` |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8080/gmail/callback` | OAuth 2.0 callback URL. Must match the redirect URI configured in the Google Cloud Console |

## GitHub Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | (optional) | GitHub personal access token. Loaded as `SecretStr` |
| `GITHUB_DEFAULT_REPO` | (optional) | Default repository in `owner/repo` format |
| `GITHUB_API_TIMEOUT` | `30` | Timeout in seconds for GitHub API requests |

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging verbosity. Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_TO_FILE` | `true` | Enable writing logs to rotating files |
| `LOG_DIRECTORY` | `logs` | Directory path for log files |
| `LOG_FILE_MAX_BYTES` | `52428800` | Maximum size in bytes per log file before rotation (default: 50 MB) |
| `LOG_FILE_BACKUP_COUNT` | `10` | Number of rotated log files to retain |
| `LOG_ERROR_FILE_ENABLED` | `true` | Enable a separate error log file capturing WARNING-level and above |

## Application

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `production` | Environment name. Set to `development` for colored console logging and debug behavior |

## Dynamic Settings

The `Settings` class supports a three-level cascade for runtime-configurable values:

```
PostgreSQL override  ->  Environment variable  ->  Default value
```

Use the `get_dynamic(namespace, key, default)` function for settings that may be changed at runtime without restarting the bot. The function reads from an in-memory cache populated from PostgreSQL and never blocks on database I/O.

```python
from zetherion_ai.config import get_dynamic

model = get_dynamic("models", "claude_model", "claude-sonnet-4-5-20250929")
```

## Example .env (Minimal)

The minimum configuration needed to start the bot:

```env
DISCORD_TOKEN=your_discord_token
GEMINI_API_KEY=your_gemini_key
ENCRYPTION_PASSPHRASE=your-16-char-minimum-passphrase
```

## Example .env (Full Featured)

A production-ready configuration with all major features enabled:

```env
# Required
DISCORD_TOKEN=MTQ2ODc4...
GEMINI_API_KEY=AIzaSy...
ENCRYPTION_PASSPHRASE=your-very-secure-passphrase

# Optional LLM providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Router
ROUTER_BACKEND=gemini

# Access control
ALLOWED_USER_IDS=123456789,987654321
OWNER_USER_ID=123456789

# Skills Service
SKILLS_API_SECRET=your-skills-api-secret

# Budgets
DAILY_BUDGET_USD=5.00
MONTHLY_BUDGET_USD=50.00

# Gmail
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_DEFAULT_REPO=owner/repo

# Logging
LOG_LEVEL=INFO
LOG_TO_FILE=true

# PostgreSQL (use a strong password in production)
POSTGRES_DSN=postgresql://zetherion:strong-random-password@postgres:5432/zetherion
```

## Related Docs

- [Architecture](architecture.md) -- System design and component interactions
- [Security Model](security.md) -- Encryption, access control, and threat mitigation
- [Docker Deployment](docker.md) -- Container setup and orchestration
