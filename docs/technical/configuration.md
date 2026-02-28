# Configuration Reference

## Overview

Zetherion runtime configuration is resolved with this precedence:

1. Runtime overrides in PostgreSQL (`settings` table)
2. Environment variables (`.env`, container env)
3. Defaults in `src/zetherion_ai/config.py`

Create local config from template:

```bash
cp .env.example .env
```

---

## Required Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token |
| `GEMINI_API_KEY` | Gemini API key |
| `ENCRYPTION_PASSPHRASE` | Encryption passphrase (min 16 chars) |

---

## Core Access and Identity

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_USER_IDS` | empty | Comma-separated Discord user IDs |
| `ALLOW_ALL_USERS` | `false` | Allow all users when allowlist is empty |
| `OWNER_USER_ID` | unset | Bootstrap owner RBAC user |
| `ALLOW_BOT_MESSAGES` | `false` | Accept bot-origin messages (testing) |
| `DEV_AGENT_WEBHOOK_NAME` | `zetherion-dev-agent` | Trusted dev webhook sender |
| `DEV_AGENT_WEBHOOK_ID` | empty | Optional Discord webhook ID allowlist for dev-agent ingestion |
| `DEV_AGENT_ENABLED` | `false` | Enable dev-agent monitoring and cleanup automation |
| `DEV_AGENT_SERVICE_URL` | `http://zetherion-ai-dev-agent:8787` | Dev-agent sidecar base URL |
| `DEV_AGENT_BOOTSTRAP_SECRET` | empty | One-time bootstrap secret for dev-agent provisioning |
| `DEV_AGENT_CLEANUP_HOUR` | `2` | Daily cleanup hour (0-23) for dev-agent tasks |
| `DEV_AGENT_CLEANUP_MINUTE` | `30` | Daily cleanup minute (0-59) for dev-agent tasks |
| `DEV_AGENT_APPROVAL_REPROMPT_HOURS` | `24` | Re-prompt interval for pending cleanup approvals |
| `DEV_AGENT_DISCORD_CHANNEL_ID` | empty | Discord channel ID used by dev-agent prompts/events |
| `DEV_AGENT_DISCORD_GUILD_ID` | empty | Discord guild ID used by dev-agent prompts/events |
| `DEV_JOURNAL_RETENTION_DAYS` | `120` | Days to retain dev journal entries |

---

## Router and Model Selection

| Variable | Default | Description |
|---|---|---|
| `ROUTER_BACKEND` | `gemini` | Router backend: `gemini`, `ollama`, `groq` |
| `ROUTER_MODEL` | `gemini-2.5-flash` | Gemini router model |
| `CLAUDE_MODEL` | `claude-sonnet-4-5-20250929` | Anthropic model |
| `OPENAI_MODEL` | `gpt-5.2` | OpenAI model |
| `EMBEDDING_MODEL` | `text-embedding-004` | Gemini embedding model |
| `ANTHROPIC_API_KEY` | unset | Anthropic API key |
| `OPENAI_API_KEY` | unset | OpenAI API key |
| `GROQ_API_KEY` | unset | Groq API key |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Groq OpenAI-compatible base URL |

---

## Ollama and Embeddings

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `ollama` | Generation Ollama host |
| `OLLAMA_PORT` | `11434` | Generation Ollama port |
| `OLLAMA_GENERATION_MODEL` | `llama3.1:8b` | Generation model |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Local embedding model |
| `OLLAMA_TIMEOUT` | `30` | Ollama timeout (seconds) |
| `OLLAMA_ROUTER_HOST` | `ollama-router` | Router Ollama host |
| `OLLAMA_ROUTER_PORT` | `11434` | Router Ollama port |
| `OLLAMA_ROUTER_MODEL` | `llama3.2:3b` | Router model |
| `EMBEDDINGS_BACKEND` | `openai` | `openai`, `gemini`, `ollama` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-large` | OpenAI embedding model |
| `OPENAI_EMBEDDING_DIMENSIONS` | `3072` | OpenAI embedding dimensions |

---

## Storage and Encryption

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DSN` | `postgresql://zetherion:password@postgres:5432/zetherion` | PostgreSQL DSN |
| `POSTGRES_POOL_MIN_SIZE` | `1` | Minimum asyncpg pool size per service |
| `POSTGRES_POOL_MAX_SIZE` | `5` | Maximum asyncpg pool size per service |
| `QDRANT_HOST` | `qdrant` | Qdrant host |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `QDRANT_USE_TLS` | `false` | Enable TLS for Qdrant |
| `QDRANT_CERT_PATH` | unset | Qdrant TLS certificate path |
| `ENCRYPTION_SALT_PATH` | `data/salt.bin` | Salt file path |
| `ENCRYPTION_STRICT` | `false` | Fail on decrypt errors |

`ENCRYPTION_ENABLED` appears in `.env.example` as a compatibility flag, but
runtime enforcement is based on `ENCRYPTION_PASSPHRASE` and encryption settings
in `config.py`.

---

## Skills Service and Runtime Settings

| Variable | Default | Description |
|---|---|---|
| `SKILLS_SERVICE_URL` | `http://zetherion-ai-skills:8080` | Internal skills API URL |
| `SKILLS_API_SECRET` | unset | Shared secret for `X-API-Secret` |
| `SKILLS_REQUEST_TIMEOUT` | `30` | Skills API timeout (seconds) |

Runtime settings/secrets APIs are exposed by the skills service:

- `/settings` and `/settings/{namespace}/{key}`
- `/secrets` and `/secrets/{name}`

---

## Work Router and Integrations

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | unset | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | unset | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8080/gmail/callback` | OAuth callback URI |
| `WORK_ROUTER_ENABLED` | `false` | Enable provider-agnostic work router |
| `PROVIDER_OUTLOOK_ENABLED` | `false` | Enable Outlook adapter scaffold |
| `EMAIL_SECURITY_GATE_ENABLED` | `true` | Security gate for email ingestion |
| `LOCAL_EXTRACTION_REQUIRED` | `false` | Force local extraction path |

---

## GitHub Integration

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | unset | GitHub Personal Access Token for GitHub skill/API access |
| `GITHUB_DEFAULT_REPO` | unset | Default repository (`owner/repo`) for GitHub operations |
| `GITHUB_API_TIMEOUT` | `20` | GitHub API timeout (seconds) |

---

## Inference, Discovery, and Cost

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_BROKER_ENABLED` | `true` | Enable inference broker |
| `COST_TRACKING_ENABLED` | `true` | Enable per-request cost tracking |
| `MODEL_DISCOVERY_ENABLED` | `true` | Enable model discovery |
| `MODEL_REFRESH_HOURS` | `24` | Refresh interval |
| `ANTHROPIC_TIER` | `balanced` | Anthropic tier |
| `OPENAI_TIER` | `balanced` | OpenAI tier |
| `GOOGLE_TIER` | `fast` | Google tier |
| `COST_DB_PATH` | `data/costs.db` | Cost DB path |
| `DAILY_BUDGET_USD` | unset | Daily budget threshold |
| `MONTHLY_BUDGET_USD` | unset | Monthly budget threshold |
| `BUDGET_WARNING_PCT` | `80.0` | Budget warning percentage |

---

## Notifications

| Variable | Default | Description |
|---|---|---|
| `NOTIFICATIONS_ENABLED` | `true` | Enable notifications |
| `NOTIFY_ON_NEW_MODELS` | `true` | Notify on new models |
| `NOTIFY_ON_DEPRECATION` | `true` | Notify on deprecations |
| `NOTIFY_ON_MISSING_PRICING` | `false` | Notify on missing pricing |
| `DAILY_SUMMARY_ENABLED` | `false` | Daily summary toggle |
| `DAILY_SUMMARY_HOUR` | `9` | Daily summary hour |
| `PROVIDER_ISSUE_ALERTS_ENABLED` | `true` | Alert on provider auth/billing/rate-limit failures |
| `PROVIDER_ISSUE_ALERT_COOLDOWN_SECONDS` | `3600` | Cooldown between repeated provider issue alerts |
| `PROVIDER_PROBE_ENABLED` | `true` | Enable periodic paid-provider readiness probes |
| `PROVIDER_PROBE_INTERVAL_SECONDS` | `1800` | Seconds between provider readiness probes |

---

## Profile and Personalization

| Variable | Default | Description |
|---|---|---|
| `PROFILE_INFERENCE_ENABLED` | `true` | Enable profile extraction |
| `PROFILE_TIER1_ONLY` | `false` | Regex-only profile extraction |
| `PROFILE_CONFIDENCE_THRESHOLD` | `0.6` | Auto-apply threshold |
| `PROFILE_CACHE_TTL` | `300` | Cache TTL (seconds) |
| `PROFILE_DB_PATH` | `data/profiles.db` | Profile DB path |
| `PROFILE_MAX_PENDING_CONFIRMATIONS` | `5` | Pending confirmation cap |
| `PROFILE_CONFIRMATION_EXPIRY_HOURS` | `72` | Confirmation expiry |
| `DEFAULT_FORMALITY` | `0.5` | Initial formality |
| `DEFAULT_VERBOSITY` | `0.5` | Initial verbosity |
| `DEFAULT_PROACTIVITY` | `0.3` | Initial proactivity |
| `TRUST_EVOLUTION_RATE` | `0.05` | Trust growth increment |

---

## Queue and Scheduling

| Variable | Default | Description |
|---|---|---|
| `QUEUE_ENABLED` | `true` | Enable priority queue |
| `QUEUE_INTERACTIVE_WORKERS` | `3` | Interactive workers |
| `QUEUE_BACKGROUND_WORKERS` | `2` | Background workers |
| `QUEUE_POLL_INTERVAL_MS` | `100` | Interactive poll interval |
| `QUEUE_BACKGROUND_POLL_MS` | `1000` | Background poll interval |
| `QUEUE_STALE_TIMEOUT_SECONDS` | `300` | Requeue stale tasks timeout |
| `QUEUE_MAX_RETRY_ATTEMPTS` | `3` | Max retry attempts |

---

## Security Pipeline

| Variable | Default | Description |
|---|---|---|
| `SECURITY_TIER2_ENABLED` | `true` | Enable tier-2 AI analysis |
| `SECURITY_BLOCK_THRESHOLD` | `0.6` | Block threshold |
| `SECURITY_FLAG_THRESHOLD` | `0.3` | Flag threshold |
| `SECURITY_BYPASS_ENABLED` | `false` | Disable checks (testing only) |
| `SECURITY_NOTIFY_OWNER` | `true` | Notify owner on flagged events |

---

## Docs Knowledge

| Variable | Default | Description |
|---|---|---|
| `DOCS_KNOWLEDGE_ENABLED` | `true` | Enable docs-backed Q&A path |
| `DOCS_KNOWLEDGE_ROOT` | `docs` | Docs indexing root |
| `DOCS_KNOWLEDGE_STATE_PATH` | `data/docs_knowledge_state.json` | Sync state path |
| `DOCS_KNOWLEDGE_GAP_LOG_PATH` | `data/docs_unknown_questions.jsonl` | Gap log path |
| `DOCS_KNOWLEDGE_SYNC_INTERVAL_SECONDS` | `300` | Sync throttle interval |
| `DOCS_KNOWLEDGE_MAX_HITS` | `6` | Max docs retrieval hits |
| `DOCS_KNOWLEDGE_MIN_SCORE` | `0.3` | Similarity threshold |

---

## Public API, Updates, Telemetry, Health

| Variable | Default | Description |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Public API bind host |
| `API_PORT` | `8443` | Public API port |
| `API_JWT_SECRET` | unset | Session JWT signing secret |
| `CGS_GATEWAY_HOST` | `0.0.0.0` | CGS gateway bind host |
| `CGS_GATEWAY_PORT` | `8743` | CGS gateway bind port |
| `CGS_GATEWAY_ALLOWED_ORIGINS` | empty | Comma-separated CORS origins for CGS gateway |
| `CGS_AUTH_JWKS_URL` | empty | JWKS URL for validating CGS JWT bearer tokens |
| `CGS_AUTH_ISSUER` | empty | Expected JWT issuer for CGS auth tokens |
| `CGS_AUTH_AUDIENCE` | empty | Expected JWT audience for CGS auth tokens |
| `ZETHERION_PUBLIC_API_BASE_URL` | `http://zetherion-ai-traefik:8443` | Upstream Zetherion public API base URL for CGS gateway |
| `ZETHERION_SKILLS_API_BASE_URL` | `http://zetherion-ai-traefik:8080` | Upstream Zetherion skills API base URL for CGS gateway |
| `ZETHERION_SKILLS_API_SECRET` | unset | Optional override secret for CGS gateway -> skills API calls |
| `ANALYTICS_EVENT_RETENTION_DAYS` | `90` | Retention window for raw web events |
| `ANALYTICS_REPLAY_RETENTION_DAYS` | `14` | Retention window for replay chunk metadata |
| `ANALYTICS_REPLAY_ENABLED_DEFAULT` | `false` | Default replay ingest policy for tenants |
| `ANALYTICS_REPLAY_SAMPLE_RATE_DEFAULT` | `0.1` | Default replay sampling ratio (0.0-1.0) |
| `ANALYTICS_JOBS_ENABLED` | `true` | Enable periodic analytics aggregation + retention pruning jobs |
| `ANALYTICS_HOURLY_JOB_INTERVAL_SECONDS` | `3600` | Hourly analytics job loop interval |
| `ANALYTICS_DAILY_JOB_INTERVAL_SECONDS` | `86400` | Daily analytics job loop interval |
| `OBJECT_STORAGE_BACKEND` | `local` | Replay byte storage backend (`none`, `local`, `s3`) |
| `OBJECT_STORAGE_LOCAL_PATH` | `data/replay_chunks` | Local replay chunk path |
| `OBJECT_STORAGE_BUCKET` | empty | Object storage bucket for replay chunks |
| `OBJECT_STORAGE_REGION` | empty | Object storage region for replay chunks |
| `OBJECT_STORAGE_ENDPOINT` | empty | Custom S3-compatible object storage endpoint |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | empty | Object storage access key ID |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | empty | Object storage secret access key |
| `OBJECT_STORAGE_FORCE_PATH_STYLE` | `true` | Path-style addressing toggle for S3-compatible stores |
| `RELEASE_MARKER_SIGNING_SECRET` | empty | Optional HMAC secret for signed release marker ingestion |
| `RELEASE_MARKER_SIGNATURE_TTL_SECONDS` | `300` | Signed release marker timestamp freshness window |
| `APP_WATCHER_TRUST_MODE` | `recommend_only` | Trust ladder mode (`recommend_only`, `guarded_autopilot`, `full_autonomous`) |
| `APP_WATCHER_AUTOPILOT_ENABLED` | `false` | Enable guarded autopilot rollout path |
| `APP_WATCHER_GLOBAL_KILL_SWITCH` | `false` | Kill switch for autonomous app-watcher actions |
| `CLOUDFLARE_TUNNEL_TOKEN` | unset | Cloudflared tunnel token |
| `HEALTH_ANALYSIS_ENABLED` | `true` | Health analysis pipeline toggle |
| `SELF_HEALING_ENABLED` | `true` | Self-healing toggle |
| `AUTO_UPDATE_ENABLED` | `false` | Auto-update toggle |
| `AUTO_UPDATE_REPO` | empty | Update source repo |
| `AUTO_UPDATE_CHECK_INTERVAL_MINUTES` | `15` | Update check interval |
| `UPDATE_REQUIRE_APPROVAL` | `false` | Require update approval |
| `AUTO_UPDATE_PAUSE_ON_FAILURE` | `true` | Pause after failed rollout |
| `UPDATER_SERVICE_URL` | empty | Updater sidecar URL |
| `UPDATER_SECRET` | empty | Updater shared secret |
| `UPDATER_SECRET_PATH` | `/app/data/.updater-secret` | Shared secret file path |
| `UPDATER_STATE_PATH` | `/app/data/updater-state.json` | Updater state file |
| `UPDATER_VERIFY_SIGNATURES` | `true` | Require signed release verification before update apply |
| `UPDATER_VERIFY_IDENTITY` | empty | Expected Cosign certificate identity for signatures |
| `UPDATER_VERIFY_OIDC_ISSUER` | `https://token.actions.githubusercontent.com` | Expected OIDC issuer for Cosign keyless verification |
| `UPDATER_VERIFY_REKOR_URL` | `https://rekor.sigstore.dev` | Rekor transparency log URL for signature verification |
| `UPDATER_RELEASE_MANIFEST_ASSET` | `release-manifest.json` | Release asset name for signed update manifest |
| `UPDATER_RELEASE_SIGNATURE_ASSET` | `release-manifest.sig` | Release asset name for update manifest signature |
| `UPDATER_RELEASE_CERTIFICATE_ASSET` | `release-manifest.pem` | Release asset name for signing certificate |
| `UPDATER_TRAEFIK_DYNAMIC_PATH` | `/project/config/traefik/dynamic/updater-routes.yml` | Traefik route file |
| `TELEMETRY_SHARING_ENABLED` | `false` | Outbound telemetry toggle |
| `TELEMETRY_CONSENT_CATEGORIES` | empty | Allowed telemetry categories |
| `TELEMETRY_CENTRAL_URL` | empty | Central telemetry URL |
| `TELEMETRY_API_KEY` | empty | Central telemetry API key |
| `TELEMETRY_CENTRAL_MODE` | `false` | Central telemetry receiver mode |
| `TELEMETRY_INSTANCE_ID` | empty | Telemetry instance ID |
| `TELEMETRY_REPORT_INTERVAL` | `86400` | Telemetry report interval |

Legacy `REPLAY_STORAGE_*` environment variable names remain accepted for backward compatibility, but `OBJECT_STORAGE_*` is the preferred naming.

---

## Logging and Runtime

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `production` | Environment name |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_TO_FILE` | `true` | Enable file logging |
| `LOG_DIRECTORY` | `logs` | Log directory |
| `LOG_FILE_MAX_BYTES` | `52428800` | Rotation size |
| `LOG_FILE_BACKUP_COUNT` | `10` | Backup log count |
| `LOG_ERROR_FILE_ENABLED` | `true` | Dedicated warning/error log |
| `LOG_FILE_PREFIX` | `zetherion_ai` | Log file prefix |

---

## Intentionally Undocumented / Internal Exclusions

The following variables are intentionally excluded from the runtime reference or
are script/test-only compatibility knobs:

- `TEST_DISCORD_BOT_TOKEN`
- `TEST_DISCORD_CHANNEL_ID`
- `TEST_DISCORD_TARGET_BOT_ID`
- `OLLAMA_DOCKER_MEMORY`
- `DOCKER_SOCKET_PATH`
- `ANALYTICS_EVENT_RETENTION_DAYS`
- `ANALYTICS_REPLAY_RETENTION_DAYS`
- `ANALYTICS_REPLAY_ENABLED_DEFAULT`
- `ANALYTICS_REPLAY_SAMPLE_RATE_DEFAULT`
- `APP_WATCHER_TRUST_MODE`
- `APP_WATCHER_AUTOPILOT_ENABLED`
- `APP_WATCHER_GLOBAL_KILL_SWITCH`

These are used by setup/test scripts or host-level deployment wiring, not by
normal application runtime flows.

---

## Related Docs

- [Architecture](architecture.md)
- [Docker & Services](docker.md)
- [Security](security.md)
- [Skills API Reference](api-reference.md)
- [Public API Reference](public-api-reference.md)
