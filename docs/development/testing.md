# Testing Guide

Comprehensive guide to Zetherion AI's test suite, testing patterns, and coverage strategy.

## Overview

Zetherion AI maintains a rigorous testing discipline across the entire codebase:

- **3,000+ tests** across **89 test files** covering **91 source files**
- **93%+ code coverage** with branch coverage enabled
- **Three-layer testing pyramid**: Unit, Integration, and Discord E2E
- **Async-first** test design using `pytest-asyncio` with `asyncio_mode = "auto"`

```
                     /\
                    /  \
                   / E2E\
                  / Discord\
                 /  (Real API) \
                /________________\
               /                  \
              /   Integration       \
             / (Docker + Services)   \
            /     ~2-3 minutes        \
           /___________________________\
          /                              \
         /         Unit Tests              \
        /    (Mocked, Fast Feedback)        \
       /         ~60 seconds                 \
      /___________________________________________\
       Fastest | Most Isolated | Largest Volume
```

## Test Organization

Tests are organized into three directories reflecting the testing pyramid.

### Directory Structure

```
tests/
  conftest.py                      # Shared fixtures (settings, mocks, sample data)
  __init__.py
  test_agent_core.py               # Root-level: agent core (legacy location)
  test_bot.py                      # Root-level: Discord bot
  test_config.py                   # Root-level: configuration
  test_embeddings.py               # Root-level: embedding generation
  test_qdrant.py                   # Root-level: vector database
  test_router.py                   # Root-level: intent routing
  test_router_factory.py           # Root-level: router factory
  test_router_ollama.py            # Root-level: Ollama router
  test_security.py                 # Root-level: security module
  unit/
    test_agent_core.py             # Agent core logic
    test_config.py                 # Settings validation
    test_constants.py              # Constants and enums
    test_costs.py                  # Cost tracking
    test_discord_bot.py            # Discord bot handlers
    test_discord_notifier.py       # Discord notifications
    test_discovery.py              # Service discovery
    test_embeddings.py             # Embedding generation
    test_employment_profile.py     # Employment profile model
    test_encryption.py             # AES-GCM encryption
    test_github_client.py          # GitHub API client
    test_github_models.py          # GitHub data models
    test_github_skill.py           # GitHub skill logic
    test_gmail_accounts.py         # Gmail account management
    test_gmail_analytics.py        # Gmail analytics
    test_gmail_auth.py             # Gmail OAuth
    test_gmail_calendar_sync.py    # Calendar synchronization
    test_gmail_client.py           # Gmail API client
    test_gmail_conflicts.py        # Gmail conflict resolution
    test_gmail_digest.py           # Daily email digest
    test_gmail_inbox.py            # Inbox operations
    test_gmail_replies.py          # Email reply handling
    test_gmail_skill.py            # Gmail skill logic
    test_gmail_sync.py             # Gmail sync operations
    test_gmail_trust.py            # Gmail trust scoring
    test_inference_broker.py       # Multi-provider LLM broker
    test_interactive_setup.py      # Interactive setup wizard
    test_logging.py                # Structured logging
    test_main_startup.py           # Application startup
    test_models.py                 # Data models
    test_notifications.py          # Notification system
    test_observation_discord_adapter.py
    test_observation_dispatcher.py
    test_observation_extractors.py
    test_observation_gmail_adapter.py
    test_observation_models.py
    test_observation_pipeline.py
    test_personal_actions.py       # Personal action system
    test_personal_context.py       # Personal context engine
    test_personal_models.py        # Personal data models
    test_personal_storage.py       # Personal data storage
    test_profile_builder.py        # Profile construction
    test_profile_cache.py          # Profile caching
    test_profile_inference.py      # Profile inference engine
    test_profile_models.py         # Profile data models
    test_profile_storage.py        # Profile persistence
    test_providers.py              # LLM provider abstraction
    test_pyproject_sync.py         # pyproject.toml synchronization
    test_qdrant.py                 # Vector database operations
    test_registry.py               # Service registry
    test_relationship_tracker.py   # Relationship tracking
    test_router.py                 # Intent router
    test_router_factory.py         # Router factory pattern
    test_router_ollama.py          # Ollama-backed router
    test_scheduler_actions.py      # Scheduled task actions
    test_scheduler_heartbeat.py    # Scheduler heartbeat
    test_security.py               # Security module
    test_settings_manager.py       # Runtime settings
    test_skill_calendar.py         # Calendar skill
    test_skill_personal_model.py   # Personal model skill
    test_skill_profile.py          # Profile skill
    test_skill_task_manager.py     # Task manager skill
    test_skills_base.py            # Skill base class
    test_skills_client.py          # Skills HTTP client
    test_skills_permissions.py     # Skill permission system
    test_skills_registry.py        # Skill registry
    test_skills_server.py          # Skills HTTP server
    test_skills_server_main.py     # Skills server entrypoint
    test_task_classification.py    # Task classification
    test_user_manager.py           # User management
    test_utils.py                  # Utility functions
  integration/
    __init__.py
    test_agent_skills_http.py      # Agent-to-skills HTTP communication
    test_discord_e2e.py            # Real Discord API end-to-end
    test_e2e.py                    # Full stack end-to-end
    test_encryption_at_rest.py     # Encryption at rest verification
    test_heartbeat_cycle.py        # Scheduler heartbeat cycle
    test_profile_pipeline.py       # Full profile pipeline
    test_skills_e2e.py             # Skills system end-to-end
    test_skills_http.py            # Skills HTTP layer
    test_user_isolation.py         # Multi-user data isolation
```

### Test Categories

| Category | Location | Count | Speed | Dependencies |
|----------|----------|-------|-------|--------------|
| Unit tests | `tests/unit/` | ~70 files | Fast (~60s total) | Mocked |
| Root-level tests | `tests/test_*.py` | 9 files | Fast (~10s) | Mocked |
| Integration tests | `tests/integration/` | 9 files | Slow (~2-3 min) | Docker services |
| Discord E2E | `tests/integration/test_discord_e2e.py` | 1 file | Slow (~1 min) | Real Discord API |

## Running Tests

### Quick Commands

```bash
# All unit tests (fast, ~60s) -- excludes integration and Discord E2E
pytest tests/ -m "not integration and not discord_e2e"

# Specific test file
pytest tests/unit/test_agent_core.py -v

# Specific test class
pytest tests/unit/test_config.py::TestSettingsValidation -v

# Specific test function
pytest tests/unit/test_encryption.py::test_encrypt_decrypt_roundtrip -v

# Pattern matching
pytest tests/ -k "test_gmail" -v

# With coverage (HTML + terminal)
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=html --cov-report=term-missing

# Integration tests only (requires Docker)
pytest tests/integration/ -m integration -v -s

# Full suite (everything except Discord E2E)
pytest tests/ -m "not discord_e2e" -v
```

### Test Markers

Zetherion AI defines three custom markers in `pyproject.toml`:

| Marker | Description | Usage |
|--------|-------------|-------|
| `integration` | Tests requiring Docker services (Qdrant, Ollama, PostgreSQL) | `pytest -m integration` |
| `discord_e2e` | True end-to-end tests hitting the real Discord API | `pytest -m discord_e2e` |
| `slow` | Tests exceeding 5 seconds | `pytest -m "not slow"` |

Common marker combinations:

```bash
# Only fast unit tests
pytest tests/ -m "not integration and not discord_e2e and not slow"

# Integration but not Discord E2E
pytest tests/ -m "integration and not discord_e2e"

# Everything except integration
pytest tests/ -m "not integration"
```

## Writing Tests

### Unit Test Patterns

Unit tests follow the Arrange-Act-Assert pattern with mocked external dependencies.

**Basic synchronous test:**

```python
"""Tests for the encryption module."""

import pytest
from zetherion_ai.security import EncryptionManager


def test_encrypt_decrypt_roundtrip():
    """Plaintext survives an encrypt-then-decrypt cycle."""
    # Arrange
    manager = EncryptionManager(passphrase="test-passphrase")
    plaintext = "sensitive user data"

    # Act
    ciphertext = manager.encrypt(plaintext)
    result = manager.decrypt(ciphertext)

    # Assert
    assert result == plaintext
    assert ciphertext != plaintext
```

**Async test (no decorator needed with `asyncio_mode = "auto"`):**

```python
import pytest
from unittest.mock import AsyncMock, patch


async def test_agent_processes_message(mock_settings):
    """Agent generates a response for a simple user message."""
    mock_provider = AsyncMock()
    mock_provider.generate.return_value = "Hello! How can I help?"

    with patch("zetherion_ai.agent.core.get_provider", return_value=mock_provider):
        from zetherion_ai.agent.core import Agent

        agent = Agent(settings=mock_settings)
        response = await agent.process("Hi there", user_id=123)

    assert response is not None
    assert len(response) > 0
```

**Parametrized test:**

```python
import pytest


@pytest.mark.parametrize(
    "input_text, expected_intent",
    [
        ("hello", "simple_query"),
        ("remember that I like Python", "memory_store"),
        ("what do you know about me?", "memory_recall"),
        ("search my emails for invoices", "skill_gmail"),
    ],
)
async def test_router_classifies_intent(input_text, expected_intent, mock_settings):
    """Router correctly classifies user intent for common message types."""
    router = Router(settings=mock_settings)
    result = await router.classify(input_text)
    assert result.intent == expected_intent
```

### Integration Test Patterns

Integration tests use the `@pytest.mark.integration` marker and typically require Docker services.

```python
"""Integration test for the full profile pipeline."""

import pytest


@pytest.mark.asyncio
@pytest.mark.integration
async def test_profile_pipeline_stores_and_retrieves(mock_bot):
    """Profile pipeline stores observations and retrieves a coherent profile."""
    # Store an observation through the full pipeline
    await mock_bot.simulate_message("I am a senior Python developer at Acme Corp")

    # Retrieve the profile
    response = await mock_bot.simulate_message("What do you know about my career?")

    assert "python" in response.lower()
    assert response is not None
```

**MockDiscordBot usage** -- integration tests bypass the real Discord API by using `MockDiscordBot`, which injects messages directly into the agent pipeline:

```python
@pytest.mark.integration
async def test_memory_roundtrip(mock_bot):
    """Data stored in memory can be recalled in a later turn."""
    await mock_bot.simulate_message("Remember that my favorite color is blue")
    response = await mock_bot.simulate_message("What is my favorite color?")
    assert "blue" in response.lower()
```

### Fixture Patterns

The root `tests/conftest.py` provides shared fixtures used across all test layers.

**Key fixtures:**

| Fixture | Scope | Description |
|---------|-------|-------------|
| `setup_test_environment` | session (autouse) | Sets `DISCORD_TOKEN`, `GEMINI_API_KEY`, `ENCRYPTION_PASSPHRASE` as test placeholders |
| `clear_settings_cache` | function (autouse) | Clears the `get_settings` LRU cache before and after each test |
| `mock_settings` | function | A fully configured `Settings` instance with test values |
| `mock_qdrant_client` | function | `AsyncMock` of `AsyncQdrantClient` with stubbed CRUD methods |
| `mock_embeddings_client` | function | Mock Gemini embeddings client returning 768-dim vectors |
| `mock_gemini_client` | function | Mock Gemini generative client returning JSON routing responses |
| `mock_claude_client` | function | Mock Anthropic client (`claude-sonnet-4-5-20250929`) |
| `mock_openai_client` | function | Mock OpenAI client (`gpt-5.2`) |
| `mock_discord_message` | function | Mock `discord.Message` with `author`, `content`, `channel`, `reply` |
| `mock_discord_interaction` | function | Mock `discord.Interaction` with `defer` and `followup.send` |
| `sample_vector` | function | 768-dimensional float vector for embedding tests |
| `sample_conversation_messages` | function | Two-message conversation history (user + assistant) |
| `sample_memories` | function | Two sample long-term memories (preference + fact) |

**Custom fixture example:**

```python
@pytest.fixture
def gmail_client(mock_settings):
    """Gmail client with mocked OAuth credentials."""
    with patch("zetherion_ai.skills.gmail.client.get_credentials") as mock_creds:
        mock_creds.return_value = Mock(valid=True, token="test-token")
        client = GmailClient(settings=mock_settings)
        yield client
```

## Coverage

### Current State

| Metric | Value |
|--------|-------|
| Overall coverage | 93%+ |
| Total tests | 3,000+ |
| Test files | 89 |
| Source files | 91 |
| Branch coverage | Enabled |

### Coverage Targets

| Scope | Target | Rationale |
|-------|--------|-----------|
| New modules | 85%+ | Maintain overall quality |
| Security paths (encryption, auth, permissions) | 95%+ | Critical user data protection |
| Core agent loop | 90%+ | Primary user-facing path |
| Skills and integrations | 85%+ | External API interaction |
| Overall project | 93%+ | Do not regress |

### Coverage by Module (Approximate)

| Module | Coverage | Notes |
|--------|----------|-------|
| `security/` | 97% | Encryption, user auth, permissions |
| `agent/` | 95% | Core agent, router, providers |
| `memory/` | 94% | Qdrant, embeddings |
| `config.py` | 96% | Settings validation |
| `discord/` | 92% | Bot handlers, user manager |
| `skills/` | 91% | Gmail, GitHub, calendar, tasks |
| `observation/` | 93% | Pipeline, extractors, adapters |
| `personal/` | 92% | Context, actions, storage |
| `profile/` | 93% | Builder, cache, inference |
| `models/` | 95% | Pydantic data models |
| `costs/` | 94% | Usage tracking |
| `scheduler/` | 90% | Heartbeat, actions |
| `notifications/` | 91% | Discord notifier |

### Generating Reports

```bash
# HTML report (opens in browser)
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=html
open htmlcov/index.html

# Terminal report with missing lines
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=term-missing

# XML report (for CI / Codecov)
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=xml

# Combined (all three at once -- this is the default via pyproject.toml addopts)
pytest tests/ -m "not discord_e2e"
```

## Test Configuration

All test configuration lives in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
    "--cov=src/zetherion_ai",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-report=xml",
]
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "discord_e2e: marks tests as Discord end-to-end tests (deselect with '-m \"not discord_e2e\"')",
]

[tool.coverage.run]
source = ["src/zetherion_ai"]
omit = ["*/tests/*", "*/__pycache__/*", "*/site-packages/*"]
branch = true

[tool.coverage.report]
precision = 2
show_missing = true
skip_covered = false
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "@abstractmethod",
]
```

Key settings:

- **`asyncio_mode = "auto"`** -- async test functions are detected and run automatically without needing `@pytest.mark.asyncio` on every test.
- **`--strict-markers`** -- unregistered markers cause an error, preventing typos like `@pytest.mark.intgration`.
- **`pythonpath = ["src"]`** -- allows `from zetherion_ai.x import y` without installing the package.
- **`branch = true`** -- coverage tracks branch paths, not just line execution.

## Debugging Tests

### Common Debugging Flags

```bash
# Show print statements and log output (pytest normally captures stdout)
pytest tests/unit/test_agent_core.py -s

# Drop into the Python debugger on first failure
pytest tests/unit/test_agent_core.py --pdb

# Stop on first failure (do not run remaining tests)
pytest tests/ -x

# Stop after N failures
pytest tests/ --maxfail=3

# Extra-verbose output (shows full assertion diffs)
pytest tests/ -vv

# Full traceback on failure (default is --tb=short)
pytest tests/ --tb=long

# Show slowest 10 tests
pytest tests/ --durations=10

# Run last-failed tests only
pytest tests/ --lf

# Run failed-first, then the rest
pytest tests/ --ff
```

### Inspecting Docker Logs (Integration Tests)

When integration tests fail, inspect the service logs:

```bash
# View Zetherion AI bot logs
docker compose -p zetherion_ai-test logs zetherion-ai-bot

# View Qdrant logs
docker compose -p zetherion_ai-test logs qdrant

# View Ollama generation container logs
docker compose -p zetherion_ai-test logs ollama

# View Ollama router container logs
docker compose -p zetherion_ai-test logs ollama-router

# View PostgreSQL logs
docker compose -p zetherion_ai-test logs postgres

# Follow all logs in real time
docker compose -p zetherion_ai-test logs -f
```

### Debugging Async Tests

Async tests can be tricky. Common patterns:

```bash
# Run a single async test with full output
pytest tests/unit/test_inference_broker.py::test_broker_retries_on_failure -s -vv

# If you see "RuntimeError: Event loop is closed", ensure asyncio_mode = "auto"
# in pyproject.toml and that you are not manually creating event loops.
```

## Best Practices

### Test Naming

- File: `test_<module_name>.py` (mirrors the source file name)
- Class: `Test<ClassName>` (optional -- flat functions are preferred)
- Function: `test_<what_it_does>` using plain English, not method names
- Good: `test_encryption_rejects_empty_passphrase`
- Bad: `test_encrypt_1`, `test_it_works`

### Test Design

- **One assertion per concept.** A test can have multiple `assert` statements, but they should all verify a single logical behavior.
- **Mock external services.** LLM APIs (`claude-sonnet-4-5-20250929`, `gpt-5.2`, `gemini-2.5-flash`), Discord, Qdrant, PostgreSQL, and Ollama (`llama3.2:3b`, `llama3.1:8b`) should all be mocked in unit tests.
- **Do not test implementation details.** Test the public interface, not internal method calls.
- **Clean up resources.** Use `yield` fixtures to guarantee teardown, especially for async clients and temporary files.
- **Keep tests deterministic.** Avoid random data, time-dependent assertions, or network calls in unit tests.
- **Use parametrize for variants.** If you are writing 5 tests that differ only by input, use `@pytest.mark.parametrize`.

### Test Organization

- Each source module in `src/zetherion_ai/` should have a corresponding test file in `tests/unit/`.
- Integration tests live in `tests/integration/` and require the `@pytest.mark.integration` marker.
- Shared fixtures go in `tests/conftest.py`. Module-specific fixtures go in the test file itself.

## Troubleshooting

### Import Errors

**Symptom:** `ModuleNotFoundError: No module named 'zetherion_ai'`

```bash
# Ensure the package is installed in editable mode
pip install -e ".[dev]"

# Or verify pythonpath is set in pyproject.toml
# [tool.pytest.ini_options]
# pythonpath = ["src"]
```

### Docker Service Issues

**Symptom:** Integration tests fail with connection errors.

```bash
# Verify Docker is running
docker info

# Check that test containers are healthy
docker compose -p zetherion_ai-test ps

# Verify no port conflicts (Qdrant: 6333, Ollama: 11434, PostgreSQL: 5432)
lsof -i :6333
lsof -i :11434
lsof -i :5432

# Clean up stale test containers
docker compose -p zetherion_ai-test down -v
```

### Async Test Pitfalls

**Symptom:** `RuntimeError: Event loop is closed` or `PytestUnraisableExceptionWarning`

- Ensure `asyncio_mode = "auto"` is set in `pyproject.toml`.
- Do not use `asyncio.run()` inside test functions. Let pytest-asyncio manage the event loop.
- Use `AsyncMock` (not `Mock`) for coroutine return values.

**Symptom:** `ScopeMismatch` when using session-scoped async fixtures

- Session-scoped async fixtures require `pytest-asyncio >= 0.23` and `scope="session"` on the fixture.

### Coverage Gaps

**Symptom:** Coverage is lower than expected on a module you tested.

```bash
# Check which lines are missing
pytest tests/unit/test_encryption.py --cov=src/zetherion_ai/security --cov-report=term-missing

# Check if the coverage source is correct
# Ensure [tool.coverage.run] source = ["src/zetherion_ai"]
```

Common causes:
- Lines inside `if TYPE_CHECKING:` blocks (excluded by default).
- Exception handlers that are hard to trigger.
- Platform-specific code paths.

### Settings Cache Interference

**Symptom:** Tests pass individually but fail when run together.

The `get_settings()` function uses `@lru_cache`. The `clear_settings_cache` autouse fixture clears it before and after each test, but if you import `get_settings` at module level, the cache may persist.

```python
# Bad: module-level import triggers caching
from zetherion_ai.config import get_settings
settings = get_settings()  # Cached at import time

# Good: call inside the test or fixture
def test_something():
    from zetherion_ai.config import get_settings
    settings = get_settings()
```

## Related Documentation

- [CI/CD Pipeline](../development/ci-cd.md) -- Pre-commit hooks, pre-push hooks, and GitHub Actions
- [Architecture](../technical/architecture.md) -- System design and component interactions
- [Security](../technical/security.md) -- Encryption, authentication, and threat model
