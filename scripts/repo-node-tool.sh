#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

resolve_repo_node() {
    local candidate
    for candidate in \
        "$REPO_DIR/node_modules/.bin/node" \
        "/c/Program Files/nodejs/node.exe" \
        "/mnt/c/Program Files/nodejs/node.exe"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    command -v node >/dev/null 2>&1 && printf '%s\n' "node"
}

NODE_BIN="$(resolve_repo_node)"
if [ -z "${NODE_BIN:-}" ]; then
    echo "ERROR: Unable to locate a usable Node.js binary for this workspace." >&2
    exit 1
fi

exec "$NODE_BIN" "$@"
