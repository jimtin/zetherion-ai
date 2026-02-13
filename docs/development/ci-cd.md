# CI/CD Pipeline

Complete guide to Zetherion AI's three-tier quality gate: pre-commit hooks, pre-push hooks, and GitHub Actions CI/CD.

## Overview

Every code change passes through three automated quality tiers before reaching production.

```
+---------------------------------------------------------------+
|  Tier 1: Pre-Commit Hooks (~10-15s)                           |
|  Runs: on every git commit                                    |
|  Checks: Ruff lint + format, file hygiene, Bandit,            |
|          Gitleaks, Hadolint, Mypy                              |
|  Effect: blocks commit if any check fails                     |
+---------------------------------------------------------------+
                             |
                             v
+---------------------------------------------------------------+
|  Tier 2: Local Push Gates                                      |
|  Runs: on every git push (hook) and pre-merge validation       |
|  Checks: lightweight pre-push hook + full production-parity     |
|          pipeline via scripts/pre-push-tests.sh                |
|  Effect: blocks push/merge if checks fail                      |
+---------------------------------------------------------------+
                             |
                             v
+---------------------------------------------------------------+
|  Tier 3: GitHub Actions CI/CD (~5-10 min)                     |
|  Runs: on push/PR to main or develop                          |
|  Checks: lint, type-check, security (Bandit + Semgrep),       |
|          dependency audit, license compliance, pre-commit,     |
|          tests (Python 3.12 + 3.13 matrix), Docker build +    |
|          Trivy scan, SBOM generation                           |
|  Effect: blocks PR merge if any required check fails          |
+---------------------------------------------------------------+
```

**Design philosophy:**

- **Fast feedback locally** -- pre-commit catches formatting and security issues in seconds.
- **Confidence before push** -- local gates catch failures before CI, including a full containerized validation path.
- **Comprehensive CI** -- GitHub Actions catches cross-version issues, container problems, and dependency vulnerabilities.

## Pre-Commit Hooks

Pre-commit hooks run automatically on every `git commit`. They are defined in `.pre-commit-config.yaml` and managed by the [pre-commit](https://pre-commit.com/) framework.

### What Runs (7 Checks)

| # | Hook | Tool | What It Does | Auto-Fix |
|---|------|------|-------------|----------|
| 1 | Ruff linter | `ruff check --fix` | Code style (PEP 8), unused imports, complexity, import sorting | Yes |
| 2 | Ruff formatter | `ruff format` | Consistent code formatting (replaces Black + isort) | Yes |
| 3 | File checks | `pre-commit-hooks` | Trailing whitespace, EOF newlines, large files (>1MB), merge conflicts, case conflicts, YAML/TOML/JSON syntax, private key detection | Yes (whitespace, EOF) |
| 4 | Bandit | `bandit -c pyproject.toml` | Common security issues (SQL injection, hardcoded passwords, eval usage). Excludes `tests/` and `scripts/` | No |
| 5 | Gitleaks | `gitleaks detect` | API keys, tokens, passwords, private keys, high-entropy strings. Uses custom rules in `.gitleaks.toml` | No |
| 6 | Hadolint | `hadolint-docker` | Dockerfile best practices. Ignores DL3007/DL3008/DL3009 (Chainguard-specific) | No |
| 7 | Mypy | `mypy --config-file=pyproject.toml` | Static type checking against Python 3.12 with `strict = true`. Excludes `tests/` and `scripts/` | No |

### Setup

```bash
# One-time setup using the provided script
./scripts/setup-git-hooks.sh

# Manual setup if the script fails
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type pre-push
```

### Running Manually

```bash
# Run all hooks on all files
pre-commit run --all-files

# Run on staged files only (same as what happens on commit)
pre-commit run

# Run a specific hook
pre-commit run ruff --all-files
pre-commit run gitleaks --all-files
pre-commit run bandit --all-files

# Update hooks to latest versions
pre-commit autoupdate
```

### Bypass

```bash
# Skip all pre-commit hooks (not recommended)
git commit --no-verify -m "Emergency fix"

# Skip a specific hook
SKIP=ruff git commit -m "Skip ruff only"
SKIP=mypy git commit -m "Skip type checking"
```

Bypassing hooks is discouraged. If you bypass locally, GitHub Actions CI will still enforce the same checks and block the PR.

## Pre-Push and Pre-Merge Validation

Zetherion AI has two local validation paths:

1. `.git-hooks/pre-push` (automatic on `git push`) for fast feedback.
2. `scripts/pre-push-tests.sh` (manual/CI-style) for production-parity validation.

### Lightweight Git Hook (`.git-hooks/pre-push`)

| Step | Command | What It Checks |
|------|---------|----------------|
| 1 | `ruff check src/ tests/` | Linting |
| 2 | `mypy src/zetherion_ai --config-file=pyproject.toml` | Type checking |
| 3 | `pytest tests/ -m "not integration" ... --cov=src/zetherion_ai` | Unit suite + coverage gate |

### Production-Parity Pipeline (`scripts/pre-push-tests.sh`)

This script is the canonical full local gate before merge. It performs:

1. Static analysis (`ruff`, `bandit`, `gitleaks`, `hadolint`, license checks)
2. Unit tests + `mypy` + `pip-audit` (parallel)
3. In-process integration tests
4. Docker test environment rebuild/start (`docker-compose.test.yml`)
5. Full Docker E2E and Discord E2E suites

Run it directly:

```bash
bash scripts/pre-push-tests.sh
```

### Setup

The pre-push hook is installed as a symlink:

```bash
ln -sf ../../.git-hooks/pre-push .git/hooks/pre-push
```

This is handled automatically by `./scripts/setup-git-hooks.sh`.

### Bypass

```bash
# Skip pre-push hook (not recommended)
git push --no-verify origin main
```

If you bypass the pre-push hook, GitHub Actions will still catch failures. However, this means slower feedback -- you will not discover test failures until CI runs 5-10 minutes later.

## GitHub Actions

The CI/CD pipeline is defined in `.github/workflows/ci.yml`.

### Workflow Triggers

| Trigger | Condition |
|---------|-----------|
| `push` | To `main` or `develop` branches |
| `pull_request` | Targeting `main` or `develop` branches |
| `workflow_dispatch` | Manual trigger from GitHub UI or `gh workflow run ci.yml` |

### Jobs

The pipeline runs the following jobs. Jobs without dependency arrows run in parallel.

```
+------------------+    +-------------------+    +-------------------+
|   lint (~5s)     |    | type-check (~10s) |    | security (~10s)   |
|  Ruff lint +     |    | mypy strict mode  |    | Bandit scan on    |
|  format check    |    | on src/           |    | src/              |
+------------------+    +-------------------+    +-------------------+

+------------------+    +-------------------+    +-------------------+
| semgrep (~30s)   |    | dependency-audit  |    | license-check     |
| SAST analysis    |    | pip-audit with    |    | pip-licenses       |
| uploads SARIF    |    | --strict --desc   |    | allow-list check  |
+------------------+    +-------------------+    +-------------------+

+------------------+    +-------------------------------+
| pre-commit (~30s)|    | test (~60s)                   |
| All hooks via    |    | Python 3.12 + 3.13 matrix     |
| pre-commit/action|    | pytest + coverage + Codecov   |
+------------------+    +-------------------------------+

+----------------------------------------------+
| docker-build (~2 min)                        |
| Build image, Trivy vulnerability scan,       |
| SBOM generation, docker-compose config check |
+----------------------------------------------+

            +-------------------------------+
            | summary (~5s)                 |
            | Aggregates all job results    |
            | Posts table to PR summary     |
            | Fails if any job failed       |
            +-------------------------------+
```

### Job Details

**lint** -- Installs `ruff==0.8.4` and runs `ruff check` + `ruff format --check` against `src/` and `tests/`.

**type-check** -- Installs full project dependencies, then runs `mypy src/zetherion_ai --config-file=pyproject.toml` in strict mode.

**security** -- Runs `bandit -r src/ -c pyproject.toml` to detect common Python security issues.

**semgrep** -- Runs Semgrep with `--config auto` for static application security testing. Uploads SARIF results to the GitHub Security tab.

**dependency-audit** -- Runs `pip-audit -r requirements.txt --strict --desc on` to check all dependencies for known CVEs.

**license-check** -- Runs `pip-licenses` to verify all dependency licenses are in the approved allow-list (MIT, BSD, Apache, ISC, PSF, MPL-2.0, Unlicense, CC0, Zlib).

**pre-commit** -- Runs all pre-commit hooks via the `pre-commit/action@v3` GitHub Action.

**test** -- Matrix build across Python 3.12 and 3.13. Runs `pytest tests/ -m "not integration"` with coverage. Uploads coverage XML to Codecov (Python 3.12 only). Uploads HTML coverage report as a build artifact (retained 30 days).

**docker-build** -- Builds the Docker image using Buildx with GHA caching. Runs Trivy vulnerability scanner (CRITICAL + HIGH severity). Generates an SPDX SBOM via Anchore. Validates `docker-compose.yml` syntax. Uploads Trivy SARIF to GitHub Security tab and SBOM as a build artifact (retained 90 days).

**summary** -- Waits for all other jobs. Checks every job result. Posts a markdown results table to the PR summary. Exits with failure if any required job failed.

### Integration Test Control

Integration tests can be skipped by including `[skip integration]` in the commit message:

```bash
git commit -m "docs: Update README [skip integration]"
```

**When to skip:**
- Documentation-only changes
- Configuration updates that do not affect runtime behavior
- README or comment-only edits

**When NOT to skip:**
- Any change under `src/`
- Docker configuration changes
- Dependency updates
- New or modified test files

### Coverage Reporting

Coverage is reported through multiple channels:

| Channel | Details |
|---------|---------|
| Terminal | `--cov-report=term-missing` shows missing lines inline during the test run |
| HTML | `--cov-report=html` generates a browsable report. Uploaded as a GitHub Actions artifact |
| XML | `--cov-report=xml` generates `coverage.xml`. Uploaded to Codecov |
| Codecov | Badge in README shows current coverage percentage. PR comments show coverage delta |

## Secret Scanning

Gitleaks runs as a pre-commit hook with custom rules defined in `.gitleaks.toml`.

### What It Detects

| Secret Type | Pattern | Examples |
|-------------|---------|----------|
| Discord tokens | Bot token format | `MTIzNDU2...` |
| Google/Gemini API keys | `AIza...` pattern | `AIzaSy...` |
| Anthropic API keys | `sk-ant-api03-...` pattern | Claude API keys |
| OpenAI API keys | `sk-...` pattern | GPT API keys |
| AWS access keys | `AKIA...` pattern | IAM credentials |
| GitHub tokens | `ghp_...` pattern | Personal access tokens |
| Private keys | PEM format headers | RSA, SSH, PGP, OpenSSH |
| JWT tokens | `eyJ...` pattern | Encoded JWTs |
| Slack tokens | `xox[baprs]-...` pattern | Bot and user tokens |
| Passwords in URLs | `proto://user:pass@host` | Database connection strings |
| High-entropy strings | Entropy > 4.5 | Potential unclassified secrets |

### What Is Excluded

The `.gitleaks.toml` allowlist prevents false positives from:

- `.env.example` (template file with placeholder values)
- Test files (`tests/**/*.py`) containing fake tokens
- Documentation files with example patterns
- Generated files (lock files, cache directories, coverage reports)
- GitHub workflow files with test placeholder secrets
- Source code calling `.get_secret_value()` (the method name, not actual secrets)

### Response Protocol for Exposed Secrets

If a secret is accidentally committed:

1. **Do not push.** If the commit is local only, amend it to remove the secret.
2. **Rotate the secret immediately.** Regenerate the token/key in the provider dashboard (Discord Developer Portal, Google Cloud Console, Anthropic Dashboard, OpenAI Dashboard).
3. **If already pushed**, use BFG Repo-Cleaner to remove the secret from git history:

```bash
git clone --mirror git@github.com:user/repo.git
bfg --replace-text passwords.txt repo.git
cd repo.git
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push
```

4. **Update `.env`** with the new rotated secret.
5. **Update GitHub Actions secrets** if the exposed key was used in CI.

## Configuration Files

| File | Purpose |
|------|---------|
| `.pre-commit-config.yaml` | Pre-commit hook definitions (7 hooks across 6 tool repositories) |
| `.git-hooks/pre-push` | Lightweight pre-push hook (lint, type-check, unit tests) |
| `scripts/pre-push-tests.sh` | Full production-parity test pipeline (unit + integration + Docker E2E + Discord E2E) |
| `.github/workflows/ci.yml` | GitHub Actions CI/CD workflow (9 jobs) |
| `.gitleaks.toml` | Gitleaks secret scanning rules and allowlists |
| `pyproject.toml` | Unified configuration for pytest, mypy, ruff, coverage, and bandit |

### pyproject.toml (Relevant Sections)

**Pytest:**
- `asyncio_mode = "auto"` -- async tests run without explicit markers
- `testpaths = ["tests"]` -- test discovery root
- `pythonpath = ["src"]` -- import resolution
- `--strict-markers` -- typos in marker names cause errors

**Mypy:**
- `python_version = "3.12"` -- target version
- `strict = true` -- all strict checks enabled
- `warn_unused_ignores = false` -- suppresses cross-environment false positives

**Ruff:**
- `target-version = "py312"`
- `line-length = 100`
- Select rules: E, F, I, N, W, UP, B, C4, SIM

**Coverage:**
- `source = ["src/zetherion_ai"]`
- `branch = true` -- tracks branch paths
- `omit` excludes tests, pycache, and site-packages

**Bandit:**
- Excludes `tests/` and `scripts/`
- Skips B101 (assert_used) to avoid false positives

## Troubleshooting

### Hook Installation Issues

**Problem:** `pre-commit: command not found`

```bash
pip install pre-commit
pre-commit install
```

**Problem:** Pre-push hook not running

```bash
# Verify the symlink exists
ls -la .git/hooks/pre-push

# Recreate if missing
ln -sf ../../.git-hooks/pre-push .git/hooks/pre-push

# Or re-run setup
./scripts/setup-git-hooks.sh
```

**Problem:** Hooks installed but not triggering

```bash
# Verify hook types are installed
pre-commit install --hook-type pre-commit --hook-type pre-push

# Test manually
pre-commit run --all-files
```

### Test Failures Blocking Push

**Problem:** Tests fail during local gates and block push/merge.

```bash
# Fast path (same shape as hook)
pytest tests/ -m "not integration and not discord_e2e" -v --tb=long

# Full production-parity path
bash scripts/pre-push-tests.sh

# Run only the failing test with debug output
pytest tests/unit/test_agent_core.py::test_failing_case -s -vv

# If tests pass individually but fail together, check for fixture pollution
pytest tests/ -m "not integration and not discord_e2e" -p no:randomly --tb=short
```

**Problem:** Coverage threshold not met

```bash
# Check current coverage with missing lines
pytest tests/ -m "not integration and not discord_e2e" --cov=src/zetherion_ai --cov-report=term-missing

# Generate HTML report for detailed analysis
pytest tests/ -m "not integration and not discord_e2e" --cov=src/zetherion_ai --cov-report=html
open htmlcov/index.html
```

### CI Failures

**Problem:** Tests pass locally but fail on GitHub Actions.

Common causes and solutions:

| Cause | Solution |
|-------|----------|
| Python version difference | Test locally with both 3.12 and 3.13 using `pyenv` |
| Missing environment variable | Set secrets in GitHub Settings > Secrets and variables > Actions |
| Dependency version drift | Run `pip install -r requirements.txt` to sync local deps |
| Platform-specific behavior | Check for OS-dependent code paths (macOS vs Linux) |
| Stale cache | Delete the GitHub Actions cache from the Actions tab |

**Problem:** Docker build fails on GitHub Actions

```bash
# Test the Docker build locally
docker build -t zetherion_ai:test .

# Validate docker-compose.yml
docker compose config

# Check for Dockerfile linting issues
hadolint Dockerfile
```

**Problem:** Trivy reports CRITICAL vulnerabilities

Trivy runs with `exit-code: 0` (non-blocking) by default. Results appear in the GitHub Security tab. To investigate locally:

```bash
# Install Trivy
brew install trivy

# Scan the local image
docker build -t zetherion_ai:test .
trivy image --severity CRITICAL,HIGH zetherion_ai:test
```

### Semgrep or Bandit False Positives

**Problem:** Security scanner flags legitimate code.

For Bandit, add a `# nosec` comment with justification:

```python
subprocess.run(cmd, shell=False)  # nosec B603 -- input is validated above
```

For Semgrep, add a `# nosemgrep` comment:

```python
eval(trusted_expression)  # nosemgrep: python.lang.security.audit.eval
```

For persistent false positives, update the tool configuration in `pyproject.toml` (Bandit) or add a `.semgrepignore` file.

## Related Documentation

- [Testing Guide](../development/testing.md) -- Test suite details, writing tests, coverage strategy
- [Architecture](../technical/architecture.md) -- System design and 6 Docker services
- [Security](../technical/security.md) -- Encryption, authentication, and threat model
- [Setup and Contributing](setup.md) -- Development workflow and coding standards
