# Development Guide

This guide provides in-depth information for developers working on Zetherion AI. For contribution guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Development Environment](#development-environment)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Testing Patterns](#testing-patterns)
- [Debugging](#debugging)
- [Performance Optimization](#performance-optimization)
- [Adding New Features](#adding-new-features)
- [Docker Development](#docker-development)
- [Common Tasks](#common-tasks)

---

## Architecture Overview

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Discord Bot                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Commands   │  │  Security    │  │  Message Handler │  │
│  │  (/channels │  │  - Allowlist │  │  - DM vs Mention │  │
│  │  /remember  │  │  - Rate Limit│  │  - Splitting     │  │
│  │  /summarize)│  │  - Injection │  │                  │  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      Agent Core                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Message Router (Factory Pattern)                   │   │
│  │  ┌──────────────────┐  ┌──────────────────────┐    │   │
│  │  │ Gemini Backend   │  │ Ollama Backend       │    │   │
│  │  │ - Classification │  │ - Classification     │    │   │
│  │  │ - Simple Queries │  │ - Simple Queries     │    │   │
│  │  └──────────────────┘  └──────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Response Generators                                │   │
│  │  ┌──────────────┐  ┌──────────────┐                │   │
│  │  │ Claude/OpenAI│  │ Gemini/Ollama│                │   │
│  │  │ (Complex)    │  │ (Simple)     │                │   │
│  │  └──────────────┘  └──────────────┘                │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Memory Manager                                     │   │
│  │  - Context Building                                 │   │
│  │  - Deduplication                                    │   │
│  │  - Retry Logic                                      │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   Memory Layer                              │
│  ┌───────────────────┐  ┌───────────────────────────────┐  │
│  │  Qdrant Vector DB │  │  Embeddings (Gemini)          │  │
│  │  - Async Client   │  │  - text-embedding-004         │  │
│  │  - Collections    │  │  - Parallel Batch Processing  │  │
│  │  - Semantic Search│  │  - 768-dimensional vectors    │  │
│  └───────────────────┘  └───────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Patterns

1. **Factory Pattern** - Router backend selection (Gemini vs Ollama)
2. **Strategy Pattern** - Pluggable LLM backends
3. **Repository Pattern** - Memory abstraction layer
4. **Singleton Pattern** - Configuration management with LRU cache
5. **Async/Await** - Non-blocking I/O throughout the stack

### Data Flow

```
User Message (Discord)
  ↓
Security Checks (Allowlist, Rate Limit, Injection Detection)
  ↓
Message Router (Intent Classification)
  ↓
┌─────────────────┬─────────────────┐
│ Simple Query    │ Complex Task    │
│ (Gemini/Ollama) │ (Claude/OpenAI) │
└─────────────────┴─────────────────┘
  ↓
Memory Search (Qdrant - Parallel Embeddings)
  ↓
Context Building (Retrieved Memory + Current Message)
  ↓
Response Generation (LLM API Call with Retry)
  ↓
Response Splitting (2000 char Discord limit)
  ↓
User Response (Discord)
```

---

## Development Environment

### Prerequisites

- **Python**: 3.12 or higher (tested on 3.12 and 3.13)
- **Docker Desktop**: Latest version (for Qdrant and Ollama)
- **Git**: 2.30+
- **Code Editor**: VSCode recommended (see `.vscode/settings.json`)

### Initial Setup

1. **Clone and set up remote**:
   ```bash
   git clone https://github.com/jimtin/zetherion-ai.git
   cd secureclaw
   git remote add upstream https://github.com/jimtin/zetherion-ai.git
   ```

2. **Create virtual environment**:
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate  # macOS/Linux
   # OR
   venv\Scripts\activate  # Windows
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

4. **Install pre-commit hooks**:
   ```bash
   pre-commit install
   pre-commit install --hook-type pre-push
   ```

5. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

6. **Verify setup**:
   ```bash
   python -c "from secureclaw.config import get_settings; print('Config loaded successfully')"
   pytest tests/ -m "not integration and not discord_e2e" --maxfail=1
   ```

### VSCode Configuration

Recommended extensions (see `.vscode/extensions.json`):
- Python (ms-python.python)
- Pylance (ms-python.vscode-pylance)
- Ruff (charliermarsh.ruff)
- Docker (ms-azuretools.vscode-docker)
- GitLens (eamodio.gitlens)

Settings (`.vscode/settings.json`):
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
  }
}
```

---

## Project Structure

```
secureclaw/
├── src/secureclaw/          # Main application code
│   ├── agent/               # Agent core logic
│   │   ├── core.py          # Agent class (message handling, retry)
│   │   ├── router.py        # Gemini router backend
│   │   ├── router_ollama.py # Ollama router backend
│   │   ├── router_factory.py# Router factory (backend selection)
│   │   └── router_base.py   # Abstract router interface
│   ├── discord/             # Discord bot
│   │   ├── bot.py           # Main bot class
│   │   ├── commands.py      # Slash commands
│   │   └── security.py      # Security controls
│   ├── memory/              # Vector memory
│   │   ├── embeddings.py    # Gemini embeddings
│   │   └── qdrant.py        # Qdrant client
│   ├── config.py            # Pydantic settings
│   ├── logging.py           # Structured logging setup
│   └── main.py              # Entry point
├── tests/                   # Test suite
│   ├── unit/                # Unit tests (fast)
│   │   ├── test_agent_core.py
│   │   ├── test_discord_bot.py
│   │   ├── test_security.py
│   │   └── ...
│   ├── integration/         # Integration tests (slow)
│   │   ├── test_e2e.py      # Full flow tests
│   │   └── test_discord_e2e.py  # Real Discord tests
│   ├── conftest.py          # Shared fixtures
│   └── test_*.py            # Module-specific tests
├── docs/                    # Documentation
│   ├── ARCHITECTURE.md      # System architecture
│   ├── SECURITY.md          # Security controls
│   ├── TESTING.md           # Testing guide
│   ├── TROUBLESHOOTING.md   # Common issues
│   ├── FAQ.md               # Frequently asked questions
│   └── COMMANDS.md          # Discord command reference
├── scripts/                 # Utility scripts
│   ├── assess-system.py     # Hardware assessment
│   └── increase-docker-memory.sh  # Docker memory tuning
├── memory/                  # Auto memory (persistent)
│   ├── MEMORY.md            # Project memory
│   └── phase5-plan.md       # Future roadmap
├── .github/                 # CI/CD workflows
│   ├── workflows/
│   │   ├── ci.yml           # Main CI pipeline
│   │   └── codeql.yml       # Security analysis
│   └── dependabot.yml       # Dependency updates
├── docker-compose.yml       # Production compose
├── docker-compose.dev.yml   # Development compose
├── Dockerfile               # Container image
├── start.sh                 # Startup script (498 lines)
├── stop.sh                  # Shutdown script
├── status.sh                # Status check script
├── pyproject.toml           # Python project config
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
├── .pre-commit-config.yaml  # Pre-commit hooks
├── .gitleaks.toml           # Secret scanning config
└── .env.example             # Environment template
```

### Module Responsibilities

| Module | Responsibility | Dependencies |
|--------|---------------|--------------|
| `agent/core.py` | Message handling, retry logic, dual generators | router, memory, LLM APIs |
| `agent/router.py` | Gemini classification and simple responses | Gemini API |
| `agent/router_ollama.py` | Ollama classification and simple responses | Ollama HTTP API |
| `agent/router_factory.py` | Backend selection and health checks | router, router_ollama |
| `discord/bot.py` | Discord event handling, commands | discord.py, agent |
| `discord/security.py` | Rate limiting, allowlist, injection detection | None (standalone) |
| `memory/embeddings.py` | Parallel batch embeddings | Gemini API |
| `memory/qdrant.py` | Vector storage and search | Qdrant |
| `config.py` | Environment configuration | Pydantic |
| `logging.py` | Structured logging setup | structlog |

---

## Development Workflow

### Feature Development Cycle

1. **Create feature branch**:
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Write failing tests first** (TDD):
   ```bash
   # Create test file
   touch tests/test_my_feature.py

   # Write test
   # Run tests (should fail)
   pytest tests/test_my_feature.py -v
   ```

3. **Implement feature**:
   ```bash
   # Create/modify source files
   # Run tests iteratively
   pytest tests/test_my_feature.py -v --maxfail=1
   ```

4. **Verify all tests pass**:
   ```bash
   pytest tests/ -m "not discord_e2e" --cov=src/secureclaw
   ```

5. **Check code quality**:
   ```bash
   ruff check --fix .
   ruff format .
   mypy src/secureclaw
   ```

6. **Commit with conventional format**:
   ```bash
   git add .
   git commit -m "feat: add amazing new feature"
   # Pre-commit hooks run automatically
   ```

7. **Push and create PR**:
   ```bash
   git push origin feature/my-new-feature
   # Create PR on GitHub
   ```

### Branch Strategy

- `main` - Production-ready code (protected)
- `feature/*` - New features
- `fix/*` - Bug fixes
- `docs/*` - Documentation updates
- `test/*` - Test improvements
- `refactor/*` - Code refactoring

### Pre-Commit Workflow

When you commit, the following hooks run automatically:

1. **File Checks** (100ms)
   - Trailing whitespace removal
   - End-of-file fixer
   - Merge conflict markers
   - Large file prevention

2. **Gitleaks** (1-2s)
   - Secret scanning (12 custom rules)
   - Zero false positives (tuned allowlists)

3. **Ruff Linting** (1-2s)
   - 600+ lint rules
   - Auto-fixes applied
   - Import sorting

4. **Ruff Formatting** (0.5s)
   - PEP 8 compliance
   - 100-char line length

5. **Mypy Type Checking** (3-5s)
   - Strict mode
   - All source files

6. **Bandit Security Scan** (2-3s)
   - Python security issues
   - OWASP top 10 checks

7. **Hadolint** (0.5s)
   - Dockerfile best practices

**Total pre-commit time**: ~10-15 seconds

### Pre-Push Workflow

Before pushing, additional checks run:

1. **Ruff** (full project)
2. **Mypy** (full project)
3. **Pytest** (all tests with coverage)

**Total pre-push time**: ~30-60 seconds

---

## Testing Patterns

### Test Organization

```
tests/
├── unit/                    # Fast, isolated tests (~24s)
│   ├── test_agent_core.py   # Agent logic
│   ├── test_discord_bot.py  # Discord bot
│   ├── test_security.py     # Security controls
│   └── ...
├── integration/             # Slow, full-stack tests (~2 min)
│   ├── test_e2e.py          # Gemini + Ollama flows
│   └── test_discord_e2e.py  # Real Discord API
└── conftest.py              # Shared fixtures
```

### Fixture Patterns

**Parametrized fixtures for multi-backend testing**:
```python
@pytest.fixture(params=["gemini", "ollama"])
def backend_type(request):
    """Test both Gemini and Ollama backends."""
    return request.param

def test_routing(backend_type):
    """This test runs twice (once for each backend)."""
    router = create_router(backend_type)
    # Test implementation
```

**Mock fixtures for unit tests**:
```python
@pytest.fixture
def mock_discord_interaction():
    """Mock Discord interaction for command testing."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=123456789, name="testuser")
    interaction.guild = MagicMock(id=987654321, name="Test Server")
    interaction.channel = MagicMock(id=111222333, name="test-channel")
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction
```

**Async fixtures with proper cleanup**:
```python
@pytest.fixture
async def qdrant_client():
    """Async Qdrant client with cleanup."""
    client = AsyncQdrantClient(url="http://localhost:6333")
    yield client
    await client.close()
```

### Testing Async Code

**Basic async test**:
```python
@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await some_async_function()
    assert result == expected_value
```

**Testing with asyncio.gather**:
```python
@pytest.mark.asyncio
async def test_parallel_execution():
    """Test parallel async operations."""
    tasks = [async_function(i) for i in range(10)]
    results = await asyncio.gather(*tasks)
    assert len(results) == 10
```

**Testing timeouts**:
```python
@pytest.mark.asyncio
async def test_timeout_handling():
    """Test timeout scenarios."""
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(slow_function(), timeout=1.0)
```

### Mocking Patterns

**Patching with context managers**:
```python
from unittest.mock import patch, AsyncMock

def test_with_mock():
    """Test with mocked dependency."""
    with patch("secureclaw.agent.router.gemini") as mock_gemini:
        mock_gemini.generate_content.return_value = AsyncMock(
            text='{"intent": "simple_query", "confidence": 0.9}'
        )
        router = MessageRouter()
        result = await router.classify("Hello")
        assert result.intent == "simple_query"
```

**Patching multiple targets**:
```python
@patch("secureclaw.memory.embeddings.genai")
@patch("secureclaw.memory.qdrant.AsyncQdrantClient")
async def test_with_multiple_mocks(mock_qdrant, mock_genai):
    """Test with multiple mocked dependencies."""
    # Configure mocks
    mock_genai.embed_content_async.return_value = MagicMock(
        embeddings=[MagicMock(values=[0.1] * 768)]
    )
    # Test implementation
```

### Coverage Best Practices

1. **Aim for 85%+ coverage** on new modules
2. **Maintain overall 85%+ coverage** across the project
3. **Focus on critical paths** (security, core logic)
4. **Avoid over-testing** trivial code (getters, simple properties)
5. **Test edge cases** (empty inputs, None values, errors)

**Generate coverage report**:
```bash
# HTML report
pytest tests/ -m "not discord_e2e" --cov=src/secureclaw --cov-report=html

# Terminal summary
pytest tests/ -m "not discord_e2e" --cov=src/secureclaw --cov-report=term

# XML for CI
pytest tests/ -m "not discord_e2e" --cov=src/secureclaw --cov-report=xml
```

---

## Debugging

### Logging Levels

```python
import structlog

log = structlog.get_logger(__name__)

# Development
log.debug("Detailed debug info", variable=value)
log.info("Normal operation", event="message_received")

# Production
log.warning("Potential issue", error=str(e))
log.error("Error occurred", exc_info=True)
log.critical("System failure", reason="Out of memory")
```

### Debug Configuration

**Enable debug logging**:
```bash
# .env
LOG_LEVEL=DEBUG
ENVIRONMENT=development
```

**Console output (development)**:
- Colored output with `structlog.dev.ConsoleRenderer`
- Pretty-printed key-value pairs
- Timestamp and level

**File output (production)**:
- JSON format for parsing with `jq`
- Rotating log files (10MB × 6 files)
- Located in `logs/secureclaw.log`

### Common Debugging Tasks

**1. Debug Discord bot interactions**:
```bash
# Watch logs in real-time
tail -f logs/secureclaw.log | jq 'select(.event | contains("discord"))'
```

**2. Debug router decisions**:
```bash
# Filter routing events
jq 'select(.event == "message_routed")' logs/secureclaw.log
```

**3. Debug LLM API calls**:
```bash
# Set LOG_LEVEL=DEBUG
# Check logs for API request/response
jq 'select(.event | contains("api"))' logs/secureclaw.log
```

**4. Debug memory searches**:
```bash
# Check embedding and search operations
jq 'select(.event | contains("memory") or contains("embedding"))' logs/secureclaw.log
```

**5. Interactive debugging with pdb**:
```python
import pdb; pdb.set_trace()  # Add breakpoint
```

**6. Pytest debugging**:
```bash
# Drop into pdb on first failure
pytest tests/ --pdb

# Show print statements
pytest tests/ -s

# Verbose output
pytest tests/ -vv

# Show local variables on failure
pytest tests/ -l
```

### Docker Debugging

**Check container logs**:
```bash
docker compose logs secureclaw -f
docker compose logs qdrant -f
docker compose logs ollama -f
```

**Exec into containers**:
```bash
# Zetherion AI container
docker exec -it secureclaw-bot bash

# Qdrant container
docker exec -it secureclaw-qdrant bash

# Ollama container
docker exec -it secureclaw-ollama bash
```

**Check container health**:
```bash
docker compose ps
docker inspect secureclaw-bot | jq '.[0].State.Health'
```

**Network debugging**:
```bash
# Check network connectivity
docker exec secureclaw-bot ping qdrant
docker exec secureclaw-bot ping ollama

# Check port availability
docker exec secureclaw-bot nc -zv qdrant 6333
docker exec secureclaw-bot nc -zv ollama 11434
```

---

## Performance Optimization

### Critical Optimizations Applied

1. **Async Qdrant Client** - Prevents event loop blocking
   ```python
   # ❌ Bad (blocks event loop)
   from qdrant_client import QdrantClient
   client = QdrantClient(url="http://qdrant:6333")

   # ✅ Good (non-blocking)
   from qdrant_client import AsyncQdrantClient
   client = AsyncQdrantClient(url="http://qdrant:6333")
   ```

2. **Parallel Embeddings** - 10x faster for batches
   ```python
   # ❌ Bad (sequential)
   embeddings = [await embed_text(text) for text in texts]

   # ✅ Good (parallel)
   tasks = [embed_text(text) for text in texts]
   embeddings = await asyncio.gather(*tasks)
   ```

3. **Deduplicated Memory Searches** - 50% fewer API calls
   ```python
   # ❌ Bad (searches twice)
   context_claude = await search_memory(message)
   context_gemini = await search_memory(message)

   # ✅ Good (searches once)
   context = await search_memory(message)
   response_claude = await generate_claude(message, context)
   response_gemini = await generate_gemini(message, context)
   ```

4. **Retry with Exponential Backoff** - Handles transient failures
   ```python
   retries = 0
   while retries < max_retries:
       try:
           return await api_call()
       except TransientError:
           await asyncio.sleep(2 ** retries)
           retries += 1
   ```

### Performance Monitoring

**Measure async operation times**:
```python
import time
import structlog

log = structlog.get_logger(__name__)

async def timed_operation(name: str):
    start = time.time()
    result = await some_async_function()
    elapsed = time.time() - start
    log.info(f"{name} completed", duration_ms=elapsed * 1000)
    return result
```

**Profile with cProfile**:
```bash
python -m cProfile -o output.prof src/secureclaw/main.py
python -m pstats output.prof
# (Pstats) sort cumulative
# (Pstats) stats 20
```

**Async profiling with yappi**:
```bash
pip install yappi
python -m yappi --clock wall src/secureclaw/main.py
```

---

## Adding New Features

### Example: Adding a New Discord Command

1. **Define command in `discord/commands.py`**:
   ```python
   @app_commands.command(name="status", description="Show bot statistics")
   async def status_command(interaction: discord.Interaction) -> None:
       """Handle /status command."""
       await interaction.response.defer()

       stats = {
           "uptime": get_uptime(),
           "messages_processed": message_count,
           "memory_entries": await get_memory_count(),
       }

       response = f"**Bot Statistics**\n"
       response += f"Uptime: {stats['uptime']}\n"
       response += f"Messages: {stats['messages_processed']}\n"
       response += f"Memory Entries: {stats['memory_entries']}"

       await interaction.followup.send(response)
   ```

2. **Register command in `discord/bot.py`**:
   ```python
   async def _setup_commands(self) -> None:
       """Set up slash commands."""
       self.tree.add_command(commands.status_command)
       await self.tree.sync()
   ```

3. **Write tests in `tests/unit/test_discord_commands.py`**:
   ```python
   @pytest.mark.asyncio
   async def test_status_command(mock_interaction):
       """Test /status command."""
       await status_command(mock_interaction)

       mock_interaction.response.defer.assert_called_once()
       mock_interaction.followup.send.assert_called_once()
       response = mock_interaction.followup.send.call_args[0][0]
       assert "Bot Statistics" in response
       assert "Uptime:" in response
   ```

4. **Update documentation**:
   - Add command to `docs/COMMANDS.md`
   - Update README.md if user-facing
   - Add to CHANGELOG.md

### Example: Adding a New Router Backend

1. **Create backend implementation**:
   ```python
   # src/secureclaw/agent/router_newbackend.py
   import structlog
   from secureclaw.agent.router_base import RoutingDecision

   log = structlog.get_logger(__name__)

   class NewBackendRouter:
       """Router backend using NewBackend API."""

       async def classify(self, message: str) -> RoutingDecision:
           """Classify message intent."""
           # Implementation

       async def generate_simple_response(self, message: str) -> str:
           """Generate simple response."""
           # Implementation

       async def health_check(self) -> bool:
           """Check if backend is healthy."""
           # Implementation
   ```

2. **Update factory**:
   ```python
   # src/secureclaw/agent/router_factory.py
   from secureclaw.agent.router_newbackend import NewBackendRouter

   def create_router() -> MessageRouter:
       settings = get_settings()

       if settings.router_backend == "newbackend":
           return MessageRouter(NewBackendRouter())
       # ... existing backends
   ```

3. **Add configuration**:
   ```python
   # src/secureclaw/config.py
   router_backend: str = Field(
       default="gemini",
       description="Router backend: 'gemini', 'ollama', or 'newbackend'"
   )

   @field_validator("router_backend")
   @classmethod
   def validate_router_backend(cls, v: str) -> str:
       valid = ["gemini", "ollama", "newbackend"]
       if v not in valid:
           raise ValueError(f"router_backend must be one of {valid}")
       return v
   ```

4. **Write comprehensive tests**:
   ```python
   # tests/test_router_newbackend.py
   @pytest.mark.asyncio
   async def test_newbackend_classify():
       """Test NewBackend classification."""
       # Test implementation
   ```

---

## Docker Development

### Development vs Production Compose

**Development** (`docker-compose.dev.yml`):
- Hot-reload for code changes
- Volume mounts for local development
- Exposed ports for debugging

**Production** (`docker-compose.yml`):
- Optimized image layers
- Health checks
- Restart policies
- Resource limits (optional)

### Common Docker Tasks

**Start development environment**:
```bash
docker compose -f docker-compose.dev.yml up -d
```

**Rebuild after dependency changes**:
```bash
docker compose build --no-cache
docker compose up -d
```

**View logs**:
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs secureclaw -f

# Last 100 lines
docker compose logs --tail=100 secureclaw
```

**Restart single service**:
```bash
docker compose restart secureclaw
```

**Clean up everything**:
```bash
docker compose down -v  # Removes volumes (data loss!)
docker system prune -a  # Removes all unused images
```

### Dockerfile Best Practices

Current optimizations:
- Multi-stage builds (not yet implemented, see Future)
- Slim base image (`python:3.12-slim`)
- Layer caching (dependencies before code)
- Non-root user (not yet implemented, see Gap Analysis)
- Health checks

### Docker Memory Management

Check Docker memory:
```bash
docker stats
```

Increase Docker memory (macOS):
```bash
./scripts/increase-docker-memory.sh 8  # 8GB
```

Monitor Ollama memory usage:
```bash
docker stats secureclaw-ollama
```

---

## Common Tasks

### Update Model Versions

1. **Check latest models**:
   - Claude: https://docs.anthropic.com/en/docs/about-claude/models
   - OpenAI: https://platform.openai.com/docs/models
   - Gemini: https://ai.google.dev/gemini-api/docs/models

2. **Update `src/secureclaw/config.py`**:
   ```python
   claude_model: str = Field(default="claude-sonnet-4-5-20250929")
   openai_model: str = Field(default="gpt-4o")
   router_model: str = Field(default="gemini-2.5-flash")
   ```

3. **Test new models**:
   ```bash
   pytest tests/integration/test_e2e.py -v
   ```

4. **Update documentation** (comment in config.py with date)

### Update Dependencies

**Check for updates**:
```bash
pip list --outdated
```

**Update specific package**:
```bash
pip install --upgrade package-name
pip freeze | grep package-name >> requirements.txt
```

**Update all dev dependencies**:
```bash
pip install --upgrade -r requirements-dev.txt
pip freeze > requirements-dev.txt
```

**Test after updates**:
```bash
pytest tests/ -m "not discord_e2e"
```

### Run Security Scans

**Gitleaks (secret scanning)**:
```bash
pre-commit run gitleaks --all-files
```

**Bandit (Python security)**:
```bash
bandit -r src/secureclaw -ll
```

**Dependency vulnerabilities** (not yet implemented):
```bash
pip install pip-audit
pip-audit
```

### Generate Documentation

**API documentation with Sphinx** (not yet implemented):
```bash
pip install sphinx sphinx-rtd-theme
sphinx-quickstart docs/api
sphinx-build -b html docs/api docs/api/_build
```

**Coverage badge**:
```bash
pytest tests/ --cov=src/secureclaw --cov-report=term
# Update badge URL in README.md
```

---

## Troubleshooting

### Common Development Issues

**Issue: Pre-commit hooks fail**
```bash
# Fix Ruff issues
ruff check --fix .
ruff format .

# Fix Mypy issues
mypy src/secureclaw --show-error-codes

# Skip hooks (emergency only)
git commit --no-verify
```

**Issue: Tests fail with import errors**
```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall in editable mode
pip install -e .
```

**Issue: Docker services won't start**
```bash
# Check Docker daemon
docker info

# Check ports are free
lsof -i :6333  # Qdrant
lsof -i :11434  # Ollama

# Restart Docker Desktop
# macOS: osascript -e 'quit app "Docker"' && open -a Docker
```

**Issue: Ollama model not found**
```bash
docker exec secureclaw-ollama ollama list
docker exec secureclaw-ollama ollama pull llama3.1:8b
```

**Issue: Qdrant connection refused**
```bash
# Check Qdrant is running
docker ps | grep qdrant

# Check health
curl http://localhost:6333/health

# Restart Qdrant
docker compose restart qdrant
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines.

**Quick checklist before submitting PR**:
- [ ] All tests pass (`pytest tests/ -m "not discord_e2e"`)
- [ ] Coverage maintained or improved
- [ ] Pre-commit hooks pass
- [ ] Documentation updated
- [ ] Conventional commit messages
- [ ] CHANGELOG.md updated

---

## Additional Resources

- [Architecture Documentation](docs/ARCHITECTURE.md)
- [Security Documentation](docs/SECURITY.md)
- [Testing Guide](docs/TESTING.md)
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
- [FAQ](docs/FAQ.md)
- [Discord Commands Reference](docs/COMMANDS.md)

---

## Contact

- **Issues**: https://github.com/jimtin/zetherion-ai/issues
- **Discussions**: https://github.com/jimtin/zetherion-ai/discussions
- **Email**: [Your email if public]
