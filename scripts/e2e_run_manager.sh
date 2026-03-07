#!/usr/bin/env bash
# Shared helper for isolated Docker-backed E2E runs.

set -euo pipefail

init_e2e_run_manager() {
    : "${REPO_DIR:?REPO_DIR must be set before sourcing e2e_run_manager.sh}"
    : "${PYTHON_BIN:?PYTHON_BIN must be set before sourcing e2e_run_manager.sh}"

    E2E_RUNS_ROOT="${E2E_RUNS_ROOT:-$REPO_DIR/.artifacts/e2e-runs}"
    E2E_RUN_TTL_MINUTES="${E2E_RUN_TTL_MINUTES:-180}"
    E2E_PROJECT_PREFIX="${E2E_PROJECT_PREFIX:-zetherion-ai-test}"
    E2E_RUN_MANIFEST_PATH="${E2E_RUN_MANIFEST_PATH:-}"
    E2E_DOCKER_CLEANUP_STATUS="${E2E_DOCKER_CLEANUP_STATUS:-not_run}"
    export E2E_RUNS_ROOT E2E_RUN_TTL_MINUTES E2E_PROJECT_PREFIX E2E_RUN_MANIFEST_PATH E2E_DOCKER_CLEANUP_STATUS
}

start_e2e_run() {
    init_e2e_run_manager
    "$PYTHON_BIN" "$REPO_DIR/scripts/e2e_run_manager.py" janitor --runs-root "$E2E_RUNS_ROOT" >/dev/null || true

    local exports
    exports="$($PYTHON_BIN "$REPO_DIR/scripts/e2e_run_manager.py" start \
        --runs-root "$E2E_RUNS_ROOT" \
        --compose-file "$COMPOSE_FILE" \
        --project-prefix "$E2E_PROJECT_PREFIX" \
        --ttl-minutes "$E2E_RUN_TTL_MINUTES" \
        --shell)"
    eval "$exports"
    export E2E_RUN_ID E2E_PROJECT_NAME E2E_STACK_ROOT E2E_RUN_MANIFEST_PATH E2E_RUN_ENV_PATH PROJECT COMPOSE_FILE \
        E2E_API_HOST_PORT E2E_CGS_GATEWAY_HOST_PORT E2E_SKILLS_HOST_PORT E2E_WHATSAPP_BRIDGE_HOST_PORT \
        E2E_OLLAMA_ROUTER_HOST_PORT E2E_OLLAMA_HOST_PORT E2E_POSTGRES_HOST_PORT E2E_QDRANT_HOST_PORT
}

cleanup_e2e_run() {
    init_e2e_run_manager
    local reason="${1:-explicit_cleanup}"

    if [[ -z "${E2E_RUN_MANIFEST_PATH:-}" || ! -f "$E2E_RUN_MANIFEST_PATH" ]]; then
        E2E_DOCKER_CLEANUP_STATUS="not_run"
        export E2E_DOCKER_CLEANUP_STATUS
        return 0
    fi

    "$PYTHON_BIN" "$REPO_DIR/scripts/e2e_run_manager.py" cleanup \
        --manifest "$E2E_RUN_MANIFEST_PATH" \
        --reason "$reason" >/dev/null || true

    E2E_DOCKER_CLEANUP_STATUS="$($PYTHON_BIN - "$E2E_RUN_MANIFEST_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding='utf-8'))
except Exception:
    print('cleanup_failed')
    raise SystemExit(0)
print(payload.get('cleanup', {}).get('status', 'cleanup_failed'))
PY
)"
    export E2E_DOCKER_CLEANUP_STATUS
}

compose_service_container_id() {
    local service="$1"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps -q "$service" | head -n 1
}

inspect_service_field() {
    local service="$1"
    local format="$2"
    local container_id
    container_id="$(compose_service_container_id "$service")"
    if [[ -z "$container_id" ]]; then
        echo "missing"
        return 0
    fi
    docker inspect --format "$format" "$container_id" 2>/dev/null || echo "missing"
}

exec_service() {
    local service="$1"
    shift
    local container_id
    container_id="$(compose_service_container_id "$service")"
    if [[ -z "$container_id" ]]; then
        return 1
    fi
    docker exec "$container_id" "$@"
}
