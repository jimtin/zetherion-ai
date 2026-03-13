#!/usr/bin/env bash
# Full local gate: runs the full test suite before allowing a push.
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
DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"
USE_DOCKER_PYTHON="${ZETHERION_USE_DOCKER_PYTHON:-false}"
EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"
DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"

is_generated_e2e_env_file() {
    local env_file="${1:-}"
    case "$env_file" in
        */zetherion-e2e-runs/stacks/*/run.env|*/.artifacts/e2e-runs/stacks/*/run.env|*/.artifacts/ci-e2e-runs/stacks/*/run.env)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

resolve_repo_env_file() {
    if [ -n "$EXPLICIT_ZETHERION_ENV_FILE" ]; then
        if [ ! -f "$EXPLICIT_ZETHERION_ENV_FILE" ]; then
            if is_generated_e2e_env_file "$EXPLICIT_ZETHERION_ENV_FILE"; then
                echo "WARN: Ignoring missing generated E2E env file: $EXPLICIT_ZETHERION_ENV_FILE" >&2
            else
                echo "ERROR: ZETHERION_ENV_FILE points to a missing file: $EXPLICIT_ZETHERION_ENV_FILE"
                exit 1
            fi
        else
            printf '%s\n' "$EXPLICIT_ZETHERION_ENV_FILE"
            return 0
        fi
    fi

    if [ -f "$DEFAULT_ZETHERION_ENV_FILE" ]; then
        printf '%s\n' "$DEFAULT_ZETHERION_ENV_FILE"
        return 0
    fi

    return 1
}

source_repo_env_file() {
    local env_file="$1"
    local normalized_env
    normalized_env="$(mktemp "${TMPDIR:-/tmp}/zetherion-pre-push-env.XXXXXX")"
    tr -d '\r' <"$env_file" >"$normalized_env"
    set -a
    # shellcheck disable=SC1090
    source "$normalized_env"
    set +a
    rm -f "$normalized_env"
}

python_supports_required_version() {
    local python_bin="$1"
    "$python_bin" - <<'PY' >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

if [ "$USE_DOCKER_PYTHON" != "true" ] \
    && [ -f "$REPO_DIR/venv/bin/activate" ] \
    && [ -x "$REPO_DIR/venv/bin/python" ] \
    && python_supports_required_version "$REPO_DIR/venv/bin/python"; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/venv/bin/activate"
elif [ "$USE_DOCKER_PYTHON" != "true" ] \
    && [ -f "$REPO_DIR/.venv/bin/activate" ] \
    && [ -x "$REPO_DIR/.venv/bin/python" ] \
    && python_supports_required_version "$REPO_DIR/.venv/bin/python"; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/.venv/bin/activate"
fi

resolve_python_bin() {
    if [ "$USE_DOCKER_PYTHON" = "true" ]; then
        printf '%s\n' "$DOCKER_PYTHON_WRAPPER"
        return 0
    fi

    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/bin/python3" \
        "$REPO_DIR/venv/bin/python3"; do
        if [ -x "$candidate" ] && python_supports_required_version "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    for candidate in python3.12 python3 python; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        if python_supports_required_version "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(resolve_python_bin || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Could not find Python executable (expected .venv/bin/python or python3 on PATH)."
    exit 1
fi

run_python_module() {
    "$PYTHON_BIN" -m "$@"
}

run_ruff() {
    run_python_module ruff "$@"
}

run_mypy() {
    run_python_module mypy "$@"
}

run_bandit() {
    run_python_module bandit "$@"
}

run_pip_audit() {
    run_python_module pip_audit "$@"
}

run_pip_licenses() {
    run_python_module piplicenses "$@"
}

# ── Tool version pins (must match CI and requirements-dev.txt) ────────
EXPECTED_RUFF="0.8.4"

# ── All Python source directories to check ────────────────────────────
# CI pre-commit scans ALL files, so we must include updater_sidecar/ too
SRC_DIRS="src/ tests/ updater_sidecar/"
LINT_DIRS="src/ updater_sidecar/"

# License allowlist (must match CI — see .github/workflows/ci.yml)
LICENSE_ALLOWLIST="MIT License;MIT;BSD License;BSD-2-Clause;BSD-3-Clause;Apache Software License;Apache License 2.0;Apache-2.0;ISC License;ISC;Python Software Foundation License;PSF-2.0;Mozilla Public License 2.0 (MPL 2.0);MPL-2.0;Artistic License;Public Domain;The Unlicense;Unlicense;CC0-1.0;0BSD;Zlib;UNKNOWN"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.test.yml}"
PROJECT="${PROJECT:-zetherion-ai-test}"
DOCKER_STARTED_BY_US=false
DOCKER_LOG="$(mktemp)"
DOCKER_PID=""
STRICT_REQUIRED_TESTS="${STRICT_REQUIRED_TESTS:-true}"
RUN_OPTIONAL_E2E="${RUN_OPTIONAL_E2E:-false}"
SKIP_OLLAMA_PULLS="${SKIP_OLLAMA_PULLS:-false}"
PRESERVE_TEST_VOLUMES="${PRESERVE_TEST_VOLUMES:-false}"
RUN_DISCORD_E2E_REQUIRED="${RUN_DISCORD_E2E_REQUIRED:-true}"
DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
RUN_DISCORD_E2E_LOCAL_MODEL="${RUN_DISCORD_E2E_LOCAL_MODEL:-false}"
E2E_ENABLE_OLLAMA="${E2E_ENABLE_OLLAMA:-false}"
RUN_BANDIT_CHECK="${RUN_BANDIT_CHECK:-true}"
RUN_LICENSE_COMPLIANCE_CHECK="${RUN_LICENSE_COMPLIANCE_CHECK:-true}"
OLLAMA_PULL_PROFILE="none"
EMBEDDINGS_BACKEND="${EMBEDDINGS_BACKEND:-openai}"
OPENAI_EMBEDDING_MODEL="${OPENAI_EMBEDDING_MODEL:-text-embedding-3-large}"
OPENAI_EMBEDDING_DIMENSIONS="${OPENAI_EMBEDDING_DIMENSIONS:-3072}"
MYPY_TIMEOUT_SECONDS="${MYPY_TIMEOUT_SECONDS:-1200}"
PIPAUDIT_TIMEOUT_SECONDS="${PIPAUDIT_TIMEOUT_SECONDS:-300}"
STATIC_TIMEOUT_SECONDS="${STATIC_TIMEOUT_SECONDS:-600}"
COVERAGE_MINIMUM="${COVERAGE_MINIMUM:-90}"

# shellcheck source=/dev/null
source "$SCRIPT_DIR/e2e_run_manager.sh"

ts() { date "+%H:%M:%S"; }

require_env_var() {
    local var_name="$1"
    if [ -z "${!var_name:-}" ]; then
        echo "[$(ts)] ERROR: Required environment variable '$var_name' is not set."
        return 1
    fi
    return 0
}

normalize_bool() {
    local value="${1:-false}"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$value" in
        1|true|yes|on) printf 'true\n' ;;
        *) printf 'false\n' ;;
    esac
}

ensure_optional_ollama_profile() {
    E2E_ENABLE_OLLAMA="$(normalize_bool "$E2E_ENABLE_OLLAMA")"
    export E2E_ENABLE_OLLAMA

    if [ "$E2E_ENABLE_OLLAMA" != "true" ]; then
        return 0
    fi

    case ",${COMPOSE_PROFILES:-}," in
        *,ollama,*)
            ;;
        *)
            export COMPOSE_PROFILES="${COMPOSE_PROFILES:+${COMPOSE_PROFILES},}ollama"
            ;;
    esac
}

ensure_python_ca_bundle() {
    # Discord gateway tests use aiohttp/websockets, which rely on Python/OpenSSL
    # trust settings. Some local Python installs have missing default cert paths.
    if [ -n "${SSL_CERT_FILE:-}" ] && [ -r "${SSL_CERT_FILE:-}" ]; then
        return 0
    fi

    local ca_bundle
    ca_bundle="$(
        "$PYTHON_BIN" - <<'PY'
import os
import ssl
from pathlib import Path

def _readable(path: str | None) -> bool:
    return bool(path) and Path(path).is_file() and os.access(path, os.R_OK)

verify = ssl.get_default_verify_paths()
if _readable(verify.cafile):
    print(verify.cafile)
    raise SystemExit(0)

try:
    import certifi  # type: ignore
except Exception:
    raise SystemExit(1)

certifi_path = certifi.where()
if _readable(certifi_path):
    print(certifi_path)
    raise SystemExit(0)

raise SystemExit(1)
PY
    )" || true

    if [ -z "$ca_bundle" ] || [ ! -r "$ca_bundle" ]; then
        echo "[$(ts)] ERROR: Could not determine a readable CA bundle for Python TLS verification."
        echo "[$(ts)] Install certifi in the repo virtualenv or configure SSL_CERT_FILE."
        return 1
    fi

    export SSL_CERT_FILE="$ca_bundle"
    echo "[$(ts)] Using SSL_CERT_FILE=$SSL_CERT_FILE for Python TLS verification."
    return 0
}

can_bind_local_socket() {
    "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import socket

sock = socket.socket()
try:
    sock.bind(("127.0.0.1", 0))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

compose_down() {
    if [ "$PRESERVE_TEST_VOLUMES" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down 2>/dev/null || true
    else
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true
    fi
}

contains_skips() {
    local log_file="$1"
    local pattern="[1-9][0-9]* skipped|\\bSKIPPED\\b"
    if command -v rg >/dev/null 2>&1; then
        rg -q "$pattern" "$log_file"
    else
        grep -Eq "$pattern" "$log_file"
    fi
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

wait_for_background_task() {
    local pid="$1"
    local name="$2"
    local timeout_seconds="$3"
    local start_seconds="$SECONDS"
    local last_heartbeat="$SECONDS"

    while kill -0 "$pid" 2>/dev/null; do
        local elapsed=$((SECONDS - start_seconds))
        if [ "$elapsed" -ge "$timeout_seconds" ]; then
            echo "[$(ts)] ERROR: ${name} timed out after ${timeout_seconds}s."
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi

        if [ $((SECONDS - last_heartbeat)) -ge 30 ]; then
            echo "[$(ts)]   ...waiting for ${name} (${elapsed}s elapsed)"
            last_heartbeat="$SECONDS"
        fi
        sleep 2
    done

    if wait "$pid"; then
        return 0
    fi
    return $?
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
    if [ -n "${E2E_RUN_MANIFEST_PATH:-}" ]; then
        echo "[$(ts)] Cleaning isolated E2E run..."
        cleanup_e2e_run "pre_push_exit"
    elif [ "$DOCKER_STARTED_BY_US" = true ]; then
        echo "[$(ts)] Tearing down Docker test environment..."
        compose_down
    fi
    rm -f "$DOCKER_LOG"
}
trap cleanup EXIT

# Arrays to track background PIDs
declare -a BG_PIDS=()
declare -a E2E_PIDS=()
declare -a E2E_NAMES=()
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
    compose_down

    echo "[$(ts)] [docker] Building and starting containers..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build 2>&1 | tail -5

    # Wait for ALL services to be healthy
    echo "[$(ts)] [docker] Waiting for services to become healthy..."
    for i in $(seq 1 90); do
        postgres=$(inspect_service_field "postgres" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
        qdrant=$(inspect_service_field "qdrant" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
        ollama="not_required"
        ollama_router="not_required"
        ollama_ready=true
        if [ "$E2E_ENABLE_OLLAMA" = "true" ]; then
            ollama=$(inspect_service_field "ollama" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
            ollama_router=$(inspect_service_field "ollama-router" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
            if [ "$ollama" != "healthy" ] || [ "$ollama_router" != "healthy" ]; then
                ollama_ready=false
            fi
        fi
        skills=$(inspect_service_field "zetherion-ai-skills" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
        api=$(inspect_service_field "zetherion-ai-api" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
        cgs_gateway=$(inspect_service_field "zetherion-ai-cgs-gateway" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
        bot=$(inspect_service_field "zetherion-ai-bot" '{{.State.Status}}')

        if [ "$postgres" = "healthy" ] && [ "$qdrant" = "healthy" ] && [ "$skills" = "healthy" ] && [ "$api" = "healthy" ] && [ "$cgs_gateway" = "healthy" ] && [ "$bot" = "running" ] && [ "$ollama_ready" = "true" ]; then
            echo "[$(ts)] [docker] All services ready."
            break
        fi
        if [ "$i" -eq 90 ]; then
            echo "[$(ts)] [docker] ERROR: Services failed to become healthy within 4.5 minutes."
            echo "  postgres=$postgres qdrant=$qdrant ollama=$ollama router=$ollama_router skills=$skills api=$api cgs_gateway=$cgs_gateway bot=$bot"
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --tail=30
            echo "DOCKER_ERROR: Services failed to become healthy" > "$DOCKER_LOG"
            return 1
        fi
        # Log progress every 15 seconds (every 5th iteration at 3s interval)
        if [ $((i % 5)) -eq 0 ]; then
            echo "[$(ts)] [docker]   ...waiting (pg=$postgres qd=$qdrant ol=$ollama rt=$ollama_router sk=$skills api=$api cgs=$cgs_gateway bot=$bot)"
        fi
        sleep 3
    done

    if [ "$SKIP_OLLAMA_PULLS" = "true" ]; then
        echo "[$(ts)] [docker] Skipping Ollama model pulls/warm-up (SKIP_OLLAMA_PULLS=true)."
        echo "DOCKER_READY" > "$DOCKER_LOG"
        echo "[$(ts)] [docker] Docker environment fully ready."
        return 0
    fi

    if [ "$OLLAMA_PULL_PROFILE" = "none" ]; then
        echo "[$(ts)] [docker] Skipping Ollama model pulls/warm-up (OLLAMA_PULL_PROFILE=none)."
        echo "DOCKER_READY" > "$DOCKER_LOG"
        echo "[$(ts)] [docker] Docker environment fully ready."
        return 0
    fi

    if [ "$OLLAMA_PULL_PROFILE" != "full" ] && [ "$OLLAMA_PULL_PROFILE" != "generation_only" ]; then
        echo "[$(ts)] [docker] ERROR: Unsupported OLLAMA_PULL_PROFILE='$OLLAMA_PULL_PROFILE'."
        echo "DOCKER_ERROR: Unsupported OLLAMA_PULL_PROFILE '$OLLAMA_PULL_PROFILE'" > "$DOCKER_LOG"
        return 1
    fi

    # Pull Ollama models (skip if already cached)
    echo "[$(ts)] [docker] Checking Ollama models (profile=$OLLAMA_PULL_PROFILE)..."

    if [ "$OLLAMA_PULL_PROFILE" = "full" ]; then
        if ! exec_service "ollama-router" ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
            echo "[$(ts)] [docker]   Pulling llama3.2:3b → ollama-router..."
            exec_service "ollama-router" ollama pull llama3.2:3b 2>&1 | tail -1
        else
            echo "[$(ts)] [docker]   llama3.2:3b already cached in ollama-router"
        fi
    fi

    if ! exec_service "ollama" ollama list 2>/dev/null | grep -q "llama3.1:8b"; then
        echo "[$(ts)] [docker]   Pulling llama3.1:8b → ollama..."
        exec_service "ollama" ollama pull llama3.1:8b 2>&1 | tail -1
    else
        echo "[$(ts)] [docker]   llama3.1:8b already cached in ollama"
    fi

    echo "[$(ts)] [docker] Models ready."

    # Pre-warm models with a throwaway inference (loads weights into memory)
    echo "[$(ts)] [docker] Pre-warming models..."
    if [ "$OLLAMA_PULL_PROFILE" = "full" ]; then
        exec_service "ollama-router" curl -sf http://localhost:11434/api/generate \
            -d '{"model":"llama3.2:3b","prompt":"hi","stream":false}' >/dev/null 2>&1 &
    fi
    exec_service "ollama" curl -sf http://localhost:11434/api/generate \
        -d '{"model":"llama3.1:8b","prompt":"hi","stream":false}' >/dev/null 2>&1 &
    wait
    echo "[$(ts)] [docker] Models pre-warmed."

    echo "DOCKER_READY" > "$DOCKER_LOG"
    echo "[$(ts)] [docker] Docker environment fully ready."
}

echo "========================================"
echo "  Pre-push: Full test suite"
echo "  Started at $(ts)"
echo "========================================"

DISCORD_E2E_PROVIDER="$(printf '%s' "$DISCORD_E2E_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
RUN_DISCORD_E2E_LOCAL_MODEL="$(printf '%s' "$RUN_DISCORD_E2E_LOCAL_MODEL" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [ "$DISCORD_E2E_PROVIDER" != "groq" ] && [ "$DISCORD_E2E_PROVIDER" != "local" ]; then
    echo "[$(ts)] ERROR: DISCORD_E2E_PROVIDER must be 'groq' or 'local' (got '$DISCORD_E2E_PROVIDER')."
    exit 1
fi
if [ "$RUN_DISCORD_E2E_LOCAL_MODEL" != "true" ] && [ "$RUN_DISCORD_E2E_LOCAL_MODEL" != "false" ]; then
    echo "[$(ts)] ERROR: RUN_DISCORD_E2E_LOCAL_MODEL must be 'true' or 'false' (got '$RUN_DISCORD_E2E_LOCAL_MODEL')."
    exit 1
fi
if [ "$RUN_DISCORD_E2E_LOCAL_MODEL" = "true" ]; then
    DISCORD_E2E_PROVIDER="local"
fi
if [ "$DISCORD_E2E_PROVIDER" = "groq" ]; then
    export ROUTER_BACKEND="groq"
    OLLAMA_PULL_PROFILE="none"
    E2E_ENABLE_OLLAMA="$(normalize_bool "$E2E_ENABLE_OLLAMA")"
else
    export ROUTER_BACKEND="ollama"
    OLLAMA_PULL_PROFILE="full"
    E2E_ENABLE_OLLAMA="true"
fi
ensure_optional_ollama_profile
echo "[$(ts)] Discord E2E provider mode: $DISCORD_E2E_PROVIDER (ROUTER_BACKEND=$ROUTER_BACKEND, OLLAMA_PULL_PROFILE=$OLLAMA_PULL_PROFILE)"

if [ "$RUN_DISCORD_E2E_REQUIRED" = "true" ]; then
    REPO_ENV_FILE="$(resolve_repo_env_file || true)"
    if [ -n "$REPO_ENV_FILE" ]; then
        source_repo_env_file "$REPO_ENV_FILE"
    fi
    # Canonical full gate runs cloud embeddings only (OpenAI), never local embedding pulls.
    EMBEDDINGS_BACKEND="openai"
    OPENAI_EMBEDDING_MODEL="${OPENAI_EMBEDDING_MODEL:-text-embedding-3-large}"
    OPENAI_EMBEDDING_DIMENSIONS="${OPENAI_EMBEDDING_DIMENSIONS:-3072}"

    require_env_var "TEST_DISCORD_BOT_TOKEN"
    DISCORD_E2E_ENABLED="${DISCORD_E2E_ENABLED:-true}"
    DISCORD_E2E_GUILD_ID="${DISCORD_E2E_GUILD_ID:-${TEST_DISCORD_GUILD_ID:-}}"
    DISCORD_E2E_CHANNEL_PREFIX="${DISCORD_E2E_CHANNEL_PREFIX:-${TEST_DISCORD_E2E_CHANNEL_PREFIX:-zeth-e2e}}"
    export DISCORD_E2E_ENABLED DISCORD_E2E_GUILD_ID DISCORD_E2E_CHANNEL_PREFIX

    require_env_var "TEST_DISCORD_GUILD_ID"
    if [ -z "${DISCORD_TOKEN_TEST:-}" ] && [ -z "${DISCORD_TOKEN:-}" ]; then
        echo "[$(ts)] ERROR: Set DISCORD_TOKEN_TEST or DISCORD_TOKEN for isolated Discord E2E runs."
        exit 1
    fi
    require_env_var "DISCORD_E2E_ENABLED"
    require_env_var "DISCORD_E2E_ALLOWED_AUTHOR_IDS"
    if [ -z "${TEST_DISCORD_E2E_CATEGORY_ID:-}" ] && [ -z "${TEST_DISCORD_E2E_CATEGORY_NAME:-}" ]; then
        echo "[$(ts)] ERROR: Set TEST_DISCORD_E2E_CATEGORY_ID or TEST_DISCORD_E2E_CATEGORY_NAME for isolated Discord E2E runs."
        exit 1
    fi
    require_env_var "OPENAI_API_KEY"
    if [ "$DISCORD_E2E_PROVIDER" = "groq" ]; then
        require_env_var "GROQ_API_KEY"
    fi
fi
export EMBEDDINGS_BACKEND OPENAI_EMBEDDING_MODEL OPENAI_EMBEDDING_DIMENSIONS
echo "[$(ts)] Embeddings backend: $EMBEDDINGS_BACKEND (model=$OPENAI_EMBEDDING_MODEL, dimensions=$OPENAI_EMBEDDING_DIMENSIONS)"

if ! ensure_python_ca_bundle; then
    exit 1
fi

if [ "${SKIP_LOCAL_SOCKET_PREFLIGHT:-false}" != "true" ]; then
    if ! can_bind_local_socket; then
        echo "[$(ts)] ERROR: Local TCP socket bind preflight failed."
        echo "[$(ts)] This environment blocks localhost binds, so aiohttp/HTTP integration tests cannot run."
        echo "[$(ts)] Re-run outside sandbox restrictions or set SKIP_LOCAL_SOCKET_PREFLIGHT=true if you know what you are doing."
        exit 1
    fi
fi

start_e2e_run
echo "[$(ts)] Isolated E2E run: run_id=${E2E_RUN_ID} project=${PROJECT} stack_root=${E2E_STACK_ROOT}"

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
if [ "$USE_DOCKER_PYTHON" = "true" ]; then
    # The Docker-backed tool image is built from the pinned repo requirements,
    # so version parity is guaranteed by the image context hash instead of a
    # host-installed ruff binary.
    ACTUAL_RUFF="$EXPECTED_RUFF"
else
    ACTUAL_RUFF="$(run_ruff --version 2>/dev/null | awk 'NR==1 {print $2}' || echo "unknown")"
fi
if [ "$ACTUAL_RUFF" != "$EXPECTED_RUFF" ]; then
    echo "ERROR: ruff version mismatch: local=$ACTUAL_RUFF, expected=$EXPECTED_RUFF"
    echo "  Fix: pip install ruff==$EXPECTED_RUFF (see requirements-dev.txt)"
    exit 1
fi

echo "[$(ts)]   Launching static checks in parallel..."

start_static_check "ruff lint" "run_ruff check $SRC_DIRS"
start_static_check "ruff format" "run_ruff format --check $SRC_DIRS"
if [ "$RUN_BANDIT_CHECK" = "true" ]; then
    start_static_check "bandit" "run_bandit -c pyproject.toml -r $LINT_DIRS -q"
else
    echo "  [skip] bandit disabled (RUN_BANDIT_CHECK=false)"
fi
start_static_check "pipeline contract" "$PYTHON_BIN scripts/check_pipeline_contract.py"
start_static_check "optional service guards" "$PYTHON_BIN scripts/check-optional-service-guards.py"
start_static_check "endpoint docs bundle" "$PYTHON_BIN scripts/check-endpoint-doc-bundle.py"
start_static_check "docs nav" "$PYTHON_BIN scripts/check-docs-nav.py"
start_static_check "docs links" "$PYTHON_BIN scripts/check-docs-links.py"
start_static_check "route doc parity" "$PYTHON_BIN scripts/check-route-doc-parity.py"
start_static_check "cgs route-doc parity" "$PYTHON_BIN scripts/check-cgs-route-doc-parity.py"
start_static_check "env doc parity" "$PYTHON_BIN scripts/check-env-doc-parity.py"
start_static_check "docs build strict" "$PYTHON_BIN -m mkdocs build --strict"

if command -v gitleaks >/dev/null 2>&1; then
    start_static_check "gitleaks" "gitleaks detect --no-git --redact --config=.gitleaks.toml"
else
    echo "  [skip] gitleaks not installed (install: brew install gitleaks)"
fi

if docker image inspect ghcr.io/hadolint/hadolint:latest >/dev/null 2>&1 || docker info >/dev/null 2>&1; then
    start_static_check \
        "hadolint" \
        "docker run --rm -v \"$(pwd):/work\" -w /work ghcr.io/hadolint/hadolint:latest hadolint --ignore DL3007 --ignore DL3008 --ignore DL3009 Dockerfile Dockerfile.updater Dockerfile.dev-agent"
else
    echo "  [skip] hadolint requires Docker (Docker not available yet)"
fi

if [ "$RUN_LICENSE_COMPLIANCE_CHECK" != "true" ]; then
    echo "  [skip] license compliance disabled (RUN_LICENSE_COMPLIANCE_CHECK=false)"
elif run_pip_licenses --version >/dev/null 2>&1; then
    start_static_check "license compliance" "run_pip_licenses --allow-only=\"$LICENSE_ALLOWLIST\" --partial-match"
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
    static_status=0
    wait_for_background_task "$pid" "$name" "$STATIC_TIMEOUT_SECONDS" || static_status=$?
    if [ "$static_status" -eq 0 ]; then
        echo "[$(ts)]   ${name} passed."
    else
        STATIC_FAILED=true
        if [ "$static_status" -eq 124 ]; then
            echo "[$(ts)]   ${name} FAILED (timeout after ${STATIC_TIMEOUT_SECONDS}s):"
        else
            echo "[$(ts)]   ${name} FAILED:"
        fi
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

# Coverage sqlite artifacts can become incompatible after interrupted runs or
# toolchain upgrades; clear them so pytest-cov starts from a clean schema.
rm -f .coverage .coverage.* .coverage-*
# Mypy cache can contain stale iCloud "dataless" files that block on read().
# Rebuild cache each run to avoid indefinite local stalls.
rm -rf .mypy_cache

# Start mypy in background
run_mypy src/zetherion_ai/ updater_sidecar/ --config-file=pyproject.toml > "$MYPY_LOG" 2>&1 &
BG_PIDS+=($!)
MYPY_PID=$!

# Start pip-audit in background
if run_pip_audit --version >/dev/null 2>&1; then
    run_pip_audit -r requirements.txt --strict --desc on > "$PIPAUDIT_LOG" 2>&1 &
    BG_PIDS+=($!)
    PIPAUDIT_PID=$!
else
    echo "  [skip] pip-audit not installed (pip install pip-audit)"
    PIPAUDIT_PID=""
fi

# Run unit tests in foreground (so output streams live)
COVERAGE_FILE=.coverage.unit "$PYTHON_BIN" -m pytest tests/ \
    -m "not integration and not discord_e2e" \
    -n 8 \
    --timeout=30 \
    --tb=short -q \
    --cov-append \
    --cov-fail-under=0

# Wait for mypy
mypy_status=0
wait_for_background_task "$MYPY_PID" "mypy" "$MYPY_TIMEOUT_SECONDS" || mypy_status=$?
if [ "$mypy_status" -ne 0 ]; then
    if [ "$mypy_status" -eq 124 ]; then
        echo ""
        echo "mypy FAILED due to timeout:"
        cat "$MYPY_LOG"
        rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
        exit 1
    fi
    if rg -q "Error reading JSON file; you likely have a bad cache" "$MYPY_LOG"; then
        echo "[$(ts)]   mypy cache is corrupted; clearing .mypy_cache and retrying once..."
        rm -rf .mypy_cache
        if ! run_mypy src/zetherion_ai/ updater_sidecar/ --config-file=pyproject.toml > "$MYPY_LOG" 2>&1; then
            echo ""
            echo "mypy FAILED after cache reset:"
            cat "$MYPY_LOG"
            rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
            exit 1
        fi
    else
        echo ""
        echo "mypy FAILED:"
        cat "$MYPY_LOG"
        rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
        exit 1
    fi
fi
echo "[$(ts)]   mypy passed."

# Wait for pip-audit
if [ -n "$PIPAUDIT_PID" ]; then
    pipaudit_status=0
    wait_for_background_task "$PIPAUDIT_PID" "pip-audit" "$PIPAUDIT_TIMEOUT_SECONDS" || pipaudit_status=$?
    if [ "$pipaudit_status" -ne 0 ]; then
        if [ "$pipaudit_status" -eq 124 ]; then
            echo ""
            echo "pip-audit FAILED due to timeout:"
            cat "$PIPAUDIT_LOG"
            rm -f "$MYPY_LOG" "$PIPAUDIT_LOG"
            exit 1
        fi
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
if ! COVERAGE_FILE=.coverage.integration "$PYTHON_BIN" -m pytest \
    tests/integration/test_skills_http.py \
    tests/integration/test_heartbeat_cycle.py \
    tests/integration/test_email_personality_persistence_integration.py \
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
    tests/integration/test_dev_watcher_onboarding_integration.py \
    tests/integration/test_milestone_e2e.py \
    tests/integration/test_youtube_http.py \
    -m "integration and not optional_e2e" \
    -n 4 \
    --timeout=60 --tb=short -q \
    --cov-append \
    --cov-fail-under=0 \
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

# ── Step 3.5: E2E smoke preflight ─────────────────────────────────
echo ""
echo "[$(ts)] [3.5/5] E2E smoke preflight..."

DOCKER_SMOKE_LOG="$(mktemp)"
DISCORD_SMOKE_LOG="$(mktemp)"

if COVERAGE_FILE=.coverage.smoke.docker DOCKER_MANAGED_EXTERNALLY=true "$PYTHON_BIN" -m pytest \
    tests/integration/test_e2e.py::test_docker_services_running \
    tests/integration/test_e2e.py::test_skills_service_health \
    -m "integration and not optional_e2e" --timeout=120 -v --tb=short -s \
    --cov-append \
    --cov-fail-under=0 \
    > "$DOCKER_SMOKE_LOG" 2>&1; then
    echo "[$(ts)] Docker E2E smoke preflight passed."
else
    echo "[$(ts)] Docker E2E smoke preflight FAILED."
    cat "$DOCKER_SMOKE_LOG"
    rm -f "$DOCKER_SMOKE_LOG" "$DISCORD_SMOKE_LOG"
    exit 1
fi
if ! assert_no_skips_in_log "Docker E2E smoke preflight" "$DOCKER_SMOKE_LOG"; then
    cat "$DOCKER_SMOKE_LOG"
    rm -f "$DOCKER_SMOKE_LOG" "$DISCORD_SMOKE_LOG"
    exit 1
fi

if [ "$RUN_DISCORD_E2E_REQUIRED" = "true" ]; then
    if COVERAGE_FILE=.coverage.smoke.discord DISCORD_E2E_ENABLE_COVERAGE=true \
        scripts/run-required-discord-e2e.sh -- \
        --cov-append \
        --cov-fail-under=0 \
        -k test_bot_responds_to_message \
        > "$DISCORD_SMOKE_LOG" 2>&1; then
        echo "[$(ts)] Discord E2E smoke preflight passed."
    else
        echo "[$(ts)] Discord E2E smoke preflight FAILED."
        cat "$DISCORD_SMOKE_LOG"
        rm -f "$DOCKER_SMOKE_LOG" "$DISCORD_SMOKE_LOG"
        exit 1
    fi
    if ! assert_no_skips_in_log "Discord E2E smoke preflight" "$DISCORD_SMOKE_LOG"; then
        cat "$DISCORD_SMOKE_LOG"
        rm -f "$DOCKER_SMOKE_LOG" "$DISCORD_SMOKE_LOG"
        exit 1
    fi
fi

rm -f "$DOCKER_SMOKE_LOG" "$DISCORD_SMOKE_LOG"
echo "[$(ts)] [3.5/5] E2E smoke preflight passed."

# ── Step 4: Required Docker E2E tests (concurrent) ────────────────
echo ""
echo "[$(ts)] [4/5] Required Docker E2E tests (concurrent)..."

# Create temp files for each parallel test output
E2E_LOG_A="$(mktemp)"     # test_e2e.py (required)
E2E_LOG_B="$(mktemp)"     # health/update/telemetry required tests
E2E_LOG_C="$(mktemp)"     # discord required tests (gated by RUN_DISCORD_E2E_REQUIRED)
E2E_OPTIONAL_LOG="$(mktemp)"
DISCORD_REQUIRED_STARTED=false
E2E_FAIL_FAST="${E2E_FAIL_FAST:-true}"

# Start a heartbeat so we can see the tests are still running
(while true; do sleep 30; echo "[$(ts)]   ...E2E tests still running"; done) &
HEARTBEAT_PID=$!

# Launch required test groups concurrently.
# Keep Discord first in wait order so a Discord failure fails fast.
if [ "$RUN_DISCORD_E2E_REQUIRED" = "true" ]; then
    COVERAGE_FILE=.coverage.e2e.discord DISCORD_E2E_ENABLE_COVERAGE=true \
        DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" DOCKER_MANAGED_EXTERNALLY=true \
        scripts/run-required-discord-e2e.sh -- \
        --cov-append \
        --cov-fail-under=0 \
        > "$E2E_LOG_C" 2>&1 &
    E2E_PIDS+=($!)
    E2E_NAMES+=("Discord E2E tests")
    DISCORD_REQUIRED_STARTED=true
else
    echo "[$(ts)] Discord required E2E skipped (set RUN_DISCORD_E2E_REQUIRED=true to enforce)."
fi

COVERAGE_FILE=.coverage.e2e.docker DOCKER_MANAGED_EXTERNALLY=true "$PYTHON_BIN" -m pytest \
    tests/integration/test_e2e.py \
    -m "integration and not optional_e2e" --timeout=120 -v --tb=short -s \
    --cov-append \
    --cov-fail-under=0 \
    > "$E2E_LOG_A" 2>&1 &
E2E_PIDS+=($!)
E2E_NAMES+=("Docker E2E tests (test_e2e.py)")

COVERAGE_FILE=.coverage.e2e.services DOCKER_MANAGED_EXTERNALLY=true "$PYTHON_BIN" -m pytest \
    tests/integration/test_health_e2e.py \
    tests/integration/test_update_e2e.py \
    tests/integration/test_telemetry_e2e.py \
    -m "integration and not optional_e2e" --timeout=120 -v --tb=short -s \
    --cov-append \
    --cov-fail-under=0 \
    > "$E2E_LOG_B" 2>&1 &
E2E_PIDS+=($!)
E2E_NAMES+=("Health/Update/Telemetry E2E tests")

# Wait for all to finish, track failures
E2E_FAILED=false

for i in "${!E2E_PIDS[@]}"; do
    pid="${E2E_PIDS[$i]}"
    name="${E2E_NAMES[$i]}"
    if ! wait "$pid"; then
        E2E_FAILED=true
        echo "[$(ts)] FAILED: ${name}"
        if [ "$E2E_FAIL_FAST" = "true" ]; then
            for j in "${!E2E_PIDS[@]}"; do
                if [ "$j" -ne "$i" ]; then
                    other_pid="${E2E_PIDS[$j]}"
                    if kill -0 "$other_pid" 2>/dev/null; then
                        kill "$other_pid" 2>/dev/null || true
                    fi
                fi
            done
            break
        fi
    else
        echo "[$(ts)] PASSED: ${name}"
    fi
done

# Reap any terminated/remaining children to avoid zombies.
for pid in "${E2E_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
done
E2E_PIDS=()
E2E_NAMES=()

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
if [ "$DISCORD_REQUIRED_STARTED" = "true" ]; then
    cat "$E2E_LOG_C"
else
    echo "[not run]"
fi

if ! assert_no_skips_in_log "Docker E2E tests (test_e2e.py)" "$E2E_LOG_A"; then
    E2E_FAILED=true
fi
if ! assert_no_skips_in_log "Health/Update/Telemetry E2E tests" "$E2E_LOG_B"; then
    E2E_FAILED=true
fi
if [ "$DISCORD_REQUIRED_STARTED" = "true" ]; then
    if ! assert_no_skips_in_log "Discord E2E tests" "$E2E_LOG_C"; then
        E2E_FAILED=true
    fi
fi

# Optional E2E tests are non-blocking and can be enabled via RUN_OPTIONAL_E2E=true.
if [ "$RUN_OPTIONAL_E2E" = "true" ]; then
    echo ""
    echo "[$(ts)] Running optional E2E tests (non-blocking)..."
    if DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" DOCKER_MANAGED_EXTERNALLY=true "$PYTHON_BIN" -m pytest \
        tests/integration/test_inbound_groq_rollout_e2e.py \
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

# ── Step 4.5: Combined coverage gate ─────────────────────────────
echo ""
echo "[$(ts)] [4.5/5] Combined coverage report..."

mapfile -t COVERAGE_DATA_FILES < <(find . -maxdepth 1 -type f -name '.coverage*' | sort)
if [ "${#COVERAGE_DATA_FILES[@]}" -gt 1 ]; then
    if ! run_python_module coverage combine > /dev/null 2>&1; then
        echo "[$(ts)] ERROR: Failed to combine coverage data."
        exit 1
    fi
fi

if [ ! -f .coverage ]; then
    echo "[$(ts)] ERROR: Coverage data file .coverage was not produced."
    exit 1
fi

if ! run_python_module coverage report --fail-under="$COVERAGE_MINIMUM"; then
    echo ""
    echo "[$(ts)] [4.5/5] Combined coverage FAILED."
    exit 1
fi

echo "[$(ts)] [4.5/5] Combined coverage passed."

# ── Step 5: Summary ──────────────────────────────────────────────
echo ""
echo "========================================"
echo "  All tests passed. Push allowed."
echo "  Finished at $(ts)"
echo "========================================"
