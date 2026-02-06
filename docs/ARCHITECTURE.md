# Zetherion AI Architecture

This document provides a comprehensive overview of Zetherion AI's system architecture, design decisions, and key components.

## Table of Contents

- [Overview](#overview)
- [High-Level Architecture](#high-level-architecture)
- [Core Components](#core-components)
- [Data Flow](#data-flow)
- [Design Patterns](#design-patterns)
- [Technology Stack](#technology-stack)
- [Scalability & Performance](#scalability--performance)
- [Security Architecture](#security-architecture)
- [Future Roadmap](#future-roadmap)

---

## Overview

Zetherion AI is a Discord bot with advanced AI capabilities, featuring:
- **Dual LLM backends** for intelligent routing (Gemini + Ollama for routing, Claude/OpenAI for complex tasks)
- **Vector memory** for long-term context using Qdrant
- **Comprehensive security** with rate limiting, allowlists, and prompt injection detection
- **Full Docker containerization** for reproducible deployment

### Key Design Principles

1. **Modularity** - Clean separation of concerns (Discord, Agent, Memory, Security)
2. **Extensibility** - Pluggable backends via factory pattern
3. **Resilience** - Retry logic, fallbacks, graceful degradation
4. **Security-First** - Defense in depth, least privilege, secrets management
5. **Performance** - Async-first, parallel operations, efficient caching
6. **Maintainability** - 87.58% test coverage, type hints, comprehensive logging

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interface                          │
│                      Discord (discord.py)                       │
│  Commands: /channels, /remember, /summarize                    │
│  Interactions: DMs, @mentions, slash commands                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Security Layer                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐    │
│  │  Allowlist   │  │ Rate Limiter │  │ Injection Detect  │    │
│  │  User IDs    │  │ 10 msg/60s   │  │ 17 Regex Patterns │    │
│  └──────────────┘  └──────────────┘  └───────────────────┘    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Agent Core                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Message Router (Factory Pattern)                       │   │
│  │  ┌──────────────────┐  ┌──────────────────────┐        │   │
│  │  │ Gemini Backend   │  │ Ollama Backend       │        │   │
│  │  │ - Cloud API      │  │ - Local Container    │        │   │
│  │  │ - Fast, reliable │  │ - Privacy-focused    │        │   │
│  │  │ - Default        │  │ - Cost-effective     │        │   │
│  │  └──────────────────┘  └──────────────────────┘        │   │
│  │  Output: {intent: "simple_query" | "complex_task"}     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Response Generation (Dual Generators)                  │   │
│  │  ┌──────────────────────┐  ┌──────────────────────┐    │   │
│  │  │ Complex Task Handler │  │ Simple Query Handler │    │   │
│  │  │ - Claude Sonnet 4.5  │  │ - Gemini 2.5 Flash   │    │   │
│  │  │ - OpenAI GPT-4o      │  │ - Ollama Llama 3.1   │    │   │
│  │  │ - Code, reasoning    │  │ - Facts, greetings   │    │   │
│  │  └──────────────────────┘  └──────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Memory Manager                                         │   │
│  │  - Context building from vector search                 │   │
│  │  - Deduplication (search once, use twice)              │   │
│  │  - Retry logic with exponential backoff                │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Memory Layer                                  │
│  ┌───────────────────────────┐  ┌───────────────────────────┐  │
│  │  Qdrant Vector Database   │  │  Embeddings Service       │  │
│  │  - AsyncQdrantClient      │  │  - Gemini text-embed-004  │  │
│  │  - Collections per user   │  │  - 768-dim vectors        │  │
│  │  - Semantic search        │  │  - Parallel batching      │  │
│  │  - Docker container       │  │  - Caching (TODO)         │  │
│  └───────────────────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Infrastructure                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐    │
│  │   Docker    │  │  Logging    │  │  Configuration      │    │
│  │   Compose   │  │  Structlog  │  │  Pydantic Settings  │    │
│  │   - qdrant  │  │  - Console  │  │  - SecretStr        │    │
│  │   - ollama  │  │  - Files    │  │  - .env validation  │    │
│  │   - bot     │  │  - JSON     │  │                     │    │
│  └─────────────┘  └─────────────┘  └─────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Discord Interface (`src/secureclaw/discord/`)

**Purpose:** Handle all Discord interactions and command routing.

**Key Files:**
- `bot.py` - Main bot class, event handlers
- `commands.py` - Slash command definitions
- `security.py` - Security controls (allowlist, rate limiting, injection detection)

**Features:**
- Slash commands: `/channels`, `/remember`, `/summarize`
- DM and @mention support
- Message splitting for 2000-char limit
- Typing indicators for better UX
- Error handling and user feedback

**Design Decisions:**
- Used `discord.py` for stable API and strong typing
- Commands implemented as app_commands for modern Discord UI
- Security checks run before any agent processing
- Async throughout for non-blocking I/O

### 2. Agent Core (`src/secureclaw/agent/`)

**Purpose:** Intelligent message routing and response generation.

**Key Files:**
- `core.py` - Agent class (orchestrates routing and generation)
- `router.py` - Gemini router backend
- `router_ollama.py` - Ollama router backend
- `router_factory.py` - Backend selection factory
- `router_base.py` - Abstract router interface (Protocol)

**Message Flow:**
1. Router classifies message intent (simple vs complex)
2. Agent searches memory for relevant context
3. Appropriate generator creates response
4. Response returned to Discord

**Routing Decision Logic:**
```python
{
  "intent": "simple_query",  # or "complex_task"
  "confidence": 0.95,
  "use_claude": false  # true for complex tasks
}
```

**Design Decisions:**
- Factory pattern allows runtime backend selection
- Dual generators optimize cost and latency
- Retry logic with exponential backoff (max 3 retries)
- Context deduplication (search once, use for both generators)

### 3. Memory System (`src/secureclaw/memory/`)

**Purpose:** Long-term semantic memory via vector embeddings.

**Key Files:**
- `qdrant.py` - Vector database client
- `embeddings.py` - Gemini embedding generation

**Features:**
- Per-user collections in Qdrant
- Semantic search for context retrieval
- Parallel batch embeddings (10x faster)
- Async operations throughout

**Vector Storage:**
- **Model:** `text-embedding-004` (Gemini)
- **Dimensions:** 768
- **Similarity:** Cosine similarity
- **Storage:** Qdrant Docker container

**Design Decisions:**
- AsyncQdrantClient prevents event loop blocking
- Parallel embeddings via `asyncio.gather()`
- Collections auto-created on first use
- No embedding caching (TODO for Phase 5)

### 4. Configuration (`src/secureclaw/config.py`)

**Purpose:** Centralized settings with validation and secrets management.

**Features:**
- Pydantic Settings for type-safe configuration
- `SecretStr` for all credentials (never logged)
- Environment variable loading from `.env`
- Field validators for critical settings
- Computed properties for derived values

**Configuration Sources:**
1. Environment variables
2. `.env` file (development)
3. Default values (fallback)

**Design Decisions:**
- `SecretStr` ensures credentials never leak to logs
- LRU cache prevents repeated file reads
- Validators catch misconfigurations early
- Clear separation of dev vs prod settings

### 5. Logging (`src/secureclaw/logging.py`)

**Purpose:** Structured logging for debugging and monitoring.

**Features:**
- Dual handlers (console + rotating files)
- Structured logs with `structlog`
- JSON format for files (parseable with `jq`)
- Colored console output in development
- Log rotation (10MB × 6 files)

**Log Levels:**
- **DEBUG:** Detailed diagnostics (development only)
- **INFO:** Normal operations
- **WARNING:** Potential issues
- **ERROR:** Errors that don't crash the bot
- **CRITICAL:** System failures

**Design Decisions:**
- Structlog for structured, performant logging
- Separate formatters for console vs files
- Reduced third-party noise (discord.py, httpx)
- Rotation prevents disk filling

---

## Data Flow

### Example: User Sends "What is async programming?"

```
1. Discord Event
   ├─ User sends message in channel
   └─ on_message() handler triggered

2. Security Checks
   ├─ Allowlist: Is user authorized?
   ├─ Rate Limit: Within 10 msg/60s?
   └─ Injection: Contains malicious patterns?

3. Message Routing
   ├─ Router Backend (Gemini or Ollama) classifies
   └─ Result: {intent: "complex_task", confidence: 0.92, use_claude: true}

4. Memory Search (Parallel)
   ├─ Generate embedding: embed_text("What is async programming?")
   ├─ Search Qdrant: top_k=5 similar memories
   └─ Return context: ["Previous async discussion...", "User prefers Python..."]

5. Response Generation (Claude)
   ├─ Build prompt: message + context
   ├─ Call Claude API: claude-sonnet-4-5-20250929
   └─ Get response: "Async programming is..."

6. Response Delivery
   ├─ Split if > 2000 chars
   ├─ Send to Discord channel
   └─ Store interaction in memory

7. Memory Storage
   ├─ Embed user message + bot response
   ├─ Store in Qdrant collection (user-specific)
   └─ Ready for future context retrieval
```

---

## Design Patterns

### 1. Factory Pattern (Router Backend Selection)

**Problem:** Need to support multiple routing backends (Gemini, Ollama) without tight coupling.

**Solution:** Factory function creates appropriate backend at runtime.

```python
def create_router() -> MessageRouter:
    settings = get_settings()

    if settings.router_backend == "ollama":
        try:
            backend = OllamaRouterBackend()
            if await backend.health_check():
                return MessageRouter(backend)
        except Exception:
            log.warning("Ollama failed, falling back to Gemini")

    # Default to Gemini
    return MessageRouter(GeminiRouterBackend())
```

**Benefits:**
- Easy to add new backends
- Runtime configuration
- Graceful fallbacks

### 2. Strategy Pattern (LLM Backends)

**Problem:** Different LLMs excel at different tasks (Claude for code, Gemini for speed).

**Solution:** Agent selects generator based on router decision.

```python
if routing.use_claude:
    response = await self._generate_claude(message, context)
else:
    response = await self._generate_gemini(message, context)
```

**Benefits:**
- Cost optimization
- Performance tuning
- Easy to swap implementations

### 3. Repository Pattern (Memory Abstraction)

**Problem:** Need to abstract vector database operations from business logic.

**Solution:** `QdrantMemory` class provides high-level memory interface.

```python
class QdrantMemory:
    async def store(self, text: str, metadata: dict) -> None:
        """Store text with metadata in vector DB."""

    async def search(self, query: str, top_k: int = 5) -> list[str]:
        """Search for similar memories."""
```

**Benefits:**
- Easy to swap Qdrant for another vector DB
- Business logic decoupled from storage
- Testable with mocks

### 4. Singleton Pattern (Configuration)

**Problem:** Settings should be loaded once and reused.

**Solution:** LRU cache ensures single settings instance.

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Benefits:**
- Fast lookups (no repeated .env parsing)
- Consistent configuration throughout app
- Memory efficient

### 5. Retry Pattern (API Resilience)

**Problem:** Cloud APIs have transient failures (rate limits, timeouts).

**Solution:** Exponential backoff retry logic.

```python
retries = 0
while retries < 3:
    try:
        return await api_call()
    except (ConnectionError, TimeoutError, RateLimitError):
        await asyncio.sleep(2 ** retries)
        retries += 1
raise MaxRetriesExceeded()
```

**Benefits:**
- Handles transient failures
- Prevents retry storms
- User-friendly (no immediate errors)

---

## Technology Stack

### Core Technologies

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| **Language** | Python | 3.12+ | Modern async features, type hints |
| **Discord Library** | discord.py | 2.4.0+ | Discord bot API |
| **Vector DB** | Qdrant | Latest | Semantic memory storage |
| **Embeddings** | Gemini | text-embedding-004 | 768-dim vectors |
| **LLMs** | Claude | Sonnet 4.5 | Complex reasoning, code |
| | OpenAI | GPT-4o | Alternative complex tasks |
| | Gemini | 2.5 Flash | Simple queries, routing |
| | Ollama | Llama 3.1:8b | Local routing (optional) |
| **Containerization** | Docker | Latest | Reproducible deployment |
| **Logging** | structlog | Latest | Structured, performant logs |
| **Config** | Pydantic | 2.0+ | Type-safe settings |

### Development Tools

| Tool | Purpose |
|------|---------|
| **Ruff** | Linting, formatting (600+ rules) |
| **Mypy** | Type checking (strict mode) |
| **Pytest** | Testing framework |
| **Pre-commit** | Git hooks for quality checks |
| **Gitleaks** | Secret scanning |
| **Bandit** | Security scanning |
| **Hadolint** | Dockerfile linting |
| **GitHub Actions** | CI/CD pipeline |
| **Dependabot** | Dependency updates |
| **CodeQL** | Static analysis |

### API Integrations

| Provider | API | Purpose |
|----------|-----|---------|
| **Anthropic** | Claude API | Complex task generation |
| **OpenAI** | Chat Completions | Alternative complex tasks |
| **Google** | Gemini API | Routing, simple queries, embeddings |
| **Ollama** | HTTP API | Local routing (optional) |
| **Discord** | Gateway & REST | Bot interactions |

---

## Scalability & Performance

### Current Performance Characteristics

| Operation | Latency | Notes |
|-----------|---------|-------|
| **Router Classification** | 200-500ms | Gemini Flash (fast) |
| **Simple Response** | 500-1000ms | Gemini Flash |
| **Complex Response** | 2-5s | Claude Sonnet |
| **Memory Search** | 100-200ms | Qdrant local |
| **Embedding Generation** | 200-400ms | Parallel batching |
| **Discord Message Send** | 100-300ms | Discord API |

### Optimization Strategies

1. **Parallel Operations**
   - Embeddings: `asyncio.gather()` for batch processing
   - Memory searches: Single search reused for both generators
   - Independent API calls: Concurrent execution

2. **Caching** (TODO for Phase 5)
   - Embedding cache for repeated queries
   - Response cache for identical messages
   - Memory cache for frequently accessed context

3. **Async-First Architecture**
   - AsyncQdrantClient (non-blocking vector DB)
   - Async Discord handlers
   - Async LLM API calls
   - No blocking I/O in event loop

4. **Resource Limits**
   - Rate limiting (10 msg/60s per user)
   - Memory search top_k=5 (limits context size)
   - Message splitting (prevents Discord rate limits)

### Scalability Considerations

**Current Scale:**
- Single Discord server
- ~10-50 concurrent users
- ~100-500 messages/hour
- Docker Compose on single host

**Future Scale (Phase 5+):**
- Multiple Discord servers
- 100-1000 concurrent users
- Kubernetes deployment
- Distributed Qdrant cluster
- Horizontal scaling of bot instances
- Load balancing across LLM providers

---

## Security Architecture

See [docs/SECURITY.md](SECURITY.md) for comprehensive security documentation.

### Defense in Depth

```
Layer 1: Network (Docker bridge network, no exposed ports)
  ↓
Layer 2: Input Validation (Allowlist, rate limiting)
  ↓
Layer 3: Content Filtering (Prompt injection detection)
  ↓
Layer 4: Secrets Management (Pydantic SecretStr, env vars)
  ↓
Layer 5: Monitoring (Structured logs, error tracking)
```

### Key Security Controls

1. **User Allowlist** - Only authorized Discord user IDs can interact
2. **Rate Limiting** - 10 messages per 60 seconds per user
3. **Prompt Injection Detection** - 17 regex patterns + Unicode obfuscation
4. **Secrets Management** - SecretStr, never logged, .env gitignored
5. **Secret Scanning** - Gitleaks pre-commit hook
6. **Dependency Scanning** - Dependabot weekly updates
7. **Static Analysis** - CodeQL weekly scans
8. **Container Security** - Slim base image, health checks

### Security Gap Analysis

See [docs/SECURITY.md#12-gap-analysis](SECURITY.md#12-gap-analysis) for:
- Container image scanning (Trivy)
- SBOM generation
- Signed commits
- Read-only container filesystem
- And 8 more recommendations

---

## Future Roadmap

### Phase 5: Advanced Features (Planned)

See [`memory/phase5-plan.md`](../memory/phase5-plan.md) for detailed plan.

**5A: Encrypted Memory**
- AES-256-GCM encryption for sensitive data
- PyCA cryptography library
- Encrypted fields in Qdrant metadata

**5B: User Profiling**
- Preferences tracking (language, timezone, interests)
- Automatic profile updates from conversations
- Profile-aware context building

**5C: Skills Framework**
- Pluggable command system
- Separate Docker container for skills
- Python sandbox for user-contributed skills

**5D: Smart Multi-Provider Routing**
- Provider capability matrix (Claude for code, OpenAI for reasoning, Gemini for docs)
- Dynamic routing based on message analysis
- Cost-aware provider selection

**5E: Heartbeat Scheduler**
- Proactive tasks (daily summaries, reminders)
- Cron-like scheduling
- Background task management

**5F: Advanced Memory**
- Embedding caching
- Multi-modal memory (images, files)
- Memory consolidation and pruning

**5G: Production Hardening**
- Kubernetes deployment
- Distributed tracing (OpenTelemetry)
- Metrics (Prometheus)
- Alerting (PagerDuty/Slack)

### Long-Term Vision

- **Multi-Channel Support** - Slack, Teams, Telegram
- **Web Dashboard** - Admin UI for configuration
- **Mobile App** - React Native or Flutter
- **Custom Model Fine-Tuning** - Domain-specific models
- **Federated Deployment** - Multi-region for low latency
- **Plugin Marketplace** - Community-contributed skills

---

## Design Decisions Log

### Why Gemini for Embeddings?

**Decision:** Use Gemini `text-embedding-004` instead of OpenAI `text-embedding-3-small`.

**Rationale:**
- Same API as routing (fewer integrations)
- 768 dimensions (good balance of quality vs storage)
- Free tier available
- Proven quality in benchmarks

**Trade-offs:**
- Tied to Google ecosystem
- Less flexibility than OpenAI

### Why Dual Routing Backends?

**Decision:** Support both Gemini (cloud) and Ollama (local) for routing.

**Rationale:**
- Privacy: Some users prefer local inference
- Cost: Ollama has no API costs
- Reliability: Fallback if one provider fails
- Flexibility: Users can choose based on needs

**Trade-offs:**
- Increased complexity
- More testing required
- Ollama requires GPU for good performance

### Why Discord.py Over Other Libraries?

**Decision:** Use `discord.py` instead of alternatives like `discord.js` or `pycord`.

**Rationale:**
- Python ecosystem (same language as backend)
- Strong typing support
- Active maintenance
- Excellent documentation
- Mature slash command support

**Trade-offs:**
- Python slower than Node.js (not critical for this use case)
- Fewer real-time features than discord.js

### Why Qdrant Over Alternatives?

**Decision:** Use Qdrant instead of Pinecone, Weaviate, or Milvus.

**Rationale:**
- Self-hosted (no vendor lock-in)
- Docker-native (easy deployment)
- Excellent async Python client
- Good performance on consumer hardware
- Open source

**Trade-offs:**
- Less managed than Pinecone
- Smaller ecosystem than Weaviate
- Manual scaling required

### Why Pydantic Settings Over python-decouple?

**Decision:** Use Pydantic Settings for configuration.

**Rationale:**
- Type validation at load time
- SecretStr for credentials
- Computed properties
- Same library as data models
- Better IDE support

**Trade-offs:**
- Heavier than python-decouple
- Requires Pydantic knowledge

---

## Diagrams

### Component Interaction Diagram

```
┌─────────────┐
│   Discord   │
│    User     │
└──────┬──────┘
       │ Message
       ▼
┌─────────────────────────────────────────┐
│          Security Layer                 │
│  ┌────────┐ ┌────────┐ ┌─────────────┐ │
│  │Allowlist│→│Rate    │→│Injection    │ │
│  │Check    │ │Limit   │ │Detection    │ │
│  └────────┘ └────────┘ └─────────────┘ │
└─────────────────────────────────────────┘
       │ Authorized
       ▼
┌─────────────────────────────────────────┐
│          Agent Core                     │
│  ┌─────────────────────────────────┐   │
│  │  Router Factory                 │   │
│  │  ┌──────────┐   ┌──────────┐   │   │
│  │  │ Gemini   │ or│ Ollama   │   │   │
│  │  └──────────┘   └──────────┘   │   │
│  └─────────────────────────────────┘   │
│       │                                 │
│       │ Routing Decision                │
│       ▼                                 │
│  ┌─────────────────────────────────┐   │
│  │  Memory Manager                 │   │
│  │  Search Qdrant for context      │   │
│  └─────────────────────────────────┘   │
│       │                                 │
│       │ Context                         │
│       ▼                                 │
│  ┌─────────────────────────────────┐   │
│  │  Generator Selection            │   │
│  │  ┌─────────┐   ┌──────────┐    │   │
│  │  │ Claude  │ or│ Gemini   │    │   │
│  │  └─────────┘   └──────────┘    │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
       │ Response
       ▼
┌─────────────────────────────────────────┐
│          Memory Storage                 │
│  Embed and store in Qdrant              │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────┐
│   Discord   │
│   Response  │
└─────────────┘
```

---

## References

- [Discord.py Documentation](https://discordpy.readthedocs.io/)
- [Qdrant Documentation](https://qdrant.tech/documentation/)
- [Anthropic Claude API](https://docs.anthropic.com/)
- [OpenAI API](https://platform.openai.com/docs/)
- [Google Gemini API](https://ai.google.dev/docs)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Docker Documentation](https://docs.docker.com/)
- [Structlog Documentation](https://www.structlog.org/)

---

## Contact & Support

- **Issues:** https://github.com/jimtin/zetherion-ai/issues
- **Discussions:** https://github.com/jimtin/zetherion-ai/discussions
- **Documentation:** See [docs/](.) directory for detailed guides

---

**Last Updated:** 2026-02-06
**Version:** 1.0.0 (Phases 1-4 complete, 87.58% test coverage)
