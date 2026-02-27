# Security Model

## Overview

Zetherion uses layered security controls across container runtime, network
boundaries, authentication, content security, encrypted persistence, and action
policy gates.

This document reflects the current shipped topology:

- internal Skills API (`:8080`)
- public API (`:8443`) with tenant auth
- blue/green API and skills deployment behind Traefik

---

## Container and Runtime Hardening

| Control | Implementation |
|---|---|
| Non-root execution | Distroless/non-root runtime for bot/skills/api images |
| Read-only root FS | `read_only: true` with `tmpfs` write mounts where needed |
| Privilege escalation guard | `no-new-privileges:true` |
| Resource limits | CPU/memory limits in compose |
| Restart behavior | `restart: unless-stopped` |

---

## Network Security

| Boundary | Current behavior |
|---|---|
| Internal service network | Docker bridge network for service-to-service traffic |
| Public exposure | Public API reachable through routed API service path; tunnel optional via cloudflared |
| Host-exposed ports | Qdrant (`6333`) and Ollama generation (`11434`) by default in compose |
| Internal-only services | Skills/API backends, Traefik internal entrypoints, updater sidecar, postgres, ollama-router |

Important: Zetherion now includes an inbound public API surface (`/api/v1`).
The older “Discord-only inbound” assumption is no longer valid.

---

## Authentication and Authorization

### Discord Runtime

- allowlist and RBAC-backed user validation
- command-level admin restrictions for sensitive operations

### Internal Skills API

- `X-API-Secret` on most routes
- callback exemptions: `/oauth/{provider}/callback`, `/gmail/callback`
- health exemption: `/health`

### Public API

- `X-API-Key` for tenant control-plane endpoints
- Bearer session tokens (`zt_sess_...`) for chat endpoints
- tenant-session ownership checks enforced on every chat call
- per-tenant rate limiting in middleware

---

## Encryption and Secrets

### Data Encryption

- AES-256-GCM field encryption for sensitive stored values
- key material derived from `ENCRYPTION_PASSPHRASE` + persistent salt
- strict decrypt behavior controlled by `ENCRYPTION_STRICT`

### Secrets Handling

- runtime secret resolution supports encrypted-at-rest secret storage
- secrets API exposes metadata only (`GET /secrets`)
- secret values are never returned once stored

---

## Content Security Gate

Incoming content passes through a security pipeline combining:

- heuristic/regex prompt-injection checks
- optional tier-2 AI analysis (`SECURITY_TIER2_ENABLED`)
- threshold-based block/flag behavior

Primary knobs:

- `SECURITY_BLOCK_THRESHOLD`
- `SECURITY_FLAG_THRESHOLD`
- `SECURITY_BYPASS_ENABLED` (testing only)
- `SECURITY_NOTIFY_OWNER`

---

## Email/Work Router Security

When `WORK_ROUTER_ENABLED=true`:

- inbound routing passes through mandatory security gate unless disabled
- route policy can downgrade actions (`auto` -> `ask`/`draft`/`review`)
- dependencies failing can force queueing/fail-closed behavior
- optional local-extraction requirement controlled by `LOCAL_EXTRACTION_REQUIRED`

---

## Update Path Security

Updater sidecar is the only component intended to coordinate rollout/rollback.
It uses a shared secret (`X-Updater-Secret`) and persisted updater state.

Key controls:

- `UPDATER_SECRET` / `UPDATER_SECRET_PATH`
- `AUTO_UPDATE_PAUSE_ON_FAILURE`
- health-gated route flips through Traefik dynamic config

---

## Logging and Audit

- structured logging with rotation controls (`LOG_*` vars)
- RBAC and settings changes written to audit records
- security events (flagged/blocked inputs, auth failures) logged
- sensitive values are treated as secrets and should not be logged in plaintext

---

## Operational Checklist

- Set a strong `ENCRYPTION_PASSPHRASE` and back up salt safely.
- Set and rotate `SKILLS_API_SECRET`.
- Set and rotate updater secret for sidecar paths.
- Restrict Discord access with allowlist/RBAC.
- Keep `SECURITY_BYPASS_ENABLED=false` outside tests.
- Review public API credentials and tenant key lifecycle.

---

## Related Docs

- [Architecture](architecture.md)
- [Configuration](configuration.md)
- [Docker & Services](docker.md)
- [Skills API Reference](api-reference.md)
- [Public API Reference](public-api-reference.md)
