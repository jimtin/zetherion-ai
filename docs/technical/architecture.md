# System Architecture

## Overview

Zetherion AI is a source-agnostic personal AI assistant built on a microservice architecture with 6 Docker services. The system accepts input from any source (Discord is the first interface, with REST API, email sync, and webhooks also available) and provides user-controlled multi-provider LLM routing, AES-256-GCM encrypted semantic memory, Gmail integration, GitHub management, and deep personal understanding through passive observation, proactive prompting, and explicit learning.

The codebase comprises 91 source files with 3,000+ tests across 89 test files, maintaining 93%+ code coverage. The architecture prioritizes security, modularity, and cost-efficient inference by routing queries to the most appropriate LLM provider based on complexity, privacy requirements, and task type.

### Key Design Principles

1. **Security-First** -- Defense in depth from container to application layer
2. **Modularity** -- Clean separation across services (Bot, Skills, Storage)
3. **Cost Efficiency** -- Intelligent routing sends simple queries to cheap/local models
4. **Privacy-Aware** -- Local inference via Ollama for sensitive operations
5. **Resilience** -- Fallback chains, retry logic, graceful degradation
6. **Async-First** -- Non-blocking I/O throughout the entire stack

---

## Architecture Diagram

```
Any Input Source
(Discord / REST API / Email / Webhooks)
    |
    v
+---[Bot Service]---+
|  Security Layer   |---> Prompt Injection Detection (17 regex patterns)
|  (rate limit,     |---> User Allowlist / RBAC
|   auth)           |
|                   |
|  Agent Core       |---> InferenceBroker
|   - Router        |     |-> Gemini 2.5 Flash      (simple/routing)
|   - Providers     |     |-> Claude Sonnet 4.5      (complex reasoning)
|   - Context       |     |-> GPT-5.2               (alternative complex)
|                   |     |-> Ollama llama3.1:8b     (local/private)
|                   |     |-> Ollama llama3.2:3b     (router classification)
+--------+----------+
         |
    +----+----+
    |         |
    v         v
+-------+  +--------+
|Skills |  |Memory  |
|Service|  |Layer   |
+---+---+  +---+----+
    |          |
    v          v
+-------+  +-------+  +----------+
|Gmail  |  |Qdrant |  |PostgreSQL|
|GitHub |  |(vectors|  |(users,   |
|Tasks  |  | memory)|  | contacts,|
|Calendar| |        |  | policies)|
|Profile|  +-------+  +----------+
+-------+
```

---

## Service Architecture

### Bot Service

The Bot Service is the primary entry point for user interactions. Discord is the first supported interface, but the service orchestrates the entire request lifecycle independently of the input source. The agent core, security layer, router, and inference broker all operate on messages regardless of origin.

**Core Responsibilities:**

- **Input Gateway** -- Currently connects to Discord via the gateway protocol using `discord.py`. Handles direct messages, @mentions, and slash commands. The architecture supports additional input sources (REST API, email, webhooks) through the same agent pipeline.

- **Security Layer** -- Three-stage defense applied to every incoming message before any processing occurs:
  - *User Allowlist*: Only pre-authorized Discord user IDs may interact with the bot. Supports RBAC with owner, admin, and user roles stored in PostgreSQL.
  - *Rate Limiting*: Per-user message throttling (configurable, default 10 messages per 60 seconds) to prevent abuse and runaway API costs.
  - *Prompt Injection Detection*: 17 compiled regex patterns that scan for known injection techniques including role override attempts, delimiter injection, and Unicode obfuscation.

- **Agent Core** -- Manages the full conversation flow: context assembly, provider selection, response generation, and post-response observation. Handles retry logic with exponential backoff (max 3 retries) for transient API failures.

- **Router** -- Classifies each incoming query by intent and complexity. The router can operate through multiple backends: Gemini Flash (cloud, fast), Ollama llama3.2:3b (local, dedicated container), or local regex fallback. Classification categories include `simple_query`, `complex_task`, `memory_store`, `memory_recall`, `task_management`, and others.

- **InferenceBroker** -- Multi-provider routing engine with fallback chains and cost tracking. Selects the optimal LLM provider based on task type, privacy requirements, cost constraints, and availability. Tracks per-request costs in a local SQLite database.

**LLM Providers:**

| Provider | Model | Use Case |
|----------|-------|----------|
| Google Gemini | gemini-2.5-flash | Simple queries, routing, fast responses |
| Anthropic Claude | claude-sonnet-4-5-20250929 (Sonnet 4.5) | Complex reasoning, code, analysis |
| OpenAI | gpt-5.2 | Alternative complex tasks |
| Ollama (local) | llama3.1:8b | Privacy-sensitive operations |
| Ollama (local) | llama3.2:3b | Fast query classification (router) |

**Embedding Providers:**

| Provider | Model | Dimensions |
|----------|-------|------------|
| Google Gemini | text-embedding-004 | 768 |
| Ollama | nomic-embed-text | 768 |
| OpenAI | text-embedding-3-large | 3072 |

### Skills Service

The Skills Service is an independent aiohttp REST API running on port 8080 (internal network only, not exposed to the host). It provides a pluggable skill framework with lifecycle management.

**Core Responsibilities:**

- **Skill Registry** -- Manages registration, initialization, and shutdown of skill modules. Each skill implements a standard interface with `initialize()`, `execute()`, and `cleanup()` lifecycle methods.

- **Built-in Skills:**
  - *TaskManager* -- Create, update, complete, and query tasks with priority and due date support.
  - *Calendar* -- Schedule events, check availability, and receive proactive reminders.
  - *Profile* -- Manage personal data, preferences, and contacts. Supports passive learning from conversations.
  - *Gmail* -- Read, search, draft, and send emails. Includes trust scoring for senders and sync state management.
  - *GitHub* -- Repository management, issue tracking, PR reviews, and audit logging with configurable autonomy levels.

- **Heartbeat Scheduler** -- A background scheduler that triggers proactive actions such as daily summaries, upcoming event reminders, and email digest notifications. Runs on configurable intervals.

- **Authentication** -- All requests from the Bot Service must include an `X-API-Secret` header with an HMAC-based token. Requests without valid authentication are rejected.

### Qdrant

Qdrant serves as the vector database for semantic memory storage and retrieval.

- **Collections**: `long_term_memory` and `conversation_history`, with per-user filtering via metadata payloads.
- **Embeddings**: Supports three embedding providers with different dimensionalities -- 768-dim (Gemini text-embedding-004), 768-dim (Ollama nomic-embed-text), or 3072-dim (OpenAI text-embedding-3-large).
- **Encryption**: All memory content undergoes AES-256-GCM field-level encryption before being stored in Qdrant payloads. Encryption keys are derived using PBKDF2 from a master secret and per-record salt.
- **Search**: Cosine similarity with configurable `top_k` (default 5). Results are decrypted, ranked, and returned as context fragments.

### PostgreSQL

PostgreSQL provides persistent relational storage for structured data that does not benefit from vector search.

- **User Management** -- RBAC system with three roles: owner (full control), admin (manage users and settings), and user (standard interaction). User records include Discord ID, role, and metadata.
- **Dynamic Settings** -- Namespace/key/value store with full audit trail. Supports runtime configuration changes without restarts. Falls back to environment variables when database values are not set.
- **Personal Understanding:**
  - *Profiles*: User preferences, communication style, timezone, interests.
  - *Contacts*: People the user mentions, with relationship context and interaction frequency.
  - *Policies*: User-defined rules (e.g., "always respond formally", "never mention competitor X").
  - *Learnings*: Passively observed facts extracted from conversations by the observation pipeline.
- **Gmail Integration** -- Account registration, OAuth state, sync cursors, message metadata, trust scores per sender, and draft management.
- **GitHub Integration** -- Audit log of all actions taken, autonomy configuration per repository, and webhook state.

### Ollama (Generation)

A dedicated Ollama container for privacy-sensitive LLM operations and local embedding generation.

- **Default Model**: llama3.1:8b (8 billion parameters). Provides capable text generation for queries that should not leave the local network.
- **Embeddings**: Also serves `nomic-embed-text` for local embedding generation, ensuring that sensitive text never reaches external APIs.
- **Resource Allocation**: 2G-8G memory, 1-4 CPU cores. The model remains loaded in memory to eliminate cold-start latency.
- **Port**: Exposed on 11434 to the host for debugging and direct model interaction.

### Ollama Router

A separate, lightweight Ollama container dedicated exclusively to fast query classification.

- **Default Model**: llama3.2:3b (3 billion parameters). Small enough to classify queries in under 500ms on CPU.
- **Purpose**: Eliminates the 2-10 second model-swapping delay that would occur if routing and generation shared a single Ollama instance. Each container keeps exactly one model loaded in memory at all times.
- **Classification Output**: Returns structured JSON with intent category, confidence score, and provider recommendation.
- **Resource Allocation**: 1.5G-3G memory, 0.5-2 CPU cores. Minimal footprint due to the small model size.
- **Network**: Internal only, no port exposed to host.

---

## Request Flow

A complete request lifecycle from incoming message to response:

1. **Message received** -- The Bot Service receives a message from an input source (currently Discord via the gateway WebSocket, but the pipeline is source-agnostic).

2. **Security checks** -- Three sequential checks are applied:
   - Rate limit verification (reject if user has exceeded their message quota).
   - Allowlist check (reject if the user's Discord ID is not authorized).
   - Prompt injection scan (reject if the message matches any of the 17 injection detection patterns).

3. **Router classification** -- The message is sent to the router for intent classification. The router attempts Gemini Flash first, falls back to Ollama llama3.2:3b, and finally to local regex patterns if both external calls fail.

4. **Skill dispatch** -- If the router identifies a skill-related intent (e.g., `task_management`, `email_read`, `github_action`), the Bot Service forwards the request to the Skills Service via an internal HTTP call to `http://zetherion-ai-skills:8080`.

5. **Provider selection** -- The InferenceBroker selects an LLM provider based on the routing classification and user configuration. You control which providers handle which task types:
   - Simple queries can route to Gemini 2.5 Flash or Ollama (your choice).
   - Complex reasoning can route to Claude Sonnet 4.5, GPT-5.2, or local Ollama (your choice).
   - Privacy-sensitive queries can be restricted to Ollama llama3.1:8b (local inference, never leaves your machine).
   - Fallback chains ensure a response even if the primary provider is unavailable.

6. **Context assembly** -- The system builds a rich context window by combining:
   - Recent conversation history (last N messages from the current session).
   - Semantic memory search results from Qdrant (top-k similar memories).
   - User profile data from PostgreSQL (preferences, communication style).
   - Skill-specific context fragments (e.g., upcoming tasks, recent emails).

7. **LLM generation** -- The assembled prompt is sent to the selected LLM provider. The response is streamed or returned as a complete message depending on provider capabilities.

8. **Observation pipeline** -- After generating the response, a passive observation step extracts learnings from the conversation (e.g., "user mentioned they prefer Python over JavaScript") and stores them in PostgreSQL for future context enrichment.

9. **Response delivery** -- The response is sent back to the originating input source. For Discord, messages exceeding 2,000 characters are automatically split across multiple messages.

10. **Cost tracking** -- The token usage and estimated cost for the request are recorded in a local SQLite database (`costs.db`) for monitoring and budget management.

---

## Data Flow

### Storage Responsibilities

| Data Type | Storage | Details |
|-----------|---------|---------|
| Conversation context | Qdrant + in-memory | Last N messages held in memory; semantic history in Qdrant vectors |
| Long-term memory | Qdrant | Encrypted vector embeddings with metadata payloads |
| User profiles | PostgreSQL | personal_profiles, personal_contacts, personal_policies tables |
| Gmail data | PostgreSQL | gmail_accounts, gmail_messages, gmail_drafts, gmail_trust tables |
| GitHub data | PostgreSQL | github_audit_log, github_autonomy_config tables |
| User/role data | PostgreSQL | users table with RBAC roles |
| Dynamic settings | PostgreSQL | settings table with namespace/key/value and audit trail |
| Cost tracking | SQLite | costs.db in ./data directory |
| Application logs | Filesystem | Structured JSON logs in ./logs with rotation |
| Encryption salt | Filesystem | Persistent salt file in ./data |

### Configuration Cascade

Settings are resolved in the following priority order:

1. PostgreSQL dynamic settings (highest priority, runtime-changeable)
2. Environment variables (set in docker-compose.yml or .env)
3. Default values in Pydantic Settings (lowest priority, compile-time)

---

## Security Architecture

Security is implemented as defense in depth across every layer of the stack. For comprehensive details, see [security.md](security.md).

**Container Security:**
- Distroless base images for bot and skills services (no shell, minimal attack surface)
- Read-only root filesystems with tmpfs mounts for writable temporary directories
- `no-new-privileges` flag on all 6 containers prevents privilege escalation
- Resource limits (CPU and memory quotas) on every service prevent denial-of-service

**Network Security:**
- All services communicate over an internal Docker bridge network (`zetherion-ai-net`)
- Only Qdrant (6333) and Ollama generation (11434) expose ports to the host
- The Skills Service, PostgreSQL, and Ollama Router are entirely internal

**Application Security:**
- AES-256-GCM encryption with PBKDF2 key derivation for all stored memories
- User allowlist with RBAC (owner/admin/user roles)
- Rate limiting per user to prevent abuse
- 17 compiled regex patterns for prompt injection detection
- Pydantic `SecretStr` for all credentials (never logged or serialized)
- HMAC-based service-to-service authentication

**Supply Chain Security:**
- Pinned Docker image digests for Qdrant and Ollama (Dependabot auto-updates)
- Gitleaks pre-commit hook for secret scanning
- CodeQL weekly static analysis scans
- Dependabot weekly dependency updates
- Bandit security scanning in CI pipeline

---

## Related Documentation

- [Docker Services and Deployment](docker.md) -- Service configuration, resource limits, and deployment procedures
- [Security Architecture](security.md) -- Comprehensive security controls, threat model, and hardening measures
- [Configuration Guide](configuration.md) -- Environment variables, settings hierarchy, and secrets management
- [Skills Framework](skills-framework.md) -- Building and registering custom skills
