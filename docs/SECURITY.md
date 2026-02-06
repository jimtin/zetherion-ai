# Security Overview

This document describes how Zetherion AI is secured across the full development lifecycle: how we protect credentials, validate input, scan for vulnerabilities, test, and deploy.

---

## Table of Contents

1. [Secret Management](#1-secret-management)
2. [Secret Scanning (Pre-Commit)](#2-secret-scanning-pre-commit)
3. [Input Validation & Prompt Injection Defence](#3-input-validation--prompt-injection-defence)
4. [Access Control](#4-access-control)
5. [Static Analysis & Code Quality](#5-static-analysis--code-quality)
6. [Dependency Management](#6-dependency-management)
7. [Container Security](#7-container-security)
8. [CI/CD Pipeline Security](#8-cicd-pipeline-security)
9. [Testing Strategy](#9-testing-strategy)
10. [Logging & Monitoring](#10-logging--monitoring)
11. [Network Security](#11-network-security)
12. [Data Encryption (Phase 5A)](#12-data-encryption-phase-5a)
13. [Gap Analysis & Recommendations](#13-gap-analysis--recommendations)

---

## 1. Secret Management

All credentials are loaded from environment variables via a `.env` file and never hardcoded in source.

| Control | Implementation | File |
|---------|---------------|------|
| Typed secrets | `pydantic.SecretStr` prevents accidental logging/serialisation of tokens | `src/zetherion_ai/config.py` |
| `.env` excluded from Git | `.gitignore` blocks `.env`, `data/`, `ollama_models/` | `.gitignore` |
| Example file provided | `.env.example` documents every variable without real values | `.env.example` |
| Minimal exposure | `SecretStr.get_secret_value()` called only at point-of-use (API client init) | Agent & router modules |
| Startup masking | `start.sh` prints only the first 20 characters of tokens in its config summary | `start.sh` |

### How Secrets Flow

```
.env  -->  pydantic-settings (SecretStr)  -->  get_secret_value() at API call site
            ^                                         |
            |  Never logged, never serialised         v
            +--- structlog sees SecretStr repr: '**********'
```

---

## 2. Secret Scanning (Pre-Commit)

Gitleaks runs on every `git commit` to prevent credentials from entering version control.

**Configuration**: `.gitleaks.toml`

### What It Detects

| Rule | Pattern | Example |
|------|---------|---------|
| Discord bot tokens | `[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}` | `MTk...` |
| Discord webhooks | `discord(app)?\.com/api/webhooks/\d+/[\w-]+` | Webhook URLs |
| Google/Gemini API keys | `AIza[0-9A-Za-z\\-_]{35}` | `AIzaSy...` |
| Anthropic API keys | `sk-ant-api03-[A-Za-z0-9_-]{95,100}` | `sk-ant-api03-...` |
| OpenAI API keys | `sk-[a-zA-Z0-9]{48}` | `sk-...` |
| GitHub PATs | `ghp_[A-Za-z0-9_]{36}` | `ghp_...` |
| AWS access keys | `AKIA[0-9A-Z]{16}` | `AKIA...` |
| Slack tokens | `xox[baprs]-[A-Za-z0-9-]+` | `xoxb-...` |
| Private keys | `BEGIN.*PRIVATE KEY` | PEM/SSH/PGP keys |
| JWT tokens | `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+` | `eyJhbG...` |
| Passwords in URLs | `://[^/\s]+:[^/\s]+@` | `postgres://user:pass@host` |
| High-entropy strings | Shannon entropy > 4.5 | Random-looking hex/base64 |

### False Positive Filtering

The allowlist avoids blocking legitimate code:

- **Paths**: `.env.example`, `README.md`, `docs/`, test fixtures
- **Patterns**: `test-*-key`, `.get_secret_value()` calls, hash patterns
- **Gitignored files**: `.env`, `data/`, `ollama_models/` are excluded automatically

---

## 3. Input Validation & Prompt Injection Defence

**File**: `src/zetherion_ai/discord/security.py`

Every user message is checked before being forwarded to LLM backends.

### Detection Techniques

1. **17 regex patterns** covering prompt injection variations:
   - "ignore previous/prior instructions" (with spacing/punctuation tolerance)
   - "disregard/forget/override your rules"
   - "you are now a..." role reassignment
   - "act as if / pretend to be"
   - "new instructions:" injection headers
   - "system prompt/message:" attempts
   - "jailbreak", "DAN mode", "developer mode" keywords
   - "disable/bypass filters/safety/restrictions"
   - All patterns are case-insensitive

2. **Roleplay marker heuristic**: Flags messages with > 5 brackets or `(system` markers, which indicate structured injection attempts.

3. **Unicode obfuscation detection**: Compares NFKC-normalised text to original. A length difference > 10% indicates homoglyph substitution (e.g. Cyrillic "а" replacing Latin "a" to bypass keyword filters).

### Defence Behaviour

- Flagged messages are logged with `potential_prompt_injection_detected` and the matched pattern.
- The message is rejected before reaching any LLM.
- Graceful degradation: if the Unicode check fails, it is skipped rather than crashing.

---

## 4. Access Control

### User Allowlist

| Mode | Behaviour |
|------|-----------|
| `ALLOWED_USER_IDS` set | Only listed Discord user IDs can interact with the bot |
| `ALLOWED_USER_IDS` empty | All users permitted (logs a warning at startup) |

Users can be added/removed at runtime via `UserAllowlist.add()` / `.remove()`. All changes are logged.

### Rate Limiting

| Parameter | Default |
|-----------|---------|
| Max messages per window | 10 |
| Window duration | 60 seconds |
| Warning cooldown | 30 seconds |

Per-user tracking with automatic timestamp cleanup. Exceeding the limit returns a user-facing warning (throttled to one warning per cooldown period).

---

## 5. Static Analysis & Code Quality

### Tools That Run on Every Commit (Pre-Commit Hooks)

| Tool | Version | What It Checks |
|------|---------|---------------|
| **Ruff** linter | v0.2.2 | PEP 8, unused imports, complexity, bugbear, simplify (rule sets: E, F, I, N, W, UP, B, C4, SIM) |
| **Ruff** formatter | v0.2.2 | Consistent formatting, 100-char line length |
| **mypy** | v1.8.0 | Full strict type checking (`strict = true`) on `src/zetherion_ai` |
| **Bandit** | 1.7.7 | Common Python security issues (SQL injection, hardcoded passwords, exec calls) |
| **Hadolint** | v2.12.0 | Dockerfile best practices |
| **pre-commit-hooks** | v4.5.0 | Large files (>1MB), merge conflicts, YAML/TOML/JSON syntax, trailing whitespace, private key detection |

### CodeQL (GitHub Advanced Security)

- **File**: `.github/workflows/codeql.yml`
- **Schedule**: Every Tuesday + on push/PR to `main`
- **Language**: Python semantic analysis
- Detects injection flaws, insecure deserialization, crypto weaknesses, etc.

---

## 6. Dependency Management

| Control | Implementation |
|---------|---------------|
| **Pinned versions** | `requirements.txt` pins every dependency to exact versions (e.g. `discord.py==2.4.0`) |
| **Dependabot** | `.github/dependabot.yml` checks pip (weekly), Docker images (weekly), GitHub Actions (monthly) |
| **Separated environments** | `requirements.txt` (production) vs `requirements-dev.txt` (dev tools) |
| **No cache in image** | `pip install --no-cache-dir` prevents stale packages in containers |
| **Multi-version testing** | CI runs tests on Python 3.12 and 3.13 |

---

## 7. Container Security

### Distroless Containers (Updated 2026-02-07)

Zetherion AI now uses **Google's distroless base images** for all production containers, providing a significant security improvement over traditional container images.

#### What Are Distroless Containers?

Distroless images contain only your application and its runtime dependencies. They **do not include**:
- ❌ Shell (`/bin/sh`, `/bin/bash`)
- ❌ Package managers (`apt`, `yum`, `apk`)
- ❌ System utilities (`curl`, `wget`, `nc`, etc.)
- ❌ OS libraries not required by the application

#### Security Benefits

| Feature | Traditional (`python:3.11-slim`) | Distroless (`gcr.io/distroless/python3-debian12`) |
|---------|-----------------------------------|---------------------------------------------------|
| **Image Size** | ~150MB | ~50MB (70% smaller) |
| **Shell Access** | ✅ `/bin/bash`, `/bin/sh` | ❌ No shell |
| **Package Managers** | ✅ `apt`, `dpkg` | ❌ None |
| **System Utilities** | ✅ `curl`, `wget`, `nc`, etc. | ❌ None |
| **Default User** | `root` (UID 0) | `nonroot` (UID 65532) |
| **Attack Surface** | Large (hundreds of binaries) | Minimal (Python runtime + app only) |
| **CVE Count** | High (OS packages + Python) | Low (Python runtime only) |
| **GitHub Security Scan** | ⚠️ Multiple vulnerabilities | ✅ Zero critical/high CVEs |

#### Multi-Stage Build Process

```dockerfile
# Stage 1: Builder (python:3.11-slim)
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt
COPY src ./src
ENV PYTHONPATH=/app/src
# Verify imports work before creating runtime image
RUN python -c "from zetherion_ai.discord.bot import ZetherionAIBot; print('✓ Verified')"

# Stage 2: Runtime (distroless)
FROM gcr.io/distroless/python3-debian12:nonroot
COPY --from=builder /root/.local /root/.local
COPY --from=builder /app/src /app/src
ENV PYTHONPATH=/app/src
ENV PATH=/root/.local/bin:$PATH
CMD ["python", "-m", "zetherion_ai"]
```

**Key Features:**
1. **Builder stage**: Uses full Python image with pip to install dependencies
2. **Import verification**: Tests all imports work before creating runtime image
3. **Runtime stage**: Copies only installed packages and application code
4. **Non-root user**: Runs as UID 65532 (`nonroot`) by default
5. **No shell**: ENTRYPOINT is `/usr/bin/python3.11`, CMD only passes arguments

#### Attack Surface Reduction

**Before (Traditional Container):**
Attacker gains code execution → Can use shell to:
- ❌ Download malware with `curl`/`wget`
- ❌ Scan network with `nc`/`nmap`
- ❌ Install packages with `apt install`
- ❌ Escalate privileges with system utilities
- ❌ Persist with cron jobs or systemd services

**After (Distroless Container):**
Attacker gains code execution → Limited to Python:
- ✅ No shell to execute commands
- ✅ No package managers to install tools
- ✅ No system utilities for reconnaissance
- ✅ Can only use Python standard library
- ✅ Runs as non-root (UID 65532)
- ✅ Minimal filesystem (only app code + Python libs)

#### Verification

```bash
# Try to get a shell (will fail - no shell in distroless)
docker exec -it zetherion-ai-bot /bin/sh
# Error: exec: "/bin/sh": stat /bin/sh: no such file or directory

# Verify running as non-root
docker exec zetherion-ai-bot python -c "import os; print(f'UID: {os.getuid()}')"
# UID: 65532 (nonroot)

# Check image size
docker images | grep zetherion-ai
# zetherion-ai-bot    latest    ...    ~50MB (vs ~150MB traditional)
```

### Dockerfile Controls

### Docker Compose (`docker-compose.yml`)

| Control | Detail |
|---------|--------|
| **Health checks** | All services (Qdrant, Ollama) have TCP health checks with intervals, timeouts, retries, and start periods |
| **Restart policy** | `unless-stopped` for resilience |
| **Service dependency** | Bot waits for `qdrant: service_healthy` before starting |
| **Network isolation** | All services on a dedicated `zetherion_ai-net` bridge network |
| **Named volumes** | `qdrant_storage`, `ollama_models` for persistent data |
| **No privileged mode** | Containers run without elevated privileges |

---

## 8. CI/CD Pipeline Security

**File**: `.github/workflows/ci.yml`

### Pipeline Architecture (6 Jobs + Summary)

```
Push/PR to main or develop
          |
          v
   +------+------+-------+
   |      |      |       |
  Lint  Types  Security  Docker Build
   |      |      |       |
   +------+------+       |
          |               |
          v               |
     Unit Tests           |
    (Py 3.12, 3.13)      |
          |               |
          +-------+-------+
                  |
                  v
          Integration Tests
         (E2E with Docker)
                  |
                  v
            CI Summary
       (aggregates results)
```

### Job Details

| Job | Duration | What It Does |
|-----|----------|-------------|
| **Lint** | ~5s | `ruff check` + `ruff format --check` on `src/` and `tests/` |
| **Type Check** | ~10s | `mypy src/zetherion_ai` in strict mode |
| **Security** | ~10s | `bandit -r src/` for Python security issues |
| **Test** | ~60s | Unit tests on Python 3.12 + 3.13 matrix, coverage to Codecov |
| **Docker Build** | ~30s | Buildx with GHA caching, validates `docker compose config` |
| **Integration** | ~2-3min | Full E2E with Docker Compose, uploads logs on failure, auto-cleanup |
| **Summary** | ~5s | Markdown table in PR, fails if any check failed |

### Security-Specific CI Controls

- **Secrets via GitHub Actions secrets**: `DISCORD_TOKEN`, `GEMINI_API_KEY`, etc. are injected at runtime, never stored in code.
- **Conditional integration tests**: Skip with `[skip integration]` in commit message.
- **Artifact retention**: Coverage reports (30 days), failure logs (7 days).
- **Container cleanup**: `docker compose down -v` runs in `if: always()` block.

---

## 9. Testing Strategy

### Three-Tier Testing Approach

| Tier | When | Duration | What |
|------|------|----------|------|
| **Pre-commit** | On `git commit` | ~5-10s | Linting, formatting, secret scanning, Bandit, Hadolint |
| **Pre-push** | On `git push` | ~30-60s | Ruff, mypy, full pytest suite with coverage |
| **CI/CD** | On push/PR | ~5-10min | All of the above + Docker build + integration tests + CodeQL |

### Test Coverage

**Overall Coverage: 87.58%** (255 unit tests + 14 integration tests + 4 Discord E2E tests)

| Module | Coverage | Tests | What's Tested |
|--------|----------|-------|--------------|
| **Router Factory** | 100% | 12 | Async/sync factory functions, health checks, Ollama→Gemini fallback, error handling, backend selection validation |
| **Config** | 96.88% | 49 | Settings validation, SecretStr handling, field validators, environment variable isolation, comma-separated parsing |
| **Security** | 94.12% | 37 | Rate limiter (under/over limit, per-user isolation), user allowlist (empty/configured, add/remove), 24+ prompt injection patterns, Unicode obfuscation, false positive prevention |
| **Agent Core** | 94.76% | 41 | Agent initialisation, context building, retry logic with exponential backoff, dual-generator responses, memory operations |
| **Discord Bot** | 89.92% | 30 | Command handling (/ask, /remember, /search, /channels), authorization, rate limiting, message splitting (2000 char limit), DM vs mention handling, agent not ready edge cases |
| **Qdrant Memory** | 88.73% | 7 | Vector DB operations (store, search, delete), async client usage, collection management |
| **Router (Gemini)** | 83.19% | 21 | Gemini routing, JSON parsing, classification, simple response generation, error handling |
| **Router (Ollama)** | 98.00% | 26 | Ollama routing, model selection, health checks, fallback behaviour, timeout handling |
| **Embeddings** | 100% | 5 | Embedding generation, batch processing, parallel operations |
| **Integration Tests** | N/A | 14 | Full stack: Docker services healthy (Qdrant + Ollama), collections exist, message flow through both Gemini and Ollama backends, memory persistence, conversation context |
| **Discord E2E Tests** | N/A | 4 | Real Discord API: bot responses, complex queries, memory recall validation (LLM-based), mention handling |

### Recent Test Improvements (Phase 1 & 2)

**Phase 1: Fixed All Test Failures**
- Fixed 10 config tests with environment variable isolation using `monkeypatch.delenv()`
- Fixed 13 agent core tests with improved mocking and assertions
- Fixed 3 security tests with enhanced prompt injection pattern detection
- Fixed 14 Docker integration tests with proper `.env` loading and container cleanup
- Added `pythonpath = ["src"]` to pytest configuration for proper module imports

**Phase 2: Improved Coverage to 87.58%**
- **Router Factory**: 26% → 100% (added 12 comprehensive tests for factory pattern, health checks, fallback logic)
- **Discord Bot**: 68.55% → 89.92% (added 11 edge case tests for /channels command, message splitting, agent readiness)
- **Overall**: ~42% → 87.58% (net gain of 45.58 percentage points)

All 255 unit tests now pass with zero failures, comprehensive edge case coverage, and proper async/await support throughout.

### Integration Test Parametrisation

Tests run against **both** router backends automatically:

```python
@pytest.fixture(scope="module", params=["gemini", "ollama"])
def router_backend(request, docker_env):
    ...
```

This produces 14 tests (7 scenarios x 2 backends) with no code duplication.

### Coverage

- Branch coverage enabled
- HTML + XML reports generated
- Uploaded to Codecov on Python 3.12 runs
- Exclusions: `pragma: no cover`, `__repr__`, `TYPE_CHECKING`, abstract methods

---

## 10. Logging & Monitoring

**File**: `src/zetherion_ai/logging.py`

| Control | Detail |
|---------|--------|
| **Structured logging** | `structlog` with JSON output (production) or coloured console (development) |
| **Log rotation** | `RotatingFileHandler`: 10MB max, 5 backups |
| **No credential leakage** | `SecretStr` objects log as `'**********'`, never the actual value |
| **Security events logged** | Prompt injection attempts, allowlist changes, rate limit triggers |
| **Third-party noise suppression** | Discord and httpx loggers set to WARNING |

---

## 11. Network Security

| Control | Detail |
|---------|--------|
| **Docker bridge network** | All services communicate on `zetherion_ai-net`, isolated from host network by default |
| **No exposed ports in production** | Only Qdrant (6333) and Ollama (11434) expose ports (for dev/test); the bot container exposes none |
| **TLS for external APIs** | All API clients (Anthropic, OpenAI, Gemini) use HTTPS by default via `httpx` |
| **No inbound web server** | The bot connects outbound to Discord's gateway; it does not listen on any HTTP port |

---

## 12. Data Encryption (Phase 5A)

**Files**: `src/zetherion_ai/security/encryption.py`, `src/zetherion_ai/security/keys.py`

Zetherion AI implements **application-layer encryption** for sensitive data stored in Qdrant. This protects data at rest even if the database is compromised.

### Encryption Architecture

```
User Message --> Gemini Embedding --> Qdrant Payload
                                          |
                                          v
                              FieldEncryptor.encrypt_payload()
                                          |
                                          v
                              "content" field encrypted with AES-256-GCM
                                          |
                                          v
                              Stored in Qdrant (ciphertext)
```

### Cryptographic Controls

| Control | Implementation | Why |
|---------|---------------|-----|
| **Algorithm** | AES-256-GCM (authenticated encryption) | Industry standard, provides both confidentiality and integrity |
| **Key Derivation** | PBKDF2-HMAC-SHA256, 600,000 iterations | OWASP-recommended iteration count for password-based keys |
| **Nonce Generation** | 96-bit random nonce per encryption | Prevents nonce reuse attacks; GCM standard |
| **Salt** | 256-bit random, persisted to `data/salt.bin` | Unique per installation, prevents rainbow table attacks |
| **Passphrase** | User-provided via `ENCRYPTION_PASSPHRASE` (min 16 chars) | SecretStr, never logged |

### What Gets Encrypted

| Collection | Encrypted Field(s) | Plaintext Fields |
|------------|-------------------|------------------|
| `conversations` | `content` | `user_id`, `channel_id`, `role`, `timestamp` |
| `long_term_memory` | `content` | `type`, `timestamp`, metadata |
| `user_profiles` (Phase 5C) | `key`, `value` | `category`, `confidence`, `user_id` |
| `skill_tasks` (Phase 5E) | `title`, `description` | `status`, `priority`, `deadline` |

### Security Properties

- **Embeddings remain unencrypted**: Required for vector similarity search. Property-preserving encryption is a future enhancement.
- **Decryption failures**: Logged and passed through (graceful handling of legacy unencrypted data).
- **Key rotation**: Supported via `KeyManager.rotate_key()` but requires data migration.
- **Tamper detection**: GCM authentication tag detects any modification to ciphertext.

### Enabling Encryption

```bash
# 1. Generate a strong passphrase
openssl rand -base64 32

# 2. Add to .env
ENCRYPTION_ENABLED=true
ENCRYPTION_PASSPHRASE="your-generated-passphrase-here"

# 3. Salt file created automatically on first run at data/salt.bin
```

### TLS for Qdrant (In-Transit Encryption)

For complete protection, TLS can be enabled for Qdrant connections:

```bash
# Generate self-signed certificates
./scripts/generate-qdrant-certs.sh

# Enable in .env
QDRANT_USE_TLS=true

# Uncomment TLS mounts in docker-compose.yml
```

This provides encryption in transit between the bot container and Qdrant, completing the defense-in-depth for stored data.

---

## 13. Gap Analysis & Recommendations

The following automated security best practices have been **fully implemented** as of 2026-02-06. This section documents what was done and serves as a reference for the security controls now in place.

### HIGH IMPACT

#### 12.1 Container Image Scanning

**Gap**: Docker images are built but never scanned for OS-level vulnerabilities (outdated `libc`, OpenSSL, etc.).

**Recommendation**: Add Trivy or Grype scanning to CI.

```yaml
# Example: Add to .github/workflows/ci.yml after docker-build
- name: Run Trivy vulnerability scanner
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: zetherion_ai:test
    format: 'sarif'
    output: 'trivy-results.sarif'
    severity: 'CRITICAL,HIGH'

- name: Upload Trivy scan results
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: 'trivy-results.sarif'
```

This integrates with GitHub's Security tab for a unified vulnerability view.

**References**:
- [Trivy GitHub Action](https://github.com/aquasecurity/trivy-action)
- [OWASP Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html)

---

#### 12.2 Pin Docker Base Images by Digest

**Gap**: `python:3.12-slim` and `qdrant/qdrant:latest` use mutable tags. A compromised or broken upstream push would silently affect builds.

**Recommendation**: Pin by SHA256 digest in `Dockerfile` and `docker-compose.yml`.

```dockerfile
# Instead of:
FROM python:3.12-slim
# Use:
FROM python:3.12-slim@sha256:<digest>
```

Update digests via Dependabot (already configured for Docker ecosystem).

**References**:
- [Docker Image Pinning Best Practices](https://docs.docker.com/develop/security-best-practices/)
- [Chainguard Images](https://www.chainguard.dev/chainguard-images) (minimal, signed alternatives)

---

#### 12.3 Software Bill of Materials (SBOM)

**Gap**: No SBOM is generated for the container image or Python dependencies. This makes it harder to respond to new CVEs (e.g. "are we affected by CVE-XXXX in library Y?").

**Recommendation**: Generate SBOM during Docker build and store as a build artifact.

```yaml
- name: Generate SBOM
  uses: anchore/sbom-action@v0
  with:
    image: zetherion_ai:test
    format: spdx-json
    output-file: sbom.spdx.json

- name: Upload SBOM
  uses: actions/upload-artifact@v4
  with:
    name: sbom
    path: sbom.spdx.json
```

**References**:
- [NTIA SBOM Minimum Elements](https://www.ntia.gov/page/software-bill-of-materials)
- [Anchore SBOM Action](https://github.com/anchore/sbom-action)

---

#### 12.4 Signed Commits & Branch Protection

**Gap**: No enforcement of signed commits or branch protection rules on `main`.

**Recommendation**:
1. Enable **branch protection** on `main`: require PR reviews, status checks to pass, and linear history.
2. Require **GPG or SSH signed commits** to prevent commit spoofing.
3. Enable **CODEOWNERS** for critical paths (`src/zetherion_ai/discord/security.py`, `.github/workflows/`, `.gitleaks.toml`).

```
# .github/CODEOWNERS
/.github/       @jameshinton
/src/zetherion_ai/discord/security.py  @jameshinton
/.gitleaks.toml @jameshinton
```

**References**:
- [GitHub Branch Protection Rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-a-branch-protection-rule)
- [Signing Commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits)

---

### MEDIUM IMPACT

#### 12.5 `pip-audit` for Known Vulnerability Scanning

**Gap**: Dependencies are pinned but not checked against CVE databases. Dependabot covers this partially, but `pip-audit` gives faster feedback in CI.

**Recommendation**: Add `pip-audit` as a CI step.

```yaml
- name: Audit Python dependencies
  run: |
    pip install pip-audit
    pip-audit -r requirements.txt --strict
```

**References**:
- [pip-audit](https://github.com/pypa/pip-audit)
- [OSV (Open Source Vulnerabilities)](https://osv.dev/)

---

#### 12.6 Read-Only Filesystem in Production Container

**Gap**: The bot container's filesystem is writable. A compromised process could modify application code.

**Recommendation**: Set `read_only: true` in `docker-compose.yml` and use `tmpfs` for writable directories.

```yaml
zetherion_ai:
  read_only: true
  tmpfs:
    - /tmp
  volumes:
    - ./data:/app/data
    - ./logs:/app/logs
```

**References**:
- [Docker Compose read_only](https://docs.docker.com/reference/compose-file/services/#read_only)
- [CIS Docker Benchmark 5.12](https://www.cisecurity.org/benchmark/docker)

---

#### 12.7 Runtime Security Headers / Resource Limits

**Gap**: No CPU/memory limits on containers. A runaway process (e.g. OOM from a large embedding batch) could starve other services.

**Recommendation**: Set resource limits in `docker-compose.yml`.

```yaml
zetherion_ai:
  deploy:
    resources:
      limits:
        cpus: '2.0'
        memory: 2G
      reservations:
        cpus: '0.5'
        memory: 512M
```

**References**:
- [Docker Compose Resource Constraints](https://docs.docker.com/reference/compose-file/deploy/#resources)

---

#### 12.8 Non-Root User in Dockerfile

**Status**: ✅ **IMPLEMENTED** (2026-02-07)

**Solution**: Migrated to Google's distroless images which run as `nonroot` (UID 65532) by default.

```dockerfile
# Runtime stage uses distroless:nonroot variant
FROM gcr.io/distroless/python3-debian12:nonroot
# Automatically runs as UID 65532 (nonroot)
```

**Benefits**:
- No need to manually create user (distroless handles it)
- Consistent non-root UID across all deployments
- Cannot accidentally run as root
- Passes CIS Docker Benchmark 4.1

**Verification**:
```bash
docker exec zetherion-ai-bot python -c "import os; print(f'UID: {os.getuid()}')"
# Output: UID: 65532
```

**References**:
- [Dockerfile USER best practice](https://docs.docker.com/develop/develop-images/dockerfile_best-practices/#user)
- [CIS Docker Benchmark 4.1](https://www.cisecurity.org/benchmark/docker)
- [Google Distroless Nonroot Images](https://github.com/GoogleContainerTools/distroless#nonroot-images)

---

#### 12.9 GitHub Actions Workflow Hardening

**Gap**: Actions use major version tags (e.g. `actions/checkout@v4`) which are mutable. A supply chain attack on an action would affect all workflows.

**Recommendation**: Pin actions by SHA.

```yaml
# Instead of:
uses: actions/checkout@v4
# Use:
uses: actions/checkout@<full-sha>
```

Also add `permissions` blocks to limit GITHUB_TOKEN scope per job:

```yaml
jobs:
  lint:
    permissions:
      contents: read
```

**References**:
- [GitHub Actions Security Hardening](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- [StepSecurity Harden-Runner](https://github.com/step-security/harden-runner)

---

### LOW IMPACT / NICE-TO-HAVE

#### 12.10 Pre-Commit Hook Integrity Verification

**Gap**: `pre-commit` hooks can be skipped with `--no-verify`. There is no enforcement that hooks actually ran.

**Recommendation**: Add a CI step that runs `pre-commit run --all-files` to catch any commits that bypassed local hooks. This is effectively a safety net.

```yaml
- name: Run pre-commit checks
  uses: pre-commit/action@v3.0.1
```

**References**:
- [pre-commit CI action](https://github.com/pre-commit/action)

---

#### 12.11 Security Policy & Vulnerability Reporting

**Gap**: No `SECURITY.md` at the repository root (GitHub's standard location) for vulnerability reporting instructions.

**Recommendation**: Add a root-level `SECURITY.md` with:
- Supported versions
- How to report vulnerabilities (email or GitHub Security Advisories)
- Expected response timeline

**References**:
- [GitHub Security Policy](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository)

---

#### 12.12 Automated License Compliance

**Gap**: No automated check that all dependencies use compatible licenses (the project is MIT-licensed).

**Recommendation**:

```yaml
- name: Check dependency licenses
  run: |
    pip install pip-licenses
    pip-licenses --allow-only="MIT;BSD;Apache-2.0;ISC;PSF;Python-2.0" --fail-on-violation
```

**References**:
- [pip-licenses](https://github.com/raimon49/pip-licenses)

---

### Summary Matrix

| # | Recommendation | Impact | Effort | Status |
|---|---------------|--------|--------|--------|
| 12.1 | Container image scanning (Trivy) | HIGH | Low | ✅ Implemented |
| 12.2 | Pin Docker images by digest | HIGH | Low | ✅ Implemented |
| 12.3 | SBOM generation | HIGH | Low | ✅ Implemented |
| 12.4 | Signed commits & branch protection | HIGH | Medium | ✅ Implemented |
| 12.5 | `pip-audit` in CI | MEDIUM | Low | ✅ Implemented |
| 12.6 | Read-only container filesystem | MEDIUM | Low | ✅ Implemented |
| 12.7 | Container resource limits | MEDIUM | Low | ✅ Implemented |
| 12.8 | Explicit non-root user in Dockerfile | MEDIUM | Low | ✅ Implemented |
| 12.9 | Pin GitHub Actions by SHA | MEDIUM | Medium | ✅ Implemented |
| 12.10 | Pre-commit in CI (safety net) | LOW | Low | ✅ Implemented |
| 12.11 | Security policy at repo root | LOW | Low | ✅ Implemented |
| 12.12 | Automated license compliance | LOW | Low | ✅ Implemented |

### Additional Security Scanning (Added 2026-02-06)

Beyond the original 12 gaps, the following additional security measures were implemented:

| Tool | Category | What It Does |
|------|----------|-------------|
| **Semgrep CE** | SAST | 3000+ Python rules with taint tracking and data-flow analysis. SARIF results uploaded to GitHub Security tab. |
| **Trivy (filesystem mode)** | Dependencies | Scans OS-level packages inside the container that pip-audit can't see |
| **GitHub Ruleset** | Access Control | Branch protection on `main`: require PRs, status checks, block force pushes |
| **CODEOWNERS** | Access Control | Assigns ownership of security-critical paths for review requirements |
