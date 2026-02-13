# Configuration Reference

## Overview

Zetherion AI configuration comes from environment variables (`.env`) plus runtime
overrides stored in PostgreSQL.

Configuration precedence:

1. Runtime override in PostgreSQL `settings` table
2. Environment variable (`.env`, container env)
3. Code default in `src/zetherion_ai/config.py`

Create local config:

```bash
cp .env.example .env
```

## Required Variables

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Discord bot token |
| `GEMINI_API_KEY` | Gemini key used for routing/embeddings fallback paths |
| `ENCRYPTION_PASSPHRASE` | Master passphrase for AES-256-GCM data encryption |

## Core Bot and Access

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_USER_IDS` | empty | Comma-separated Discord user IDs |
| `ALLOW_ALL_USERS` | `false` | Allow all users when allowlist is empty |
| `OWNER_USER_ID` | unset | Bootstrap RBAC owner user |
| `ALLOW_BOT_MESSAGES` | `false` | Allow bot-to-bot messages (E2E testing) |
| `DEV_AGENT_WEBHOOK_NAME` | `zetherion-dev-agent` | Trusted webhook sender name |

## Model and Routing

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUTER_BACKEND` | `gemini` | Router backend: `gemini` or `ollama` |
| `ROUTER_MODEL` | `gemini-2.5-flash` | Cloud router model |
| `CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | Anthropic complex-task model |
| `OPENAI_MODEL` | `gpt-5.2` | OpenAI complex-task model |
| `EMBEDDING_MODEL` | `text-embedding-004` | Gemini embedding model |
| `ANTHROPIC_API_KEY` | unset | Anthropic API key |
| `OPENAI_API_KEY` | unset | OpenAI API key |

## Ollama and Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `ollama` | Main Ollama service host |
| `OLLAMA_PORT` | `11434` | Main Ollama service port |
| `OLLAMA_GENERATION_MODEL` | `llama3.1:8b` | Local generation model |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Local embedding model |
| `OLLAMA_TIMEOUT` | `30` | Ollama request timeout (seconds) |
| `OLLAMA_ROUTER_HOST` | `ollama-router` | Dedicated router Ollama host |
| `OLLAMA_ROUTER_PORT` | `11434` | Dedicated router Ollama port |
| `OLLAMA_ROUTER_MODEL` | `llama3.2:3b` | Local router model |
| `EMBEDDINGS_BACKEND` | `ollama` | `ollama`, `gemini`, or `openai` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | OpenAI embeddings model |
| `OPENAI_EMBEDDING_DIMENSIONS` | `3072` | OpenAI embedding dimension |

## Storage and Encryption

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DSN` | `postgresql://zetherion:password@postgres:5432/zetherion` | PostgreSQL DSN |
| `QDRANT_HOST` | `qdrant` | Qdrant host |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `QDRANT_USE_TLS` | `false` | Use TLS for Qdrant |
| `QDRANT_CERT_PATH` | unset | Path to Qdrant TLS cert |
| `ENCRYPTION_SALT_PATH` | `data/salt.bin` | Salt file for key derivation |
| `ENCRYPTION_STRICT` | `false` | Fail hard on decrypt errors |

## Skills Service and Runtime Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SKILLS_SERVICE_URL` | `http://zetherion-ai-skills:8080` | Internal skills API URL |
| `SKILLS_API_SECRET` | unset | Shared API secret (`X-API-Secret`) |
| `SKILLS_REQUEST_TIMEOUT` | `30` | Skills request timeout (seconds) |

Discord admin slash commands are available for runtime overrides:

- `/config_list`
- `/config_set`
- `/config_reset`

`/config_set` infers and stores typed values:

- booleans: `true`, `false`, `yes`, `no`
- integers: `42`, `-1`
- floats: `0.6`, `1e-3`
- JSON: `{"enabled": true}`, `[1,2,3]`
- fallback string

## Dynamic Settings API

Use `get_dynamic(namespace, key, default)` for values that may change at runtime.

Lookup order:

1. PostgreSQL runtime setting (`namespace`, `key`)
2. Environment setting named `<namespace>_<key>` (for example `SECURITY_BLOCK_THRESHOLD`)
3. Environment setting named `<key>`
4. Provided `default`

Example:

```python
from zetherion_ai.config import get_dynamic

block_threshold = get_dynamic("security", "block_threshold", 0.6)
```

## Inference, Cost, and Discovery

| Variable | Default | Description |
|----------|---------|-------------|
| `INFERENCE_BROKER_ENABLED` | `true` | Enable multi-provider broker |
| `COST_TRACKING_ENABLED` | `true` | Enable request cost tracking |
| `MODEL_DISCOVERY_ENABLED` | `true` | Enable provider model discovery |
| `MODEL_REFRESH_HOURS` | `24` | Discovery refresh interval |
| `ANTHROPIC_TIER` | `balanced` | Anthropic default tier |
| `OPENAI_TIER` | `balanced` | OpenAI default tier |
| `GOOGLE_TIER` | `fast` | Google default tier |
| `COST_DB_PATH` | `data/costs.db` | Cost SQLite DB path |
| `DAILY_BUDGET_USD` | unset | Daily budget alert threshold |
| `MONTHLY_BUDGET_USD` | unset | Monthly budget alert threshold |
| `BUDGET_WARNING_PCT` | `80.0` | Warning trigger percentage |

## Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFICATIONS_ENABLED` | `true` | Enable notifications |
| `NOTIFY_ON_NEW_MODELS` | `true` | Notify when new models are discovered |
| `NOTIFY_ON_DEPRECATION` | `true` | Notify on model deprecation |
| `NOTIFY_ON_MISSING_PRICING` | `false` | Notify on missing pricing data |
| `DAILY_SUMMARY_ENABLED` | `false` | Enable daily summary notification |
| `DAILY_SUMMARY_HOUR` | `9` | Daily summary hour (0-23) |

## Profile and Personalization

| Variable | Default | Description |
|----------|---------|-------------|
| `PROFILE_INFERENCE_ENABLED` | `true` | Enable profile extraction |
| `PROFILE_TIER1_ONLY` | `false` | Restrict to regex-only extraction |
| `PROFILE_CONFIDENCE_THRESHOLD` | `0.6` | Auto-apply confidence threshold |
| `PROFILE_CACHE_TTL` | `300` | Profile cache TTL (seconds) |
| `PROFILE_DB_PATH` | `data/profiles.db` | Profile SQLite DB path |
| `PROFILE_MAX_PENDING_CONFIRMATIONS` | `5` | Pending confirmations cap |
| `PROFILE_CONFIRMATION_EXPIRY_HOURS` | `72` | Confirmation expiry |
| `DEFAULT_FORMALITY` | `0.5` | Initial formality |
| `DEFAULT_VERBOSITY` | `0.5` | Initial verbosity |
| `DEFAULT_PROACTIVITY` | `0.3` | Initial proactivity |
| `TRUST_EVOLUTION_RATE` | `0.05` | Trust evolution increment |

## Queue and Scheduling

| Variable | Default | Description |
|----------|---------|-------------|
| `QUEUE_ENABLED` | `true` | Enable priority queue processing |
| `QUEUE_INTERACTIVE_WORKERS` | `3` | Workers for P0-P1 traffic |
| `QUEUE_BACKGROUND_WORKERS` | `2` | Workers for P2-P3 traffic |
| `QUEUE_POLL_INTERVAL_MS` | `100` | Interactive poll interval |
| `QUEUE_BACKGROUND_POLL_MS` | `1000` | Background poll interval |
| `QUEUE_STALE_TIMEOUT_SECONDS` | `300` | Requeue stale processing items |
| `QUEUE_MAX_RETRY_ATTEMPTS` | `3` | Max retry attempts per item |

Priority bands:

- P0-P1 (`INTERACTIVE`, `NEAR_INTERACTIVE`) handled by interactive workers
- P2-P3 (`SCHEDULED`, `BULK`) handled by background workers

## Security Pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `SECURITY_TIER2_ENABLED` | `true` | Enable AI-based tier-2 analysis |
| `SECURITY_BLOCK_THRESHOLD` | `0.6` | Threat score for block |
| `SECURITY_FLAG_THRESHOLD` | `0.3` | Threat score for flag |
| `SECURITY_BYPASS_ENABLED` | `false` | Disable checks (testing only) |
| `SECURITY_NOTIFY_OWNER` | `true` | Notify owner on flagged events |

## Public API, Health, Updates, Telemetry

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Public API bind host |
| `API_PORT` | `8443` | Public API port |
| `API_JWT_SECRET` | unset | Session JWT signing secret |
| `HEALTH_ANALYSIS_ENABLED` | `true` | Enable health analysis pipeline |
| `SELF_HEALING_ENABLED` | `true` | Enable auto-healing routines |
| `AUTO_UPDATE_ENABLED` | `false` | Enable update checker |
| `AUTO_UPDATE_REPO` | empty | Update source repository |
| `AUTO_UPDATE_CHECK_INTERVAL_MINUTES` | `15` | Minutes between update checks |
| `UPDATE_REQUIRE_APPROVAL` | `false` | Require approval for updates |
| `AUTO_UPDATE_PAUSE_ON_FAILURE` | `true` | Pause future rollouts after failed update |
| `UPDATER_SERVICE_URL` | empty | Updater sidecar URL |
| `UPDATER_SECRET` | empty | Updater sidecar shared secret |
| `UPDATER_SECRET_PATH` | `/app/data/.updater-secret` | Shared secret file path |
| `UPDATER_STATE_PATH` | `/app/data/updater-state.json` | Persisted updater state path |
| `UPDATER_TRAEFIK_DYNAMIC_PATH` | `/project/config/traefik/dynamic/updater-routes.yml` | Traefik dynamic route file |
| `TELEMETRY_SHARING_ENABLED` | `false` | Enable outbound telemetry sharing |
| `TELEMETRY_CONSENT_CATEGORIES` | empty | Allowed telemetry categories |
| `TELEMETRY_CENTRAL_URL` | empty | Central telemetry endpoint |
| `TELEMETRY_API_KEY` | empty | Central telemetry API key |
| `TELEMETRY_CENTRAL_MODE` | `false` | Run as central telemetry receiver |
| `TELEMETRY_INSTANCE_ID` | empty | Instance ID |
| `TELEMETRY_REPORT_INTERVAL` | `86400` | Telemetry report interval |

## Logging and Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `production` | Environment name |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_TO_FILE` | `true` | Enable file logging |
| `LOG_DIRECTORY` | `logs` | Log directory |
| `LOG_FILE_MAX_BYTES` | `52428800` | Rotation size (50 MB) |
| `LOG_FILE_BACKUP_COUNT` | `10` | Number of rotated files |
| `LOG_ERROR_FILE_ENABLED` | `true` | Separate warning/error log |
| `LOG_FILE_PREFIX` | `zetherion_ai` | Log file prefix |

## Related Docs

- [System Architecture](architecture.md)
- [Docker and Services](docker.md)
- [Security](security.md)
- [API Reference](api-reference.md)
