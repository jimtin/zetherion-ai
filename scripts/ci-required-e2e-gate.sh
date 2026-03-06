#!/usr/bin/env bash
# Enforce required E2E suite execution based on risk-classifier output.

set -euo pipefail

RECEIPT_PATH="${RECEIPT_PATH:-e2e-contract-receipt.json}"
DOCKER_LOG_PATH="${DOCKER_LOG_PATH:-docker-e2e.log}"
DISCORD_LOG_PATH="${DISCORD_LOG_PATH:-discord-e2e.log}"

E2E_REQUIRED="${E2E_REQUIRED:-true}"
E2E_DECISION_REASON_CODE="${E2E_DECISION_REASON_CODE:-unknown}"
E2E_DECISION_REASON="${E2E_DECISION_REASON:-No reason provided.}"
DISCORD_E2E_PROVIDER="${DISCORD_E2E_PROVIDER:-groq}"

SUITE_DOCKER_STATUS="not_run"
SUITE_DOCKER_REASON="not_applicable"
SUITE_DISCORD_STATUS="not_run"
SUITE_DISCORD_REASON="not_applicable"
RECEIPT_STATUS="failed"
RECEIPT_REASON_CODE="uninitialized"
RECEIPT_REASON="Gate did not run."
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
    E2E_REQUIRED="$E2E_REQUIRED" \
    E2E_DECISION_REASON_CODE="$E2E_DECISION_REASON_CODE" \
    E2E_DECISION_REASON="$E2E_DECISION_REASON" \
    RECEIPT_STATUS="$RECEIPT_STATUS" \
    RECEIPT_REASON_CODE="$RECEIPT_REASON_CODE" \
    RECEIPT_REASON="$RECEIPT_REASON" \
    SUITE_DOCKER_STATUS="$SUITE_DOCKER_STATUS" \
    SUITE_DOCKER_REASON="$SUITE_DOCKER_REASON" \
    SUITE_DISCORD_STATUS="$SUITE_DISCORD_STATUS" \
    SUITE_DISCORD_REASON="$SUITE_DISCORD_REASON" \
    MISSING_ENV="$MISSING_ENV" \
    DISCORD_E2E_PROVIDER="$DISCORD_E2E_PROVIDER" \
    python - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

missing_env_raw = (os.environ.get("MISSING_ENV") or "").strip()
missing_env = [item for item in missing_env_raw.split(",") if item]

payload = {
    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
    "required": (os.environ.get("E2E_REQUIRED", "true").lower() == "true"),
    "classifier": {
        "reason_code": os.environ.get("E2E_DECISION_REASON_CODE", ""),
        "reason": os.environ.get("E2E_DECISION_REASON", ""),
    },
    "status": os.environ.get("RECEIPT_STATUS", "failed"),
    "reason_code": os.environ.get("RECEIPT_REASON_CODE", ""),
    "reason": os.environ.get("RECEIPT_REASON", ""),
    "missing_env": missing_env,
    "provider": os.environ.get("DISCORD_E2E_PROVIDER", "groq"),
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

path = Path(os.environ.get("RECEIPT_PATH", "e2e-contract-receipt.json"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
}

if [[ "$E2E_REQUIRED" != "true" ]]; then
    RECEIPT_STATUS="not_required"
    RECEIPT_REASON_CODE="not_required_by_risk_classifier"
    RECEIPT_REASON="Risk classifier marked this change set as low risk."
    write_receipt
    exit 0
fi

declare -a required_env=("TEST_DISCORD_BOT_TOKEN" "TEST_DISCORD_CHANNEL_ID" "OPENAI_API_KEY")
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
    RECEIPT_REASON="Required Discord/Docker E2E credentials are missing."
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
    RECEIPT_REASON="Required Docker and Discord E2E suites passed."
    write_receipt
    exit 0
fi

RECEIPT_STATUS="failed"
RECEIPT_REASON_CODE="required_suite_failed"
RECEIPT_REASON="One or more required E2E suites failed."
write_receipt
exit 1
