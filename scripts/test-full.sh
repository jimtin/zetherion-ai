#!/usr/bin/env bash
# Canonical full local validation pipeline.
# This is the only supported full gate for local and agent workflows.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"
READINESS_WRITER="$SCRIPT_DIR/write-local-readiness-receipt.py"
LOCAL_READINESS_RECEIPT_PATH="${LOCAL_READINESS_RECEIPT_PATH:-.artifacts/local-readiness-receipt.json}"
LOCAL_RELEASE_RECEIPT_PATH="${LOCAL_RELEASE_RECEIPT_PATH:-.ci/e2e-receipt.json}"
CURRENT_HEAD_SHA="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || true)"

cd "$REPO_DIR"

log() {
    printf '[test-full] %s\n' "$*"
}

python_supports_required_version() {
    local python_bin="$1"
    "$python_bin" - <<'PY' >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

pick_python() {
    local candidate
    for candidate in python3.12 python3 python; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        if python_supports_required_version "$(command -v "$candidate")"; then
            printf '%s\n' "$(command -v "$candidate")"
            return 0
        fi
    done
    return 1
}

resolve_receipt_python() {
    if [ "${ZETHERION_USE_DOCKER_PYTHON:-false}" = "true" ]; then
        printf '%s\n' "$DOCKER_PYTHON_WRAPPER"
        return 0
    fi
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        printf 'python\n'
        return 0
    fi
    pick_python
}

write_local_readiness_receipt() {
    local exit_code="$1"
    local receipt_python
    receipt_python="$(resolve_receipt_python || true)"
    if [ -z "$receipt_python" ] || [ ! -f "$READINESS_WRITER" ]; then
        return 0
    fi

    local status="failed"
    local summary="Zetherion local full gate failed."
    local merge_ready="false"
    local deploy_ready="false"
    local failed_path="local_full_gate"
    local missing_evidence=""
    local receipt_args=()

    if [ "$exit_code" -eq 0 ]; then
        status="success"
        summary="Zetherion local full gate passed."
        merge_ready="true"
        deploy_ready="true"
        failed_path=""
        if [ ! -f "$LOCAL_RELEASE_RECEIPT_PATH" ]; then
            missing_evidence="$LOCAL_RELEASE_RECEIPT_PATH"
        fi
    fi

    receipt_args=(
        --repo-id "zetherion-ai"
        --output "$LOCAL_READINESS_RECEIPT_PATH"
        --status "$status"
        --summary "$summary"
        --merge-ready "$merge_ready"
        --deploy-ready "$deploy_ready"
        --git-sha "$CURRENT_HEAD_SHA"
        --source "test_full"
    )
    if [ -n "$failed_path" ]; then
        receipt_args+=(--failed-path "$failed_path")
    fi
    if [ -n "$missing_evidence" ]; then
        receipt_args+=(--missing-evidence "$missing_evidence")
    fi
    if [ -n "$LOCAL_RELEASE_RECEIPT_PATH" ]; then
        receipt_args+=(--release-receipt "$LOCAL_RELEASE_RECEIPT_PATH")
    fi

    "$receipt_python" "$READINESS_WRITER" "${receipt_args[@]}" >/dev/null 2>&1 || true
}

finish() {
    local exit_code="$1"
    write_local_readiness_receipt "$exit_code"
}

trap 'finish $?' EXIT

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
    "mkdocs",
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
    python_supports_required_version "$venv_dir/bin/python" || return 1
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
            if [ -x "$DOCKER_PYTHON_WRAPPER" ]; then
                log "Local Python 3.12 is unavailable. Falling back to Docker-backed Python tooling."
                export ZETHERION_USE_DOCKER_PYTHON=true
            else
                log "Python 3.12+ is required. Install Python and re-run."
                exit 1
            fi
        else
            bootstrap_venv "$python_bin"
        fi
    fi
fi

if [ "${ZETHERION_USE_DOCKER_PYTHON:-false}" != "true" ] && ! venv_has_required_modules; then
    if [ -x "$DOCKER_PYTHON_WRAPPER" ]; then
        log "Active virtualenv does not have test dependencies. Falling back to Docker-backed Python tooling."
        export ZETHERION_USE_DOCKER_PYTHON=true
    else
        log "Active virtualenv does not have test dependencies."
        log "Run: python -m pip install -r requirements-dev.txt && python -m pip install -e ."
        exit 1
    fi
fi

# Enforce strict canonical defaults.
export RUN_DISCORD_E2E_REQUIRED="${RUN_DISCORD_E2E_REQUIRED:-true}"
export STRICT_REQUIRED_TESTS="${STRICT_REQUIRED_TESTS:-true}"
export RUN_OPTIONAL_E2E="${RUN_OPTIONAL_E2E:-false}"
export DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
export RUN_DISCORD_E2E_LOCAL_MODEL="${RUN_DISCORD_E2E_LOCAL_MODEL:-false}"
export RUN_BANDIT_CHECK="${RUN_BANDIT_CHECK:-true}"
export RUN_LICENSE_COMPLIANCE_CHECK="${RUN_LICENSE_COMPLIANCE_CHECK:-true}"
export EMBEDDINGS_BACKEND="${EMBEDDINGS_BACKEND:-openai}"
export OPENAI_EMBEDDING_MODEL="${OPENAI_EMBEDDING_MODEL:-text-embedding-3-large}"
export OPENAI_EMBEDDING_DIMENSIONS="${OPENAI_EMBEDDING_DIMENSIONS:-3072}"
export COVERAGE_ARTIFACTS_DIR="${COVERAGE_ARTIFACTS_DIR:-.artifacts/coverage}"

"$SCRIPT_DIR/pre-push-tests.sh" "$@"
