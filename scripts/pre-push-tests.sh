#!/usr/bin/env bash
# Pre-push hook: runs the full test suite before allowing a push.
# Any failure exits non-zero, blocking the push.
#
# Pipeline structure:
#   Phase A (concurrent):
#     Background — Docker teardown, build, health wait, model pulls + warm-up
#     Foreground — Static checks in parallel, then unit tests + mypy + pip-audit
#                  in parallel (90% coverage gate), then in-process integration
#                  tests in parallel workers.
#   Phase B (concurrent, requires Docker):
#     Required Docker E2E groups run in parallel once Docker is ready.
#     Optional E2E marker tests can run afterward (non-blocking).
set -euo pipefail

# Activate virtualenv so ruff/python/pytest are available
# even when invoked by pre-commit (which doesn't inherit the venv)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$REPO_DIR/venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/venv/bin/activate"
elif [ -f "$REPO_DIR/.venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/.venv/bin/activate"
fi

# ── Tool version pins (must match CI and requirements-dev.txt) ────────
EXPECTED_RUFF="0.8.4"

# ── All Python source directories to check ────────────────────────────
# CI pre-commit scans ALL files, so we must include updater_sidecar/ too
SRC_DIRS="src/ tests/ updater_sidecar/"
LINT_DIRS="src/ updater_sidecar/"

# License allowlist (must match CI — see .github/workflows/ci.yml)
LICENSE_ALLOWLIST="MIT License;MIT;BSD License;BSD-2-Clause;BSD-3-Clause;Apache Software License;Apache License 2.0;Apache-2.0;ISC License;ISC;Python Software Foundation License;PSF-2.0;Mozilla Public License 2.0 (MPL 2.0);MPL-2.0;Artistic License;Public Domain;The Unlicense;Unlicense;CC0-1.0;0BSD;Zlib;UNKNOWN"

COMPOSE_FILE="docker-compose.test.yml"
PROJECT="zetherion-ai-test"
DOCKER_STARTED_BY_US=false
DOCKER_LOG="$(mktemp)"
DOCKER_PID=""
STRICT_REQUIRED_TESTS="${STRICT_REQUIRED_TESTS:-true}"
RUN_OPTIONAL_E2E="${RUN_OPTIONAL_E2E:-false}"

ts() { date "+%H:%M:%S"; }

contains_skips() {
    local log_file="$1"
    rg -q "[1-9][0-9]* skipped|\\bSKIPPED\\b" "$log_file"
}

assert_no_skips_in_log() {
    local suite="$1"
    local log_file="$2"
    if [ "$STRICT_REQUIRED_TESTS" = "true" ] && contains_skips "$log_file"; then
        echo "[$(ts)] ERROR: ${suite} reported skipped tests (STRICT_REQUIRED_TESTS=true)."
        return 1
    fi
    return 0
}

start_static_check() {
    local name="$1"
    local command="$2"
    local log_file
    log_file="$(mktemp)"
    (eval "$command" > "$log_file" 2>&1) &
    STATIC_PIDS+=($!)
    STATIC_NAMES+=("$name")
    STATIC_LOGS+=("$log_file")
}

cleanup() {
    # Kill heartbeat if running
    if [ -n "${HEARTBEAT_PID:-}" ] && kill -0 "$HEARTBEAT_PID" 2>/dev/null; then
        kill "$HEARTBEAT_PID" 2>/dev/null || true
    fi
    # Kill background Docker prep if still running
    if [ -n "${DOCKER_PID:-}" ] && kill -0 "$DOCKER_PID" 2>/dev/null; then
        kill "$DOCKER_PID" 2>/dev/null || true
        wait "$DOCKER_PID" 2>/dev/null || true
    fi
    # Kill any background test processes (mypy, pip-audit, E2E)
    for pid in "${BG_PIDS[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    for pid in "${STATIC_PIDS[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    for pid in "${E2E_PIDS[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    if [ "$DOCKER_STARTED_BY_US" = true ]; then
        echo "[$(ts)] Tearing down Docker test environment..."
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true
    fi
    rm -f "$DOCKER_LOG"
}
trap cleanup EXIT

# Arrays to track background PIDs
declare -a BG_PIDS=()
declare -a E2E_PIDS=()
declare -a STATIC_PIDS=()
declare -a STATIC_NAMES=()
declare -a STATIC_LOGS=()

# ── Background Docker Preparation ──────────────────────────────────
# Runs Docker startup concurrently with fast foreground tests.
start_docker_background() {
    echo "[$(ts)] [docker] Checking Docker daemon..."
    if ! docker info >/dev/null 2>&1; then
        echo "DOCKER_ERROR: Docker is not running. Start Docker Desktop and try again." > "$DOCKER_LOG"
        return 1
    fi

    echo "[$(ts)] [docker] Tearing down any stale test environment..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true

    echo "[$(ts)] [docker] Building and starting containers..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build 2>&1 | tail -5

    # Wait for ALL services to be healthy
    echo "[$(ts)] [docker] Waiting for services to become healthy..."
    for i in $(seq 1 90); do
        postgres=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-postgres" 2>/dev/null || echo "missing")
        qdrant=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-qdrant" 2>/dev/null || echo "missing")
        ollama=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama" 2>/dev/null || echo "missing")
        ollama_router=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama-router" 2>/dev/null || echo "missing")
        skills=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-skills" 2>/dev/null || echo "missing")
        api=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-api" 2>/dev/null || echo "missing")
        bot=$(docker inspect --format='{{.State.Status}}' "${PROJECT}-bot" 2>/dev/null || echo "missing")

        if [ "$postgres" = "healthy" ] && [ "$qdrant" = "healthy" ] && [ "$ollama" = "healthy" ] && [ "$ollama_router" = "healthy" ] && [ "$skills" = "healthy" ] && [ "$api" = "healthy" ] && [ "$bot" = "running" ]; then
            echo "[$(ts)] [docker] All services ready."
            break
        fi
        if [ "$i" -eq 90 ]; then
            echo "[$(ts)] [docker] ERROR: Services failed to become healthy within 4.5 minutes."
            echo "  postgres=$postgres qdrant=$qdrant ollama=$ollama router=$ollama_router skills=$skills api=$api bot=$bot"
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --tail=30
            echo "DOCKER_ERROR: Services failed to become healthy" > "$DOCKER_LOG"
            return 1
        fi
        # Log progress every 15 seconds (every 5th iteration at 3s interval)
        if [ $((i % 5)) -eq 0 ]; then
            echo "[$(ts)] [docker]   ...waiting (pg=$postgres qd=$qdrant ol=$ollama rt=$ollama_router sk=$skills api=$api bot=$bot)"
        fi
        sleep 3
    done

    # Pull Ollama models (skip if already cached)
    echo "[$(ts)] [docker] Checking Ollama models..."

    if ! docker exec "${PROJECT}-ollama-router" ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
        echo "[$(ts)] [docker]   Pulling llama3.2:3b → ollama-router..."
        docker exec "${PROJECT}-ollama-router" ollama pull llama3.2:3b 2>&1 | tail -1
    else
        echo "[$(ts)] [docker]   llama3.2:3b already cached in ollama-router"
    fi

    if ! docker exec "${PROJECT}-ollama" ollama list 2>/dev/null | grep -q "nomic-embed-text"; then
        echo "[$(ts)] [docker]   Pulling nomic-embed-text → ollama..."
        docker exec "${PROJECT}-ollama" ollama pull nomic-embed-text 2>&1 | tail -1
    else
        echo "[$(ts)] [docker]   nomic-embed-text already cached in ollama"
    fi

    if ! docker exec "${PROJECT}-ollama" ollama list 2>/dev/null | grep -q "llama3.1:8b"; then
        echo "[$(ts)] [docker]   Pulling llama3.1:8b → ollama..."
        docker exec "${PROJECT}-ollama" ollama pull llama3.1:8b 2>&1 | tail -1
    else
        echo "[$(ts)] [docker]   llama3.1:8b already cached in ollama"
    fi

    echo "[$(ts)] [docker] Models ready."

    # Pre-warm models with a throwaway inference (loads weights into memory)
    echo "[$(ts)] [docker] Pre-warming models..."
    docker exec "${PROJECT}-ollama-router" curl -sf http://localhost:11434/api/generate \
        -d '{"model":"llama3.2:3b","prompt":"hi","stream":false}' >/dev/null 2>&1 &
    docker exec "${PROJECT}-ollama" curl -sf http://localhost:11434/api/generate \
        -d '{"model":"llama3.1:8b","prompt":"hi","stream":false}' >/dev/null 2>&1 &
    wait  # Wait for both warm-ups to complete
    echo "[$(ts)] [docker] Models pre-warmed."

    echo "DOCKER_READY" > "$DOCKER_LOG"
    echo "[$(ts)] [docker] Docker environment fully ready."
}

echo "========================================"
echo "  Pre-push: Full test suite"
echo "  Started at $(ts)"
echo "========================================"

# ═══════════════════════════════════════════════════════════════════
# Phase A: Start Docker in background, run fast tests in foreground
# ═══════════════════════════════════════════════════════════════════

echo ""
echo "[$(ts)] Starting Docker environment in background..."
start_docker_background &
DOCKER_PID=$!
DOCKER_STARTED_BY_US=true

# ── Step 1: Static analysis (parallel) ────────────────────────────
# Catches: ruff lint+format, bandit, gitleaks, hadolint, licenses,
#          Python 3.13 syntax compat — all checks CI would run.
echo ""
echo "[$(ts)] [1/5] Static analysis..."

# 1a. Ruff version check — prevents version-drift formatting failures
ACTUAL_RUFF=$(ruff version 2>/dev/null | awk '{print $2}' || echo "unknown")
if [ "$ACTUAL_RUFF" != "$EXPECTED_RUFF" ]; then
    echo "ERROR: ruff version mismatch: local=$ACTUAL_RUFF, expected=$EXPECTED_RUFF"
    echo "  Fix: pip install ruff==$EXPECTED_RUFF (see requirements-dev.txt)"
    exit 1
fi

echo "[$(ts)]   Launching static checks in parallel..."

start_static_check "ruff lint" "ruff check $SRC_DIRS"
start_static_check "ruff format" "ruff format --check $SRC_DIRS"
start_static_check "bandit" "bandit -c pyproject.toml -r $LINT_DIRS -q"

if command -v gitleaks >/dev/null 2>&1; then
    start_static_check "gitleaks" "gitleaks detect --no-git --redact --config=.gitleaks.toml"
else
    echo "  [skip] gitleaks not installed (install: brew install gitleaks)"
fi

if docker image inspect ghcr.io/hadolint/hadolint:latest >/dev/null 2>&1 || docker info >/dev/null 2>&1; then
    start_static_check \
        "hadolint" \
        "docker run --rm -v \"$(pwd):/work\" -w /work ghcr.io/hadolint/hadolint:latest hadolint --ignore DL3007 --ignore DL3008 --ignore DL3009 Dockerfile Dockerfile.updater"
else
    echo "  [skip] hadolint requires Docker (Docker not available yet)"
fi

if command -v pip-licenses >/dev/null 2>&1; then
    start_static_check "license compliance" "pip-licenses --allow-only=\"$LICENSE_ALLOWLIST\" --partial-match"
else
    echo "  [skip] pip-licenses not installed (pip install pip-licenses)"
fi

if command -v python3.13 >/dev/null 2>&1; then
    start_static_check "python3.13 compileall" "python3.13 -m compileall -q src/ updater_sidecar/"
else
    echo "  [skip] python3.13 not available for syntax compat check"
fi

STATIC_FAILED=false
for i in "${!STATIC_PIDS[@]}"; do
    name="${STATIC_NAMES[$i]}"
    log_file="${STATIC_LOGS[$i]}"
    pid="${STATIC_PIDS[$i]}"
    if wait "$pid"; then
        echo "[$(ts)]   ${name} passed."
    else
        STATIC_FAILED=true
        echo "[$(ts)]   ${name} FAILED:"
        cat "$log_file"
    fi
    rm -f "$log_file"
done
STATIC_PIDS=()
STATIC_NAMES=()
STATIC_LOGS=()

if [ "$STATIC_FAILED" = true ]; then
    exit 1
fi

echo "[$(ts)] [1/5] Static analysis passed."

# ── Step 2: Unit tests + mypy + pip-audit (parallel) ─────────────
# Unit tests (~90s), mypy (~93s), pip-audit (~14s) run concurrently.
# Wall time ≈ max(unit_tests, mypy) ≈ 93s.
echo ""
echo "[$(ts)] [2/5] Unit tests + mypy + pip-audit (parallel)..."

MYPY_LOG="$(mktemp)"
PIPAUDIT_LOG="$(mktemp)"

# Start mypy in background
mypy src/zetherion_ai/ updater_sidecar/ --config-file=pyproject.toml > "$MYPY_LOG" 2>&1 &
BG_PIDS+=($!)
MYPY_PID=$!

# Start pip-audit in background
if command -v pip-audit >/dev/null 2>&1; then
    pip-audit -r requirements.txt --strict --desc on > "$PIPAUDIT_LOG" 2>&1 &
    BG_PIDS+=($!)
    PIPAUDIT_PID=$!
else
    echo "  [skip] pip-audit not installed (pip install pip-audit)"
    PIPAUDIT_PID=""
fi

# Run unit tests in foreground (so output streams live)
python -m pytest tests/ \
    -m "not integration and not discord_e2e" \
    -n 8 \
    --timeout=30 \
    --tb=short -q

# Wait for mypy
if ! wait "$MYPY_PID"; then
    echo ""
    echo "mypy FAILED:"
    cat "$MYPY_LOG"
    rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
    exit 1
fi
echo "[$(ts)]   mypy passed."

# Wait for pip-audit
if [ -n "$PIPAUDIT_PID" ]; then
    if ! wait "$PIPAUDIT_PID"; then
        echo ""
        echo "pip-audit FAILED:"
        cat "$PIPAUDIT_LOG"
        rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
        exit 1
    fi
    echo "[$(ts)]   pip-audit passed."
fi

rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
BG_PIDS=()

echo "[$(ts)] [2/5] Unit tests + mypy + pip-audit passed."

# ── Step 3: In-process integration tests (no Docker) ──────────────
echo ""
echo "[$(ts)] [3/5] In-process integration tests..."
INTEGRATION_LOG="$(mktemp)"
if ! python -m pytest \
    tests/integration/test_skills_http.py \
    tests/integration/test_heartbeat_cycle.py \
    tests/integration/test_profile_pipeline.py \
    tests/integration/test_agent_skills_http.py \
    tests/integration/test_skills_e2e.py \
    tests/integration/test_user_isolation.py \
    tests/integration/test_encryption_at_rest.py \
    tests/integration/test_health_skill_http.py \
    tests/integration/test_update_skill_http.py \
    tests/integration/test_telemetry_http.py \
    tests/integration/test_api_http.py \
    tests/integration/test_dev_watcher_e2e.py \
    tests/integration/test_milestone_e2e.py \
    tests/integration/test_youtube_http.py \
    -m "integration and not optional_e2e" \
    -n 4 \
    --timeout=60 --tb=short -q --no-cov \
    > "$INTEGRATION_LOG" 2>&1; then
    cat "$INTEGRATION_LOG"
    rm -f "$INTEGRATION_LOG"
    exit 1
fi
cat "$INTEGRATION_LOG"
if ! assert_no_skips_in_log "In-process integration tests" "$INTEGRATION_LOG"; then
    rm -f "$INTEGRATION_LOG"
    exit 1
fi
rm -f "$INTEGRATION_LOG"
echo "[$(ts)] [3/5] Integration tests passed."

# ═══════════════════════════════════════════════════════════════════
# Phase B: Wait for Docker, run ALL E2E tests concurrently
# ═══════════════════════════════════════════════════════════════════

echo ""
echo "[$(ts)] Waiting for Docker environment to be ready..."
if ! wait "$DOCKER_PID"; then
    DOCKER_EXIT=$?
else
    DOCKER_EXIT=0
fi
DOCKER_PID=""

if [ "$DOCKER_EXIT" -ne 0 ] || grep -q "DOCKER_ERROR" "$DOCKER_LOG" 2>/dev/null; then
    echo "[$(ts)] ERROR: Docker environment failed to start."
    [ -f "$DOCKER_LOG" ] && cat "$DOCKER_LOG"
    exit 1
fi
echo "[$(ts)] Docker environment confirmed ready."

# ── Step 4: Required Docker E2E tests (concurrent) ────────────────
echo ""
echo "[$(ts)] [4/5] Required Docker E2E + Discord E2E tests (concurrent)..."

# Create temp files for each parallel test output
E2E_LOG_A="$(mktemp)"     # test_e2e.py (required)
E2E_LOG_B="$(mktemp)"     # health/update/telemetry required tests
E2E_LOG_C="$(mktemp)"     # discord required tests (optional_e2e marker excluded)
E2E_OPTIONAL_LOG="$(mktemp)"

# Start a heartbeat so we can see the tests are still running
(while true; do sleep 30; echo "[$(ts)]   ...E2E tests still running"; done) &
HEARTBEAT_PID=$!

# Launch all 3 test groups concurrently
DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_e2e.py \
    -m "integration and not optional_e2e" --timeout=120 -v --tb=short -s --no-cov \
    > "$E2E_LOG_A" 2>&1 &
E2E_PIDS+=($!)

DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_health_e2e.py \
    tests/integration/test_update_e2e.py \
    tests/integration/test_telemetry_e2e.py \
    -m "integration and not optional_e2e" --timeout=120 -v --tb=short -s --no-cov \
    > "$E2E_LOG_B" 2>&1 &
E2E_PIDS+=($!)

DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_discord_e2e.py \
    -m "discord_e2e and not optional_e2e" --timeout=180 -v --tb=short -s --no-cov \
    > "$E2E_LOG_C" 2>&1 &
E2E_PIDS+=($!)

# Wait for all to finish, track failures
E2E_FAILED=false

for i in "${!E2E_PIDS[@]}"; do
    pid="${E2E_PIDS[$i]}"
    if ! wait "$pid"; then
        E2E_FAILED=true
        case $i in
            0) echo "[$(ts)] FAILED: Docker E2E tests (test_e2e.py)" ;;
            1) echo "[$(ts)] FAILED: Health/Update/Telemetry E2E tests" ;;
            2) echo "[$(ts)] FAILED: Discord E2E tests" ;;
        esac
    else
        case $i in
            0) echo "[$(ts)] PASSED: Docker E2E tests (test_e2e.py)" ;;
            1) echo "[$(ts)] PASSED: Health/Update/Telemetry E2E tests" ;;
            2) echo "[$(ts)] PASSED: Discord E2E tests" ;;
        esac
    fi
done
E2E_PIDS=()

# Stop heartbeat
kill "$HEARTBEAT_PID" 2>/dev/null || true
wait "$HEARTBEAT_PID" 2>/dev/null || true
unset HEARTBEAT_PID

# Print all outputs
echo ""
echo "═══ Docker E2E output ═══"
cat "$E2E_LOG_A"
echo ""
echo "═══ Health/Update/Telemetry E2E output ═══"
cat "$E2E_LOG_B"
echo ""
echo "═══ Discord E2E output ═══"
cat "$E2E_LOG_C"

if ! assert_no_skips_in_log "Docker E2E tests (test_e2e.py)" "$E2E_LOG_A"; then
    E2E_FAILED=true
fi
if ! assert_no_skips_in_log "Health/Update/Telemetry E2E tests" "$E2E_LOG_B"; then
    E2E_FAILED=true
fi
if ! assert_no_skips_in_log "Discord E2E tests" "$E2E_LOG_C"; then
    E2E_FAILED=true
fi

# Optional E2E tests are non-blocking and can be enabled via RUN_OPTIONAL_E2E=true.
if [ "$RUN_OPTIONAL_E2E" = "true" ]; then
    echo ""
    echo "[$(ts)] Running optional E2E tests (non-blocking)..."
    if DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
        tests/integration/test_health_e2e.py \
        tests/integration/test_discord_e2e.py \
        -m optional_e2e --timeout=180 -v --tb=short -s --no-cov \
        > "$E2E_OPTIONAL_LOG" 2>&1; then
        echo "[$(ts)] Optional E2E tests passed."
    else
        echo "[$(ts)] Optional E2E tests FAILED (non-blocking)."
    fi
    echo ""
    echo "═══ Optional E2E output ═══"
    cat "$E2E_OPTIONAL_LOG"
else
    echo "[$(ts)] Optional E2E tests skipped (set RUN_OPTIONAL_E2E=true to run)."
fi

# Clean up temp files
rm -f "$E2E_LOG_A" "$E2E_LOG_B" "$E2E_LOG_C" "$E2E_OPTIONAL_LOG"

if [ "$E2E_FAILED" = true ]; then
    echo ""
    echo "[$(ts)] [4/5] E2E tests FAILED — see output above."
    exit 1
fi

echo "[$(ts)] [4/5] All E2E tests passed."

# ── Step 5: Summary ──────────────────────────────────────────────
echo ""
echo "========================================"
echo "  All tests passed. Push allowed."
echo "  Finished at $(ts)"
echo "========================================"
