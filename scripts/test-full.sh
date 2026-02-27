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

    if ! python -c "import pytest, pytest_asyncio, pytest_timeout" >/dev/null 2>&1; then
        log "Installing dev dependencies in .venv"
        python -m pip install --upgrade pip
        python -m pip install -r requirements-dev.txt
        python -m pip install -e .
    fi
}

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

if ! python -c "import pytest" >/dev/null 2>&1; then
    log "Active virtualenv does not have test dependencies."
    log "Run: python -m pip install -r requirements-dev.txt && python -m pip install -e ."
    exit 1
fi

# Enforce strict canonical defaults.
export RUN_DISCORD_E2E_REQUIRED="${RUN_DISCORD_E2E_REQUIRED:-true}"
export STRICT_REQUIRED_TESTS="${STRICT_REQUIRED_TESTS:-true}"
export RUN_OPTIONAL_E2E="${RUN_OPTIONAL_E2E:-false}"

exec "$SCRIPT_DIR/pre-push-tests.sh" "$@"
