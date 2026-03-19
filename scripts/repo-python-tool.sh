#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

resolve_repo_python() {
    local candidate
    for candidate in "$REPO_DIR/.venv/bin/python" "$REPO_DIR/venv/bin/python"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "python3"
        return 0
    fi

    echo "ERROR: Unable to locate a repo Python interpreter (.venv/bin/python, venv/bin/python, or python3)." >&2
    return 1
}

PYTHON_BIN="$(resolve_repo_python)"
exec "$PYTHON_BIN" "$@"
