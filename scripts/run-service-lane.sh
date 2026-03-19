#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

LANE_ID=""
SERVICE_SLOT="slot_a"
SERVICES=()
ARTIFACTS_ROOT=""
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-180}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lane)
            LANE_ID="$2"
            shift 2
            ;;
        --slot)
            SERVICE_SLOT="$2"
            shift 2
            ;;
        --services)
            IFS=',' read -r -a SERVICES <<<"$2"
            shift 2
            ;;
        --artifacts-root)
            ARTIFACTS_ROOT="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$LANE_ID" ]]; then
    echo "Missing required --lane argument" >&2
    exit 2
fi

if [[ ${#SERVICES[@]} -eq 0 ]]; then
    echo "Missing required --services argument" >&2
    exit 2
fi

if [[ -z "$ARTIFACTS_ROOT" ]]; then
    ARTIFACTS_ROOT="$REPO_DIR/.artifacts/$LANE_ID"
fi

mkdir -p "$ARTIFACTS_ROOT"

python_supports_minimum_version() {
    local python_bin="$1"
    local major="$2"
    local minor="$3"
    "$python_bin" - <<PY >/dev/null 2>&1
import sys

raise SystemExit(0 if sys.version_info >= (${major}, ${minor}) else 1)
PY
}

python_supports_module() {
    local python_bin="$1"
    local module_name="$2"
    "$python_bin" -c "import ${module_name}" >/dev/null 2>&1
}

resolve_control_python_bin() {
    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        python3.12 python3.11 python3.10 python3.9 python3 python; do
        if [[ -x "$candidate" ]] && python_supports_minimum_version "$candidate" 3 9; then
            printf '%s\n' "$candidate"
            return 0
        fi
        if command -v "$candidate" >/dev/null 2>&1; then
            local resolved
            resolved="$(command -v "$candidate")"
            if python_supports_minimum_version "$resolved" 3 9; then
                printf '%s\n' "$resolved"
                return 0
            fi
        fi
    done

    return 1
}

resolve_test_runner() {
    local candidate
    for candidate in \
        "$REPO_DIR/.venv/bin/python" \
        "$REPO_DIR/venv/bin/python" \
        python3.12 python3 python; do
        if [[ -x "$candidate" ]] \
            && python_supports_minimum_version "$candidate" 3 12 \
            && python_supports_module "$candidate" pytest; then
            printf '%s\n' "$candidate"
            return 0
        fi
        if command -v "$candidate" >/dev/null 2>&1; then
            local resolved
            resolved="$(command -v "$candidate")"
            if python_supports_minimum_version "$resolved" 3 12 \
                && python_supports_module "$resolved" pytest; then
                printf '%s\n' "$resolved"
                return 0
            fi
        fi
    done

    printf '%s\n' "$REPO_DIR/scripts/docker-python-tool.sh"
}

CONTROL_PYTHON_BIN="${CONTROL_PYTHON_BIN:-$(resolve_control_python_bin)}"
if [[ -z "$CONTROL_PYTHON_BIN" ]]; then
    echo "A host Python 3.9+ interpreter is required for service-lane orchestration" >&2
    exit 1
fi

TEST_RUNNER="${TEST_RUNNER:-$(resolve_test_runner)}"
PYTHON_BIN="$CONTROL_PYTHON_BIN"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.test.yml}"
E2E_SERVICE_SLOT="$SERVICE_SLOT"
if [[ "${DISCORD_E2E_ENABLED:-false}" == "true" ]]; then
    ZETHERION_HEADLESS_DISCORD="${ZETHERION_HEADLESS_DISCORD:-false}"
else
    ZETHERION_HEADLESS_DISCORD="${ZETHERION_HEADLESS_DISCORD:-true}"
fi
export PYTHON_BIN TEST_RUNNER REPO_DIR COMPOSE_FILE E2E_SERVICE_SLOT
export ZETHERION_HEADLESS_DISCORD

# shellcheck source=/dev/null
source "$SCRIPT_DIR/e2e_run_manager.sh"

write_cleanup_receipt() {
    local receipt_path="$ARTIFACTS_ROOT/cleanup-receipt.json"
    if [[ -z "${E2E_RUN_MANIFEST_PATH:-}" || ! -f "$E2E_RUN_MANIFEST_PATH" ]]; then
        printf '{\n  "status": "not_run"\n}\n' >"$receipt_path"
        return 0
    fi

    python3 - <<'PY' "$E2E_RUN_MANIFEST_PATH" "$receipt_path"
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cleanup = manifest.get("cleanup") or {"status": "unknown"}
Path(sys.argv[2]).write_text(json.dumps(cleanup, indent=2) + "\n", encoding="utf-8")
PY
}

capture_artifacts() {
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps >"$ARTIFACTS_ROOT/compose.ps" 2>&1 || true
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --no-color \
        >"$ARTIFACTS_ROOT/service-logs.txt" 2>&1 || true
}

cleanup_lane() {
    capture_artifacts
    cleanup_e2e_run "${LANE_ID}_exit"
    write_cleanup_receipt
}

inspect_service_state() {
    local service="$1"
    local container_id
    container_id="$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps -q "$service" | head -n 1)"
    if [[ -z "$container_id" ]]; then
        printf 'missing\n'
        return 0
    fi
    docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$container_id" 2>/dev/null || printf 'missing\n'
}

wait_for_services() {
    local waited=0
    while [[ "$waited" -lt "$WAIT_TIMEOUT_SECONDS" ]]; do
        local all_ready=true
        local service
        for service in "${SERVICES[@]}"; do
            local state
            state="$(inspect_service_state "$service")"
            case "$state" in
                healthy|running)
                    ;;
                *)
                    all_ready=false
                    ;;
            esac
        done
        if [[ "$all_ready" == "true" ]]; then
            return 0
        fi
        sleep 3
        waited=$((waited + 3))
    done
    return 1
}

start_e2e_run
trap cleanup_lane EXIT

docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build "${SERVICES[@]}"

if ! wait_for_services; then
    capture_artifacts
    echo "Service lane '$LANE_ID' did not become healthy in ${WAIT_TIMEOUT_SECONDS}s" >&2
    exit 1
fi

capture_artifacts

if [[ $# -eq 0 ]]; then
    echo "Missing command after --" >&2
    exit 2
fi

"$TEST_RUNNER" "$@"
