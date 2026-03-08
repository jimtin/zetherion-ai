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
|  Checks: commit-state preflight + canonical full pipeline via  |
|          scripts/run-local-gate-preflight.sh +                |
|          scripts/test-full.sh                                  |
|  Effect: blocks push/merge if checks fail                      |
+---------------------------------------------------------------+
                             |
                             v
+---------------------------------------------------------------+
|  Tier 3: GitHub Actions CI/CD (~1-3 min fast path on PRs)     |
|  Runs: on push/PR to main or develop, plus schedule/manual    |
|  Checks: PR fast-path invariants, diff secret scan, boundary  |
|          checks, and exact-SHA receipt validation for         |
|          required E2E evidence                                |
|  Effect: blocks PR merge using the active required-check set  |
+---------------------------------------------------------------+
```

**Design philosophy:**

- **Fast feedback locally** -- pre-commit catches formatting and hygiene issues in seconds.
- **Confidence before push** -- local gates produce the authoritative heavy-lane evidence, including the required local receipt path for substantial E2E work.
- **Cost-aware GitHub validation** -- PRs run only the fast-path invariant checks plus committed local-receipt validation; heavier local-equivalent jobs are deferred to push or scheduled/manual runs.

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

Zetherion AI uses one canonical local validation path:

1. `.git-hooks/pre-push` (automatic on `git push`) first runs `scripts/run-local-gate-preflight.sh` against the exact `<base, head>` push range.
2. If preflight passes, it executes `scripts/test-full.sh`.
3. `scripts/test-full.sh` can still be run directly for manual full validation once commit-state preflight is clean.

### Production-Parity Pipeline (`scripts/test-full.sh`)

This script is the canonical full local gate before merge. It performs:

1. Static analysis (`ruff`, `bandit`, `gitleaks`, `hadolint`, license checks)
2. Unit tests + `mypy` + `pip-audit` (parallel)
3. In-process integration tests
4. Isolated Docker test environment provisioning via `scripts/e2e_run_manager.py` + `docker-compose.test.yml`
5. Docker test environment rebuild/start against the per-run Compose project
6. Docker and Discord E2E smoke preflight against the externally managed per-run test stack
7. Full Docker E2E and Discord E2E suites

Run it directly:

```bash
bash scripts/test-full.sh
```

Run the changed-file preflight directly when you need fast feedback before the full gate:

```bash
bash scripts/run-local-gate-preflight.sh --base-ref origin/main --head-ref HEAD
```

Each invocation now provisions a unique Compose project, host-port map, and stack root under `.artifacts/e2e-runs/` before Docker-backed E2E begins. Residual stacks are cleaned through the same run manager and an expired-run janitor path.

The preflight is driven by the source-controlled manifest at `.ci/local_gate_manifest.json`. It currently requires local fast-fail coverage for:

- endpoint docs bundle changes on API/CGS route files
- strict `mypy src/zetherion_ai --config-file=pyproject.toml` for runtime Python changes
- bounded `unit-full` for shared trust/personal/profile/portfolio/routing/queue/model/context/storage/startup paths that can move the repo-wide coverage floor
- targeted Qdrant/data-plane regression tests
- targeted replay-store regression tests
- targeted receipt/workflow-support regression suites for receipt validation, local gate enforcement, and CI failure-attribution changes
- targeted Windows deploy-preflight regression suites for deployment-receipt validation and optional-service guard changes

Protected shared-infra and shared-runtime coverage-sensitive paths must stay mapped in `.ci/local_gate_manifest.json`; local validation fails fast when a protected path changes without an explicit local gate mapping.

When a PR is classified `e2e_required=true`, generate local receipt evidence for CI after the full local gate passes:

```bash
bash scripts/local-required-e2e-receipt.sh
```

This writes `.ci/e2e-receipt.json` (head-SHA bound) and must be committed with the PR. Segment 5 extends the receipt with `e2e_run_id`, `compose_project`, `stack_root`, and `docker_cleanup_status` so CI and operators can trace isolated Docker-backed E2E runs.

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

Normal workflows must not bypass the pre-push hook. If you do, GitHub may still reject the PR through fast invariant checks, path-gated jobs, or receipt validation, but that is treated as a process breach because it wastes CI minutes and delays feedback.

## GitHub Actions

The CI/CD pipeline is defined in `.github/workflows/ci.yml`.

### Workflow Triggers

| Trigger | Condition |
|---------|-----------|
| `push` | To `main` or `develop` branches |
| `pull_request` | Targeting `main` or `develop` branches |
| `workflow_dispatch` | Manual trigger from GitHub UI or `gh workflow run ci.yml` |

### Current Required PR Checks

The active `main` branch ruleset currently requires these check contexts:

- `CI Summary`
- `Linting & Formatting`
- `Pipeline Contract`
- `Secret Scan (Gitleaks)`
- `Zetherion Boundary Check`

The `required-e2e-gate` job validates the committed local receipt contract when the risk classifier marks a PR `e2e_required=true`; GitHub does not execute the full E2E suites directly on PRs. Committed receipts use `head_sha=local` because tracked receipt files cannot self-hash the final commit without recursion. The PR fast path is now limited to `detect-changes`, `risk-classifier`, `lint`, `secret-scan`, `pipeline-contract`, `zetherion-boundary-check`, `required-e2e-gate`, `CI Summary`, and `CI Failure Attribution`.

Weekly or manual GitHub runs remain the independent heavy-verification cadence for `type-check`, `security`, `semgrep`, `dependency-audit`, `license-check`, `pre-commit`, `docs-contract`, `unit-test`, `integration-test`, `docker-build-test`, and CodeQL.

### Additional Main-Branch Automation

| Workflow | Trigger | Contract |
|---|---|---|
| `docs.yml` (`Deploy Documentation`) | push to `main` when docs-site sources change, or manual dispatch | Build strict MkDocs output and republish GitHub Pages without rerunning docs-contract checks |
| `codeql.yml` (`CodeQL`) | weekly schedule or manual dispatch | Independent GitHub-native code scanning, kept off the PR fast path |
| Windows local promotions worker (`scripts/windows/promotions-runner.ps1` + `scripts/windows/promotions-watch.ps1`) | successful Windows deployment receipt for `main` SHA | Validate deployment receipt, build merge intelligence, generate/publish CGS blog, and auto-increment GitHub release |
| Windows Discord production canary (`scripts/windows/discord-canary-runner.ps1`) | startup plus every 6 hours by default on the Windows host | Run the isolated full-parity Discord E2E canary against the production bot, persist receipts/logs, and degrade host health on failure without rolling back deploys |

Windows promotion gates:

1. `deployment-receipt.json` must be `status=success`.
2. `target_sha == deployed_sha` and must match triggering SHA.
3. Required receipt checks must all be `true`:
   - `containers_healthy`
   - `bot_startup_markers`
   - `postgres_model_keys`
   - `fallback_probe`
   - `recovery_tasks_registered`
   - `runner_service_persistent`
   - `docker_service_persistent`
4. Blog generation requires high-tier models only:
   - draft: `gpt-5.2`
   - refine: `claude-sonnet-4-6`
   - no lower-tier fallback

### Windows Promotions Ops Runbook

Use these commands on the Windows host (`C:\ZetherionAI`) for promotions/release operations.

#### 1. Configure and validate promotions secrets

```powershell
pwsh -File .\scripts\windows\set-promotions-secrets.ps1 `
  -CgsBlogPublishUrl "https://<cgs-host>/..." `
  -CgsBlogPublishToken "<token>" `
  -OpenAiApiKey "<openai-key>" `
  -AnthropicApiKey "<anthropic-key>" `
  -GitHubPromotionToken "<github-token>" `
  -GitHubRepository "jimtin/zetherion-ai" `
  -AnnouncementEmitEnabled "true" `
  -AnnouncementApiSecret "<skills-api-secret>" `
  -AnnouncementApiUrl "http://127.0.0.1:8080/announcements/events" `
  -AnnouncementTargetUserId "<discord-user-id>"

pwsh -File .\scripts\windows\test-promotions-secrets.ps1
```

#### 2. Bootstrap resilience scheduled tasks (run elevated)

```powershell
pwsh -File .\scripts\windows\bootstrap-resilience-tasks.ps1 `
  -DeployPath "C:\ZetherionAI" `
  -OutputPath "C:\ZetherionAI\data\resilience-bootstrap.json"
```

#### 3. Verify resilience scheduled task contract

```powershell
pwsh -File .\scripts\windows\verify-resilience-tasks.ps1 `
  -DeployPath "C:\ZetherionAI" `
  -OutputPath "C:\ZetherionAI\data\resilience-verify.json"
```

Expected `checks` in the JSON output:
- `startup_task_registered=true`
- `watchdog_task_registered=true`
- `promotions_task_registered=true`
- `canary_task_registered=true`
- `all_tasks_registered=true`

#### 4. Run the Windows Discord production canary manually

```powershell
pwsh -File .\scripts\windows\discord-canary-runner.ps1 `
  -DeployPath "C:\ZetherionAI" `
  -OutputPath "C:\ZetherionAI\data\discord-canary\manual-run.json" `
  -StatePath "C:\ZetherionAI\data\discord-canary\state.json" `
  -LogPath "C:\ZetherionAI\data\discord-canary\manual-run.log" `
  -ResultPath "C:\ZetherionAI\data\discord-canary\manual-discord-result.json"

Get-Content "C:\ZetherionAI\data\discord-canary\manual-run.json"
```

Expected outcomes:
- `status=success`: canary ran and cleaned its isolated synthetic channel
- `status=cleanup_degraded`: test execution passed, but channel or synthetic artifact cleanup needs attention
- `status=lease_contended`: another active canary or E2E run holds the target-bot lease; the scheduled task should retry later
- `status=failed|timeout|runner_error`: production-parity canary failed and should surface in host verification plus announcements

The host verifier reads `C:\ZetherionAI\data\discord-canary\last-run.json` and `state.json`, treating stale or failing canaries as degraded health rather than deploy rollback triggers. Windows deploy receipts also split `core_status` from `aux_status`, so `cloudflared` or `whatsapp-bridge` degradation surfaces as a warning without rolling back unrelated releases unless `WINDOWS_REQUIRE_HEALTHY_AUXILIARY_SERVICES=true`.

#### 5. Verify announcement notification path

```powershell
python .\scripts\windows\announcement-emit.py `
  --event deploy `
  --sha "<sha>" `
  --status success `
  --run-url "https://github.com/jimtin/zetherion-ai/actions/runs/<run-id>" `
  --stage-results "manual=smoke" `
  --dry-run

pwsh -File .\scripts\windows\announcements-flush.ps1 `
  -DeployPath "C:\ZetherionAI" `
  -OutputPath "C:\ZetherionAI\data\announcements\flush-manual.json"
```

Expected statuses:
- `dry_run` or `deduped`: secret resolution and idempotency wiring are working.
- `queued_non_blocking`: API path was unavailable and event was safely spooled for retry.

#### 6. Troubleshoot CGS publish contract and promotions pipeline

```powershell
pwsh -File .\scripts\windows\promotions-runner.ps1 `
  -Sha "<sha>" `
  -ReceiptPath "C:\ZetherionAI\data\deployment-receipts\<sha>.json" `
  -DeployPath "C:\ZetherionAI" `
  -OutputPath "C:\ZetherionAI\data\promotions\last-run.json"

python .\scripts\windows\promotions-pipeline.py `
  --sha "<sha>" `
  --deploy-path "C:\ZetherionAI" `
  --deployment-receipt "C:\ZetherionAI\data\deployment-receipts\<sha>.json" `
  --data-root "C:\ZetherionAI\data\promotions" `
  --repo "jimtin/zetherion-ai"
```

Use these artifacts to diagnose failures:
- `C:\ZetherionAI\data\promotions\analysis\<sha>.json`
- `C:\ZetherionAI\data\promotions\receipts\<sha>.json`
- `C:\ZetherionAI\data\promotions\state.json`
- `C:\ZetherionAI\data\promotions\queue.json`

#### 7. Promotions-task strict mode

`Deploy Windows` supports a strict gate toggle:
- `WINDOWS_REQUIRE_PROMOTIONS_TASK=false` (default): missing promotions task is warning-only.
- `WINDOWS_REQUIRE_PROMOTIONS_TASK=true`: receipt gate fails if `promotions_task_registered=false`.

Keep the default during burn-in. Enable strict mode only after repeated successful deployments confirm task registration is stable.

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

**type-check** -- Installs full project dependencies, then runs `mypy src/zetherion_ai --config-file=pyproject.toml` in strict mode. This job is deferred off PRs and runs on push code changes plus scheduled/manual heavy verification.

**security** -- Runs `bandit -r src/ -c pyproject.toml` to detect common Python security issues. This job is deferred off PRs and runs on push code changes plus scheduled/manual heavy verification.

**semgrep** -- Runs Semgrep with `--config auto` for static application security testing. Uploads SARIF results to the GitHub Security tab.

**dependency-audit** -- Runs `pip-audit -r requirements.txt --strict --desc on` to check all dependencies for known CVEs.

**license-check** -- Runs `pip-licenses` to verify all dependency licenses are in the approved allow-list (MIT, BSD, Apache, ISC, PSF, MPL-2.0, Unlicense, CC0, Zlib).

**pre-commit** -- Runs all pre-commit hooks via the `pre-commit/action@v3` GitHub Action.

**docs-contract** -- Runs docs navigation/link checks and route/env parity checks, then builds docs with `mkdocs build --strict`. This job is deferred off PRs and runs on push docs changes plus scheduled/manual verification.

**pipeline-contract** -- Validates CI pipeline-contract mappings plus endpoint docs bundle and announcement DM guardrails (`scripts/check-announcement-dm-guard.py`) to block direct `user.send(...)` regressions in announcement-producing paths.

**risk-classifier** -- Computes server-side `e2e_required=true|false` from changed-path policy. Ambiguous classifications fail-safe to `e2e_required=true`.

**required-e2e-gate** -- Always emits `e2e-contract-receipt` artifact. If `e2e_required=true`, CI validates the committed local receipt contract (`.ci/e2e-receipt.json`). Committed receipts use `head_sha=local`, and the tested commit is recorded separately to avoid hash recursion. CI does not run full E2E suites directly.

**zetherion-boundary-check** -- Enforces Zetherion-only repository boundary and fails when top-level `cgs/**` UI paths are introduced.

**test** -- Matrix build across Python 3.12 and 3.13. Runs `pytest tests/ -m "not integration"` with coverage. Uploads coverage XML to Codecov (Python 3.12 only). Uploads HTML coverage report as a build artifact (retained 14 days). This job is deferred off PRs and runs on push code changes plus scheduled/manual heavy verification.

**docker-build** -- Validates `docker-compose.yml` syntax and builds the Skills image. This job is deferred off PRs and runs on push Docker changes plus scheduled/manual verification.

**summary** -- Waits for all other jobs. On pull requests it evaluates only the fast-path contract jobs. On push/scheduled/manual runs it evaluates the full active job graph. Posts a markdown results table to the workflow summary and fails when the required contract for that event fails.

**ci-cost-report** -- Best-effort observability job that queries the current workflow run, emits `ci-cost-report.json`, and posts execution-class / duration summaries to the workflow summary without becoming a merge blocker.

**ci-maintenance** -- Separate weekly/manual workflow that emits a 7-day CI usage summary and prunes stale GitHub Actions caches using auditable JSON receipts.

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
| `.git-hooks/pre-push` | Commit-state preflight plus canonical full local gate orchestration |
| `.ci/local_gate_manifest.json` | Source-controlled changed-file to local-gate requirement mappings |
| `scripts/local_gate_plan.py` | Deterministic classifier that turns changed files into required local checks/tests |
| `scripts/run-local-gate-preflight.sh` | Fast-fail pre-push runner for manifest-mapped docs, mypy, and regression suites |
| `scripts/test-full.sh` | Full production-parity test pipeline (unit + integration + Docker E2E + Discord E2E) |
| `scripts/ci_e2e_risk_classifier.py` | Changed-path risk classifier for required E2E enforcement |
| `scripts/e2e_run_manager.py` + `scripts/e2e_run_manager.sh` | Allocate per-run Docker E2E project names, ports, artifact roots, and janitor cleanup for local/canary runs |
| `scripts/local-required-e2e-receipt.sh` | Local required-E2E runner + receipt writer (`.ci/e2e-receipt.json`) |
| `scripts/ci-required-e2e-gate.sh` | CI validator for local required-E2E receipt contract (`e2e-contract-receipt`) |
| `.github/workflows/ci.yml` | GitHub Actions CI/CD workflow (policy + quality + test jobs) |
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
bash scripts/test-full.sh

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
