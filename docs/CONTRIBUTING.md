# Contributing to Zetherion AI

Thank you for your interest in contributing to Zetherion AI! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Code Style](#code-style)
- [Commit Messages](#commit-messages)
- [Pull Request Process](#pull-request-process)
- [Documentation](#documentation)

## Code of Conduct

### Our Standards

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members

### Unacceptable Behavior

- Harassment, trolling, or derogatory comments
- Publishing others' private information
- Other conduct which could reasonably be considered inappropriate

## Getting Started

### Prerequisites

- Python 3.12 or higher
- Docker Desktop (for Qdrant and optionally Ollama)
- Git
- A Discord bot token (see [README.md](README.md#setup-guide) for setup instructions)
- Gemini API key (free tier available)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/zetherion_ai.git
   cd zetherion_ai
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/jimtin/zetherion-ai.git
   ```
4. Create a new branch for your feature:
   ```bash
   git checkout -b feature/your-feature-name
   ```

### Set Up Development Environment

1. Create `.env` file:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. Run the startup script:
   ```bash
   ./start.sh
   ```
   This will:
   - Set up Python virtual environment
   - Install dependencies
   - Install pre-commit hooks
   - Start required services (Qdrant, Ollama if selected)

3. Verify setup:
   ```bash
   ./status.sh
   ```

## Development Workflow

### Branch Strategy

- `main` - Production-ready code
- `feature/*` - New features
- `fix/*` - Bug fixes
- `docs/*` - Documentation updates
- `test/*` - Test improvements

### Making Changes

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Make your changes**:
   - Write code following our [Code Style](#code-style)
   - Add tests for new functionality
   - Update documentation as needed

3. **Run tests**:
   ```bash
   # Unit tests
   pytest tests/ -m "not integration and not discord_e2e"

   # Integration tests
   pytest tests/integration/test_e2e.py -m integration

   # All tests with coverage
   pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=html
   ```

4. **Check code quality**:
   ```bash
   # Linting and formatting (auto-fixes)
   ruff check --fix .
   ruff format .

   # Type checking
   mypy src/zetherion_ai

   # Security scan
   bandit -r src/zetherion_ai
   ```

5. **Commit your changes**:
   ```bash
   git add .
   git commit -m "feat: Add amazing new feature"
   ```

   Pre-commit hooks will automatically run:
   - Ruff (linting and formatting)
   - Mypy (type checking)
   - Gitleaks (secret scanning)
   - Bandit (security scanning)

## Testing

### Test Coverage Standards

- New features must include tests
- Maintain or improve overall coverage (currently 87.58%)
- Aim for 85%+ coverage on new modules

### Writing Tests

1. **Unit Tests** (`tests/test_*.py`):
   ```python
   import pytest
   from zetherion_ai.module import YourClass

   @pytest.mark.asyncio
   async def test_your_feature():
       """Test description."""
       # Arrange
       instance = YourClass()

       # Act
       result = await instance.method()

       # Assert
       assert result == expected_value
   ```

2. **Integration Tests** (`tests/integration/test_e2e.py`):
   - Use `@pytest.mark.integration` marker
   - Test with real Docker services (Qdrant, Ollama)
   - Use MockDiscordBot to bypass Discord API

3. **Discord E2E Tests** (`tests/integration/test_discord_e2e.py`):
   - Use `@pytest.mark.discord_e2e` marker
   - Optional: requires test bot credentials
   - Test real Discord API interactions

### Running Tests

```bash
# Unit tests only (fast, ~24s)
pytest tests/ -m "not integration and not discord_e2e"

# Integration tests (~2 min)
pytest tests/integration/test_e2e.py -m integration

# Discord E2E tests (~1 min, requires test bot)
pytest tests/integration/test_discord_e2e.py -m discord_e2e

# All tests with coverage
pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai --cov-report=html

# Coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

## Code Style

### Python Style Guide

We follow **PEP 8** with the following specifics:

- **Line length**: 100 characters (enforced by Ruff)
- **Imports**: Use `isort` ordering (automated by Ruff)
- **Quotes**: Double quotes for strings
- **Type hints**: Required for all functions (enforced by mypy strict mode)
- **Docstrings**: Google style for all public functions/classes

### Example

```python
"""Module docstring describing the file."""

from typing import Any

from zetherion_ai.module import SomeClass


async def example_function(param: str, optional: int = 0) -> dict[str, Any]:
    """Brief description of what the function does.

    Args:
        param: Description of param.
        optional: Description of optional parameter.

    Returns:
        Dictionary containing the result.

    Raises:
        ValueError: If param is invalid.
    """
    if not param:
        raise ValueError("param must not be empty")

    return {"result": param, "count": optional}
```

### Automated Formatting

Ruff handles most formatting automatically:

```bash
# Auto-fix linting issues
ruff check --fix .

# Format code
ruff format .
```

## Commit Messages

### Format

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `style`: Code style changes (formatting, etc.)
- `chore`: Maintenance tasks
- `ci`: CI/CD changes

### Examples

```bash
# Simple feature
git commit -m "feat: add /status command to show bot statistics"

# Bug fix with scope
git commit -m "fix(router): handle timeout errors gracefully"

# Breaking change
git commit -m "feat!: change router interface to async-only

BREAKING CHANGE: RouterBackend.classify() is now async
Migration: Add 'await' to all classify() calls"

# Multi-line with body
git commit -m "test: improve Discord bot coverage to 89.92%

- Add /channels command tests (6 tests)
- Add message splitting edge cases (4 tests)
- Add agent not ready scenario (1 test)

Closes #123"
```

### Co-Authored Commits

If working with Claude Code or pair programming:

```bash
git commit -m "feat: implement new feature

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

## Pull Request Process

### Before Submitting

1. **Sync with upstream**:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run full test suite**:
   ```bash
   pytest tests/ -m "not discord_e2e" --cov=src/zetherion_ai
   ```

3. **Ensure all pre-commit hooks pass**:
   ```bash
   pre-commit run --all-files
   ```

4. **Update documentation** if needed

### Submitting a Pull Request

1. Push your branch:
   ```bash
   git push origin feature/your-feature-name
   ```

2. Go to GitHub and create a Pull Request

3. **PR Title**: Use conventional commit format
   ```
   feat: Add new feature description
   ```

4. **PR Description** should include:
   ```markdown
   ## Summary
   Brief description of what this PR does.

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

5. **Wait for CI/CD**: All checks must pass
   - Linting (Ruff)
   - Type checking (Mypy)
   - Security scan (Bandit)
   - Unit tests (Python 3.12 & 3.13)
   - Docker build
   - Integration tests

6. **Address review comments**: Make requested changes and push updates

7. **Squash commits** (if requested):
   ```bash
   git rebase -i HEAD~N  # N = number of commits
   ```

### PR Review Criteria

Your PR will be evaluated on:

- ✅ **Functionality**: Does it work as intended?
- ✅ **Tests**: Are there adequate tests? Do they pass?
- ✅ **Code Quality**: Follows style guide, no code smells
- ✅ **Documentation**: Updated docs, clear docstrings
- ✅ **Security**: No vulnerabilities introduced
- ✅ **Performance**: No significant performance degradation

## Documentation

### What to Document

1. **Code Documentation**:
   - Docstrings for all public functions/classes
   - Inline comments for complex logic
   - Type hints for all parameters and returns

2. **User Documentation**:
   - Update `README.md` for new features
   - Add to `docs/COMMANDS.md` for new commands
   - Update `docs/TROUBLESHOOTING.md` for common issues
   - Update `docs/FAQ.md` for frequently asked questions

3. **Developer Documentation**:
   - Update `docs/ARCHITECTURE.md` for structural changes
   - Update `docs/TESTING.md` for new test patterns
   - Update `docs/SECURITY.md` for security changes
   - Update `CHANGELOG.md` for user-facing changes

### Documentation Style

- Use Markdown for all documentation
- Include code examples
- Add screenshots for UI features
- Keep language clear and concise
- Use tables for comparisons
- Include links to related documentation

## Getting Help

- **Discord**: Join our [Discord server](https://discord.gg/your-server) (if available)
- **Issues**: Search [existing issues](https://github.com/jimtin/zetherion-ai/issues) or create a new one
- **Discussions**: Use [GitHub Discussions](https://github.com/jimtin/zetherion-ai/discussions) for questions

## Recognition

Contributors are recognized in:
- `CONTRIBUTORS.md` file
- Release notes
- GitHub contributors page

Thank you for contributing to Zetherion AI! 🎉
