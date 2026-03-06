#!/usr/bin/env bash
# Blessed single-suite wrapper for required Discord E2E debugging.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

usage() {
    cat <<'USAGE'
Usage: ./scripts/run-required-discord-e2e.sh [-- <extra pytest args>]

Runs the required Discord E2E suite using the repo-local contract:
- sources .env if present
- activates the repo-local virtualenv if present
- validates required credentials
- executes the canonical required Discord E2E pytest command

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

load_repo_env() {
    if [[ -f "$REPO_DIR/.env" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$REPO_DIR/.env"
        set +a
    fi
}

activate_repo_venv() {
    local candidate
    for candidate in "$REPO_DIR/.venv/bin/activate" "$REPO_DIR/venv/bin/activate"; do
        if [[ -f "$candidate" ]]; then
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
    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        "$REPO_DIR/.venv/bin/python3" \
        "$REPO_DIR/venv/bin/python3"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    return 1
}

ensure_python_ca_bundle() {
    if [[ -n "${SSL_CERT_FILE:-}" && -r "${SSL_CERT_FILE:-}" ]]; then
        return 0
    fi

    local ca_bundle
    ca_bundle="$($PYTHON_BIN - <<'PY'
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

    if [[ -z "$ca_bundle" || ! -r "$ca_bundle" ]]; then
        echo "ERROR: Could not determine a readable CA bundle for Python TLS verification." >&2
        echo "Install certifi in the repo virtualenv or configure SSL_CERT_FILE." >&2
        exit 1
    fi

    export SSL_CERT_FILE="$ca_bundle"
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
require_env_var "TEST_DISCORD_CHANNEL_ID"
require_env_var "DISCORD_TOKEN"
require_env_var "OPENAI_API_KEY"
require_env_var "GEMINI_API_KEY"
if [[ "$DISCORD_E2E_PROVIDER" == "groq" ]]; then
    require_env_var "GROQ_API_KEY"
fi

echo "[discord-e2e] Running required Discord E2E via blessed wrapper (provider=$DISCORD_E2E_PROVIDER, ssl_cert=$SSL_CERT_FILE)"
exec env DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" DOCKER_MANAGED_EXTERNALLY=true SSL_CERT_FILE="$SSL_CERT_FILE" \
    "$PYTHON_BIN" -m pytest \
    tests/integration/test_discord_e2e.py \
    -m "discord_e2e and not optional_e2e" \
    --timeout=180 \
    -v \
    --tb=short \
    -s \
    --no-cov \
    "$@"
