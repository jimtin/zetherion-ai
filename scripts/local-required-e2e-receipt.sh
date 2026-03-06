#!/usr/bin/env bash
# Run required local E2E suites and write a machine-readable receipt.

set -euo pipefail

RECEIPT_PATH="${LOCAL_E2E_RECEIPT_PATH:-.ci/e2e-receipt.json}"
DOCKER_LOG_PATH="${DOCKER_LOG_PATH:-docker-e2e.log}"
DISCORD_LOG_PATH="${DISCORD_LOG_PATH:-discord-e2e.log}"
DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || true)"

SUITE_DOCKER_STATUS="not_run"
SUITE_DOCKER_REASON="not_applicable"
SUITE_DISCORD_STATUS="not_run"
SUITE_DISCORD_REASON="not_applicable"
RECEIPT_STATUS="failed"
RECEIPT_REASON_CODE="uninitialized"
RECEIPT_REASON="Local required E2E did not run."
MISSING_ENV=""

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
    python - <<'PY'
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
    pytest tests/integration/test_e2e.py \
    -m "integration and not optional_e2e" \
    --timeout=120 \
    -v \
    --tb=short \
    -s \
    --no-cov

run_suite \
    "discord" \
    "$DISCORD_LOG_PATH" \
    env DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" \
    pytest tests/integration/test_discord_e2e.py \
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
