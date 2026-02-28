#!/usr/bin/env bash
# Canonical full local validation pipeline.
# This is the only supported full gate for local and agent workflows.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

log() {
    printf '[test-full] %s\n' "$*"
}

pick_python() {
    if command -v python3.12 >/dev/null 2>&1; then
        printf '%s\n' "$(command -v python3.12)"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "$(command -v python3)"
        return 0
    fi
    return 1
}

venv_has_required_modules() {
    python - <<'PY' >/dev/null 2>&1
import importlib.util

required = (
    "pytest",
    "pytest_asyncio",
    "pytest_timeout",
    "ruff",
    "mypy",
    "bandit",
    "pip_audit",
)
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
}

is_repo_virtualenv() {
    local active_venv="$1"
    [ -n "$active_venv" ] || return 1
    local resolved_active
    resolved_active="$(cd "$active_venv" 2>/dev/null && pwd -P)" || return 1
    for candidate in "$REPO_DIR/.venv" "$REPO_DIR/venv"; do
        [ -d "$candidate" ] || continue
        if [ "$resolved_active" = "$(cd "$candidate" 2>/dev/null && pwd -P)" ]; then
            return 0
        fi
    done
    return 1
}

validate_existing_venv() {
    local venv_dir="$1"
    [ -x "$venv_dir/bin/python" ] || return 1
    "$venv_dir/bin/python" -c "import sys" >/dev/null 2>&1 || return 1
    return 0
}

bootstrap_venv() {
    local python_bin="$1"
    if [ ! -d ".venv" ]; then
        log "Creating .venv with ${python_bin}"
        "$python_bin" -m venv .venv
    fi

    source .venv/bin/activate

    if ! venv_has_required_modules; then
        log "Installing dev dependencies in .venv"
        python -m pip install --upgrade pip
        python -m pip install -r requirements-dev.txt
        python -m pip install -e .
    fi
}

if [ -n "${VIRTUAL_ENV:-}" ] && ! is_repo_virtualenv "$VIRTUAL_ENV"; then
    log "Ignoring external virtualenv at ${VIRTUAL_ENV}; using repo-local venv."
    unset VIRTUAL_ENV
fi

if [ -z "${VIRTUAL_ENV:-}" ]; then
    if [ -d ".venv" ] && validate_existing_venv ".venv"; then
        # shellcheck source=/dev/null
        source .venv/bin/activate
    elif [ -d "venv" ] && validate_existing_venv "venv"; then
        # shellcheck source=/dev/null
        source venv/bin/activate
    else
        if [ -d ".venv" ] && ! validate_existing_venv ".venv"; then
            log "Detected broken .venv; removing and recreating"
            rm -rf .venv
        fi
        if [ -d "venv" ] && ! validate_existing_venv "venv"; then
            log "Detected broken venv; ignoring and using .venv"
        fi
        python_bin="$(pick_python || true)"
        if [ -z "$python_bin" ]; then
            log "Python 3.12+ is required. Install Python and re-run."
            exit 1
        fi
        bootstrap_venv "$python_bin"
    fi
fi

if ! venv_has_required_modules; then
    log "Active virtualenv does not have test dependencies."
    log "Run: python -m pip install -r requirements-dev.txt && python -m pip install -e ."
    exit 1
fi

# Enforce strict canonical defaults.
export RUN_DISCORD_E2E_REQUIRED="${RUN_DISCORD_E2E_REQUIRED:-true}"
export STRICT_REQUIRED_TESTS="${STRICT_REQUIRED_TESTS:-true}"
export RUN_OPTIONAL_E2E="${RUN_OPTIONAL_E2E:-false}"
export DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
export RUN_DISCORD_E2E_LOCAL_MODEL="${RUN_DISCORD_E2E_LOCAL_MODEL:-false}"
export EMBEDDINGS_BACKEND="${EMBEDDINGS_BACKEND:-openai}"
export OPENAI_EMBEDDING_MODEL="${OPENAI_EMBEDDING_MODEL:-text-embedding-3-large}"
export OPENAI_EMBEDDING_DIMENSIONS="${OPENAI_EMBEDDING_DIMENSIONS:-3072}"

exec "$SCRIPT_DIR/pre-push-tests.sh" "$@"
