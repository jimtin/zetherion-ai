#!/usr/bin/env bash
# Run required local E2E suites and write a machine-readable receipt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"
EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"
DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"
cd "$REPO_DIR"

RECEIPT_PATH="${LOCAL_E2E_RECEIPT_PATH:-.ci/e2e-receipt.json}"
DOCKER_LOG_PATH="${DOCKER_LOG_PATH:-docker-e2e.log}"
DISCORD_LOG_PATH="${DISCORD_LOG_PATH:-discord-e2e.log}"
DISCORD_RESULT_PATH="${DISCORD_RESULT_PATH:-.artifacts/discord-e2e-last-run.json}"
DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
E2E_ENABLE_OLLAMA="${E2E_ENABLE_OLLAMA:-false}"
OLLAMA_DOCKER_IMAGE="${OLLAMA_DOCKER_IMAGE:-ollama/ollama:latest@sha256:37ef34d78a6f4563a11cbbb336bbaa75f01eb19671d639973f98baa58f11a5ed}"
CURRENT_HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
RECEIPT_HEAD_SHA="${LOCAL_E2E_RECEIPT_HEAD_SHA:-local}"
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

normalize_bool() {
    local value="${1:-false}"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$value" in
        1|true|yes|on) printf 'true\n' ;;
        *) printf 'false\n' ;;
    esac
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

ensure_optional_ollama_profile() {
    E2E_ENABLE_OLLAMA="$(normalize_bool "$E2E_ENABLE_OLLAMA")"
    if [[ "$DISCORD_E2E_PROVIDER" == "local" ]]; then
        E2E_ENABLE_OLLAMA="true"
    fi
    export E2E_ENABLE_OLLAMA

    if [[ "$E2E_ENABLE_OLLAMA" != "true" ]]; then
        return 0
    fi

    case ",${COMPOSE_PROFILES:-}," in
        *,ollama,*)
            ;;
        *)
            export COMPOSE_PROFILES="${COMPOSE_PROFILES:+${COMPOSE_PROFILES},}ollama"
            ;;
    esac
}

ensure_ollama_base_image() {
    if [[ "$E2E_ENABLE_OLLAMA" != "true" ]]; then
        return 0
    fi

    if docker image inspect "$OLLAMA_DOCKER_IMAGE" >/dev/null 2>&1; then
        echo "Ollama base image already cached." >&2
        return 0
    fi

    echo "Pulling Ollama base image for local-provider E2E..." >&2
    echo "First-time pull can take several minutes." >&2
    if ! docker pull "$OLLAMA_DOCKER_IMAGE"; then
        echo "ERROR: Failed to pull Ollama base image." >&2
        exit 1
    fi
    echo "Ollama base image ready." >&2
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

    if [[ -f "$env_file" ]]; then
        local normalized_env
        normalized_env="$(mktemp "${TMPDIR:-/tmp}/zetherion-e2e-env.XXXXXX")"
        awk 'NR == 1 {sub(/^\xef\xbb\xbf/, "")} {gsub(/\r/, "")} {print}' \
            "$env_file" >"$normalized_env"
        set -a
        # shellcheck disable=SC1090
        source "$normalized_env"
        set +a
        rm -f "$normalized_env"
    fi
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

refresh_receipt_cleanup_status() {
    if [[ -z "${RECEIPT_PATH:-}" || ! -f "$RECEIPT_PATH" ]]; then
        return 0
    fi
    local helper_python=""
    helper_python="$(command -v python3 || command -v python || true)"
    if [[ -z "$helper_python" ]]; then
        return 0
    fi
    RECEIPT_PATH="$RECEIPT_PATH" \
    E2E_DOCKER_CLEANUP_STATUS="$E2E_DOCKER_CLEANUP_STATUS" \
    DISCORD_RESULT_PATH="$DISCORD_RESULT_PATH" \
    "$helper_python" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ['RECEIPT_PATH'])
payload = json.loads(path.read_text(encoding='utf-8'))
payload['docker_cleanup_status'] = os.environ.get('E2E_DOCKER_CLEANUP_STATUS', 'unknown')
path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
PY
    return 0
}

cleanup() {
    if [[ -n "${E2E_RUN_MANIFEST_PATH:-}" ]]; then
        cleanup_e2e_run "local_required_e2e_exit" || true
        refresh_receipt_cleanup_status || true
    elif [[ "$DOCKER_STARTED_BY_SCRIPT" == "true" ]]; then
        compose_down
    fi
    return 0
}

wait_for_docker_health() {
    local i
    for i in $(seq 1 90); do
        local postgres
        local qdrant
        local ollama="not_required"
        local ollama_router="not_required"
        local skills
        local api
        local cgs_gateway
        local bot
        local ollama_ready=true

        postgres="$(inspect_service_field "postgres" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
        qdrant="$(inspect_service_field "qdrant" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
        if [[ "$E2E_ENABLE_OLLAMA" == "true" ]]; then
            ollama="$(inspect_service_field "ollama" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
            ollama_router="$(inspect_service_field "ollama-router" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
            if [[ "$ollama" != "healthy" || "$ollama_router" != "healthy" ]]; then
                ollama_ready=false
            fi
        fi
        skills="$(inspect_service_field "zetherion-ai-skills" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
        api="$(inspect_service_field "zetherion-ai-api" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
        cgs_gateway="$(inspect_service_field "zetherion-ai-cgs-gateway" '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')"
        bot="$(inspect_service_field "zetherion-ai-bot" '{{.State.Status}}')"

        if [[ "$postgres" == "healthy" && "$qdrant" == "healthy" \
            && "$skills" == "healthy" && "$api" == "healthy" \
            && "$cgs_gateway" == "healthy" && "$bot" == "running" \
            && "$ollama_ready" == "true" ]]; then
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
    ensure_ollama_base_image
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

# shellcheck source=/dev/null
source "$SCRIPT_DIR/e2e_run_manager.sh"

ensure_python_ca_bundle
trap 'cleanup || true' EXIT

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
    HEAD_SHA="$RECEIPT_HEAD_SHA" \
    CURRENT_HEAD_SHA="$CURRENT_HEAD_SHA" \
    RECEIPT_STATUS="$RECEIPT_STATUS" \
    RECEIPT_REASON_CODE="$RECEIPT_REASON_CODE" \
    RECEIPT_REASON="$RECEIPT_REASON" \
    SUITE_DOCKER_STATUS="$SUITE_DOCKER_STATUS" \
    SUITE_DOCKER_REASON="$SUITE_DOCKER_REASON" \
    SUITE_DISCORD_STATUS="$SUITE_DISCORD_STATUS" \
    SUITE_DISCORD_REASON="$SUITE_DISCORD_REASON" \
    DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" \
    DISCORD_RESULT_PATH="$DISCORD_RESULT_PATH" \
    MISSING_ENV="$MISSING_ENV" \
    "$PYTHON_BIN" - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

missing_env_raw = (os.environ.get("MISSING_ENV") or "").strip()
missing_env = [item for item in missing_env_raw.split(",") if item]

discord_result_path = Path(os.environ.get("DISCORD_RESULT_PATH", ""))
discord_result = {}
if discord_result_path.is_file():
    discord_result = json.loads(discord_result_path.read_text(encoding="utf-8"))

payload = {
    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    "run_context": "local",
    "head_sha": os.environ.get("HEAD_SHA", "local").strip(),
    "source_head_sha": os.environ.get("CURRENT_HEAD_SHA", "").strip(),
    "status": os.environ.get("RECEIPT_STATUS", "failed"),
    "reason_code": os.environ.get("RECEIPT_REASON_CODE", ""),
    "reason": os.environ.get("RECEIPT_REASON", ""),
    "provider": os.environ.get("DISCORD_E2E_PROVIDER", "groq"),
    "e2e_run_id": os.environ.get("E2E_RUN_ID", ""),
    "compose_project": os.environ.get("PROJECT", ""),
    "docker_cleanup_status": os.environ.get("E2E_DOCKER_CLEANUP_STATUS", "pending"),
    "stack_root": os.environ.get("E2E_STACK_ROOT", ""),
    "discord_channel_id": discord_result.get("channel_id", ""),
    "discord_cleanup_status": discord_result.get("cleanup_status", "not_run"),
    "target_lease_status": discord_result.get("target_lease_status", "not_run"),
    "synthetic_test_run": bool(discord_result.get("synthetic_test_run", False)),
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
    "TEST_DISCORD_GUILD_ID"
    "DISCORD_E2E_ENABLED"
    "DISCORD_E2E_ALLOWED_AUTHOR_IDS"
    "OPENAI_API_KEY"
)
provider_normalized="$(printf '%s' "$DISCORD_E2E_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
if [[ -z "$provider_normalized" ]]; then
    provider_normalized="groq"
fi
DISCORD_E2E_PROVIDER="$provider_normalized"
ensure_optional_ollama_profile

if [[ "$DISCORD_E2E_PROVIDER" == "groq" ]]; then
    required_env+=("GROQ_API_KEY")
fi

declare -a missing_env=()
for var_name in "${required_env[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        missing_env+=("$var_name")
    fi
done
if [[ -z "${DISCORD_TOKEN_TEST:-}" && -z "${DISCORD_TOKEN:-}" ]]; then
    missing_env+=("DISCORD_TOKEN_TEST|DISCORD_TOKEN")
fi
if [[ -z "${TEST_DISCORD_E2E_CATEGORY_ID:-}" && -z "${TEST_DISCORD_E2E_CATEGORY_NAME:-}" ]]; then
    missing_env+=("TEST_DISCORD_E2E_CATEGORY_ID|TEST_DISCORD_E2E_CATEGORY_NAME")
fi

if [[ "${#missing_env[@]}" -gt 0 ]]; then
    MISSING_ENV="$(IFS=,; echo "${missing_env[*]}")"
    RECEIPT_STATUS="failed"
    RECEIPT_REASON_CODE="missing_required_env"
    RECEIPT_REASON="Required local E2E credentials are missing."
    write_receipt
    echo "ERROR: missing required env: $MISSING_ENV"
    exit 1
fi

start_e2e_run
echo "Isolated E2E run: run_id=${E2E_RUN_ID} project=${PROJECT} stack_root=${E2E_STACK_ROOT}"

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
        if [[ "$suite_key" == "discord" && -f "$DISCORD_RESULT_PATH" ]]; then
            suite_reason="$($PYTHON_BIN - "$DISCORD_RESULT_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("pytest_exit_nonzero")
    raise SystemExit(0)
lease_status = str(payload.get("target_lease_status", "")).strip()
if lease_status:
    print(lease_status)
else:
    print("pytest_exit_nonzero")
PY
)"
        fi
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
    env DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" DOCKER_MANAGED_EXTERNALLY=true SSL_CERT_FILE="$SSL_CERT_FILE" DISCORD_E2E_RESULT_PATH="$DISCORD_RESULT_PATH" \
    scripts/run-required-discord-e2e.sh

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
