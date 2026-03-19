#!/usr/bin/env bash
# Blessed isolated Discord E2E wrapper. Standalone use is diagnostic-only; merge evidence comes from local-required-e2e-receipt.sh, which boots the local bot container before invoking this wrapper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"
EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"
DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"
cd "$REPO_DIR"

usage() {
    cat <<'USAGE'
Usage: ./scripts/run-required-discord-e2e.sh [-- <extra pytest args>]

Runs the required Discord E2E suite using the repo-local contract. Standalone execution is diagnostic-only; authoritative merge evidence comes from `bash scripts/local-required-e2e-receipt.sh`:
- sources an explicit `ZETHERION_ENV_FILE`, otherwise `.env` if present
- activates the repo-local virtualenv if present
- validates required credentials and E2E scope config
- creates an isolated Discord channel with a target-bot lease
- executes the canonical required Discord E2E pytest command against the configured target bot
- cleans the channel and synthetic test artifacts on exit

Examples:
  ./scripts/run-required-discord-e2e.sh
  ./scripts/run-required-discord-e2e.sh -- -k test_bot_responds_to_message
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

if [[ "${1:-}" == "--" ]]; then
    shift
fi

python_supports_required_version() {
    local python_bin="$1"
    "$python_bin" - <<'PY' >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

python_has_required_modules() {
    local python_bin="$1"
    "$python_bin" - <<'PY' >/dev/null 2>&1
import importlib.util

required = (
    "httpx",
    "pytest",
    "pytest_asyncio",
)
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
}

is_generated_e2e_env_file() {
    local env_file="${1:-}"
    case "$env_file" in
        */zetherion-e2e-runs/stacks/*/run.env|*/.artifacts/e2e-runs/stacks/*/run.env|*/.artifacts/ci-e2e-runs/stacks/*/run.env)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

load_repo_env() {
    local env_file="$DEFAULT_ZETHERION_ENV_FILE"

    if [[ -n "$EXPLICIT_ZETHERION_ENV_FILE" ]]; then
        if [[ ! -f "$EXPLICIT_ZETHERION_ENV_FILE" ]]; then
            if is_generated_e2e_env_file "$EXPLICIT_ZETHERION_ENV_FILE"; then
                echo "WARN: Ignoring missing generated E2E env file: $EXPLICIT_ZETHERION_ENV_FILE" >&2
            else
                echo "ERROR: ZETHERION_ENV_FILE points to a missing file: $EXPLICIT_ZETHERION_ENV_FILE" >&2
                exit 1
            fi
        else
            env_file="$EXPLICIT_ZETHERION_ENV_FILE"
        fi
    fi

    if [[ ! -f "$env_file" ]]; then
        return 0
    fi

    local preserved_keys=(
        DISCORD_E2E_ALLOWED_AUTHOR_IDS
        DISCORD_E2E_ENABLED
        DISCORD_E2E_MODE
        DISCORD_E2E_PROVIDER
        DISCORD_E2E_RESULT_PATH
        DISCORD_TOKEN
        GEMINI_API_KEY
        GROQ_API_KEY
        OPENAI_API_KEY
        SSL_CERT_FILE
        TEST_DISCORD_BOT_TOKEN
        TEST_DISCORD_CHANNEL_ID
        TEST_DISCORD_E2E_CATEGORY_ID
        TEST_DISCORD_E2E_CATEGORY_NAME
        TEST_DISCORD_E2E_CHANNEL_PREFIX
        TEST_DISCORD_E2E_TTL_MINUTES
        TEST_DISCORD_GUILD_ID
        TEST_DISCORD_TARGET_BOT_ID
    )
    local key
    local restore_file
    local normalized_env
    restore_file="$(mktemp "${TMPDIR:-/tmp}/discord-e2e-env-restore.XXXXXX")"
    normalized_env="$(mktemp "${TMPDIR:-/tmp}/discord-e2e-env.XXXXXX")"

    for key in "${preserved_keys[@]}"; do
        if [[ -n "${!key+x}" ]]; then
            printf 'export %s=%q\n' "$key" "${!key}" >>"$restore_file"
        else
            printf 'unset %s\n' "$key" >>"$restore_file"
        fi
    done

    awk 'NR == 1 {sub(/^\xef\xbb\xbf/, "")} {gsub(/\r/, "")} {print}' \
        "$env_file" >"$normalized_env"

    set -a
    # shellcheck disable=SC1090
    source "$normalized_env"
    set +a

    # shellcheck source=/dev/null
    source "$restore_file"
    rm -f "$restore_file"
    rm -f "$normalized_env"
}

activate_repo_venv() {
    if [[ "${ZETHERION_USE_DOCKER_PYTHON:-false}" == "true" ]]; then
        return 0
    fi
    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/activate" \
        "$REPO_DIR/venv/bin/activate" \
        "$REPO_DIR/.venv/Scripts/activate" \
        "$REPO_DIR/venv/Scripts/activate"; do
        local python_candidate=""
        case "$candidate" in
            */bin/activate)
                python_candidate="${candidate%/activate}/python"
                ;;
            */Scripts/activate)
                python_candidate="${candidate%/activate}/python.exe"
                ;;
        esac
        if [[ -f "$candidate" ]] \
            && [[ -n "$python_candidate" ]] \
            && [[ -x "$python_candidate" || -f "$python_candidate" ]] \
            && python_supports_required_version "$python_candidate" \
            && python_has_required_modules "$python_candidate"; then
            # shellcheck source=/dev/null
            source "$candidate"
            return 0
        fi
    done
    return 0
}

require_env_var() {
    local var_name="$1"
    if [[ -z "${!var_name:-}" ]]; then
        echo "ERROR: Required environment variable '$var_name' is not set." >&2
        exit 1
    fi
}

resolve_python_bin() {
    if [[ "${ZETHERION_USE_DOCKER_PYTHON:-false}" == "true" && -x "$DOCKER_PYTHON_WRAPPER" ]]; then
        printf '%s\n' "$DOCKER_PYTHON_WRAPPER"
        return 0
    fi

    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/bin/python3" \
        "$REPO_DIR/venv/bin/python3" \
        "$REPO_DIR/.venv/Scripts/python.exe" \
        "$REPO_DIR/venv/Scripts/python.exe"; do
        if [[ -x "$candidate" || -f "$candidate" ]] \
            && python_supports_required_version "$candidate" \
            && python_has_required_modules "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    local fallback
    for fallback in python3.12 python3 python; do
        if ! command -v "$fallback" >/dev/null 2>&1; then
            continue
        fi
        if python_supports_required_version "$(command -v "$fallback")" \
            && python_has_required_modules "$(command -v "$fallback")"; then
            command -v "$fallback"
            return 0
        fi
    done

    if [[ -x "$DOCKER_PYTHON_WRAPPER" ]]; then
        printf '%s\n' "$DOCKER_PYTHON_WRAPPER"
        return 0
    fi
    return 1
}

ensure_python_ca_bundle() {
    if [[ "${PYTHON_BIN:-}" == *"/docker-python-tool.sh" ]]; then
        unset SSL_CERT_FILE
        return 0
    fi

    local provided_bundle="${SSL_CERT_FILE:-}"
    if [[ -n "$provided_bundle" && ! -r "$provided_bundle" ]] && command -v cygpath >/dev/null 2>&1; then
        local normalized_provided_bundle
        normalized_provided_bundle="$(cygpath -u "$provided_bundle" 2>/dev/null || true)"
        if [[ -n "$normalized_provided_bundle" ]]; then
            provided_bundle="$normalized_provided_bundle"
        fi
    fi
    if [[ -n "$provided_bundle" && -r "$provided_bundle" ]]; then
        export SSL_CERT_FILE="$provided_bundle"
        return 0
    fi

    local preferred_bundle
    for preferred_bundle in \
        /usr/lib/ssl/cert.pem \
        /etc/ssl/cert.pem \
        /etc/ssl/certs/ca-certificates.crt \
        /mingw64/ssl/certs/ca-bundle.crt \
        "/c/Program Files/Git/mingw64/ssl/certs/ca-bundle.crt" \
        /c/ProgramData/chocolatey/lib/git.install/tools/mingw64/ssl/certs/ca-bundle.crt; do
        if [[ -r "$preferred_bundle" ]]; then
            export SSL_CERT_FILE="$preferred_bundle"
            return 0
        fi
    done

    local ca_bundle
    ca_bundle="$($PYTHON_BIN - <<'PY' | tr -d '\r'
import os
import ssl
from pathlib import Path


def _readable(path: str | None) -> bool:
    return bool(path) and Path(path).is_file() and os.access(path, os.R_OK)


verify = ssl.get_default_verify_paths()
if _readable(verify.cafile):
    print(verify.cafile)
    raise SystemExit(0)

try:
    import certifi  # type: ignore
except Exception:
    raise SystemExit(1)

certifi_path = certifi.where()
if _readable(certifi_path):
    print(certifi_path)
    raise SystemExit(0)

raise SystemExit(1)
PY
)" || true

    if [[ -n "$ca_bundle" && ! -r "$ca_bundle" ]] && command -v cygpath >/dev/null 2>&1; then
        local normalized_bundle
        normalized_bundle="$(cygpath -u "$ca_bundle" 2>/dev/null || true)"
        if [[ -n "$normalized_bundle" ]]; then
            ca_bundle="$normalized_bundle"
        fi
    fi

    if [[ -n "$ca_bundle" && ! -r "$ca_bundle" ]] && command -v cygpath >/dev/null 2>&1; then
        local normalized_bundle
        normalized_bundle="$(cygpath -u "$ca_bundle" 2>/dev/null || true)"
        if [[ -n "$normalized_bundle" ]]; then
            ca_bundle="$normalized_bundle"
        fi
    fi

    if [[ -z "$ca_bundle" || ! -r "$ca_bundle" ]]; then
        echo "ERROR: Could not determine a readable CA bundle for Python TLS verification." >&2
        echo "Install certifi in the repo virtualenv or configure SSL_CERT_FILE." >&2
        exit 1
    fi

    export SSL_CERT_FILE="$ca_bundle"
}

write_result_json() {
    local exit_code="$1"
    if [[ -z "${DISCORD_E2E_RESULT_PATH:-}" ]]; then
        return 0
    fi

    DISCORD_E2E_RESULT_PATH="$DISCORD_E2E_RESULT_PATH" \
    DISCORD_E2E_RUN_MANIFEST_PATH="${DISCORD_E2E_RUN_MANIFEST_PATH:-}" \
    DISCORD_E2E_CHANNEL_ID="${DISCORD_E2E_CHANNEL_ID:-}" \
    DISCORD_E2E_CHANNEL_NAME="${DISCORD_E2E_CHANNEL_NAME:-}" \
    DISCORD_E2E_TARGET_BOT_ID="${DISCORD_E2E_TARGET_BOT_ID:-}" \
    DISCORD_E2E_TEST_BOT_ID="${DISCORD_E2E_TEST_BOT_ID:-}" \
    DISCORD_E2E_TARGET_LEASE_STATUS="${DISCORD_E2E_TARGET_LEASE_STATUS:-not_run}" \
    DISCORD_E2E_CLEANUP_STATUS="${DISCORD_E2E_CLEANUP_STATUS:-not_run}" \
    DISCORD_E2E_RUN_ID="${DISCORD_E2E_RUN_ID:-}" \
    DISCORD_E2E_MODE="${DISCORD_E2E_MODE:-local_required}" \
    WRAPPER_EXIT_CODE="$exit_code" \
    "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

result_path = Path(os.environ["DISCORD_E2E_RESULT_PATH"])
manifest_path = os.environ.get("DISCORD_E2E_RUN_MANIFEST_PATH", "")
manifest = {}
if manifest_path and Path(manifest_path).is_file():
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

payload = {
    "run_id": os.environ.get("DISCORD_E2E_RUN_ID", ""),
    "mode": os.environ.get("DISCORD_E2E_MODE", "local_required"),
    "channel_id": os.environ.get("DISCORD_E2E_CHANNEL_ID", ""),
    "channel_name": os.environ.get("DISCORD_E2E_CHANNEL_NAME", ""),
    "target_bot_id": os.environ.get("DISCORD_E2E_TARGET_BOT_ID", ""),
    "test_bot_id": os.environ.get("DISCORD_E2E_TEST_BOT_ID", ""),
    "target_lease_status": os.environ.get("DISCORD_E2E_TARGET_LEASE_STATUS", "not_run"),
    "cleanup_status": os.environ.get("DISCORD_E2E_CLEANUP_STATUS", "not_run"),
    "synthetic_test_run": True,
    "exit_code": int(os.environ.get("WRAPPER_EXIT_CODE", "1")),
    "manifest_path": manifest_path,
    "cleanup": manifest.get("cleanup", {}),
}
result_path.parent.mkdir(parents=True, exist_ok=True)
result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

DISCORD_E2E_WRAPPER_CLEANED=false
DISCORD_E2E_WRAPPER_FINALIZED=false

cleanup() {
    local exit_code="${1:-$?}"
    if [[ "$DISCORD_E2E_WRAPPER_CLEANED" == "true" ]]; then
        return 0
    fi
    DISCORD_E2E_WRAPPER_CLEANED=true
    cleanup_discord_e2e_run "required_discord_e2e_exit"
    write_result_json "$exit_code"
    return 0
}

finalize_wrapper() {
    local exit_code="$1"
    if [[ "$DISCORD_E2E_WRAPPER_FINALIZED" == "true" ]]; then
        return 0
    fi
    DISCORD_E2E_WRAPPER_FINALIZED=true
    cleanup "$exit_code"
    return 0
}

handle_signal() {
    local signal_exit_code="$1"
    finalize_wrapper "$signal_exit_code"
    exit "$signal_exit_code"
}

load_repo_env
activate_repo_venv

PYTHON_BIN="$(resolve_python_bin || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Could not find Python executable for Discord E2E." >&2
    exit 1
fi

ensure_python_ca_bundle

DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
DISCORD_E2E_PROVIDER="$(printf '%s' "$DISCORD_E2E_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [[ -z "$DISCORD_E2E_PROVIDER" ]]; then
    DISCORD_E2E_PROVIDER="groq"
fi
if [[ "$DISCORD_E2E_PROVIDER" != "groq" && "$DISCORD_E2E_PROVIDER" != "local" ]]; then
    echo "ERROR: DISCORD_E2E_PROVIDER must be 'groq' or 'local'." >&2
    exit 1
fi

require_env_var "TEST_DISCORD_BOT_TOKEN"
require_env_var "TEST_DISCORD_GUILD_ID"
if [[ -z "${DISCORD_TOKEN_TEST:-}" && -z "${DISCORD_TOKEN:-}" ]]; then
    echo "ERROR: Set DISCORD_TOKEN_TEST or DISCORD_TOKEN for Discord E2E isolation." >&2
    exit 1
fi
require_env_var "DISCORD_E2E_ENABLED"
require_env_var "DISCORD_E2E_ALLOWED_AUTHOR_IDS"
require_env_var "OPENAI_API_KEY"
if [[ "$DISCORD_E2E_PROVIDER" == "groq" ]]; then
    require_env_var "GROQ_API_KEY"
fi
if [[ -z "${TEST_DISCORD_E2E_CATEGORY_ID:-}" && -z "${TEST_DISCORD_E2E_CATEGORY_NAME:-}" ]]; then
    echo "ERROR: Set TEST_DISCORD_E2E_CATEGORY_ID or TEST_DISCORD_E2E_CATEGORY_NAME for Discord E2E isolation." >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$SCRIPT_DIR/discord_e2e_run_manager.sh"
trap 'finalize_wrapper $?' EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

start_discord_e2e_run
start_discord_e2e_heartbeat

PYTEST_TIMEOUT_ARGS=(--timeout=180)
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        PYTEST_TIMEOUT_ARGS+=(--timeout-method=thread)
        ;;
esac

echo "[discord-e2e] Running required Discord E2E via blessed wrapper (provider=$DISCORD_E2E_PROVIDER, run_id=${DISCORD_E2E_RUN_ID}, channel_id=${TEST_DISCORD_CHANNEL_ID}, ssl_cert=$SSL_CERT_FILE)"

PYTEST_COVERAGE_ARGS=(--no-cov)
if [[ "${DISCORD_E2E_ENABLE_COVERAGE:-false}" == "true" ]]; then
    PYTEST_COVERAGE_ARGS=()
fi

PYTEST_ARGS=(
    tests/integration/test_discord_e2e.py
    -m "discord_e2e and not optional_e2e"
    "${PYTEST_TIMEOUT_ARGS[@]}"
    -v
    --tb=short
    -s
)
if [[ ${#PYTEST_COVERAGE_ARGS[@]} -gt 0 ]]; then
    PYTEST_ARGS+=("${PYTEST_COVERAGE_ARGS[@]}")
fi
if [[ "$#" -gt 0 ]]; then
    PYTEST_ARGS+=("$@")
fi

set +e
env \
    DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" \
    DOCKER_MANAGED_EXTERNALLY=true \
    SSL_CERT_FILE="$SSL_CERT_FILE" \
    TEST_DISCORD_CHANNEL_ID="$TEST_DISCORD_CHANNEL_ID" \
    TEST_DISCORD_TARGET_BOT_ID="$TEST_DISCORD_TARGET_BOT_ID" \
    DISCORD_E2E_RUN_ID="$DISCORD_E2E_RUN_ID" \
    DISCORD_E2E_CLEANUP_LEDGER_PATH="$DISCORD_E2E_CLEANUP_LEDGER_PATH" \
    "$PYTHON_BIN" -m pytest \
    "${PYTEST_ARGS[@]}"
pytest_exit_code=$?
set -e

finalize_wrapper "$pytest_exit_code"
exit "$pytest_exit_code"
