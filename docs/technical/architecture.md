# System Architecture

## Overview

Zetherion AI is a source-agnostic assistant with two API surfaces:

- **Internal Skills API** (`:8080`) for bot/service orchestration
- **Public API** (`:8443`) for tenant-scoped external integrations

The current Docker compose topology supports blue/green switching for both
skills and public API services, with Traefik as the internal switch and an
updater sidecar handling rollout/rollback orchestration.

The project enforces `>=90%` coverage in test configuration and currently
contains 5,000+ tests.

---

## High-Level Topology

```text
Discord Gateway / External App Clients
            |
            v
+-------------------------------+
| zetherion-ai-bot              |
| - Security pipeline           |
| - Router + inference broker   |
| - Skills client               |
+---------------+---------------+
                |
                v
      +---------------------+
      | zetherion-ai-traefik|
      +---+-------------+---+
          |             |
          v             v
+----------------+  +----------------+
| skills-blue    |  | skills-green   |
| :8080 internal |  | :8080 internal |
+----------------+  +----------------+

Public API path (tenant integrations):

External Client -> Cloudflared (optional) -> Traefik -> api-blue/api-green

Data plane:
- PostgreSQL (RBAC, settings, integrations, tenant/session data)
- Qdrant (vector memory)
- Ollama (generation/embeddings)
- Ollama Router (classification)

Update plane:
- updater sidecar performs blue/green rollout and rollback
```

---

## Service Inventory

| Service | Role |
|---|---|
| `zetherion-ai-bot` | Discord runtime and main orchestration path |
| `zetherion-ai-skills-blue/green` | Internal skills control plane |
| `zetherion-ai-api-blue/green` | Public `/api/v1` service |
| `zetherion-ai-traefik` | Internal routing/switch layer |
| `zetherion-ai-updater` | Rollout/rollback sidecar |
| `zetherion-ai-cloudflared` | Optional secure tunnel for public API exposure |
| `postgres` | Relational persistence |
| `qdrant` | Vector memory persistence |
| `ollama` | Local generation + embeddings |
| `ollama-router` | Local routing model |

---

## API Surfaces

### Internal Skills API (`:8080`)

Primary uses:

- skill execution (`/handle`)
- heartbeat actions (`/heartbeat`)
- runtime RBAC/settings/secrets operations
- OAuth authorization/callback handling

Auth model:

- `X-API-Secret` required for most routes
- health and OAuth callback routes are exempt by design

See [Skills API Reference](api-reference.md).

### Public API (`:8443`)

Primary uses:

- tenant session lifecycle (`/api/v1/sessions`)
- session-token chat (`/api/v1/chat`, `/chat/stream`, `/chat/history`)
- optional tenant-scoped YouTube endpoints (`/api/v1/youtube/...`)

Auth model:

- `X-API-Key` for tenant control-plane routes
- Bearer session token for chat routes
- per-tenant in-memory rate limiting

See [Public API Reference](public-api-reference.md).

---

## Runtime Flows

### 1. Discord Bot Flow

1. Message arrives from Discord gateway.
2. Bot security checks run (allowlist/RBAC, rate limits, content security).
3. Router classifies intent and complexity.
4. Bot either:
   - handles direct inference path, or
   - dispatches to Skills API (`/handle`), depending on intent.
5. Memory/profile context is merged as needed.
6. Response is returned to Discord.

### 2. Skills/Heartbeat Flow

1. Scheduler triggers heartbeat cycle.
2. Skills service aggregates `on_heartbeat` actions.
3. Actions are prioritized and returned to bot.
4. Bot executes actions with quiet-hours/rate-limit safeguards.

### 3. Public API Chat Flow

1. External app creates session with tenant API key.
2. API returns prefixed JWT session token.
3. Client calls chat endpoints with bearer token.
4. API validates tenant + session, applies tenant rate limits.
5. Chat skill/inference path generates response.
6. Messages are persisted to tenant session history.

---

## Data Architecture

| Data | Store |
|---|---|
| Tenant/session/chat records | PostgreSQL |
| RBAC users + audit log | PostgreSQL |
| Runtime settings + encrypted secrets metadata | PostgreSQL |
| Integrations/work router state | PostgreSQL |
| Personal/profile/contact/policy records | PostgreSQL |
| Vectorized memory and docs-knowledge embeddings | Qdrant |
| Cost tracking operational DB | SQLite (`data/costs.db`) |

---

## Inference and Routing

- Router backend is configurable: `gemini`, `ollama`, or `groq`.
- Inference broker supports provider-aware task routing across local and cloud models.
- Dedicated Ollama router model avoids generation model swap latency.
- Work router/email routing path supports provider-agnostic ingestion and policy gates.

---

## Update and Rollback Architecture

Updater sidecar coordinates zero-downtime style swaps for API/skills services:

1. Build inactive color
2. Health-check inactive services
3. Flip Traefik routing to inactive color
4. Restart bot with graceful reconnect path
5. Stop old color

On failure, sidecar rolls back and can pause future rollouts until unpaused.
State is persisted in `UPDATER_STATE_PATH`.

See [Docker & Services](docker.md) and [Auto-Update](../user/auto-update.md).

---

## Known Script Drift

`start.sh` and `status.sh` still contain a few legacy checks using old
single-service names (for example `zetherion-ai-skills`).

When script output disagrees with live topology, use `docker compose ps` as
canonical runtime state.

---

## Related Docs

- [Docker & Services](docker.md)
- [Security](security.md)
- [Skills API Reference](api-reference.md)
- [Public API Reference](public-api-reference.md)
- [AI Agent Integration](ai-agent-integration.md)
