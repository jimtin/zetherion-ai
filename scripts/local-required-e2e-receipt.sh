#!/usr/bin/env bash
# Run required local E2E suites and write a machine-readable receipt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

RECEIPT_PATH="${LOCAL_E2E_RECEIPT_PATH:-.ci/e2e-receipt.json}"
DOCKER_LOG_PATH="${DOCKER_LOG_PATH:-docker-e2e.log}"
DISCORD_LOG_PATH="${DISCORD_LOG_PATH:-discord-e2e.log}"
DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.test.yml}"
PROJECT="${PROJECT:-zetherion-ai-test}"
PRESERVE_TEST_VOLUMES="${PRESERVE_TEST_VOLUMES:-false}"
DOCKER_STARTED_BY_SCRIPT=false

SUITE_DOCKER_STATUS="not_run"
SUITE_DOCKER_REASON="not_applicable"
SUITE_DISCORD_STATUS="not_run"
SUITE_DISCORD_REASON="not_applicable"
RECEIPT_STATUS="failed"
RECEIPT_REASON_CODE="uninitialized"
RECEIPT_REASON="Local required E2E did not run."
MISSING_ENV=""

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
        echo "ERROR: Could not determine a readable CA bundle for Python TLS verification."
        echo "Install certifi in the repo virtualenv or configure SSL_CERT_FILE."
        exit 1
    fi

    export SSL_CERT_FILE="$ca_bundle"
}

compose_down() {
    if [[ "$PRESERVE_TEST_VOLUMES" == "true" ]]; then
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down >/dev/null 2>&1 || true
    else
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v >/dev/null 2>&1 || true
    fi
}

cleanup() {
    if [[ "$DOCKER_STARTED_BY_SCRIPT" == "true" ]]; then
        compose_down
    fi
}

wait_for_docker_health() {
    local i
    for i in $(seq 1 90); do
        local postgres
        local qdrant
        local ollama
        local ollama_router
        local skills
        local api
        local cgs_gateway
        local bot

        postgres="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-postgres" 2>/dev/null || echo "missing")"
        qdrant="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-qdrant" 2>/dev/null || echo "missing")"
        ollama="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama" 2>/dev/null || echo "missing")"
        ollama_router="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-ollama-router" 2>/dev/null || echo "missing")"
        skills="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-skills" 2>/dev/null || echo "missing")"
        api="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-api" 2>/dev/null || echo "missing")"
        cgs_gateway="$(docker inspect --format='{{.State.Health.Status}}' "${PROJECT}-cgs-gateway" 2>/dev/null || echo "missing")"
        bot="$(docker inspect --format='{{.State.Status}}' "${PROJECT}-bot" 2>/dev/null || echo "missing")"

        if [[ "$postgres" == "healthy" && "$qdrant" == "healthy" && "$ollama" == "healthy" \
            && "$ollama_router" == "healthy" && "$skills" == "healthy" && "$api" == "healthy" \
            && "$cgs_gateway" == "healthy" && "$bot" == "running" ]]; then
            return 0
        fi

        sleep 3
    done

    return 1
}

start_external_docker_stack() {
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker is not running." >&2
        exit 1
    fi

    compose_down
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build >/dev/null
    DOCKER_STARTED_BY_SCRIPT=true

    if ! wait_for_docker_health; then
        echo "ERROR: Docker test stack did not become healthy." >&2
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --tail=50 >&2 || true
        exit 1
    fi
}

load_repo_env
activate_repo_venv

PYTHON_BIN="$(resolve_python_bin || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Could not find Python executable for local required E2E." >&2
    exit 1
fi

ensure_python_ca_bundle
trap cleanup EXIT

contains_skips() {
    local log_file="$1"
    local pattern="[1-9][0-9]* skipped|\\bSKIPPED\\b"
    if command -v rg >/dev/null 2>&1; then
        rg -q "$pattern" "$log_file"
    else
        grep -Eq "$pattern" "$log_file"
    fi
}

write_receipt() {
    RECEIPT_PATH="$RECEIPT_PATH" \
    HEAD_SHA="$HEAD_SHA" \
    RECEIPT_STATUS="$RECEIPT_STATUS" \
    RECEIPT_REASON_CODE="$RECEIPT_REASON_CODE" \
    RECEIPT_REASON="$RECEIPT_REASON" \
    SUITE_DOCKER_STATUS="$SUITE_DOCKER_STATUS" \
    SUITE_DOCKER_REASON="$SUITE_DOCKER_REASON" \
    SUITE_DISCORD_STATUS="$SUITE_DISCORD_STATUS" \
    SUITE_DISCORD_REASON="$SUITE_DISCORD_REASON" \
    DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" \
    MISSING_ENV="$MISSING_ENV" \
    "$PYTHON_BIN" - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

missing_env_raw = (os.environ.get("MISSING_ENV") or "").strip()
missing_env = [item for item in missing_env_raw.split(",") if item]

payload = {
    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    "run_context": "local",
    "head_sha": os.environ.get("HEAD_SHA", "").strip(),
    "status": os.environ.get("RECEIPT_STATUS", "failed"),
    "reason_code": os.environ.get("RECEIPT_REASON_CODE", ""),
    "reason": os.environ.get("RECEIPT_REASON", ""),
    "provider": os.environ.get("DISCORD_E2E_PROVIDER", "groq"),
    "missing_env": missing_env,
    "suites": {
        "docker_e2e": {
            "test_path": "tests/integration/test_e2e.py",
            "status": os.environ.get("SUITE_DOCKER_STATUS", "not_run"),
            "reason_code": os.environ.get("SUITE_DOCKER_REASON", ""),
        },
        "discord_required_e2e": {
            "test_path": "tests/integration/test_discord_e2e.py",
            "marker": "discord_e2e and not optional_e2e",
            "status": os.environ.get("SUITE_DISCORD_STATUS", "not_run"),
            "reason_code": os.environ.get("SUITE_DISCORD_REASON", ""),
        },
    },
}

path = Path(os.environ.get("RECEIPT_PATH", ".ci/e2e-receipt.json"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
}

declare -a required_env=(
    "TEST_DISCORD_BOT_TOKEN"
    "TEST_DISCORD_CHANNEL_ID"
    "OPENAI_API_KEY"
    "GEMINI_API_KEY"
    "DISCORD_TOKEN"
)
provider_normalized="$(printf '%s' "$DISCORD_E2E_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [[ -z "$provider_normalized" ]]; then
    provider_normalized="groq"
fi
DISCORD_E2E_PROVIDER="$provider_normalized"

if [[ "$DISCORD_E2E_PROVIDER" == "groq" ]]; then
    required_env+=("GROQ_API_KEY")
fi

declare -a missing_env=()
for var_name in "${required_env[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        missing_env+=("$var_name")
    fi
done

if [[ "${#missing_env[@]}" -gt 0 ]]; then
    MISSING_ENV="$(IFS=,; echo "${missing_env[*]}")"
    RECEIPT_STATUS="failed"
    RECEIPT_REASON_CODE="missing_required_env"
    RECEIPT_REASON="Required local E2E credentials are missing."
    write_receipt
    echo "ERROR: missing required env: $MISSING_ENV"
    exit 1
fi

start_external_docker_stack

run_suite() {
    local suite_key="$1"
    local log_file="$2"
    shift 2

    set +e
    "$@" 2>&1 | tee "$log_file"
    local exit_code=${PIPESTATUS[0]}
    set -e

    local suite_status="passed"
    local suite_reason="ok"

    if [[ "$exit_code" -ne 0 ]]; then
        suite_status="failed"
        suite_reason="pytest_exit_nonzero"
    elif contains_skips "$log_file"; then
        suite_status="failed"
        suite_reason="required_suite_reported_skips"
    fi

    if [[ "$suite_key" == "docker" ]]; then
        SUITE_DOCKER_STATUS="$suite_status"
        SUITE_DOCKER_REASON="$suite_reason"
    else
        SUITE_DISCORD_STATUS="$suite_status"
        SUITE_DISCORD_REASON="$suite_reason"
    fi
}

run_suite \
    "docker" \
    "$DOCKER_LOG_PATH" \
    env DOCKER_MANAGED_EXTERNALLY=true SSL_CERT_FILE="$SSL_CERT_FILE" \
    "$PYTHON_BIN" -m pytest tests/integration/test_e2e.py \
    -m "integration and not optional_e2e" \
    --timeout=120 \
    -v \
    --tb=short \
    -s \
    --no-cov

run_suite \
    "discord" \
    "$DISCORD_LOG_PATH" \
    env DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" DOCKER_MANAGED_EXTERNALLY=true SSL_CERT_FILE="$SSL_CERT_FILE" \
    "$PYTHON_BIN" -m pytest tests/integration/test_discord_e2e.py \
    -m "discord_e2e and not optional_e2e" \
    --timeout=180 \
    -v \
    --tb=short \
    -s \
    --no-cov

if [[ "$SUITE_DOCKER_STATUS" == "passed" && "$SUITE_DISCORD_STATUS" == "passed" ]]; then
    RECEIPT_STATUS="success"
    RECEIPT_REASON_CODE="required_suites_passed"
    RECEIPT_REASON="Required local Docker and Discord E2E suites passed."
    write_receipt
    echo "Local required E2E receipt written to $RECEIPT_PATH"
    exit 0
fi

RECEIPT_STATUS="failed"
RECEIPT_REASON_CODE="required_suite_failed"
RECEIPT_REASON="One or more required local E2E suites failed."
write_receipt
exit 1
