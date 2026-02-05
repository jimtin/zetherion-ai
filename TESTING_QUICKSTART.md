# Testing Quick Start

Quick reference for running tests after making code changes.

## 🚀 Quick Commands

```bash
# Run unit tests (fast - 10-30 seconds)
pytest -m "not integration"

# Run integration tests (slow - 2-3 minutes)
./scripts/run-integration-tests.sh

# Run all pre-commit checks
pre-commit run --all-files

# Generate coverage report
pytest --cov=src/secureclaw --cov-report=html
open htmlcov/index.html
```

## ✅ What Just Got Added

### Integration Test Suite (`tests/integration/test_e2e.py`)

Automatically tests the entire system by:
1. Starting Docker Compose with Qdrant and SecureClaw
2. Waiting for all services to be healthy
3. Simulating user messages and verifying responses
4. Testing memory storage and recall
5. Testing complex tasks and conversation context
6. Cleaning up containers when done

### Test Scenarios Covered

- ✅ Simple questions (e.g., "What is 2+2?")
- ✅ Memory storage ("Remember that my favorite color is blue")
- ✅ Memory recall ("What is my favorite color?")
- ✅ Complex tasks (e.g., "Explain async programming")
- ✅ Conversation context (remembers previous messages)
- ✅ Help commands
- ✅ Docker service health
- ✅ Qdrant collections initialization

## 🎯 When to Run Which Tests

### During Development (Every Few Minutes)
```bash
# Fast feedback - just unit tests
pytest -m "not integration" --tb=short
```

### Before Committing (Automatic)
Pre-commit hook runs automatically:
- Ruff linting and formatting
- Mypy type checking
- Security scanning
- YAML/TOML validation

### Before Pushing (Automatic)
Pre-push hook runs:
- All pre-commit checks
- Unit tests with coverage

### Before Creating PR (Manual)
```bash
# Full integration test suite
./scripts/run-integration-tests.sh
```

## 📊 Example Output

### Integration Tests Success
```
🐳 Starting Docker Compose environment...
✅ Docker is running
✅ Cleanup complete

⏳ Waiting for services to be healthy...
✅ Qdrant is healthy
✅ SecureClaw is running

tests/integration/test_e2e.py::test_simple_question PASSED
tests/integration/test_e2e.py::test_memory_store_and_recall PASSED
tests/integration/test_e2e.py::test_complex_task PASSED
tests/integration/test_e2e.py::test_conversation_context PASSED
tests/integration/test_e2e.py::test_help_command PASSED
tests/integration/test_e2e.py::test_docker_services_running PASSED
tests/integration/test_e2e.py::test_qdrant_collections_exist PASSED

═══════════════════════════════════════════════
  All Integration Tests Passed! ✓
═══════════════════════════════════════════════
```

## 🔧 Configuration

### Skip Integration Tests
```bash
# In environment
export SKIP_INTEGRATION_TESTS=true

# In pytest
pytest -m "not integration"
```

### Run Integration Tests on Push
```bash
# Enable integration tests in pre-push hook
export RUN_INTEGRATION_TESTS=1
git push
```

## 📝 Adding New Tests

### Unit Test Example
```python
# tests/test_new_feature.py
def test_new_feature():
    """Test new feature works correctly."""
    result = new_feature_function()
    assert result == expected_value
```

### Integration Test Example
```python
# tests/integration/test_e2e.py
@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_feature_e2e(mock_bot):
    """Test new feature end-to-end."""
    response = await mock_bot.simulate_message("test message")
    assert "expected response" in response.lower()
```

## 🐛 Troubleshooting

### Tests Fail with "Services failed to become healthy"
- Check Docker Desktop is running
- Verify ports 6333 and 8000 are not in use
- Check .env has valid API keys

### Tests Fail with "Missing required environment variables"
- Copy `.env.example` to `.env`
- Add your API keys (at minimum: GEMINI_API_KEY, DISCORD_TOKEN)

### Integration tests hang
- Increase timeout in `DockerEnvironment.wait_for_healthy()`
- Check Docker logs: `docker compose -p secureclaw-test logs`

## 📖 Full Documentation

See [docs/TESTING.md](docs/TESTING.md) for complete testing documentation.

## 🎉 Summary

You now have:
- ✅ Automated unit tests
- ✅ End-to-end integration tests
- ✅ Pre-commit hooks for code quality
- ✅ Pre-push hooks for test validation
- ✅ Coverage reporting
- ✅ Easy-to-use test scripts

Run `./scripts/run-integration-tests.sh` before creating PRs to ensure everything works! 🚀
