# CI/CD Pipeline Documentation

Complete guide to Zetherion AI's testing and continuous integration setup.

## Table of Contents

1. [Overview](#overview)
2. [Local Development Workflow](#local-development-workflow)
3. [Git Hooks Setup](#git-hooks-setup)
4. [Pre-Commit Hooks](#pre-commit-hooks)
5. [Pre-Push Hooks](#pre-push-hooks)
6. [GitHub Actions CI/CD](#github-actions-cicd)
7. [Running Tests Manually](#running-tests-manually)
8. [Troubleshooting](#troubleshooting)

---

## Overview

Zetherion AI uses a **three-tier testing approach** to ensure code quality:

```
┌─────────────────────────────────────────────────────────────┐
│  Tier 1: Pre-Commit Hooks (Lightweight)                     │
│  • Runs before each commit                                  │
│  • Linting (Ruff)                                           │
│  • Formatting (Ruff Format)                                 │
│  • File checks (trailing whitespace, large files, etc.)     │
│  • Security scan (Bandit)                                   │
│  Duration: ~5-10 seconds                                    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Tier 2: Pre-Push Hooks (Comprehensive)                     │
│  • Runs before each push to remote                          │
│  • Full linting                                             │
│  • Type checking (mypy)                                     │
│  • Complete test suite with coverage                        │
│  Duration: ~30-60 seconds                                   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Tier 3: GitHub Actions CI/CD (Exhaustive)                  │
│  • Runs on push/PR to main/develop                          │
│  • Multi-version testing (Python 3.12, 3.13)               │
│  • Integration tests with services (Qdrant)                 │
│  • Docker build validation                                  │
│  • Security scanning                                        │
│  • Coverage reporting (Codecov)                             │
│  Duration: ~5-10 minutes                                    │
└─────────────────────────────────────────────────────────────┘
```

**Philosophy:**
- **Fast feedback locally** (pre-commit catches simple issues in seconds)
- **Confidence before push** (pre-push ensures tests pass)
- **Comprehensive CI** (GitHub Actions catches integration issues)

---

## Local Development Workflow

### Recommended Workflow

```bash
# 1. Make your changes
vim src/zetherion_ai/some_file.py

# 2. Commit (pre-commit hooks run automatically)
git add src/zetherion_ai/some_file.py
git commit -m "Add new feature"
# → Runs linting, formatting, security checks (~5-10s)

# 3. Push (pre-push hooks run automatically)
git push origin main
# → Runs full test suite (~30-60s)
# → If tests pass, code is pushed to GitHub
# → GitHub Actions CI/CD starts automatically
```

### What Happens When

**On `git commit`:**
1. Ruff linter checks code style
2. Ruff formatter auto-fixes formatting
3. File checks (trailing whitespace, large files)
4. Security scan (Bandit)
5. If all pass → commit succeeds
6. If any fail → commit blocked, fixes applied automatically

**On `git push`:**
1. Full Ruff linting (comprehensive)
2. Type checking with mypy
3. Complete test suite with pytest
4. Coverage report
5. If all pass → push succeeds
6. If any fail → push blocked, errors shown

**On GitHub (after push):**
1. Linting job
2. Type checking job
3. Security scanning job
4. Tests on Python 3.12 & 3.13
5. Docker build validation
6. Integration tests with Qdrant
7. Summary report

---

## Git Hooks Setup

### Initial Setup (One-Time)

After cloning the repository:

```bash
# Activate virtual environment
source .venv/bin/activate

# Run setup script
./scripts/setup-git-hooks.sh
```

**What the script does:**
1. Installs `pre-commit` framework
2. Installs pre-commit hooks from `.pre-commit-config.yaml`
3. Creates symlink for custom pre-push hook
4. Optionally runs checks on all files

**Manual Setup (if script fails):**

```bash
# 1. Install pre-commit
pip install pre-commit

# 2. Install hooks
pre-commit install --hook-type pre-commit --hook-type pre-push

# 3. Link custom pre-push hook
ln -sf ../../.git-hooks/pre-push .git/hooks/pre-push
```

### Verify Installation

```bash
# Check that hooks are installed
ls -la .git/hooks/
# Should show: pre-commit, pre-push (symlinks)

# Test pre-commit hooks
pre-commit run --all-files
```

---

## Pre-Commit Hooks

### What Runs on Every Commit

Defined in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml):

1. **Ruff Linter**
   - Checks: Code style (PEP 8), unused imports, complexity
   - Auto-fixes: Import sorting, simple style issues
   - Config: `pyproject.toml`

2. **Ruff Formatter**
   - Checks: Code formatting (like Black)
   - Auto-fixes: Reformats all Python files
   - Replaces: Black, isort

3. **General File Checks**
   - Large files (>1MB blocked)
   - Trailing whitespace (auto-removed)
   - File endings (ensures newline at EOF)
   - YAML/TOML/JSON syntax
   - Merge conflict markers
   - Private keys detection

4. **Bandit Security Scan**
   - Checks: Common security issues (SQL injection, hardcoded passwords, etc.)
   - Skips: Test files
   - Config: `pyproject.toml`

5. **Gitleaks Secret Scanner**
   - Checks: API keys, tokens, passwords, private keys, credentials
   - Detects: Discord tokens, Google/Gemini keys, Anthropic keys, OpenAI keys, AWS keys, GitHub tokens, JWT tokens, high-entropy strings
   - Config: `.gitleaks.toml`
   - Prevents: Accidental commit of secrets to repository

6. **Hadolint (Dockerfile linting)**
   - Checks: Dockerfile best practices
   - Ignores: Apt-get pin warnings

### Bypassing Pre-Commit (Not Recommended)

```bash
# Skip all pre-commit hooks (NOT RECOMMENDED)
git commit --no-verify -m "Quick fix"

# Skip specific hook
SKIP=ruff git commit -m "Skip ruff only"
```

**⚠️ Warning:** Bypassing hooks may cause CI to fail.

### Running Pre-Commit Manually

```bash
# Run on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run

# Run specific hook
pre-commit run ruff --all-files

# Run only Gitleaks secret scanner
pre-commit run gitleaks --all-files

# Update hooks to latest versions
pre-commit autoupdate
```

### Secret Scanning with Gitleaks

**What Gitleaks Detects:**
- **API Keys**: Discord tokens, Google/Gemini keys, Anthropic (Claude) keys, OpenAI keys, AWS keys, GitHub tokens
- **Private Keys**: RSA, SSH, PGP, OpenSSH private keys
- **Credentials**: Passwords in URLs, JWT tokens, Slack tokens
- **High-Entropy Strings**: Potential secrets based on randomness

**Configuration:**
- Rules defined in `.gitleaks.toml`
- Custom rules for Zetherion AI-specific secrets
- Allowlist for false positives (e.g., `.env.example`, test fixtures)

**What's Excluded:**
- `.env.example` (template file with placeholders)
- Test fixtures with `test_*` prefixes
- Generated files (lock files, cache directories)
- Logs and coverage reports

**If Gitleaks Finds a Secret:**
```bash
# 1. DO NOT commit the file
# 2. Remove the secret from the file
vim .env  # Replace actual secret with placeholder

# 3. If the secret was already committed (previous commits):
# Option A: Use BFG Repo-Cleaner to remove from history
git clone --mirror git@github.com:user/repo.git
bfg --replace-text passwords.txt repo.git
cd repo.git
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push

# Option B: Interactive rebase (for recent commits)
git rebase -i HEAD~5  # Edit last 5 commits
# Mark commit as 'edit', remove secret, continue
git rebase --continue

# 4. Rotate the exposed secret immediately
# - Discord: Generate new bot token
# - API keys: Regenerate in provider dashboard
```

**Testing Gitleaks:**
```bash
# Scan all files
pre-commit run gitleaks --all-files

# Scan specific file
gitleaks detect --no-git --source=src/zetherion_ai/config.py

# Generate detailed report
gitleaks detect --no-git --report-path=gitleaks-report.json
```

---

## Pre-Push Hooks

### What Runs Before Every Push

Defined in [`.git-hooks/pre-push`](../.git-hooks/pre-push):

**Step 1: Linting (Ruff)**
```bash
ruff check src/ tests/
```
- Comprehensive linting of all source code
- Fails if any issues found

**Step 2: Type Checking (mypy)**
```bash
mypy src/zetherion_ai --config-file=pyproject.toml
```
- Static type checking
- Ensures type safety
- Skips: Tests, scripts

**Step 3: Full Test Suite**
```bash
pytest tests/ -v --tb=short --cov=src/zetherion_ai --cov-report=term-missing
```
- Runs all tests
- Generates coverage report
- Shows missing coverage

**Total Time:** ~30-60 seconds

### Bypassing Pre-Push (Not Recommended)

```bash
# Skip pre-push hook (NOT RECOMMENDED)
git push --no-verify origin main
```

**⚠️ Warning:** This will likely cause GitHub Actions to fail.

### Running Pre-Push Checks Manually

```bash
# Run the pre-push hook manually
.git-hooks/pre-push

# Or run individual steps:
ruff check src/ tests/
mypy src/zetherion_ai --config-file=pyproject.toml
pytest tests/ -v --cov=src/zetherion_ai
```

---

## GitHub Actions CI/CD

### Workflow Overview

Defined in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- Manual trigger via GitHub UI (`workflow_dispatch`)

**Jobs:**

```
lint (5s)
  ├─ Ruff linter
  └─ Ruff formatter check

type-check (10s)
  └─ mypy on src/zetherion_ai

security (10s)
  └─ Bandit security scan

test (60s)
  ├─ Python 3.12 tests + coverage
  └─ Python 3.13 tests + coverage

docker-build (30s)
  ├─ Build Docker image
  └─ Validate docker-compose.yml

integration (2-3 min) ⚡ RUNS BY DEFAULT
  ├─ Start Docker Compose (Qdrant + Zetherion AI)
  ├─ Wait for services to be healthy
  ├─ Run full end-to-end integration tests
  ├─ Upload logs on failure
  └─ Clean up Docker resources

summary (5s)
  ├─ Check all job results
  └─ Post summary to PR
```

**Total Time:** ~5-10 minutes

### ⚡ Integration Tests Run Automatically

**NEW:** Integration tests now run by default on every push/PR to ensure end-to-end functionality.

**To skip integration tests**, add `[skip integration]` to your commit message:

```bash
git commit -m "Update documentation [skip integration]"
git push
```

**When to skip:**
- Documentation-only changes
- Minor typo fixes
- README updates
- Configuration changes that don't affect functionality

**When NOT to skip:**
- Code changes in `src/`
- Changes to Docker configuration
- Dependency updates
- Any functional changes

### Viewing CI Results

**On GitHub:**
1. Go to your repository
2. Click **Actions** tab
3. Click on the latest workflow run
4. View job results and logs

**On Pull Requests:**
- Status checks appear at bottom of PR
- Required checks must pass before merging
- Click "Details" to see full logs

### Coverage Reports

**Codecov Integration:**
- Coverage reports uploaded to [codecov.io](https://codecov.io)
- Badge shows coverage percentage
- PR comments show coverage changes

**Download Coverage Report:**
```bash
# Coverage HTML report saved as artifact
# Download from GitHub Actions → Workflow run → Artifacts
```

### Manual Trigger

```bash
# Via GitHub CLI
gh workflow run ci.yml

# Via GitHub UI
# Actions → CI/CD Pipeline → Run workflow
```

---

## Running Tests Manually

### Quick Tests (During Development)

```bash
# Run specific test file
pytest tests/test_router.py -v

# Run specific test class
pytest tests/test_config.py::TestSettingsInitialization -v

# Run specific test
pytest tests/test_config.py::TestSettingsInitialization::test_settings_from_env_minimal -v

# Run with pattern matching
pytest tests/ -k "test_router" -v
```

### Full Test Suite

```bash
# With coverage
pytest tests/ --cov=src/zetherion_ai --cov-report=html

# Open coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Test Markers

```bash
# Run only fast tests (exclude slow integration tests)
pytest tests/ -v -m "not slow"

# Run only integration tests
pytest tests/ -v -m "integration"

# Run only unit tests
pytest tests/ -v -m "unit"
```

### Debugging Tests

```bash
# Show print statements
pytest tests/ -v -s

# Drop into debugger on failure
pytest tests/ -v --pdb

# Show full traceback
pytest tests/ -v --tb=long

# Stop at first failure
pytest tests/ -v -x
```

---

## Troubleshooting

### Pre-Commit Hook Fails

**Error: `pre-commit: command not found`**
```bash
# Solution: Install pre-commit
pip install pre-commit
pre-commit install
```

**Error: Hook fails with ImportError**
```bash
# Solution: Ensure dependencies installed
pip install -r requirements.txt
pip install -e ".[dev]"
```

**Error: Ruff not found**
```bash
# Solution: Install dev dependencies
pip install ruff mypy bandit[toml]
```

### Pre-Push Hook Fails

**Error: Tests fail locally**
```bash
# 1. Run tests to see details
pytest tests/ -v --tb=short

# 2. Fix failing tests

# 3. Try again
git push
```

**Error: mypy type errors**
```bash
# 1. Run mypy to see details
mypy src/zetherion_ai --config-file=pyproject.toml

# 2. Fix type errors

# 3. Try again
git push
```

**Error: Coverage too low**
```bash
# 1. Check which files lack coverage
pytest tests/ --cov=src/zetherion_ai --cov-report=term-missing

# 2. Add tests for missing coverage

# 3. Try again
git push
```

### GitHub Actions Fails

**Error: Tests pass locally but fail on GitHub**

**Possible causes:**
1. **Environment differences**
   - Solution: Ensure `.env` secrets are set in GitHub
   - Go to: Settings → Secrets and variables → Actions

2. **Dependency issues**
   - Solution: Check if `requirements.txt` is up to date
   - Run: `pip freeze > requirements.txt`

3. **Python version differences**
   - Solution: Test locally with Python 3.12 and 3.13
   - Use: `pyenv` or Docker

4. **Service dependencies**
   - Solution: Check if Qdrant service started correctly
   - View logs in GitHub Actions

**Error: Docker build fails on GitHub**
```bash
# Test Docker build locally
docker build -t zetherion_ai:test .

# If fails, fix Dockerfile and try again
```

### Bypassing Hooks Safely

**When it's okay to bypass:**
- Emergency hotfix (fix immediately, create cleanup PR later)
- Documentation-only changes
- CI configuration changes

**How to bypass safely:**
```bash
# Skip pre-commit only
git commit --no-verify -m "docs: Update README"

# Skip pre-push only (commit hooks still run)
git push --no-verify origin main

# Skip all (NOT RECOMMENDED)
git commit --no-verify -m "Emergency fix"
git push --no-verify origin main
```

**⚠️ Best Practice:**
- Use `--no-verify` sparingly
- Always create follow-up PR to fix issues
- Never bypass on `main` branch

---

## Configuration Files

### Pre-Commit Configuration

**File:** `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.2.2
    hooks:
      - id: ruff
      - id: ruff-format
```

**Update hooks:**
```bash
pre-commit autoupdate
```

### Test Configuration

**File:** `pyproject.toml`

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
    "--cov=src/zetherion_ai",
]
```

### Type Checking Configuration

**File:** `pyproject.toml`

```toml
[tool.mypy]
python_version = "3.12"
strict = true
```

---

## Best Practices

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
# Feature
git commit -m "feat: Add Ollama router backend"

# Bug fix
git commit -m "fix: Resolve Docker memory allocation bug"

# Documentation
git commit -m "docs: Update CI/CD setup guide"

# Tests
git commit -m "test: Add Ollama router tests"

# Chore
git commit -m "chore: Update dependencies"
```

### Test Coverage

**Target:** 80%+ coverage

```bash
# Check current coverage
pytest tests/ --cov=src/zetherion_ai --cov-report=term

# View detailed report
pytest tests/ --cov=src/zetherion_ai --cov-report=html
open htmlcov/index.html
```

### Code Quality

**Before committing:**
1. Run `ruff check --fix src/ tests/`
2. Run `mypy src/zetherion_ai`
3. Run `pytest tests/`
4. Review changes: `git diff`

**Before pushing:**
1. Ensure tests pass
2. Update documentation if needed
3. Check coverage hasn't decreased
4. Rebase on latest main

---

## Quick Reference

### Common Commands

```bash
# Setup git hooks (one-time)
./scripts/setup-git-hooks.sh

# Run pre-commit manually
pre-commit run --all-files

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=src/zetherion_ai

# Run linter
ruff check src/ tests/

# Run formatter
ruff format src/ tests/

# Run type checker
mypy src/zetherion_ai

# Update pre-commit hooks
pre-commit autoupdate

# Skip hooks (emergency only)
git commit --no-verify
git push --no-verify
```

### GitHub Actions Badge

Add to README.md:

```markdown
[![CI](https://github.com/yourusername/zetherion_ai/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/zetherion_ai/actions/workflows/ci.yml)
```

---

## Additional Resources

- [Pre-commit Framework](https://pre-commit.com/)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Pytest Documentation](https://docs.pytest.org/)
- [Codecov Documentation](https://docs.codecov.io/)
