#!/usr/bin/env bash
# Shared helper for isolated Docker-backed E2E runs.

set -euo pipefail

init_e2e_run_manager() {
    : "${REPO_DIR:?REPO_DIR must be set before sourcing e2e_run_manager.sh}"
    : "${PYTHON_BIN:?PYTHON_BIN must be set before sourcing e2e_run_manager.sh}"

    E2E_RUNS_ROOT="${E2E_RUNS_ROOT:-$REPO_DIR/.artifacts/ci-e2e-runs}"
    E2E_RUN_TTL_MINUTES="${E2E_RUN_TTL_MINUTES:-180}"
    E2E_PROJECT_PREFIX="${E2E_PROJECT_PREFIX:-zetherion-ai-test}"
    E2E_RUN_MANIFEST_PATH="${E2E_RUN_MANIFEST_PATH:-}"
    E2E_SERVICE_SLOT="${E2E_SERVICE_SLOT:-}"
    E2E_DOCKER_CLEANUP_STATUS="${E2E_DOCKER_CLEANUP_STATUS:-not_run}"
    export E2E_RUNS_ROOT E2E_RUN_TTL_MINUTES E2E_PROJECT_PREFIX E2E_RUN_MANIFEST_PATH E2E_SERVICE_SLOT E2E_DOCKER_CLEANUP_STATUS
}

json_helper_python() {
    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/Scripts/python.exe" \
        "$REPO_DIR/venv/Scripts/python.exe"; do
        if [[ -x "$candidate" || -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    if [[ -n "${PYTHON_BIN:-}" && "$PYTHON_BIN" != *"/docker-python-tool.sh" ]]; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi
    return 1
}

host_python_uses_windows_paths() {
    local python_bin="${1:-}"
    case "$python_bin" in
        *.exe|*.cmd|*.bat)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

normalize_host_python_path() {
    local raw_path="${1:-}"
    local python_bin="${2:-}"

    if [[ -z "$raw_path" ]] || ! host_python_uses_windows_paths "$python_bin"; then
        printf '%s\n' "$raw_path"
        return 0
    fi

    local windows_repo_root=""
    if command -v cmd.exe >/dev/null 2>&1; then
        windows_repo_root="$(cmd.exe /c cd 2>/dev/null | tr -d '\r' | tail -n 1 | tr '\\' '/')" || true
    fi
    if [[ -z "$windows_repo_root" ]]; then
        windows_repo_root="$(pwd -W 2>/dev/null | tr -d '\r' | tr '\\' '/')" || true
    fi
    if [[ -n "$windows_repo_root" ]]; then
        case "$raw_path" in
            "$REPO_DIR")
                printf '%s\n' "$windows_repo_root"
                return 0
                ;;
            "$REPO_DIR"/*)
                printf '%s/%s\n' \
                    "${windows_repo_root%/}" \
                    "${raw_path#"$REPO_DIR"/}"
                return 0
                ;;
        esac
    fi

    printf '%s\n' "$raw_path"
}

normalize_path_for_current_shell() {
    local raw_path="${1:-}"
    local python_bin="${2:-}"

    if [[ -z "$raw_path" ]] || ! host_python_uses_windows_paths "$python_bin"; then
        printf '%s\n' "$raw_path"
        return 0
    fi

    if [[ "$raw_path" =~ ^[A-Za-z]:[/\\] ]] && command -v wslpath >/dev/null 2>&1; then
        local windows_style_path="$raw_path"
        windows_style_path="${windows_style_path//\//\\}"
        wslpath -u "$windows_style_path"
        return 0
    fi

    printf '%s\n' "$raw_path"
}

start_e2e_run() {
    init_e2e_run_manager
    local helper_python=""
    helper_python="$(json_helper_python || true)"
    if [[ -z "$helper_python" ]]; then
        echo "ERROR: A host-visible Python interpreter is required for E2E run management." >&2
        exit 1
    fi

    local runs_root_arg=""
    local compose_file_arg=""
    runs_root_arg="$(normalize_host_python_path "$E2E_RUNS_ROOT" "$helper_python")"
    compose_file_arg="$(normalize_host_python_path "$COMPOSE_FILE" "$helper_python")"
    "$helper_python" scripts/e2e_run_manager.py janitor --runs-root "$runs_root_arg" >/dev/null || true

    local exports
    exports="$($helper_python scripts/e2e_run_manager.py start \
        --runs-root "$runs_root_arg" \
        --compose-file "$compose_file_arg" \
        --project-prefix "$E2E_PROJECT_PREFIX" \
        --ttl-minutes "$E2E_RUN_TTL_MINUTES" \
        --service-slot "$E2E_SERVICE_SLOT" \
        --shell | tr -d '\r')"
    eval "$exports"
    E2E_STACK_ROOT="$(normalize_path_for_current_shell "$E2E_STACK_ROOT" "$helper_python")"
    COMPOSE_FILE="$(normalize_path_for_current_shell "$COMPOSE_FILE" "$helper_python")"
    E2E_RUN_MANIFEST_PATH="$(normalize_path_for_current_shell "$E2E_RUN_MANIFEST_PATH" "$helper_python")"
    E2E_RUN_ENV_PATH="$(normalize_path_for_current_shell "$E2E_RUN_ENV_PATH" "$helper_python")"
    ZETHERION_ENV_FILE="$(normalize_path_for_current_shell "${ZETHERION_ENV_FILE:-$E2E_RUN_ENV_PATH}" "$helper_python")"
    export E2E_RUN_ID E2E_PROJECT_NAME E2E_STACK_ROOT E2E_RUN_MANIFEST_PATH E2E_RUN_ENV_PATH PROJECT COMPOSE_FILE \
        ZETHERION_ENV_FILE E2E_SERVICE_SLOT E2E_API_HOST_PORT E2E_CGS_GATEWAY_HOST_PORT E2E_SKILLS_HOST_PORT E2E_WHATSAPP_BRIDGE_HOST_PORT \
        E2E_OLLAMA_ROUTER_HOST_PORT E2E_OLLAMA_HOST_PORT E2E_POSTGRES_HOST_PORT E2E_QDRANT_HOST_PORT
}

cleanup_e2e_run() {
    init_e2e_run_manager
    local reason="${1:-explicit_cleanup}"
    local helper_python=""

    if [[ -z "${E2E_RUN_MANIFEST_PATH:-}" || ! -f "$E2E_RUN_MANIFEST_PATH" ]]; then
        E2E_DOCKER_CLEANUP_STATUS="not_run"
        export E2E_DOCKER_CLEANUP_STATUS
        return 0
    fi

    helper_python="$(json_helper_python || true)"
    if [[ -z "$helper_python" ]]; then
        E2E_DOCKER_CLEANUP_STATUS="cleanup_unknown"
        export E2E_DOCKER_CLEANUP_STATUS
        return 0
    fi

    local manifest_arg=""
    manifest_arg="$(normalize_host_python_path "$E2E_RUN_MANIFEST_PATH" "$helper_python")"

    "$helper_python" scripts/e2e_run_manager.py cleanup \
        --manifest "$manifest_arg" \
        --reason "$reason" >/dev/null || true

    E2E_DOCKER_CLEANUP_STATUS="$("$helper_python" - "$manifest_arg" <<'PY'
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
    return 0
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
