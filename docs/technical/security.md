# Security Model

## Overview

Zetherion AI implements a defense-in-depth security architecture spanning multiple layers: container hardening with distroless images, field-level encryption at rest using AES-256-GCM, role-based access control backed by PostgreSQL, prompt injection defense with 17 regex patterns and Unicode analysis, progressive trust for Gmail automation, and network isolation via Docker bridge networking. Each layer operates independently so that a breach at one level does not compromise the entire system.

## Container Security

### Distroless Images

The bot and skills services use Google's `gcr.io/distroless/python3-debian12:nonroot` as the runtime base image. Distroless images contain only the application and its runtime dependencies. They do not include:

- No shell (`/bin/sh`, `/bin/bash`)
- No package manager (`apt`, `yum`, `apk`)
- No system utilities (`curl`, `wget`, `nc`)
- No OS libraries beyond what the application requires

This reduces the container image size by approximately 70% compared to `python:3.11-slim` and eliminates the tools an attacker would need for lateral movement, reconnaissance, or privilege escalation after gaining code execution.

The image runs as the `nonroot` user (UID 65532) by default, preventing root-level filesystem access even if a container escape were attempted.

### Runtime Hardening

| Control | Detail |
|---------|--------|
| Read-only root filesystem | `read_only: true` in Docker Compose; writable paths via `tmpfs` for `/tmp` |
| No new privileges | `security_opt: no-new-privileges:true` on all containers |
| Resource limits | CPU and memory quotas via Docker Compose `deploy.resources` |
| Network isolation | All services communicate on a dedicated `zetherion_ai-net` bridge network |
| Health checks | TCP-based health checks with intervals, timeouts, retries, and start periods |
| Restart policy | `unless-stopped` for automatic recovery |

## Encryption

### Field-Level Encryption

All sensitive data stored in Qdrant and PostgreSQL is encrypted at the application layer before being written to disk.

- **Algorithm**: AES-256-GCM (authenticated encryption providing both confidentiality and integrity)
- **Key derivation**: PBKDF2-HMAC-SHA256 with 600,000 iterations (OWASP-recommended count)
- **Salt**: Random 256-bit (32 bytes), persisted at `ENCRYPTION_SALT_PATH`
- **Per-field encryption**: Each field encryption generates a unique random 96-bit nonce/IV
- **Tamper detection**: GCM authentication tag detects any modification to ciphertext

### What Gets Encrypted

| Collection / Store | Encrypted Fields | Plaintext Fields |
|--------------------|-----------------|------------------|
| `conversations` | `content` | `user_id`, `channel_id`, `role`, `timestamp` |
| `long_term_memory` | `content` | `type`, `timestamp`, metadata |
| `user_profiles` | `key`, `value` | `category`, `confidence`, `user_id` |
| `skill_tasks` | `title`, `description` | `status`, `priority`, `deadline` |
| Gmail tokens | `access_token`, `refresh_token` | `user_id`, `expiry` |

### What Is NOT Encrypted

- **Vector embeddings**: Required for similarity search in Qdrant. Encrypting embeddings would destroy the distance properties that make vector search possible.
- **Metadata**: Timestamps, IDs, user IDs, and other structural data needed for indexing and queries.
- **Configuration data**: Settings stored in PostgreSQL dynamic settings table.

### Key Management

```env
ENCRYPTION_PASSPHRASE=minimum-16-character-passphrase
ENCRYPTION_SALT_PATH=data/salt.bin
```

- The passphrase is loaded as a `SecretStr` and never stored in the database or logged.
- The salt file is generated automatically on first run and must be backed up. Loss of the salt file means loss of all encrypted data.
- Key rotation is supported via `KeyManager.rotate_key()` but requires a data migration step to re-encrypt all existing records.

## Access Control

### User Allowlist

```env
ALLOWED_USER_IDS=123456789,987654321
```

- When set, only the listed Discord user IDs can interact with the bot.
- When empty, all users are permitted (a warning is logged at startup).
- Checked on every incoming message before any processing occurs.
- Users can be added or removed at runtime via `UserAllowlist.add()` and `.remove()`.

### RBAC (Role-Based Access Control)

PostgreSQL-backed user management provides three roles with distinct permission boundaries:

| Role | Permissions |
|------|-------------|
| `owner` | Full system control, manage admin users, change all settings |
| `admin` | Manage regular users, change non-critical settings |
| `user` | Standard bot interaction, no administrative capabilities |

The bootstrap admin is set via the `OWNER_USER_ID` environment variable. This user is automatically assigned the `owner` role on first startup.

RBAC is managed through the Skills Service API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/users` | POST | Add a user with a specified role |
| `/users/{id}/role` | PATCH | Change a user's role |
| `/users/{id}` | DELETE | Remove a user's access |
| `/users/audit` | GET | Retrieve the RBAC audit trail |

All role changes are recorded in the PostgreSQL audit trail with timestamps, the acting user, and the action performed.

### Skills Service Authentication

All non-health endpoints on the Skills Service require an `X-API-Secret` header:

- The secret is configured via `SKILLS_API_SECRET` in the environment.
- Comparison uses HMAC-based constant-time comparison to prevent timing attacks.
- The health endpoint (`/health`) bypasses authentication to allow monitoring and orchestration tools to check service status.

## Prompt Injection Defense

Every user message is checked before being forwarded to any LLM backend. The defense system is implemented in `src/zetherion_ai/discord/security.py` and operates in three layers:

1. **Regex pattern matching**: 17 case-insensitive patterns detect common injection techniques including "ignore previous instructions", "you are now a...", "system prompt:", "jailbreak", "DAN mode", and "disable/bypass filters" variations. Patterns account for spacing, punctuation, and phrasing differences.

2. **Unicode obfuscation detection**: Compares NFKC-normalized text to the original. A length difference exceeding 10% indicates homoglyph substitution (for example, Cyrillic characters replacing Latin ones to bypass keyword filters).

3. **Roleplay marker heuristic**: Flags messages containing more than 5 bracket pairs or `(system` markers, which indicate structured injection attempts.

Flagged messages are logged with the matched pattern and rejected before reaching any LLM. Graceful degradation ensures that if the Unicode check fails, it is skipped rather than crashing the message pipeline.

## Gmail Security

### OAuth 2.0 Flow

Gmail integration uses the standard OAuth 2.0 authorization code flow:

- **Scopes requested**: `gmail.readonly` (minimum required), `gmail.send` (granted only when trust thresholds are met)
- **Token storage**: Access tokens and refresh tokens are encrypted at rest using the same AES-256-GCM field encryption used for all other sensitive data
- **Token refresh**: Handled automatically when the access token expires
- **Configuration**: Requires `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` environment variables

### Progressive Trust System

Gmail automation uses a two-dimensional trust model to gradually increase autonomy based on demonstrated reliability:

**Trust dimensions**:
- **Contact trust**: Trust level specific to a particular email contact, reflecting the history of interactions with that person
- **Reply type trust**: Trust level specific to the category of reply (e.g., informational, scheduling, sensitive)

**Trust evolution**:
| User Action | Trust Delta |
|-------------|------------|
| Approval (send as-is) | +0.05 |
| Minor edit (small changes) | -0.02 |
| Major edit (significant rewrite) | -0.10 |
| Rejection (discard draft) | -0.20 |

**Auto-send thresholds**:
- Trust starts at 0.0 for all new contacts and reply types
- Auto-send requires: `effective_trust >= 0.85` AND `confidence >= 0.85`
- Global trust cap: 0.95 (the system never becomes fully autonomous)
- Reply type ceilings limit trust by category (e.g., `SENSITIVE` replies are capped at 0.30)

### Trust Formula

```
effective_trust = min(type_trust, contact_trust, reply_type_ceiling)
```

The effective trust is the minimum of the three values, ensuring that all dimensions must independently reach the threshold before automation is permitted. This prevents a high contact trust from overriding a low reply type ceiling for sensitive communications.

## GitHub Security

- The personal access token is stored in `.env` as `GITHUB_TOKEN` and loaded as a `SecretStr`. It is never stored in the database.
- Configurable autonomy levels control which actions the bot can perform without confirmation.
- High-risk actions (such as merging a pull request or deleting a branch) always require explicit user confirmation regardless of autonomy settings.
- All GitHub actions are logged in the audit trail with the action type, repository, and outcome.

## PostgreSQL Security

| Control | Detail |
|---------|--------|
| Network exposure | Internal Docker network only; no host port mapping by default |
| Credentials | Stored in `.env` file, loaded via `POSTGRES_DSN` |
| Connection | Via internal Docker bridge network (`zetherion_ai-net`) |
| Password policy | Production deployments should use strong, randomly generated passwords |
| Data stored | RBAC users and roles, dynamic settings, audit trail, Gmail trust state |

The PostgreSQL container is not exposed to the host network. Only containers on the `zetherion_ai-net` bridge can connect to it.

## Network Security

| Control | Detail |
|---------|--------|
| Internal network | All services communicate on the `zetherion_ai-net` Docker bridge network |
| Host exposure | Only Qdrant (6333) and Ollama (11434) are mapped to host ports (for development/testing) |
| Bot/Skills communication | Via internal network; no external ingress required |
| External API calls | All outbound API calls (Anthropic, OpenAI, Gemini, Discord) use HTTPS via `httpx` |
| No inbound web server | The bot connects outbound to Discord's WebSocket gateway and does not listen on any HTTP port |

## Rate Limiting

| Parameter | Default | Environment Variable |
|-----------|---------|---------------------|
| Max messages per window | 10 | `RATE_LIMIT_MESSAGES` |
| Window duration | 60 seconds | `RATE_LIMIT_WINDOW` |
| Warning cooldown | 30 seconds | (hardcoded) |

Rate limiting is per-user with automatic timestamp cleanup. When a user exceeds the limit, a warning message is returned, throttled to one warning per cooldown period to avoid spam.

## Logging and Audit

| Layer | Implementation |
|-------|---------------|
| Structured logging | `structlog` with JSON output (production) or colored console (development) |
| Log rotation | `RotatingFileHandler`: 50MB max per file, 10 backup files |
| Separate error log | WARNING-level and above written to a dedicated error log file |
| Credential protection | `SecretStr` objects log as `'**********'`; passphrase never appears in logs |
| RBAC audit trail | All role changes recorded in PostgreSQL with timestamp, actor, and action |
| Gmail trust events | Trust score changes logged with contact, reply type, action, and new trust values |
| GitHub actions | All API operations logged with action type, repository, and result |
| Security events | Prompt injection attempts, allowlist changes, and rate limit triggers logged |
| Third-party noise | Discord and httpx loggers set to WARNING level |

## Security Checklist

- [ ] Set `ALLOWED_USER_IDS` to restrict access in production
- [ ] Set a strong `ENCRYPTION_PASSPHRASE` (minimum 16 characters, recommend 32+)
- [ ] Back up the salt file (`data/salt.bin`) securely and separately from the repository
- [ ] Set `SKILLS_API_SECRET` for Skills Service authentication
- [ ] Use a strong, randomly generated PostgreSQL password in `POSTGRES_DSN`
- [ ] Review rate limit settings for your expected usage pattern
- [ ] Enable Message Content Intent in the Discord Developer Portal
- [ ] Never commit `.env` to version control
- [ ] Set `OWNER_USER_ID` for RBAC bootstrap
- [ ] Review Gmail OAuth scopes and trust thresholds before enabling email automation
- [ ] Store `GITHUB_TOKEN` only in `.env`, never in code or database

## Related Docs

- [Architecture](architecture.md) -- System design and component interactions
- [Configuration Reference](configuration.md) -- Complete environment variable reference
- [Docker Deployment](docker.md) -- Container setup and orchestration
