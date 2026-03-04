# Changelog

All notable changes to Zetherion AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Note: percentage/count metrics in historical phase sections are snapshots from
> the date of that phase. Current policy is enforced by test configuration:
> overall coverage must remain `>=90%`.

---

## [Unreleased]

### Added - CGS Go-Live Closure Wave (2026-03-04)

- Added CGS operator Next.js app in `cgs/` mounted at `/cgs` with session-cookie BFF route handlers (`/cgs/api/gateway/*`).
- Added production operator screens for documents, retrieval, tenant access, bindings, settings, secrets, approvals, and audit export flows.
- Added blue/green CGS UI services in `docker-compose.yml`:
  - `zetherion-ai-cgs-ui-blue`
  - `zetherion-ai-cgs-ui-green`
- Added Traefik dynamic routing for `/cgs` while preserving `/service/ai/v1` to CGS gateway.
- Extended updater sidecar rollout/rollback orchestration to include `cgs-gateway` and `cgs-ui` service families plus routed health checks.
- Added CI jobs and contract mapping for CGS UI:
  - `cgs-lint`
  - `cgs-typecheck`
  - `cgs-test`
  - `cgs-build`
- Added local gate script `scripts/check-cgs-ui.sh` and wired it into `pre-push-tests.sh` and `validate-ci.sh`.
- Added env/config docs for:
  - `CGS_DOCUMENT_MUTATION_RPM`
  - `CGS_ADMIN_MUTATION_RPM`
  - `CGS_SESSION_COOKIE_NAME`
  - `CGS_GATEWAY_BASE_URL`

### Added - CGS-First Tenant Multi-Email Monitoring Control Plane (2026-03-04)

- Added tenant-scoped email admin persistence and intelligence model:
  - `tenant_email_provider_configs`
  - `tenant_email_oauth_states`
  - `tenant_email_accounts`
  - `tenant_email_sync_jobs`
  - `tenant_email_message_cache`
  - `tenant_email_critical_items`
  - `tenant_email_insights`
  - `tenant_email_events`
- Added Skills internal tenant email admin endpoints:
  - OAuth app config read/write
  - OAuth connect start/exchange
  - mailbox list/patch/delete
  - sync trigger
  - critical list
  - calendar list + primary calendar set
  - insights list + reindex
- Added model-assisted critical classification stage for synced emails:
  - deterministic rules + inference refinement
  - severity/reason/confidence outputs persisted for triage
- Added insight vector indexing for tenant email insights (Qdrant-backed when configured).
- Added retention enforcement in email sync path:
  - message body cache purge at 90 days
  - critical/insight purge at 365 days
- Added CGS internal admin email route family under:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/...`
- Added high-risk approval enforcement in CGS routes for:
  - tenant OAuth app credential writes
  - mailbox disconnect actions
- Added CGS docs coverage for email control-plane:
  - OpenAPI updates
  - auth/error matrix updates
  - frontend route wiring updates
  - new `docs/technical/cgs-email-monitoring-onboarding-kit.md`

### Added - CGS Tenant Admin Control Plane (2026-03-03)

- Added tenant-scoped internal admin persistence and audit model:
  - `tenant_discord_users`
  - `tenant_discord_bindings`
  - `tenant_settings_overrides`
  - `tenant_setting_versions`
  - `tenant_secrets`
  - `tenant_secret_versions`
  - `tenant_admin_audit_log`
- Added Skills internal tenant-admin API endpoints under:
  - `/admin/tenants/{tenant_id}/...`
  - includes Discord allowlist/roles, bindings, settings, secrets, and audit readout
- Added signed actor-envelope enforcement on Skills tenant-admin routes:
  - `X-Admin-Actor` + `X-Admin-Signature`
  - nonce/timestamp replay protection
- Added CGS internal tenant-admin route family:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/...`
- Added CGS approval workflow storage + routes for high-risk admin mutations:
  - pending change create/list
  - approve/reject (two-person guard)
  - apply status tracking for secret mutations
- Added tenant-aware runtime retrieval helpers:
  - `get_dynamic_for_tenant(...)`
  - `get_secret_for_tenant(...)`
- Wired Discord runtime allowlist checks to tenant-scoped controls when enabled via dynamic security settings.

### Changed - CGS-First Document Intelligence Client Kit (2026-03-02)

- Clarified exposure policy across docs and contracts: Zetherion `/api/v1` is upstream-only and CGS `/service/ai/v1` is the only public client API.
- Added internal component spec:
  - `docs/technical/zetherion-document-intelligence-component.md`
- Added external onboarding pack:
  - `docs/technical/cgs-client-onboarding-kit.md`
- Expanded CGS docs for browser upload completion options:
  - JSON completion (`tenant_id` in body)
  - multipart completion (`tenant_id` in query)
- Implemented CGS multipart passthrough for upload completion in runtime gateway route.
- Normalized document RAG provider naming to canonical `anthropic` (with `claude` alias compatibility retained).
- Updated OpenAPI specs and route wiring/auth/error docs to align with the above behavior.
- Tightened endpoint-doc bundle enforcement to include the new component spec + onboarding kit files.

### Changed - Windows Merge-Intelligence Promotions Authority (2026-03-02)

- Removed GitHub Actions `post-deploy-promotions.yml`; blog/release promotions are now executed on the Windows deployment host.
- Added Windows promotion runtime components:
  - `scripts/windows/promotions-runner.ps1`
  - `scripts/windows/promotions-watch.ps1`
  - `scripts/windows/promotions-pipeline.py`
  - `scripts/windows/set-promotions-secrets.ps1`
  - `scripts/windows/test-promotions-secrets.ps1`
- Deployment workflow now persists machine-local deployment receipts at `C:\ZetherionAI\data\deployment-receipts\<sha>.json` and invokes the local promotions runner after successful receipt build.
- Added scheduled task registration for `ZetherionPostDeployPromotions` (startup + periodic execution) through `scripts/windows/register-resilience-tasks.ps1`.
- Added per-SHA local promotions artifacts:
  - `C:\ZetherionAI\data\promotions\analysis\<sha>.json`
  - `C:\ZetherionAI\data\promotions\receipts\<sha>.json`
  - `C:\ZetherionAI\data\promotions\state.json`
- Promotions pipeline now enforces:
  - deployment receipt validation before any publish/release action
  - merge-intelligence evidence mapping across the promotion window
  - high-tier model only content generation (`gpt-5.2` -> `claude-sonnet-4-6`)
  - SEO + GEO gate checks and claim-to-evidence validation
  - idempotent blog publish + idempotent release increment with partial-failure retry behavior
- `scripts/check-cicd-success.sh` no longer requires a GitHub `Post-Deploy Promotions` workflow run for `main`.
- CI push trigger no longer runs full pipeline on `codex/**` (PR-to-main flow remains the quality gate path).

### Changed - Document Route Internal Hardening (2026-03-01)

- Internal typing and multipart parsing hardening in `src/zetherion_ai/api/routes/documents.py`.
- No API surface changes: no path/method/auth/error/schema contract changes for document endpoints.

### Changed - Windows Deploy Resilience Fallback (2026-03-01)

- Added controlled fallback in Windows resilience readiness checks for hardened runners where scheduled task registration is access-denied.
- Deployment receipt now treats persistent runner/docker service recovery as valid fallback when recovery tasks are missing and explicit fallback is enabled.

### Added - Document Intelligence + Post-Deploy Promotions (2026-02-28)

- Tenant-scoped document intelligence API endpoints:
  - `POST /api/v1/documents/uploads`
  - `POST /api/v1/documents/uploads/{upload_id}/complete`
  - `GET /api/v1/documents`
  - `GET /api/v1/documents/{document_id}`
  - `GET /api/v1/documents/{document_id}/preview`
  - `GET /api/v1/documents/{document_id}/download`
  - `POST /api/v1/documents/{document_id}/index`
  - `POST /api/v1/rag/query`
  - `GET /api/v1/models/providers`
- New tenant document persistence schema:
  - `tenant_documents`
  - `tenant_document_uploads`
  - `document_ingestion_jobs`
- Document ingestion pipeline for PDF/DOCX/text extraction, chunking, embedding, and indexing into `tenant_documents` Qdrant collection.
- CGS gateway runtime routes for document upload/list/detail/preview/download/re-index/RAG/provider catalog under `/service/ai/v1`.
- Provider/model override support in inference broker call path to support Groq/OpenAI/Claude routing for document RAG.
- Security and reliability hardening for Discord message alignment:
  - intent-aware memory-store handling in Tier 2 security path to reduce benign false positives
  - deterministic recent-context retrieval ordering in Qdrant
  - queue serialization guard for `discord_message` tasks by `(user_id, channel_id)`
  - strict reply-reference matching in Discord E2E waiter logic
- CI docs-contract enforcement additions:
  - endpoint doc-bundle checker (`scripts/check-endpoint-doc-bundle.py`)
  - pipeline contract job now runs endpoint docs bundle checks with full git history
- Documentation deployment now republishes on every `main` push (no docs-only path filter).
- Post-deploy promotions workflow (`.github/workflows/post-deploy-promotions.yml`) added:
  - gated by successful `Deploy Windows` completion and deployment receipt validation
  - mandatory SemVer patch release auto-increment/idempotency per deployed SHA
  - mandatory CGS blog generation/publish using `gpt-5.2` + `claude-sonnet-4-6` only
  - publishes release/blog receipts as workflow artifacts

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

This project is licensed under the MIT License. See the [LICENSE](https://github.com/jimtin/zetherion-ai/blob/main/LICENSE) file for details.

---

## Acknowledgments

- Anthropic for the Claude API
- Google for the Gemini API
- OpenAI for GPT models
- Discord.py community
- Qdrant team for the vector database
- Ollama team for the local LLM runtime
- Meta for the Llama model family
