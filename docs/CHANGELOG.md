# Changelog

All notable changes to Zetherion AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - Phase 5 (2026-02-06)

#### Phase 5A: Encryption Layer
- AES-256-GCM field-level encryption for sensitive Qdrant payloads
- PBKDF2-HMAC-SHA256 key derivation with 600k iterations
- TLS support for Qdrant connections (optional)
- `FieldEncryptor` and `KeyManager` classes in `zetherion_ai.security`

#### Phase 5B: InferenceBroker
- Smart multi-provider LLM routing based on task type
- Provider capability matrix: Claude (code), OpenAI (reasoning), Gemini (long docs), Ollama (lightweight)
- 16 TaskTypes for granular routing decisions
- Automatic fallback chains when primary provider unavailable

#### Phase 5B.1: Model Registry & Cost Tracking
- Dynamic model discovery via provider APIs
- Tier-based model selection (quality/balanced/fast)
- SQLite cost tracking with per-request logging
- Daily/monthly cost aggregation and reporting
- Discord notifications for budget alerts

#### Phase 5C: User Profile System
- 8 profile categories: identity, preferences, schedule, projects, relationships, skills, goals, habits
- Tiered inference: Tier 1 (regex), Tier 2 (Ollama), Tier 3 (embeddings), Tier 4 (cloud)
- 5 signal engines for implicit extraction
- TTL-based caching with confidence scoring
- Background profile extraction after responses

#### Phase 5C.1: Employment Profile
- Bot identity and relationship modeling
- Trust levels: MINIMAL → BUILDING → ESTABLISHED → HIGH → FULL
- 10 relationship milestones tracking
- Communication style adaptation (formality, verbosity, proactivity)

#### Phase 5D: Skills Framework
- Abstract Skill interface with permission-based access control
- 15 granular permissions (read/write profile, memories, messages, etc.)
- SkillRegistry for intent routing and heartbeat coordination
- Separate Docker container for skills service isolation
- REST API with authentication for bot ↔ skills communication

#### Phase 5E: Built-in Skills
- **Task Manager**: CRUD operations, priority levels, deadlines, heartbeat reminders
- **Calendar Awareness**: Event types, recurrence patterns, availability checking
- **Profile Manager**: GDPR-style view/update/delete/export, confidence reports

#### Phase 5F: Heartbeat Scheduler
- Configurable interval (default 5 min) with quiet hours support
- Rate limiting (3 msgs/hour per user)
- ActionExecutor with handlers for all skill action types
- Scheduled event handling for one-time triggers

#### Phase 5G: Router Enhancement
- 3 new skill intents: TASK_MANAGEMENT, CALENDAR_QUERY, PROFILE_QUERY
- Skill intent examples in router prompts
- Agent integration for skill intent handling

### Changed
- **Project renamed** from `secureclaw` to `zetherion-ai`
- All imports updated to use `zetherion_ai` module name
- Docker container names now use hyphens (`zetherion-ai-*`)
- Comprehensive test suite expanded to **885 unit tests** (78% coverage)
- Integration tests updated for Phase 5 features

### Previous Changes
- Comprehensive test suite with 87.58% overall coverage
- Router factory with pluggable backend architecture (Gemini/Ollama)
- Discord bot commands: `/channels`, `/remember`, `/summarize`
- File-based logging with rotation support
- Pre-commit hooks for code quality (Ruff, Mypy, Bandit, Gitleaks, Hadolint)
- CI/CD pipeline with 6 parallel jobs
- Docker Compose setup for local development
- Security controls: rate limiting, user allowlist, prompt injection detection
- Vector memory using Qdrant for long-term context
- Async embeddings with parallel batch processing
- Contributing guidelines and code of conduct

### Fixed
- All test failures resolved (Phase 1A, 1B, 1C)
- Config tests with environment variable isolation using monkeypatch
- Docker integration tests with proper service startup
- Type checking errors in async Qdrant client
- GitHub push protection issues with example tokens in documentation

## [1.0.0] - Initial Release

### Added
- Discord bot with dual LLM backends (Gemini + Ollama)
- Message routing with intent classification
- Claude/OpenAI integration for complex tasks
- Gemini/Ollama integration for simple queries
- Qdrant vector database for memory
- Google Gemini embeddings for semantic search
- Docker containerization
- Basic security controls (rate limiting, allowlist)
- Comprehensive error handling and retry logic

### Security
- Pydantic SecretStr for all credentials
- Gitleaks secret scanning in pre-commit hooks
- Bandit security scanning in CI/CD
- CodeQL weekly analysis
- Pinned dependencies with Dependabot
- Prompt injection detection (17 regex patterns)
- User allowlist for Discord interactions

---

## Version History Details

### Phase 1: Test Fixes (Coverage: ~42% → 78%)

**Phase 1A: Agent Core Tests** - Fixed 13 test failures
- Resolved Docker service dependency issues
- Fixed async Qdrant client usage
- Improved retry logic testing
- **Result**: 41 tests passing, 94.76% coverage

**Phase 1B: Security Tests** - Fixed 3 test failures
- Fixed prompt injection detection tests
- Corrected allowlist and rate limiter tests
- **Result**: 37 tests passing, 94.12% coverage

**Phase 1C: Config Tests** - Fixed 10 test failures
- Implemented environment variable isolation with monkeypatch
- Fixed allowed_user_ids parsing tests
- Resolved Docker integration test errors (14 tests)
- **Result**: All config tests passing, 96.88% coverage

### Phase 2: Coverage Improvements (Coverage: 78% → 87.58%)

**Phase 2A: Router Factory Tests** - 26% → 100% coverage
- Added 12 comprehensive tests for factory functions
- Tested async/sync router creation
- Tested health checks and fallback logic
- Tested Ollama → Gemini fallback scenarios
- **Result**: 12 new tests, 100% coverage

**Phase 2B: Discord Bot Edge Cases** - 68.55% → 89.92% coverage
- Added 11 edge case tests for uncovered code paths
- `/channels` command tests (6 tests):
  - Unauthorized user handling
  - DM vs guild context
  - Text/voice/category channel listing
  - Long response message splitting
- Agent readiness tests (1 test)
- `_send_long_message` helper tests (4 tests)
- **Result**: 11 new tests, 89.92% coverage (exceeded 85% target)

### Final Test Statistics

| Category | Count | Status |
|----------|-------|--------|
| **Unit Tests** | 255 | ✅ All passing |
| **Integration Tests** | 14 | ✅ All passing |
| **Discord E2E Tests** | 4 | ✅ 4 passing, 1 skipped |
| **Overall Coverage** | 87.58% | ✅ Target exceeded |

### Module Coverage Breakdown

| Module | Coverage | Tests | Status |
|--------|----------|-------|--------|
| Router Factory | 100% | 12 | ✅ Comprehensive |
| Config | 96.88% | 15 | ✅ Excellent |
| Agent Core | 94.76% | 41 | ✅ Excellent |
| Security | 94.12% | 37 | ✅ Excellent |
| Discord Bot | 89.92% | 30 | ✅ Very Good |
| Logging | 85.71% | 8 | ✅ Good |
| Memory (Qdrant) | 84.62% | 12 | ✅ Good |
| Memory (Embeddings) | 81.82% | 6 | ✅ Good |
| Router (Gemini) | 78.95% | 8 | ✅ Good |
| Router (Ollama) | 75.86% | 8 | ✅ Good |

---

## Documentation Updates

### Recent Documentation Improvements

- **README.md**: Added CI/CD badges, key features section, comprehensive testing table
- **SECURITY.md**: Updated test coverage table with Phase 1 & 2 improvements
- **CONTRIBUTING.md**: Created comprehensive contributor guide
- **TROUBLESHOOTING.md**: Fixed example tokens to prevent GitHub push protection issues
- **TESTING.md**: Added test organization, coverage targets, and debugging guides

### Documentation Files

| File | Purpose | Status |
|------|---------|--------|
| README.md | Project overview and quick start | ✅ Updated |
| CONTRIBUTING.md | Contribution guidelines | ✅ Created |
| CHANGELOG.md | Version history | ✅ Created |
| SECURITY.md | Security controls and testing | ✅ Updated |
| docs/ARCHITECTURE.md | System architecture | ✅ Complete |
| docs/TESTING.md | Testing guide | ✅ Complete |
| docs/TROUBLESHOOTING.md | Common issues | ✅ Updated |
| docs/FAQ.md | Frequently asked questions | ✅ Complete |
| docs/COMMANDS.md | Discord command reference | ✅ Complete |

---

## CI/CD Pipeline

### Pipeline Stages

1. **Lint** - Ruff linting and formatting
2. **Type Check** - Mypy strict mode type checking
3. **Security** - Bandit security scanning
4. **Tests** - Unit tests on Python 3.12 & 3.13
5. **Docker Build** - Container build verification
6. **Integration** - Full integration tests with Docker services

### Pre-Commit Hooks

- Ruff (linting and formatting)
- Mypy (type checking)
- Gitleaks (secret scanning)
- Bandit (security scanning)
- Hadolint (Dockerfile linting)
- File checks (trailing whitespace, EOF, merge conflicts)

---

## Breaking Changes

None yet - this is the initial stable release with comprehensive testing.

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

---

## Known Issues

None currently reported. See [GitHub Issues](https://github.com/jimtin/zetherion-ai/issues) for the latest.

---

## Future Roadmap

### Planned Features (Phase 5)

- Encrypted memory storage (AES-256-GCM)
- User profiling and preferences
- Skills framework for extensible commands
- Smart multi-provider routing (Claude for code, OpenAI for reasoning, Gemini for docs)
- Heartbeat scheduler for proactive tasks

See [`memory/phase5-plan.md`](memory/phase5-plan.md) for detailed implementation plan.

---

## Contributors

- James Hinton ([@jimtin](https://github.com/jimtin)) - Project creator and maintainer
- Claude Sonnet 4.5 - AI pair programming assistant

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Anthropic for Claude API
- Google for Gemini API
- OpenAI for GPT models
- Discord.py community
- Qdrant team for vector database
- Ollama team for local LLM runtime
