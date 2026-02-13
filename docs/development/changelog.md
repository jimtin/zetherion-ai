# Changelog

All notable changes to Zetherion AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Note: percentage/count metrics in historical phase sections are snapshots from
> the date of that phase. Current policy is enforced by test configuration:
> overall coverage must remain `>=90%`.

---

## [Unreleased]

### Added - Phase 9 (2026-02-08)

#### Phase 9: Personal Understanding System

- PostgreSQL-backed personal model with 4 data models: `PersonalProfile`, `PersonalContact`, `PersonalPolicy`, `PersonalLearning`
- asyncpg-based `PersonalStorage` with full CRUD operations for all models
- Communication style dimensions (formality, verbosity, directness, proactivity on 0-1 scale)
- Contact graph with relationship tracking and metadata
- Policy system with 5 domains and 4 modes (`AUTO`, `DRAFT`, `ASK`, `NEVER`)
- Integration with Gmail for contact-aware responses
- See [`../technical/personal-understanding.md`](../technical/personal-understanding.md) for data model details

### Added - Phase 8 (2026-02-07)

#### Phase 8: Gmail Integration

- 12-file Gmail module with complete email management capabilities
- Two-dimensional trust system (per-contact + per-type)
- Trust formula: `effective_trust = min(type_trust, contact_trust, reply_type_ceiling)`
- Progressive autonomy: read-only -> draft with approval -> auto-draft -> auto-send
- Auto-send threshold: `effective_trust >= 0.85 AND confidence >= 0.85`
- Reply type ceilings across 8 types from `ACKNOWLEDGMENT` (0.95) to `SENSITIVE` (0.30)
- Trust evolution deltas: +0.05 approval, -0.20 rejection, `GLOBAL_CAP` 0.95
- OAuth account management with encrypted token storage (AES-256-GCM)
- Unified inbox aggregation across multiple accounts
- Reply draft pipeline (7-step process from classification to send/queue)
- Digest generation (morning, evening, weekly summaries)
- Email analytics with relationship scoring
- See [`../technical/gmail-architecture.md`](../technical/gmail-architecture.md) for full design

#### Phase 8A: Observation Pipeline

- Tiered extraction: Tier 1 (regex patterns), Tier 2 (LLM-based)
- 6 learning categories for implicit knowledge extraction
- 5 learning sources with confidence scoring
- Background profile extraction after responses
- PostgreSQL storage for personal learnings
- See [`../technical/observation-pipeline.md`](../technical/observation-pipeline.md) for pipeline details

### Added - Phase 7 (2026-02-07)

#### Phase 7: GitHub Integration

- GitHub skill with 18 intents for full repository management
- Issue management: list, view, create, close, reopen, label, comment
- PR management: list, view, diff, merge
- Workflow and repo info queries
- 3 autonomy levels: `Autonomous`, `Ask`, `Always Ask`
- Per-action autonomy defaults with safety-first design
- GitHub token management via environment variables
- See [`../user/github-integration.md`](../user/github-integration.md) for usage guide

### Added - Phase 6 (2026-02-07)

#### Phase 6: Docker Hardening

- Distroless base images for bot and skills containers (~50MB runtime)
- Read-only root filesystem with tmpfs for `/tmp`
- `no-new-privileges` security option on ALL containers
- Resource limits (CPU and memory) for all 6 services
- Dual Ollama architecture (separate router + generation containers)
- PostgreSQL 17 Alpine service for personal understanding data
- Health checks on all 6 services with Python-based probes
- Network isolation between services
- See [`../technical/docker.md`](../technical/docker.md) for architecture details

### Added - Phase 5 (2026-02-06)

#### Phase 5A: Encryption Layer

- AES-256-GCM field-level encryption for sensitive Qdrant payloads
- PBKDF2-HMAC-SHA256 key derivation with 600,000 iterations and 32-byte salt
- TLS support for Qdrant connections (optional)
- `FieldEncryptor` and `KeyManager` classes in `zetherion_ai.security`

#### Phase 5B: InferenceBroker

- Smart multi-provider LLM routing based on task type
- Provider capability matrix: Claude (code), OpenAI (reasoning), Gemini (long docs), Ollama (lightweight)
- 16 TaskTypes for granular routing decisions
- Automatic fallback chains when primary provider unavailable
- See [`../technical/architecture.md`](../technical/architecture.md) for routing design

#### Phase 5B.1: Model Registry and Cost Tracking

- Dynamic model discovery via provider APIs
- Tier-based model selection (quality, balanced, fast)
- SQLite cost tracking with per-request logging
- Daily and monthly cost aggregation and reporting
- Discord notifications for budget alerts
- See [`../technical/cost-tracking.md`](../technical/cost-tracking.md) for budget management details

#### Phase 5C: User Profile System

- 8 profile categories: identity, preferences, schedule, projects, relationships, skills, goals, habits
- Tiered inference: Tier 1 (regex), Tier 2 (Ollama), Tier 3 (embeddings), Tier 4 (cloud)
- 5 signal engines for implicit extraction
- TTL-based caching with confidence scoring
- Background profile extraction after responses

#### Phase 5C.1: Employment Profile

- Bot identity and relationship modeling
- Trust levels: `MINIMAL` -> `BUILDING` -> `ESTABLISHED` -> `HIGH` -> `FULL`
- 10 relationship milestones tracking
- Communication style adaptation (formality, verbosity, proactivity)

#### Phase 5D: Skills Framework

- Abstract Skill interface with permission-based access control
- 15 granular permissions (read/write profile, memories, messages, etc.)
- `SkillRegistry` for intent routing and heartbeat coordination
- Separate Docker container for skills service isolation
- REST API with authentication on port 8080 for bot-skills communication
- See [`../technical/skills-framework.md`](../technical/skills-framework.md) for the framework design

#### Phase 5E: Built-in Skills

- **Task Manager**: CRUD operations, priority levels, deadlines, heartbeat reminders
- **Calendar Awareness**: Event types, recurrence patterns, availability checking
- **Profile Manager**: GDPR-style view/update/delete/export, confidence reports

#### Phase 5F: Heartbeat Scheduler

- Configurable interval (default 5 min) with quiet hours support
- Rate limiting (3 messages/hour per user)
- `ActionExecutor` with handlers for all skill action types
- Scheduled event handling for one-time triggers

#### Phase 5G: Router Enhancement

- 3 new skill intents: `TASK_MANAGEMENT`, `CALENDAR_QUERY`, `PROFILE_QUERY`
- Skill intent examples in router prompts
- Agent integration for skill intent handling

### Changed

- **Test suite**: Expanded to **3,000+ tests** (historical snapshot at the time:
  93%+ coverage across 89 test files and 91 source files)
- **Docker services**: Expanded to **6 services** (bot, skills, qdrant, postgres, ollama, ollama-router)
- **Ollama models**: Updated from Qwen to Meta Llama (llama3.2:3b for router, llama3.1:8b for generation)
- **Default cloud models**: claude-sonnet-4-5-20250929, gpt-5.2, gemini-2.5-flash
- **Project renamed** from `secureclaw` to `zetherion-ai`; all imports updated to `zetherion_ai`
- Docker container names now use hyphens (`zetherion-ai-*`)
- Integration tests updated for Phase 5-9 features

### Fixed

- All test failures resolved across all phases
- Config tests with environment variable isolation using monkeypatch
- Docker integration tests with proper service startup
- Type checking errors in async Qdrant client
- GitHub push protection issues with example tokens in documentation
- Chainguard ENTRYPOINT compatibility for CMD and healthchecks
- `load_dotenv` moved to fixture scope to prevent env pollution in test suite
- asyncpg mypy import issues resolved
- Security hardening and cleanup for skills server and Qdrant connections

---

## [1.0.0] - Initial Release

### Added

- Discord bot with dual LLM backends (Gemini + Ollama)
- Message routing with intent classification
- Claude / OpenAI integration for complex tasks (claude-sonnet-4-5-20250929, gpt-5.2)
- Gemini / Ollama integration for simple queries (gemini-2.5-flash, llama3.2:3b)
- Qdrant vector database for long-term memory
- Google Gemini embeddings for semantic search
- Docker containerization with Compose orchestration
- Basic security controls (rate limiting, user allowlist)
- Comprehensive error handling and retry logic

### Security

- Pydantic `SecretStr` for all credentials
- Gitleaks secret scanning in pre-commit hooks
- Bandit security scanning in CI/CD
- CodeQL weekly analysis
- Pinned dependencies with Dependabot
- Prompt injection detection (17 regex patterns)
- User allowlist for Discord interactions

---

## Version History Details

### Phase 1: Test Fixes (Coverage: ~42% -> 78%)

**Phase 1A: Agent Core Tests** -- Fixed 13 test failures

- Resolved Docker service dependency issues
- Fixed async Qdrant client usage
- Improved retry logic testing
- **Result**: 41 tests passing, 94.76% module coverage

**Phase 1B: Security Tests** -- Fixed 3 test failures

- Fixed prompt injection detection tests
- Corrected allowlist and rate limiter tests
- **Result**: 37 tests passing, 94.12% module coverage

**Phase 1C: Config Tests** -- Fixed 10 test failures

- Implemented environment variable isolation with monkeypatch
- Fixed `allowed_user_ids` parsing tests
- Resolved Docker integration test errors (14 tests)
- **Result**: All config tests passing, 96.88% module coverage

### Phase 2: Coverage Improvements (Coverage: 78% -> 87.58%)

**Phase 2A: Router Factory Tests** -- 26% -> 100% coverage

- Added 12 comprehensive tests for factory functions
- Tested async/sync router creation, health checks, and fallback logic

**Phase 2B: Discord Bot Edge Cases** -- 68.55% -> 89.92% coverage

- Added 11 edge case tests for uncovered code paths
- `/channels` command tests (6 tests): unauthorized user handling, DM vs guild context, channel listing, long response splitting
- Agent readiness tests (1 test)
- `_send_long_message` helper tests (4 tests)

### Phase 3-4: Refactoring and CI Hardening

- Project renamed from SecureClaw to Zetherion AI
- Pre-commit hooks consolidated (Ruff, Mypy, Bandit, Gitleaks, Hadolint)
- CI/CD pipeline with 6 parallel jobs
- Windows deployment scripts (PowerShell)
- MkDocs documentation site with wiki sync

### Phase 5: Skills and Routing (Historical snapshot: 87.58% -> 93%+)

- Complete InferenceBroker with multi-provider routing
- Encryption layer with AES-256-GCM
- User profile system with tiered inference
- Skills framework with 3 built-in skills
- Heartbeat scheduler with quiet hours
- Router enhancement with skill intents

### Phase 6: Docker Hardening

- Distroless images, read-only filesystems, resource limits
- Dual Ollama architecture for router and generation separation
- PostgreSQL 17 Alpine for persistent data
- Health checks and network isolation across all 6 services

### Phase 7: GitHub Integration

- 18 intents for full repository management
- 3 autonomy levels with safety-first defaults

### Phase 8: Gmail Integration

- 12-file module with trust-based progressive autonomy
- OAuth management, inbox aggregation, reply drafting, digest generation
- Observation pipeline for implicit knowledge extraction

### Phase 9: Personal Understanding

- PostgreSQL-backed personal model (4 data models)
- Communication style dimensions, contact graphs, policy system

### Historical Test Statistics (Snapshot)

| Category | Count | Status |
|----------|-------|--------|
| **Test Files** | 89 (snapshot) | All passing |
| **Source Files** | 91 (snapshot) | Covered |
| **Total Tests** | 3,000+ | All passing |
| **Overall Coverage** | 93%+ (snapshot) | Target exceeded at that time |

### Docker Services

| Service | Image | Purpose |
|---------|-------|---------|
| bot | Distroless | Discord bot and agent core |
| skills | Distroless | Skills REST API on port 8080 |
| qdrant | qdrant/qdrant | Vector memory storage |
| postgres | PostgreSQL 17 Alpine | Personal understanding data |
| ollama | ollama/ollama | Generation model (llama3.1:8b) |
| ollama-router | ollama/ollama | Router model (llama3.2:3b) |

---

## CI/CD Pipeline

### Pipeline Stages

1. **Lint** -- Ruff linting and formatting
2. **Type Check** -- Mypy strict mode type checking
3. **Security** -- Bandit security scanning
4. **Tests** -- Unit tests on Python 3.12 and 3.13
5. **Docker Build** -- Container build verification
6. **Integration** -- Full integration tests with Docker services

### Pre-Commit Hooks

- Ruff (linting and formatting)
- Mypy (type checking)
- Gitleaks (secret scanning)
- Bandit (security scanning)
- Hadolint (Dockerfile linting)
- File checks (trailing whitespace, EOF, merge conflicts)

---

## Breaking Changes

None yet. This project has not yet published a stable public API.

---

## Migration Guide

### From Development to Production

1. **Update `.env` file**:
   - Set `ENVIRONMENT=production`
   - Set `LOG_LEVEL=INFO`
   - Configure `ALLOWED_USER_IDS` for production users

2. **Review Security Settings**:
   - Ensure all API keys are properly set
   - Verify user allowlist is configured
   - Check rate limiting settings

3. **Deploy with Docker Compose**:
   ```bash
   docker compose up -d
   ```

4. **Monitor Logs**:
   ```bash
   tail -f logs/zetherion_ai.log | jq .
   ```

See [`../technical/configuration.md`](../technical/configuration.md) for full configuration reference.

---

## Known Issues

None currently reported. See [GitHub Issues](https://github.com/jimtin/zetherion-ai/issues) for the latest.

---

## Contributors

- James Hinton ([@jimtin](https://github.com/jimtin)) -- Project creator and maintainer
- Claude Sonnet 4.5 / Claude Opus 4.6 -- AI pair programming

See [Setup and Contributing](setup.md) for contribution guidelines.

---

## License

This project is licensed under the MIT License. See the [LICENSE](../../LICENSE) file for details.

---

## Acknowledgments

- Anthropic for the Claude API
- Google for the Gemini API
- OpenAI for GPT models
- Discord.py community
- Qdrant team for the vector database
- Ollama team for the local LLM runtime
- Meta for the Llama model family
