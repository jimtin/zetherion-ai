#!/usr/bin/env bash
# Pre-push hook: runs the full test suite before allowing a push.
# Any failure exits non-zero, blocking the push.
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

cleanup() {
    if [ "$DOCKER_STARTED_BY_US" = true ]; then
        echo "Tearing down Docker test environment..."
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "========================================"
echo "  Pre-push: Full test suite"
echo "========================================"

# ── Step 1: Ruff lint ──────────────────────────────────────────────
echo ""
echo "[1/7] Ruff lint check..."
ruff check src/ tests/

# ── Step 2: Unit tests ────────────────────────────────────────────
echo ""
echo "[2/7] Unit tests..."
python -m pytest tests/ \
    -m "not integration and not discord_e2e" \
    --tb=short -q

# ── Step 3: In-process integration tests (no Docker) ─────────────
echo ""
echo "[3/7] In-process integration tests..."
python -m pytest \
    tests/integration/test_skills_http.py \
    tests/integration/test_heartbeat_cycle.py \
    tests/integration/test_profile_pipeline.py \
    tests/integration/test_agent_skills_http.py \
    tests/integration/test_skills_e2e.py \
    tests/integration/test_user_isolation.py \
    tests/integration/test_encryption_at_rest.py \
    -m integration --tb=short -q

# ── Step 4: Start Docker ──────────────────────────────────────────
echo ""
echo "[4/7] Starting Docker test environment..."

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running. Start Docker Desktop and try again."
    exit 1
fi

# Tear down any stale test environment before starting fresh
echo "Tearing down any existing test environment..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true

docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build
DOCKER_STARTED_BY_US=true

# Wait for ALL services to be healthy
echo "Waiting for services..."
for i in $(seq 1 60); do
    postgres=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-postgres" 2>/dev/null || echo "missing")
    qdrant=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-qdrant" 2>/dev/null || echo "missing")
    ollama=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama" 2>/dev/null || echo "missing")
    ollama_router=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama-router" 2>/dev/null || echo "missing")
    skills=$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-skills" 2>/dev/null || echo "missing")
    bot=$(docker inspect --format='{{.State.Status}}' "${PROJECT}-bot" 2>/dev/null || echo "missing")

    if [ "$postgres" = "healthy" ] && [ "$qdrant" = "healthy" ] && [ "$ollama" = "healthy" ] && [ "$ollama_router" = "healthy" ] && [ "$skills" = "healthy" ] && [ "$bot" = "running" ]; then
        echo "All services ready (postgres=$postgres, qdrant=$qdrant, ollama=$ollama, ollama-router=$ollama_router, skills=$skills, bot=$bot)."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Services failed to become healthy within 5 minutes."
        echo "  postgres=$postgres qdrant=$qdrant ollama=$ollama ollama-router=$ollama_router skills=$skills bot=$bot"
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --tail=30
        exit 1
    fi
    sleep 5
done

# ── Step 5: Pull Ollama models ────────────────────────────────────
echo ""
echo "[5/7] Pulling Ollama models..."
docker exec "${PROJECT}-ollama-router" ollama pull llama3.2:1b >/dev/null 2>&1
docker exec "${PROJECT}-ollama" ollama pull nomic-embed-text >/dev/null 2>&1
docker exec "${PROJECT}-ollama" ollama pull llama3.1:8b >/dev/null 2>&1
echo "Models ready."

# Give bot a moment to fully initialize after models are available
sleep 5

# ── Step 6: Docker E2E tests ─────────────────────────────────────
# DOCKER_MANAGED_EXTERNALLY prevents test_e2e.py from tearing down
# the environment — the pre-push script owns the lifecycle.
echo ""
echo "[6/7] Docker E2E tests (test_e2e.py)..."
DOCKER_MANAGED_EXTERNALLY=true python -m pytest tests/integration/test_e2e.py \
    -m integration -v --tb=short -s

# ── Step 7: Discord E2E tests ────────────────────────────────────
echo ""
echo "[7/7] Discord E2E tests (test_discord_e2e.py)..."
DOCKER_MANAGED_EXTERNALLY=true python -m pytest tests/integration/test_discord_e2e.py \
    -m discord_e2e -v --tb=short -s

echo ""
echo "========================================"
echo "  All tests passed. Push allowed."
echo "========================================"
