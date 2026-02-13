# Setup and Contributing

This guide covers everything you need to start developing Zetherion AI: environment setup, project structure, workflow conventions, and the pull request process. It consolidates information previously spread across multiple documents into one reference.

For the overall system architecture, see [`../technical/architecture.md`](../technical/architecture.md).

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | Tested on 3.12 and 3.13 |
| Docker Desktop | Latest | Required for Qdrant, Ollama, PostgreSQL |
| Git | 2.30+ | Conventional Commits enforced |
| Code Editor | -- | VSCode recommended (config included) |

**API keys needed:**

- Discord Bot Token (required -- first supported input interface)
- Gemini API Key (optional, free tier available -- enables cloud routing and simple queries)
- Anthropic API Key (optional, for Claude `claude-sonnet-4-5-20250929`)
- OpenAI API Key (optional, for `gpt-5.2`)

All cloud LLM providers are optional. Ollama provides fully local inference out of the box.

---

## Quick Start

### macOS / Linux

```bash
# 1. Clone the repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai

# 2. Create a virtual environment
python3.12 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 4. Install pre-commit and pre-push hooks
pre-commit install
pre-commit install --hook-type pre-push

# 5. Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# 6. Verify setup
python -c "from zetherion_ai.config import get_settings; print('Config loaded successfully')"
pytest tests/ -m "not integration and not discord_e2e" --maxfail=1
```

### Windows

Windows deployment uses a fully automated PowerShell script that handles Docker Desktop, Git, hardware detection, and environment configuration in a single command.

**Requirements**: Administrator PowerShell (press `Win + X`, select "Terminal (Admin)").

```powershell
# 1. Clone the repository
git clone https://github.com/jimtin/zetherion-ai.git
cd zetherion-ai

# 2. Run automated deployment
.\start.ps1
```

The `start.ps1` script performs the following automatically:

1. **Prerequisites check** -- installs Docker Desktop and Git via `winget` if not present
2. **Hardware assessment** -- detects CPU, RAM, GPU and recommends an Ollama model
3. **Configuration wizard** -- interactive `.env` setup if no config file exists
4. **Docker build and deploy** -- builds containers, starts 6 Docker services, waits for health checks
5. **Model download** -- pulls the recommended Ollama model if the Ollama backend is selected
6. **Verification** -- tests Qdrant and Ollama connectivity, displays container status

First run takes approximately 3--9 minutes depending on the backend choice. Subsequent runs take roughly 30 seconds.

**Windows management commands**:

```powershell
.\status.ps1          # Service health and container summary
.\stop.ps1            # Graceful shutdown (data preserved)
.\cleanup.ps1         # Full removal (prompts for confirmation)
.\cleanup.ps1 -KeepData   # Remove containers but keep volumes
```

**Troubleshooting WSL2**: If Docker Desktop fails to start, ensure WSL 2 is installed and up to date:

```powershell
wsl --status
wsl --update
```

If the execution policy blocks the script, allow scripts for the current session:

```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
.\start.ps1
```

---

## Project Structure

```
src/zetherion_ai/                   # Core source tree
    agent/                          # Agent core, routing, inference
        core.py                     # Message handling, retry logic, dual generators
        router.py                   # Gemini router backend
        router_ollama.py            # Ollama router backend
        router_factory.py           # Router backend selection, health checks
        router_base.py              # Abstract router interface
        inference.py                # Inference broker
        providers.py                # LLM provider abstraction
        prompts.py                  # System prompt construction
    discord/                        # Discord bot
        bot.py                      # Main bot class, event handling
        security.py                 # Allowlist, rate limiting, injection detection
        user_manager.py             # RBAC user management
    memory/                         # Vector memory layer
        qdrant.py                   # Async Qdrant client
        embeddings.py               # Gemini embeddings (768-dim, parallel batch)
    security/                       # Encryption and key management
        encryption.py               # AES-256-GCM at-rest encryption
        keys.py                     # Key derivation and rotation
    skills/                         # Skills framework
        base.py                     # Skill abstract base class
        permissions.py              # Permission enum and PermissionSet
        registry.py                 # Intent-based skill routing
        server.py                   # REST API server (aiohttp)
        client.py                   # HTTP client for bot-to-skills communication
        task_manager.py             # Task management skill
        calendar.py                 # Calendar awareness skill
        profile_skill.py            # Profile management skill
        personal_model.py           # Personal model skill
        gmail/                      # Gmail integration (13 files)
            skill.py, client.py, auth.py, accounts.py,
            inbox.py, sync.py, digest.py, replies.py,
            analytics.py, trust.py, conflicts.py,
            calendar_sync.py
        github/                     # GitHub integration
            skill.py, client.py, models.py
    personal/                       # Personal understanding (PostgreSQL)
        models.py, actions.py, context.py, storage.py
    profile/                        # User profile engine
        models.py, builder.py, storage.py, cache.py,
        inference.py, relationship.py, employment.py
    observation/                    # Observation pipeline
        pipeline.py, dispatcher.py, extractors.py, models.py
        adapters/                   # Platform-specific adapters
            discord.py, gmail.py
    costs/                          # Cost tracking
        tracker.py, aggregator.py, reports.py, storage.py
    models/                         # Model registry
        registry.py, discovery.py, pricing.py, tiers.py
    notifications/                  # Notification system
        dispatcher.py, discord.py
    scheduler/                      # Scheduler
        heartbeat.py, actions.py
    config.py                       # Pydantic settings
    logging.py                      # Structured logging (structlog)
    main.py                         # Application entry point
    constants.py                    # Project-wide constants
    utils.py                        # Shared utilities
    settings_manager.py             # Runtime settings CRUD
tests/                              # 3,000+ tests, >=90% coverage gate
    unit/                           # Fast, isolated tests (~24s)
    integration/                    # Full-stack tests (~2 min)
    conftest.py                     # Shared fixtures
docs/                               # Documentation (MkDocs)
scripts/                            # Utility scripts
```

### LLM Models

| Role | Model | Provider |
|------|-------|----------|
| Complex generation | `claude-sonnet-4-5-20250929` | Anthropic |
| Complex generation | `gpt-5.2` | OpenAI |
| Router / simple queries | `gemini-2.5-flash` | Google |
| Local router | `llama3.2:3b` | Ollama |
| Local generation | `llama3.1:8b` | Ollama |

### Docker Services

The production `docker-compose.yml` defines 6 services:

| Service | Purpose |
|---------|---------|
| `zetherion-ai-bot` | Agent core -- input gateway, security, routing, inference |
| `zetherion-ai-skills` | Skills REST API server |
| `ollama` | Local LLM inference (generation) |
| `ollama-router` | Local LLM inference (routing) |
| `postgres` | PostgreSQL for structured data |
| `qdrant` | Vector database for semantic memory |

---

## Development Workflow

### Branch Strategy

| Prefix | Purpose |
|--------|---------|
| `main` | Production-ready code (protected) |
| `feature/*` | New features |
| `fix/*` | Bug fixes |
| `docs/*` | Documentation updates |
| `test/*` | Test improvements |
| `refactor/*` | Code restructuring |

### TDD Cycle

Zetherion AI follows test-driven development. The expected workflow is:

1. Create a feature branch from `main`
2. Write failing tests that define the expected behavior
3. Implement the feature until the tests pass
4. Refactor if needed while keeping tests green
5. Verify the full suite still passes

```bash
# Write test first
pytest tests/test_my_feature.py -v          # Should fail

# Implement feature, iterate
pytest tests/test_my_feature.py -v --maxfail=1

# Full suite before committing
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai
```

### Pre-Commit Hooks (7 steps, ~10--15s)

When you run `git commit`, the following hooks execute automatically:

| Step | Tool | Time | What it does |
|------|------|------|-------------|
| 1 | pre-commit-hooks | ~100ms | Trailing whitespace, end-of-file fixer, merge conflict markers, large file prevention, case conflict check, YAML/TOML/JSON syntax, private key detection |
| 2 | Gitleaks | 1--2s | Secret scanning with 12 custom rules and tuned allowlists |
| 3 | Ruff (lint) | 1--2s | 600+ lint rules with auto-fix, import sorting |
| 4 | Ruff (format) | ~0.5s | PEP 8 formatting, 100-char line length |
| 5 | mypy | 3--5s | Strict type checking on all source files |
| 6 | Bandit | 2--3s | Python security scanning (OWASP top 10) |
| 7 | Hadolint | ~0.5s | Dockerfile best practices |

### Pre-Push Hooks (3 steps, ~30--60s)

Before pushing, additional checks run:

1. **Ruff** -- full project lint and format check
2. **mypy** -- full project type check
3. **pytest** -- entire test suite with coverage

If you need to skip hooks in an emergency (not recommended):

```bash
git commit --no-verify
```

---

## Code Style

### Rules

- **Standard**: PEP 8, enforced by Ruff
- **Line length**: 100 characters
- **Quotes**: Double quotes for all strings
- **Docstrings**: Google style for all public functions and classes
- **Type hints**: Required everywhere (mypy strict mode)
- **Imports**: Sorted by Ruff (isort-compatible)

### Example

```python
"""Module docstring describing the file's purpose."""

from typing import Any

import structlog

from zetherion_ai.config import get_settings

log = structlog.get_logger(__name__)


async def process_message(
    message: str,
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process an incoming user message and return a response.

    Args:
        message: The raw message text from the user.
        user_id: Discord user ID as a string.
        context: Optional additional context for routing.

    Returns:
        Dictionary containing the response text and metadata.

    Raises:
        ValueError: If the message is empty.
    """
    if not message:
        raise ValueError("message must not be empty")

    settings = get_settings()
    log.info("processing_message", user_id=user_id, length=len(message))

    return {"response": message, "model": settings.claude_model}
```

### Automated Formatting

Ruff handles most formatting automatically. Run it manually before committing if you want to preview changes:

```bash
ruff check --fix .
ruff format .
```

---

## Testing

Zetherion AI maintains 3,000+ tests with an enforced overall coverage gate of `>=90%`.

For the full testing guide, patterns, fixtures, and mocking strategies, see [Testing](testing.md).

### Key Commands

```bash
# Unit tests only (fast, ~24s)
pytest tests/ -m "not integration and not discord_e2e"

# Production-parity validation (recommended before merge)
bash scripts/pre-push-tests.sh

# All tests except Discord E2E (includes integration)
pytest tests/ -m "not discord_e2e"

# Full suite with coverage
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=term

# HTML coverage report
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=html
open htmlcov/index.html  # macOS

# Run a single test file
pytest tests/unit/test_agent_core.py -v

# Drop into debugger on first failure
pytest tests/ --pdb --maxfail=1

# Show print output and local variables on failure
pytest tests/ -s -l
```

### Coverage Standards

- New features must include tests
- Aim for 85%+ coverage on new modules
- Maintain overall >=90% coverage across the project
- Focus coverage on critical paths: security, routing, core agent logic
- Avoid over-testing trivial code (simple getters, dataclass properties)

---

## Debugging

### Logging Levels

Zetherion AI uses `structlog` for structured logging. All modules should use the project logger:

```python
from zetherion_ai.logging import get_logger

log = get_logger(__name__)

# Usage
log.debug("detailed_debug_info", variable=value)
log.info("normal_operation", event="message_received")
log.warning("potential_issue", error=str(e))
log.error("error_occurred", exc_info=True)
log.critical("system_failure", reason="Out of memory")
```

### Debug Configuration

Enable debug logging in your `.env`:

```bash
LOG_LEVEL=DEBUG
ENVIRONMENT=development
```

In development, structlog renders colored console output with pretty-printed key-value pairs. In production, it writes JSON to rotating log files (10MB per file, 6 files) at `logs/zetherion_ai.log`.

### Docker Debugging

```bash
# Container logs (follow mode)
docker compose logs zetherion-ai-bot -f
docker compose logs qdrant -f
docker compose logs ollama -f

# Exec into a running container
docker exec -it zetherion-ai-bot bash
docker exec -it zetherion-ai-qdrant bash

# Container health status
docker compose ps
docker inspect zetherion-ai-bot | jq '.[0].State.Health'

# Network connectivity
docker exec zetherion-ai-bot ping qdrant
docker exec zetherion-ai-bot nc -zv qdrant 6333
docker exec zetherion-ai-bot nc -zv ollama 11434

# Resource usage
docker stats
```

### Common Debugging Tasks

**Filter logs by subsystem** (production JSON format):

```bash
# Routing decisions
jq 'select(.event == "message_routed")' logs/zetherion_ai.log

# Memory and embedding operations
jq 'select(.event | contains("memory") or contains("embedding"))' logs/zetherion_ai.log

# Discord events
tail -f logs/zetherion_ai.log | jq 'select(.event | contains("discord"))'

# LLM API calls (requires LOG_LEVEL=DEBUG)
jq 'select(.event | contains("api"))' logs/zetherion_ai.log
```

**Interactive debugging with pytest**:

```bash
pytest tests/ --pdb            # Drop into pdb on first failure
pytest tests/ -s               # Show print statements
pytest tests/ -vv              # Extra verbose output
pytest tests/ -l               # Show local variables on failure
```

**Ollama model issues**:

```bash
docker exec zetherion-ai-ollama ollama list
docker exec zetherion-ai-ollama ollama pull llama3.1:8b
docker exec zetherion-ai-ollama-router ollama pull llama3.2:3b
```

---

## Commit Messages

### Format

Zetherion AI follows the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `test` | Adding or updating tests |
| `refactor` | Code restructuring (no behavior change) |
| `perf` | Performance improvements |
| `style` | Formatting, whitespace (no logic change) |
| `chore` | Maintenance tasks, dependency updates |
| `ci` | CI/CD pipeline changes |
| `security` | Security improvements |

### Examples

```bash
# Simple feature
git commit -m "feat: add /status command to show bot statistics"

# Bug fix with scope
git commit -m "fix(router): handle timeout errors gracefully"

# Breaking change
git commit -m "feat!: change router interface to async-only

BREAKING CHANGE: RouterBackend.classify() is now async.
Migration: add 'await' to all classify() calls."

# Multi-line with body
git commit -m "test: improve Discord bot coverage while keeping gate >=90%

- Add /channels command tests (6 tests)
- Add message splitting edge cases (4 tests)
- Add agent not ready scenario (1 test)

Closes #123"
```

### Co-Authored Commits

When working with AI-assisted tools:

```bash
git commit -m "feat: implement weather skill

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Pull Request Process

### Before Submitting

Run through this checklist:

- [ ] All tests pass: `pytest tests/ -m "not discord_e2e"`
- [ ] Coverage maintained at >=90% (or improved)
- [ ] Pre-commit hooks pass: `pre-commit run --all-files`
- [ ] Type checking passes: `mypy src/zetherion_ai`
- [ ] Code formatted: `ruff check --fix . && ruff format .`
- [ ] Documentation updated for any user-facing changes
- [ ] Conventional commit messages used throughout the branch

Sync your branch with upstream before opening the PR:

```bash
git fetch upstream
git rebase upstream/main
```

### PR Description Template

```markdown
## Summary
Brief description of what this PR does and why.

## Changes
- Added X feature
- Fixed Y bug
- Updated Z documentation

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual testing completed

## Screenshots (if UI changes)
[Add screenshots if applicable]

## Related Issues
Closes #123
Fixes #456
```

### Review Criteria

Pull requests are evaluated on:

- **Functionality** -- Does it work as intended?
- **Tests** -- Are there adequate tests? Do they pass?
- **Code quality** -- Follows the style guide, no code smells
- **Documentation** -- Updated docs, clear docstrings
- **Security** -- No vulnerabilities introduced, secrets scanning clean
- **Performance** -- No significant performance degradation

### CI Pipeline

All of the following must pass before merge:

1. Ruff linting and formatting
2. mypy type checking
3. Bandit security scan
4. Unit tests (Python 3.12 and 3.13)
5. Docker build
6. Integration tests

---

## Contributing Guidelines

### Code of Conduct

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members
- No harassment, trolling, or derogatory comments
- Do not publish others' private information

### Fork and Clone Workflow

If you are an external contributor:

```bash
# 1. Fork the repository on GitHub

# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/zetherion-ai.git
cd zetherion-ai

# 3. Add upstream remote
git remote add upstream https://github.com/jimtin/zetherion-ai.git

# 4. Create a feature branch
git checkout -b feature/your-feature-name

# 5. Follow the Quick Start instructions above to set up your environment

# 6. Make changes, commit, push to your fork
git push origin feature/your-feature-name

# 7. Open a Pull Request on GitHub against upstream/main
```

### Getting Help

- **Issues**: [github.com/jimtin/zetherion-ai/issues](https://github.com/jimtin/zetherion-ai/issues) -- bug reports and feature requests
- **Discussions**: [github.com/jimtin/zetherion-ai/discussions](https://github.com/jimtin/zetherion-ai/discussions) -- questions and general discussion

When reporting issues, include:

- Your OS and Python version
- Docker Desktop version (`docker --version`)
- The output of `docker compose ps`
- Relevant error messages or log output

---

## VSCode Configuration

### Recommended Extensions

These are defined in `.vscode/extensions.json`:

- **Python** (`ms-python.python`) -- language support
- **Pylance** (`ms-python.vscode-pylance`) -- type checking and IntelliSense
- **Ruff** (`charliermarsh.ruff`) -- linting and formatting
- **Docker** (`ms-azuretools.vscode-docker`) -- container management
- **GitLens** (`eamodio.gitlens`) -- git blame and history

### Settings

Add the following to `.vscode/settings.json` (or use the provided config):

```json
{
  "python.linting.enabled": true,
  "python.linting.mypyEnabled": true,
  "python.formatting.provider": "none",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll": "explicit",
      "source.organizeImports": "explicit"
    }
  },
  "editor.rulers": [100],
  "files.trimTrailingWhitespace": true,
  "files.insertFinalNewline": true
}
```
