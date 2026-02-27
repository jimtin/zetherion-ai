#!/usr/bin/env bash
# Compatibility wrapper: canonical full gate now owns integration execution.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

echo "[run-integration-tests] Delegating to canonical full pipeline (includes integration + E2E)."
exec "$SCRIPT_DIR/test-full.sh" "$@"
