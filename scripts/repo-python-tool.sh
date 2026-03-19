#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"

resolve_repo_python() {
    local candidate
    for candidate in "$REPO_DIR/.venv/bin/python" "$REPO_DIR/venv/bin/python"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    command -v python3 >/dev/null 2>&1 && printf '%s\n' "python3"
}

python_supports_module() {
    local python_bin="${1:-}"
    local module_name="${2:-}"
    [ -n "$python_bin" ] || return 1
    [ -n "$module_name" ] || return 1
    "$python_bin" -c "import ${module_name}" >/dev/null 2>&1
}

invoke_with_docker_fallback() {
    if [ -x "$DOCKER_PYTHON_WRAPPER" ]; then
        exec "$DOCKER_PYTHON_WRAPPER" "$@"
    fi

    echo "ERROR: Unable to satisfy Python command locally and docker fallback is unavailable." >&2
    return 1
}

PYTHON_BIN="$(resolve_repo_python)"
if [ -z "$PYTHON_BIN" ]; then
    invoke_with_docker_fallback "$@"
fi

if [ "${1:-}" = "-m" ]; then
    MODULE_NAME="${2:-}"
    if [ -n "$MODULE_NAME" ] && ! python_supports_module "$PYTHON_BIN" "$MODULE_NAME"; then
        invoke_with_docker_fallback "$@"
    fi
fi

exec "$PYTHON_BIN" "$@"
