# Testing Guide

This document explains how to run tests for SecureClaw.

## Test Types

SecureClaw has three types of tests:

1. **Unit Tests** - Fast, isolated tests of individual components
2. **Integration Tests** - End-to-end tests that start the full Docker environment
3. **Pre-commit Hooks** - Automated linting and type checking before commits

---

## Unit Tests

### Running Unit Tests

```bash
# Run all unit tests (excluding integration tests)
pytest -m "not integration"

# Run with coverage
pytest --cov=src/secureclaw --cov-report=html

# Run specific test file
pytest tests/test_agent_core.py

# Run with verbose output
pytest -v
```

### Writing Unit Tests

Unit tests go in `tests/` directory:

```python
import pytest
from secureclaw.agent.core import Agent

def test_agent_initialization():
    """Test that agent initializes correctly."""
    agent = Agent(memory=mock_memory)
    assert agent is not None
```

---

## Integration Tests

Integration tests start the entire Docker environment and simulate real user interactions.

### Prerequisites

1. **Docker** must be running
2. **Environment variables** must be set in `.env`:
   - `GEMINI_API_KEY` (required)
   - `DISCORD_TOKEN` (required)
   - Other API keys (optional, but recommended for full testing)

### Running Integration Tests

```bash
# Easy way - use the provided script
./scripts/run-integration-tests.sh

# Manual way
pytest tests/integration/test_e2e.py -v -s -m integration
```

### What Integration Tests Do

1. **Start Docker Compose** with test project name
2. **Wait for services** to be healthy (Qdrant, SecureClaw)
3. **Run test scenarios**:
   - Simple questions
   - Memory storage and recall
   - Complex tasks
   - Conversation context
   - Help commands
   - Service health checks
4. **Clean up** - Stop and remove containers

### Integration Test Output

```
🐳 Starting Docker Compose environment...
⏳ Waiting for services to be healthy...
✅ Qdrant is healthy
✅ SecureClaw is running

test_simple_question PASSED
test_memory_store_and_recall PASSED
test_complex_task PASSED
test_conversation_context PASSED
test_help_command PASSED
test_docker_services_running PASSED
test_qdrant_collections_exist PASSED

═══════════════════════════════════════════════
  All Integration Tests Passed! ✓
═══════════════════════════════════════════════
```

### Skipping Integration Tests

Integration tests can be slow (2-3 minutes). To skip them:

```bash
# Skip in pytest
pytest -m "not integration"

# Skip via environment variable
export SKIP_INTEGRATION_TESTS=true
pytest
```

---

## Pre-commit Hooks

Git hooks run automatically before commits and pushes.

### Setup

```bash
./scripts/setup-git-hooks.sh
```

### What Runs on Commit

Pre-commit hook runs:
- **Ruff** - Linting and formatting
- **Mypy** - Type checking
- **Bandit** - Security scanning
- **YAML/TOML checks**
- **Trailing whitespace removal**
- **Private key detection**

### What Runs on Push

Pre-push hook runs:
- All pre-commit checks
- **Unit tests** (excluding integration)

### Bypass Hooks (Not Recommended)

```bash
# Skip pre-commit hook
git commit --no-verify -m "message"

# Skip pre-push hook
git push --no-verify
```

---

## Continuous Integration (GitHub Actions)

When you push to GitHub, the full CI pipeline runs:

1. **Lint & Format** (Ruff)
2. **Type Check** (Mypy)
3. **Security Scan** (Bandit)
4. **Unit Tests** (Python 3.12 & 3.13)
5. **Docker Build** (Verify images build)
6. **Integration Tests** (Optional - only if API keys in secrets)

See [.github/workflows/ci.yml](.github/workflows/ci.yml) for details.

---

## Test Coverage

### Generate Coverage Report

```bash
# HTML report
pytest --cov=src/secureclaw --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=src/secureclaw --cov-report=term-missing

# XML report (for CI)
pytest --cov=src/secureclaw --cov-report=xml
```

### Coverage Goals

- **Minimum**: 70% overall coverage
- **Target**: 85%+ for core modules
- **Critical paths**: 95%+ (authentication, security, data handling)

---

## Debugging Test Failures

### View Integration Test Logs

If integration tests fail, check Docker logs:

```bash
# View SecureClaw logs
docker compose -p secureclaw-test logs secureclaw

# View Qdrant logs
docker compose -p secureclaw-test logs qdrant

# Follow logs in real-time
docker compose -p secureclaw-test logs -f
```

### Common Issues

**Issue**: "Services failed to become healthy"
- **Solution**: Check if ports 6333 or 8000 are already in use
- **Solution**: Verify .env has valid API keys
- **Solution**: Increase timeout in test (default 120s)

**Issue**: "Missing required environment variables"
- **Solution**: Copy `.env.example` to `.env` and fill in values

**Issue**: "Docker is not running"
- **Solution**: Start Docker Desktop

**Issue**: Tests pass but bot doesn't respond in Discord
- **Solution**: Integration tests use mock bot, not real Discord
- **Solution**: For real Discord testing, use `./start.sh` instead

---

## Test Markers

Use pytest markers to run specific test categories:

```bash
# Run only integration tests
pytest -m integration

# Run everything except integration tests
pytest -m "not integration"

# Run slow tests
pytest -m slow

# Run fast tests only
pytest -m "not slow and not integration"
```

### Available Markers

- `integration` - End-to-end tests with Docker
- `slow` - Tests that take >5 seconds
- Custom markers can be added in `pyproject.toml`

---

## Best Practices

1. **Run unit tests frequently** during development
2. **Run integration tests** before creating PRs
3. **Don't commit** with failing tests
4. **Don't bypass hooks** without good reason
5. **Keep tests fast** - mock external APIs in unit tests
6. **Add tests** for new features
7. **Update tests** when changing behavior

---

## Quick Reference

```bash
# Fast feedback loop during development
pytest -m "not integration" --tb=short

# Full local test suite
pytest

# Pre-commit check (manual)
pre-commit run --all-files

# Integration tests
./scripts/run-integration-tests.sh

# Coverage report
pytest --cov=src/secureclaw --cov-report=html && open htmlcov/index.html
```

---

## Troubleshooting

### Test Discovery Issues

If pytest can't find tests:

```bash
# Ensure PYTHONPATH includes src/
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

# Or use pytest.ini/pyproject.toml configuration
```

### Import Errors in Tests

```bash
# Install in editable mode
pip install -e .

# Or use test dependencies
pip install -r requirements-dev.txt
```

### Docker Cleanup

If test containers don't clean up:

```bash
# Manual cleanup
docker compose -p secureclaw-test down -v

# Nuclear option (removes ALL stopped containers)
docker system prune -a
```

---

## Writing New Tests

### Unit Test Template

```python
"""Tests for new feature."""

import pytest
from secureclaw.feature import NewFeature


@pytest.fixture
def feature():
    """Fixture for feature instance."""
    return NewFeature()


def test_new_feature(feature):
    """Test new feature behavior."""
    result = feature.do_something()
    assert result == expected
```

### Integration Test Template

```python
"""Integration test for new feature."""

import pytest


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_feature_e2e(mock_bot):
    """Test new feature end-to-end."""
    response = await mock_bot.simulate_message("test message")
    assert "expected" in response.lower()
```

---

## Related Documentation

- [CI/CD Pipeline](CI_CD.md) - GitHub Actions workflow
- [Contributing](../CONTRIBUTING.md) - Development guidelines
- [Architecture](ARCHITECTURE.md) - System design
