#!/usr/bin/env bash
# Compatibility wrapper: canonical full gate now owns Discord E2E execution.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

echo "[run-discord-e2e-tests] Delegating to canonical full pipeline (includes required Discord E2E)."
exec "$SCRIPT_DIR/test-full.sh" "$@"
