#!/usr/bin/env bash
# Shared helper for isolated Discord E2E run management.

set -euo pipefail

init_discord_e2e_run_manager() {
    : "${REPO_DIR:?REPO_DIR must be set before sourcing discord_e2e_run_manager.sh}"
    : "${PYTHON_BIN:?PYTHON_BIN must be set before sourcing discord_e2e_run_manager.sh}"

    DISCORD_E2E_RUNS_ROOT="${DISCORD_E2E_RUNS_ROOT:-$REPO_DIR/.artifacts/discord-e2e-runs}"
    TEST_DISCORD_E2E_TTL_MINUTES="${TEST_DISCORD_E2E_TTL_MINUTES:-180}"
    TEST_DISCORD_E2E_HEARTBEAT_STALE_SECONDS="${TEST_DISCORD_E2E_HEARTBEAT_STALE_SECONDS:-300}"
    TEST_DISCORD_E2E_CHANNEL_PREFIX="${TEST_DISCORD_E2E_CHANNEL_PREFIX:-zeth-e2e}"
    DISCORD_E2E_MODE="${DISCORD_E2E_MODE:-local_required}"
    DISCORD_E2E_RUN_MANIFEST_PATH="${DISCORD_E2E_RUN_MANIFEST_PATH:-}"
    DISCORD_E2E_HEARTBEAT_PATH="${DISCORD_E2E_HEARTBEAT_PATH:-}"
    DISCORD_E2E_CLEANUP_STATUS="${DISCORD_E2E_CLEANUP_STATUS:-not_run}"
    DISCORD_E2E_TARGET_LEASE_STATUS="${DISCORD_E2E_TARGET_LEASE_STATUS:-not_run}"
    DISCORD_E2E_HEARTBEAT_PID="${DISCORD_E2E_HEARTBEAT_PID:-}"
    export DISCORD_E2E_RUNS_ROOT TEST_DISCORD_E2E_TTL_MINUTES TEST_DISCORD_E2E_CHANNEL_PREFIX \
        TEST_DISCORD_E2E_HEARTBEAT_STALE_SECONDS DISCORD_E2E_MODE \
        DISCORD_E2E_RUN_MANIFEST_PATH DISCORD_E2E_HEARTBEAT_PATH \
        DISCORD_E2E_CLEANUP_STATUS DISCORD_E2E_TARGET_LEASE_STATUS DISCORD_E2E_HEARTBEAT_PID
}

json_helper_python() {
    local candidate
    local supports_httpx
    supports_httpx() {
        "$1" - <<'PY' >/dev/null 2>&1
import importlib.util

raise SystemExit(0 if importlib.util.find_spec("httpx") is not None else 1)
PY
    }

    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/Scripts/python.exe" \
        "$REPO_DIR/venv/Scripts/python.exe"; do
        if [[ -x "$candidate" || -f "$candidate" ]] && supports_httpx "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && supports_httpx "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done
    if [[ -n "${PYTHON_BIN:-}" && "$PYTHON_BIN" != *"/docker-python-tool.sh" ]] \
        && supports_httpx "$PYTHON_BIN"; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi
    return 1
}

resolve_bootstrap_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

python_supports_project_minimum() {
    "$@" - <<'PY' >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

create_repo_helper_venv() {
    local venv_root="$1"
    local candidate
    local -a launcher=()

    if command -v py >/dev/null 2>&1; then
        for candidate in -3.14 -3.13 -3.12 -3; do
            launcher=(py "$candidate")
            if python_supports_project_minimum "${launcher[@]}"; then
                "${launcher[@]}" -m venv "$venv_root" >/dev/null
                return 0
            fi
        done
    fi

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            launcher=("$(command -v "$candidate")")
            if python_supports_project_minimum "${launcher[@]}"; then
                "${launcher[@]}" -m venv "$venv_root" >/dev/null
                return 0
            fi
        fi
    done

    return 1
}

install_repo_python_dependencies() {
    local python_bin="$1"
    "$python_bin" -m pip install --disable-pip-version-check -r "$REPO_DIR/requirements-dev.txt" >/dev/null
    "$python_bin" -m pip install --disable-pip-version-check -e "$REPO_DIR" >/dev/null
}

ensure_json_helper_python() {
    local helper_python=""
    helper_python="$(json_helper_python || true)"
    if [[ -n "$helper_python" ]]; then
        printf '%s\n' "$helper_python"
        return 0
    fi

    local venv_root="$REPO_DIR/.venv"
    local venv_python="$venv_root/Scripts/python.exe"
    if [[ ! -f "$venv_python" ]]; then
        create_repo_helper_venv "$venv_root" || return 1
    fi
    if [[ ! -f "$venv_python" ]]; then
        return 1
    fi

    install_repo_python_dependencies "$venv_python"

    helper_python="$(json_helper_python || true)"
    if [[ -n "$helper_python" ]]; then
        printf '%s\n' "$helper_python"
        return 0
    fi

    return 1
}

require_discord_e2e_scope() {
    if [[ -z "${TEST_DISCORD_GUILD_ID:-}" ]]; then
        echo "ERROR: TEST_DISCORD_GUILD_ID is required for isolated Discord E2E runs." >&2
        return 1
    fi
    if [[ -z "${TEST_DISCORD_E2E_CATEGORY_ID:-}" && -z "${TEST_DISCORD_E2E_CATEGORY_NAME:-}" ]]; then
        echo "ERROR: Set TEST_DISCORD_E2E_CATEGORY_ID or TEST_DISCORD_E2E_CATEGORY_NAME for isolated Discord E2E runs." >&2
        return 1
    fi
    return 0
}

_discord_e2e_scope_args() {
    local -a args=()
    args+=(--runs-root "$DISCORD_E2E_RUNS_ROOT")
    args+=(--guild-id "$TEST_DISCORD_GUILD_ID")
    if [[ -n "${TEST_DISCORD_E2E_CATEGORY_ID:-}" ]]; then
        args+=(--category-id "$TEST_DISCORD_E2E_CATEGORY_ID")
    fi
    if [[ -n "${TEST_DISCORD_E2E_CATEGORY_NAME:-}" ]]; then
        args+=(--category-name "$TEST_DISCORD_E2E_CATEGORY_NAME")
    fi
    args+=(--channel-prefix "$TEST_DISCORD_E2E_CHANNEL_PREFIX")
    printf '%s\0' "${args[@]}"
}

janitor_discord_e2e_runs() {
    init_discord_e2e_run_manager
    require_discord_e2e_scope
    local helper_python=""
    helper_python="$(ensure_json_helper_python || true)"
    if [[ -z "$helper_python" ]]; then
        return 0
    fi
    local -a args=("scripts/discord_e2e_run_manager.py" janitor)
    while IFS= read -r -d '' arg; do
        args+=("$arg")
    done < <(_discord_e2e_scope_args)
    "$helper_python" "${args[@]}" >/dev/null || true
}

start_discord_e2e_run() {
    init_discord_e2e_run_manager
    require_discord_e2e_scope
    janitor_discord_e2e_runs
    local helper_python=""
    helper_python="$(ensure_json_helper_python || true)"
    if [[ -z "$helper_python" ]]; then
        echo "ERROR: A host-visible Python interpreter is required for Discord E2E run management." >&2
        exit 1
    fi

    local -a args=("scripts/discord_e2e_run_manager.py" start)
    while IFS= read -r -d '' arg; do
        args+=("$arg")
    done < <(_discord_e2e_scope_args)
    args+=(--ttl-minutes "$TEST_DISCORD_E2E_TTL_MINUTES")
    args+=(--mode "$DISCORD_E2E_MODE")
    args+=(--shell)

    local exports
    exports="$($helper_python "${args[@]}")"
    eval "$exports"
    export DISCORD_E2E_RUN_ID DISCORD_E2E_RUN_MANIFEST_PATH DISCORD_E2E_CLEANUP_LEDGER_PATH \
        DISCORD_E2E_HEARTBEAT_PATH DISCORD_E2E_CHANNEL_ID DISCORD_E2E_CHANNEL_NAME \
        DISCORD_E2E_TARGET_BOT_ID DISCORD_E2E_TEST_BOT_ID DISCORD_E2E_TARGET_LEASE_STATUS \
        DISCORD_E2E_MODE TEST_DISCORD_CHANNEL_ID TEST_DISCORD_TARGET_BOT_ID
}

start_discord_e2e_heartbeat() {
    init_discord_e2e_run_manager
    if [[ -z "${DISCORD_E2E_HEARTBEAT_PATH:-}" ]]; then
        return 0
    fi
    touch "$DISCORD_E2E_HEARTBEAT_PATH"
    (
        while true; do
            touch "$DISCORD_E2E_HEARTBEAT_PATH" 2>/dev/null || exit 0
            sleep 15
        done
    ) &
    DISCORD_E2E_HEARTBEAT_PID="$!"
    export DISCORD_E2E_HEARTBEAT_PID
}

stop_discord_e2e_heartbeat() {
    init_discord_e2e_run_manager
    if [[ -n "${DISCORD_E2E_HEARTBEAT_PID:-}" ]]; then
        kill "$DISCORD_E2E_HEARTBEAT_PID" >/dev/null 2>&1 || true
        wait "$DISCORD_E2E_HEARTBEAT_PID" 2>/dev/null || true
        DISCORD_E2E_HEARTBEAT_PID=""
        export DISCORD_E2E_HEARTBEAT_PID
    fi
}

cleanup_discord_e2e_run() {
    init_discord_e2e_run_manager
    local reason="${1:-explicit_cleanup}"
    local helper_python=""
    stop_discord_e2e_heartbeat

    if [[ -z "${DISCORD_E2E_RUN_MANIFEST_PATH:-}" || ! -f "$DISCORD_E2E_RUN_MANIFEST_PATH" ]]; then
        DISCORD_E2E_CLEANUP_STATUS="not_run"
        export DISCORD_E2E_CLEANUP_STATUS
        return 0
    fi

    helper_python="$(ensure_json_helper_python || true)"
    if [[ -z "$helper_python" ]]; then
        DISCORD_E2E_CLEANUP_STATUS="cleanup_unknown"
        export DISCORD_E2E_CLEANUP_STATUS
        return 0
    fi

    "$helper_python" scripts/discord_e2e_run_manager.py cleanup \
        --manifest "$DISCORD_E2E_RUN_MANIFEST_PATH" \
        --reason "$reason" >/dev/null || true

    DISCORD_E2E_CLEANUP_STATUS="$($helper_python - "$DISCORD_E2E_RUN_MANIFEST_PATH" <<'PY'
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
    export DISCORD_E2E_CLEANUP_STATUS
    janitor_discord_e2e_runs
}
