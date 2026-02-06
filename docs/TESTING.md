# Testing Guide

Complete guide to Zetherion AI's three-layer testing approach.

## Test Types

Zetherion AI uses a **three-layer testing pyramid** for comprehensive coverage:

1. **Unit Tests** - Fast, isolated tests with mocked dependencies (Discord bot, Agent, Router, Memory)
2. **Integration Tests** - Full stack tests with Docker services (bypasses Discord API)
3. **Discord E2E Tests** - True end-to-end tests using real Discord API
4. **Pre-commit Hooks** - Automated linting and type checking before commits

```
              🔺
           Discord E2E
        (Real Discord API)
      Slowest | Most Realistic
    ╱                           ╲
   ╱   Integration Tests          ╲
  ╱ (Docker + Services + Agent)    ╲
 ╱    Medium Speed | Component      ╲
╱          Integration               ╲
╲_______________________________________╱
 ╲           Unit Tests               ╱
  ╲   (Mocked, Fast Feedback)        ╱
   ╲________________________________╱
    Fastest | Most Isolated | Largest

---

## Unit Tests

### Running Unit Tests

```bash
# Run all unit tests (excluding integration tests)
pytest -m "not integration"

# Run with coverage
pytest --cov=src/zetherion_ai --cov-report=html

# Run specific test file
pytest tests/test_agent_core.py

# Run with verbose output
pytest -v
```

### Writing Unit Tests

Unit tests go in `tests/` directory:

```python
import pytest
from zetherion_ai.agent.core import Agent

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
2. **Wait for services** to be healthy (Qdrant, Zetherion AI)
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
✅ Zetherion AI is running

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

## Discord E2E Tests

**NEW**: True end-to-end tests that send real messages through Discord API.

**Location**: `tests/integration/test_discord_e2e.py`
**What**: Tests real Discord bot message handling with actual Discord API
**Speed**: Fast (~1 minute once bot is running)
**Marker**: `discord_e2e`

### What's Different from Integration Tests?

| Feature | Integration Tests | Discord E2E Tests |
|---------|-------------------|-------------------|
| Discord API | ❌ Mocked (MockDiscordBot) | ✅ Real Discord messages |
| Agent Logic | ✅ Full stack tested | ✅ Full stack tested |
| Services | ✅ Qdrant + Ollama | ✅ Qdrant + Ollama |
| **Use Case** | Verify agent/router/memory | Verify Discord bot integration |

### Setup Requirements

#### 1. Create Test Bot in Discord Developer Portal

1. Go to https://discord.com/developers/applications
2. Click "New Application" → Name: "Zetherion AI Test Bot"
3. Navigate to "Bot" tab
4. Click "Reset Token" → **Copy token** (you'll need this)
5. **Enable Required Privileged Gateway Intents:**
   - ✅ **Message Content Intent** - Allows bot to read message content
   - ✅ **Server Members Intent** - Allows bot to see member information
6. **Configure Bot Permissions:**
   - ✅ **View Channels** - See channels in the server
   - ✅ **Send Messages** - Send responses to users
   - ✅ **Read Message History** - Read previous messages
   - ✅ **Use Application Commands** - Enable slash commands
   - ✅ **Mention Everyone, Here, and All Roles** - For @mentions in responses
7. Save changes

#### 2. Create Test Discord Server & Channel

1. Create a new Discord server (or use existing test server)
2. **Generate Bot Invite URL** (choose one method):

   **Method A - Manual URL:**
   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=274878285888&scope=bot%20applications.commands
   ```
   - Replace `YOUR_CLIENT_ID` with your bot's Application ID (from General Information tab)
   - `permissions=274878285888` = View Channels + Send Messages + Read Message History + Use Application Commands + Mention Everyone

   **Method B - Discord Developer Portal:**
   - In Discord Developer Portal, go to "OAuth2" → "URL Generator"
   - Select scopes: `bot` and `applications.commands`
   - Select bot permissions:
     - View Channels
     - Send Messages
     - Read Message History
     - Use Application Commands
     - Mention Everyone, Here, and All Roles
   - Copy generated URL at bottom of page

3. **Invite bot to test server** using the generated URL
4. Create a dedicated test channel (e.g., `#bot-testing`)
5. **Get Channel ID:**
   - Enable Developer Mode in Discord: Settings → Advanced → Developer Mode
   - Right-click the test channel → "Copy Channel ID"

#### 3. Configure Environment Variables

Add to your `.env` file:

```env
# Discord E2E Testing (separate from main bot)
TEST_DISCORD_BOT_TOKEN=your_test_bot_token_here
TEST_DISCORD_CHANNEL_ID=1234567890123456789

# Allow bot-to-bot messages (required for E2E tests)
ALLOW_BOT_MESSAGES=true
```

**⚠️ Important**:
- Use a **separate test bot**, not your production bot!
- Never commit `TEST_DISCORD_BOT_TOKEN` to git
- Test bot should only have access to test servers
- **`ALLOW_BOT_MESSAGES=true` is required** for Discord E2E tests to work
  - By default, Zetherion AI ignores messages from other bots (to prevent bot-to-bot spam)
  - Setting this to `true` allows the test bot to send messages to your production bot
  - **Keep this `false` in production** unless you specifically need bot-to-bot communication

### Running Discord E2E Tests

```bash
# Easy way - use the provided script
./scripts/run-discord-e2e-tests.sh

# Manual way
pytest tests/integration/test_discord_e2e.py -v -s -m discord_e2e

# Skip Discord E2E tests (if not configured)
pytest tests/ -m "not discord_e2e" -v
```

### What Discord E2E Tests Cover

1. ✅ **Message Handling** - Bot receives and processes messages
2. ✅ **Response Generation** - Bot sends responses back to Discord
3. ✅ **Memory Operations** - Store and recall through Discord
4. ✅ **Mentions** - Bot responds to @mentions
5. ✅ **Slash Commands** - Commands registered and functional
6. ✅ **Complex Queries** - Multi-turn conversations

### Example Discord E2E Test

```python
@pytest.mark.discord_e2e
@pytest.mark.asyncio
async def test_bot_responds_to_message(discord_test_client):
    """Test bot responds to a real Discord message."""
    # Send actual message through Discord API
    test_message = await discord_test_client.send_message(
        "Hello Zetherion AI, what is 2+2?"
    )

    # Wait for bot response (real Discord event)
    response = await discord_test_client.wait_for_bot_response(
        test_message, timeout=30.0
    )

    assert response is not None
    assert len(response.content) > 0

    # Cleanup test messages
    await discord_test_client.delete_message(test_message)
    await discord_test_client.delete_message(response)
```

### Troubleshooting Discord E2E Tests

**Error**: `TEST_DISCORD_BOT_TOKEN not set`
```bash
# Add to .env
echo "TEST_DISCORD_BOT_TOKEN=your_token" >> .env
echo "TEST_DISCORD_CHANNEL_ID=123456789" >> .env
```

**Error**: Bot not responding
```bash
# Check bot is online in Discord
# Check bot logs
docker logs zetherion_ai-bot
```

**Verify bot has required permissions in test channel:**
1. Right-click test channel → "Edit Channel" → "Permissions"
2. Find your test bot in the permissions list
3. Ensure these permissions are enabled (green checkmarks):
   - ✅ View Channels
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Use Application Commands
4. If permissions are missing, add them and try again

**Verify bot intents are enabled:**
1. Go to Discord Developer Portal → Your Application → Bot
2. Scroll to "Privileged Gateway Intents"
3. Ensure both are enabled:
   - ✅ Message Content Intent
   - ✅ Server Members Intent
4. Save changes and restart bot if you made changes

**Error**: Timeout waiting for response
```bash
# Possible causes:
# 1. Bot not in test server - reinvite bot
# 2. Bot lacks permissions - check channel permissions
# 3. Rate limiting - wait 60 seconds between test runs
# 4. Discord API issues - check https://discordstatus.com
```

### Required Permissions Summary

**Privileged Gateway Intents** (Bot tab in Developer Portal):
| Intent | Required | Purpose |
|--------|----------|---------|
| Message Content Intent | ✅ Yes | Read message content for processing |
| Server Members Intent | ✅ Yes | Access member information |

**Bot Permissions** (OAuth2 → URL Generator):
| Permission | Required | Purpose |
|-----------|----------|---------|
| View Channels | ✅ Yes | See test channel |
| Send Messages | ✅ Yes | Send responses |
| Read Message History | ✅ Yes | Read previous messages for context |
| Use Application Commands | ✅ Yes | Enable slash commands (/ask, /remember, etc.) |
| Mention Everyone, Here, and All Roles | ⚠️ Optional | Allow @mentions in responses |

**Permission Integer for OAuth URL**: `274878285888`

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
6. **Integration Tests** (MockDiscordBot + full agent stack)
7. **Discord E2E Tests** (Real Discord API - only if secrets configured)

See [.github/workflows/ci.yml](.github/workflows/ci.yml) for details.

### GitHub Actions Secrets Setup

To enable Discord E2E tests in CI, add these secrets to your GitHub repository:

1. Go to your repository on GitHub
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** and add:

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `DISCORD_TOKEN` | Main bot token (production) | `MTIzNDU2...` |
| `GEMINI_API_KEY` | Google AI API key | `AIza...` |
| `TEST_DISCORD_BOT_TOKEN` | Test bot token (separate bot) | `MTIzNDU2...` |
| `TEST_DISCORD_CHANNEL_ID` | Test channel ID | `1234567890123456789` |
| `ANTHROPIC_API_KEY` | Anthropic API key (optional) | `sk-ant-api03-...` |
| `OPENAI_API_KEY` | OpenAI API key (optional) | `sk-...` |

**Important:**
- Use a **separate test bot** for `TEST_DISCORD_BOT_TOKEN`, not your production bot
- Create a dedicated test server/channel for `TEST_DISCORD_CHANNEL_ID`
- If `TEST_DISCORD_BOT_TOKEN` or `TEST_DISCORD_CHANNEL_ID` are not set, Discord E2E tests will be gracefully skipped
- **Test bot must have same permissions as production bot:**
  - Privileged Intents: Message Content + Server Members
  - Bot Permissions: View Channels, Send Messages, Read Message History, Use Application Commands
  - See [Required Permissions Summary](#required-permissions-summary) below for details

### CI Pipeline Behavior

**Discord E2E Tests:**
- ✅ **Run**: If both `TEST_DISCORD_BOT_TOKEN` and `TEST_DISCORD_CHANNEL_ID` secrets are configured
- ⏭️ **Skip**: If either secret is missing (graceful skip, CI still passes)
- 🚫 **Fail**: If tests run but fail (e.g., bot doesn't respond, assertions fail)

**Integration Tests:**
- ✅ **Run**: Always (uses MockDiscordBot, doesn't require real Discord API)
- ⏭️ **Skip**: Only if commit message contains `[skip integration]`

---

## Test Coverage

### Generate Coverage Report

```bash
# HTML report
pytest --cov=src/zetherion_ai --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=src/zetherion_ai --cov-report=term-missing

# XML report (for CI)
pytest --cov=src/zetherion_ai --cov-report=xml
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
# View Zetherion AI logs
docker compose -p zetherion_ai-test logs zetherion_ai

# View Qdrant logs
docker compose -p zetherion_ai-test logs qdrant

# Follow logs in real-time
docker compose -p zetherion_ai-test logs -f
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

- `integration` - Service integration tests with Docker (MockDiscordBot)
- `discord_e2e` - True E2E tests with real Discord API
- `slow` - Tests that take >5 seconds
- Custom markers can be added in `pyproject.toml`

```bash
# Run only Discord E2E tests
pytest -m discord_e2e

# Run everything except Discord E2E
pytest -m "not discord_e2e"

# Run integration but not Discord E2E
pytest -m "integration and not discord_e2e"
```

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
pytest --cov=src/zetherion_ai --cov-report=html && open htmlcov/index.html
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
docker compose -p zetherion_ai-test down -v

# Nuclear option (removes ALL stopped containers)
docker system prune -a
```

---

## Writing New Tests

### Unit Test Template

```python
"""Tests for new feature."""

import pytest
from zetherion_ai.feature import NewFeature


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
