#!/usr/bin/env bash
# Pre-push hook: runs the full test suite before allowing a push.
# Any failure exits non-zero, blocking the push.
#
# Pipeline structure:
#   Phase A (concurrent):
#     Background — Docker teardown, build, health wait, model pulls + warm-up
#     Foreground — Ruff lint, unit tests (parallel + 90% coverage gate),
#                  in-process integration tests
#   Phase B (concurrent, requires Docker):
#     All Docker E2E test files run in parallel once Docker is ready.
set -euo pipefail

# Activate virtualenv so ruff/python/pytest are available
# even when invoked by pre-commit (which doesn't inherit the venv)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$REPO_DIR/.venv/bin/activate" ]; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/.venv/bin/activate"
fi

COMPOSE_FILE="docker-compose.test.yml"
PROJECT="zetherion-ai-test"
DOCKER_STARTED_BY_US=false
DOCKER_LOG="$(mktemp)"
DOCKER_PID=""

ts() { date "+%H:%M:%S"; }

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
    # Kill any background E2E test processes
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

# Array to track background E2E test PIDs
declare -a E2E_PIDS=()

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

# ── Step 1: Ruff lint ──────────────────────────────────────────────
echo ""
echo "[$(ts)] [1/4] Ruff lint check..."
ruff check src/ tests/
echo "[$(ts)] [1/4] Ruff lint passed."

# ── Step 2: Unit tests + 90% coverage gate (parallel) ─────────────
echo ""
echo "[$(ts)] [2/4] Unit tests + coverage gate (>=90%, parallel)..."
python -m pytest tests/ \
    -m "not integration and not discord_e2e" \
    -n 8 \
    --timeout=30 \
    --tb=short -q
echo "[$(ts)] [2/4] Unit tests passed (>=90% coverage verified)."

# ── Step 3: In-process integration tests (no Docker) ──────────────
echo ""
echo "[$(ts)] [3/4] In-process integration tests..."
python -m pytest \
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
    -m integration --timeout=60 --tb=short -q --no-cov
echo "[$(ts)] [3/4] Integration tests passed."

# ═══════════════════════════════════════════════════════════════════
# Phase B: Wait for Docker, run ALL E2E tests concurrently
# ═══════════════════════════════════════════════════════════════════

echo ""
echo "[$(ts)] Waiting for Docker environment to be ready..."
wait "$DOCKER_PID"
DOCKER_EXIT=$?
DOCKER_PID=""

if [ "$DOCKER_EXIT" -ne 0 ] || grep -q "DOCKER_ERROR" "$DOCKER_LOG" 2>/dev/null; then
    echo "[$(ts)] ERROR: Docker environment failed to start."
    [ -f "$DOCKER_LOG" ] && cat "$DOCKER_LOG"
    exit 1
fi
echo "[$(ts)] Docker environment confirmed ready."

# ── Step 4: All Docker E2E tests (concurrent) ─────────────────────
echo ""
echo "[$(ts)] [4/4] Docker E2E + Discord E2E tests (concurrent)..."

# Create temp files for each parallel test output
E2E_LOG_A="$(mktemp)"    # test_e2e.py (the big one)
E2E_LOG_B="$(mktemp)"    # test_health_e2e.py + test_update_e2e.py + test_telemetry_e2e.py
E2E_LOG_C="$(mktemp)"    # test_discord_e2e.py

# Start a heartbeat so we can see the tests are still running
(while true; do sleep 30; echo "[$(ts)]   ...E2E tests still running"; done) &
HEARTBEAT_PID=$!

# Launch all 3 test groups concurrently
DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_e2e.py \
    -m integration --timeout=120 -v --tb=short -s --no-cov \
    > "$E2E_LOG_A" 2>&1 &
E2E_PIDS+=($!)

DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_health_e2e.py \
    tests/integration/test_update_e2e.py \
    tests/integration/test_telemetry_e2e.py \
    -m integration --timeout=120 -v --tb=short -s --no-cov \
    > "$E2E_LOG_B" 2>&1 &
E2E_PIDS+=($!)

DOCKER_MANAGED_EXTERNALLY=true python -m pytest \
    tests/integration/test_discord_e2e.py \
    -m discord_e2e --timeout=180 -v --tb=short -s --no-cov \
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

# Clean up temp files
rm -f "$E2E_LOG_A" "$E2E_LOG_B" "$E2E_LOG_C"

if [ "$E2E_FAILED" = true ]; then
    echo ""
    echo "[$(ts)] [4/4] E2E tests FAILED — see output above."
    exit 1
fi

echo "[$(ts)] [4/4] All E2E tests passed."

echo ""
echo "========================================"
echo "  All tests passed. Push allowed."
echo "  Finished at $(ts)"
echo "========================================"
