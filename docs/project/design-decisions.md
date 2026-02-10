# Design Decisions

Architecture decision records (ADRs) documenting key technical choices in Zetherion AI.

## ADR-001: PostgreSQL for Personal Understanding

**Date:** 2026-02-07
**Status:** Accepted

### Context

Phase 9 introduced a personal understanding layer that stores structured user data: profiles, contacts, policies, and learnings. This data is relational (contacts relate to users, policies reference domains) and requires ACID transactions for consistency.

### Decision

Use PostgreSQL 17 (Alpine) as a dedicated service for personal understanding data, alongside the existing Qdrant vector database.

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| SQLite | Simple, no service needed | No concurrent writes, file locking issues in Docker |
| Qdrant only | Already deployed | Poor fit for relational data, no transactions |
| PostgreSQL | ACID, relational, concurrent, mature | Additional Docker service, more memory |

### Consequences

- Added a 6th Docker service (postgres)
- asyncpg for non-blocking database access
- RBAC support (owner, admin, user roles) with proper foreign keys
- Clear separation: Qdrant for vectors/semantic search, PostgreSQL for structured relational data

---

## ADR-002: Progressive Trust System for Gmail

**Date:** 2026-02-07
**Status:** Accepted

### Context

Gmail integration requires sending emails on behalf of users. Blindly auto-sending could cause embarrassment or damage. Users need to build confidence in the system before granting autonomy.

### Decision

Implement a two-dimensional trust system:

1. **Per-contact trust**: How much the user trusts the bot with a specific contact
2. **Per-type trust**: How much the user trusts the bot with a type of reply (acknowledgment vs. sensitive)

The effective trust is: `min(type_trust, contact_trust, reply_type_ceiling)`

Auto-send only occurs when effective trust >= 0.85 AND confidence >= 0.85.

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| Binary on/off | Simple | Too coarse, all-or-nothing |
| Single trust score | Moderate complexity | Cannot distinguish "safe to auto-ack" from "safe to negotiate" |
| Two-dimensional trust | Granular control | More complex, requires ceiling system |

### Consequences

- Trust evolves gradually: +0.05 per approval, -0.20 per rejection
- Global cap of 0.95 (never fully autonomous)
- 8 reply type ceilings (ACKNOWLEDGMENT 0.95 down to SENSITIVE 0.30)
- Users naturally progress: read-only -> draft with approval -> auto-draft -> auto-send

---

## ADR-003: Distroless Container Images

**Date:** 2026-02-07
**Status:** Accepted

### Context

The bot and skills containers need to be secure by default. Traditional Python images include shells, package managers, and utilities that expand the attack surface.

### Decision

Use distroless base images (Chainguard Python) for the bot and skills service containers.

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| python:3.12-slim | Familiar, debuggable | Includes shell, package manager, ~150MB |
| Alpine-based | Small (~50MB) | musl libc compatibility issues with some Python packages |
| Distroless | Minimal attack surface, ~50MB, 0 CVEs | No shell for debugging, harder to inspect |

### Consequences

- Runtime images contain only Python interpreter and application code
- No shell access in production (use `docker cp` + external tools for debugging)
- Read-only root filesystem with tmpfs for `/tmp`
- Combined with `no-new-privileges` and resource limits

---

## ADR-004: Dual Ollama Architecture

**Date:** 2026-02-07
**Status:** Accepted

### Context

Ollama supports only one loaded model at a time efficiently. The system needs two models: a small router model (llama3.2:1b) for fast intent classification and a larger generation model (llama3.1:8b) for responses. Model swapping between requests causes delays.

### Decision

Run two separate Ollama containers, each dedicated to one model.

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| Single Ollama (swap models) | Less resources | 5-15s delay per model swap |
| Single Ollama (both loaded) | No swap delay | Requires 2x memory, Ollama manages poorly |
| Two Ollama containers | Dedicated resources, no swapping | More Docker services, more memory |

### Consequences

- `zetherion-ai-ollama`: Generation model (llama3.1:8b) on port 11434
- `zetherion-ai-ollama-router`: Router model (llama3.2:1b) on port 11435
- Each container has dedicated memory limits
- Zero model-swap latency
- Total memory: ~6GB for both (4.7GB generation + 1.3GB router)

---

## ADR-005: Skills Framework with REST API

**Date:** 2026-02-06
**Status:** Accepted

### Context

Skills (tasks, calendar, Gmail, GitHub) need to be modular and potentially run in isolation. The bot core should not have hard dependencies on skill implementations.

### Decision

Implement skills as a separate Docker service with a REST API (aiohttp on port 8080). Skills communicate with the bot via HTTP with HMAC authentication.

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| In-process plugins | Simple, fast | Tight coupling, crashes affect bot |
| gRPC service | Typed contracts, streaming | Complex setup, code generation |
| REST API | Standard, debuggable, language-agnostic | HTTP overhead, no streaming |
| Message queue (Redis/RabbitMQ) | Decoupled, async | Additional infrastructure, complexity |

### Consequences

- Skills run in a separate container with their own lifecycle
- Authentication via `X-API-Secret` HMAC header
- Abstract `Skill` base class with `handle()`, `initialize()`, `on_heartbeat()`
- 15 granular permissions for access control
- SkillRegistry routes intents to the correct skill
- New skills can be added without modifying the bot core

---

## ADR-006: AES-256-GCM for Memory Encryption

**Date:** 2026-02-06
**Status:** Accepted

### Context

Qdrant stores user conversations as vector embeddings with payload metadata. This payload can contain sensitive information (names, locations, preferences). Data at rest should be encrypted.

### Decision

Field-level encryption using AES-256-GCM with PBKDF2-HMAC-SHA256 key derivation (600,000 iterations, 32-byte random salt per encryption operation).

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| No encryption | Simple | Data readable if Qdrant compromised |
| Full-disk encryption | Transparent | Doesn't protect against application-level access |
| AES-256-CBC | Well-known | No authentication, vulnerable to padding oracle |
| AES-256-GCM | Authenticated encryption, tamper detection | Slightly more complex |

### Consequences

- Each encrypted field includes: nonce (12 bytes) + ciphertext + auth tag (16 bytes) + salt (32 bytes)
- Key derived from user-provided passphrase via PBKDF2
- Encryption is optional (controlled by `ENCRYPTION_ENABLED` flag)
- Performance impact: ~1-2ms per encrypt/decrypt operation
- Vectors themselves are NOT encrypted (would break similarity search)

---

## ADR-007: Multi-Provider LLM Routing

**Date:** 2026-02-06
**Status:** Accepted

### Context

Different LLM providers excel at different tasks. Claude is strong at code, OpenAI at reasoning, Gemini at long documents, and Ollama provides privacy. A single provider would be suboptimal.

### Decision

Implement an InferenceBroker that routes requests to the best provider based on task type, with automatic fallback chains.

### Routing Matrix

| Task Type | Primary | Fallback 1 | Fallback 2 |
|-----------|---------|------------|------------|
| Code generation | Claude | OpenAI | Gemini |
| Complex reasoning | OpenAI | Claude | Gemini |
| Long document analysis | Gemini | Claude | OpenAI |
| Simple queries | Gemini/Ollama | - | - |
| Routing/classification | Ollama/Gemini | - | - |

### Consequences

- 16 distinct TaskTypes for granular routing
- Provider capability matrix determines routing
- Automatic fallback when primary is unavailable or rate-limited
- Cost tracking per provider and task type
- Users can override with environment variables

---

## ADR-008: Observation Pipeline for Implicit Learning

**Date:** 2026-02-07
**Status:** Accepted

### Context

Users share information implicitly during conversations ("I'm heading to Melbourne next week", "Working on the API migration"). Extracting and storing this knowledge without explicit profile commands improves the user experience.

### Decision

Implement a background observation pipeline that runs after each response, extracting implicit information using tiered inference.

### Tiers

| Tier | Method | Speed | Confidence |
|------|--------|-------|------------|
| 1 | Regex patterns | ~1ms | High (0.85-0.95) |
| 2 | LLM-based extraction | ~500ms | Medium (0.50-0.75) |

### Consequences

- Extraction runs asynchronously (does not delay responses)
- Low-confidence extractions require user confirmation
- Confidence decays over time if not reinforced (-10% at 30 days, -20% at 90 days)
- Users can disable observation entirely (`PROFILE_INFERENCE_ENABLED=false`)
- All learned data is exportable and deletable (GDPR compliance)

---

**Last Updated:** 2026-02-08
