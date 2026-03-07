#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

BASE_REF=""
HEAD_REF="HEAD"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-ref)
            BASE_REF="${2:-}"
            shift 2
            ;;
        --head-ref)
            HEAD_REF="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$BASE_REF" ]]; then
    echo "ERROR: --base-ref is required."
    exit 1
fi

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

PYTHON_BIN="$(resolve_python_bin || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Could not find a Python executable for local gate preflight."
    exit 1
fi

if ! git diff --quiet --ignore-submodules --; then
    echo "ERROR: Refusing local-gate preflight with unstaged working tree changes."
    echo "Commit or stash changes so validation runs against the exact pushed SHA."
    exit 1
fi

if ! git diff --cached --quiet --ignore-submodules --; then
    echo "ERROR: Refusing local-gate preflight with staged-but-uncommitted changes."
    echo "Commit staged changes so validation runs against the exact pushed SHA."
    exit 1
fi

HEAD_SHA="$(git rev-parse "$HEAD_REF")"
CURRENT_HEAD="$(git rev-parse HEAD)"
if [[ "$HEAD_SHA" != "$CURRENT_HEAD" ]]; then
    echo "ERROR: Refusing local-gate preflight for non-checked-out commit $HEAD_SHA."
    echo "Checkout the branch being pushed so local validation matches the pushed commit."
    exit 1
fi

PLAN_FILE="$(mktemp)"
cleanup() {
    rm -f "$PLAN_FILE"
}
trap cleanup EXIT

"$PYTHON_BIN" scripts/local_gate_plan.py \
    --base-ref "$BASE_REF" \
    --head-ref "$HEAD_SHA" \
    --output "$PLAN_FILE" \
    --fail-on-unmapped

echo "[local-gate] Planned requirements:"
"$PYTHON_BIN" - <<'PY' "$PLAN_FILE"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
requirements = payload.get("requirements", [])
matched_rules = payload.get("matched_rules", [])
if not requirements:
    print("  - none")
else:
    for requirement in requirements:
        print(f"  - {requirement['id']}: {requirement['description']}")
if matched_rules:
    print("[local-gate] Matched rules:")
    for rule in matched_rules:
        print(f"  - {rule['id']}: {', '.join(rule['matched_files'])}")
PY

REQUIREMENT_IDS=()
while IFS= read -r requirement_id; do
    if [[ -n "$requirement_id" ]]; then
        REQUIREMENT_IDS+=("$requirement_id")
    fi
done < <("$PYTHON_BIN" - <<'PY' "$PLAN_FILE"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for requirement in payload.get("requirements", []):
    print(requirement["id"])
PY
)

if [[ ${#REQUIREMENT_IDS[@]} -eq 0 ]]; then
    echo "[local-gate] Preflight completed successfully."
    exit 0
fi

LOCAL_GATE_LANE_LOG_FILE="${LOCAL_GATE_LANE_LOG_FILE:-artifacts/testing/local-gate-preflight-log.md}"
echo "[local-gate] Writing bounded lane receipts to $LOCAL_GATE_LANE_LOG_FILE"

for requirement_id in "${REQUIREMENT_IDS[@]}"; do
    case "$requirement_id" in
        endpoint-doc-bundle)
            echo "[local-gate] Running endpoint docs bundle check..."
            DOCS_BUNDLE_BASE_SHA="$BASE_REF" "$PYTHON_BIN" scripts/check-endpoint-doc-bundle.py
            ;;
        mypy-src)
            echo "[local-gate] Running strict mypy for src/zetherion_ai..."
            "$PYTHON_BIN" -m mypy src/zetherion_ai --config-file=pyproject.toml
            ;;
        bandit-src)
            echo "[local-gate] Running Bandit security scan for src/..."
            "$PYTHON_BIN" -m bandit -r src/ -c pyproject.toml
            ;;
        unit-full)
            echo "[local-gate] Running bounded unit-full lane..."
            node scripts/testing/run-bounded.mjs --lane unit-full --log-file "$LOCAL_GATE_LANE_LOG_FILE"
            ;;
        qdrant-regression-suite|replay-store-regression-suite|ci-receipt-regression-suite|ci-failure-attribution-regression-suite|deploy-preflight-regression-suite)
            PYTEST_TARGETS=()
            while IFS= read -r pytest_target; do
                if [[ -n "$pytest_target" ]]; then
                    PYTEST_TARGETS+=("$pytest_target")
                fi
            done < <("$PYTHON_BIN" - <<'PY' "$PLAN_FILE" "$requirement_id"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
requirement_id = sys.argv[2]
for requirement in payload.get("requirements", []):
    if requirement["id"] == requirement_id:
        for target in requirement.get("pytest_targets", []):
            print(target)
        break
PY
)
            if [[ ${#PYTEST_TARGETS[@]} -gt 0 ]]; then
                echo "[local-gate] Running targeted regression suite for $requirement_id..."
                node scripts/testing/run-bounded.mjs --lane targeted-unit --log-file "$LOCAL_GATE_LANE_LOG_FILE" -- \
                    "$PYTHON_BIN" -m pytest "${PYTEST_TARGETS[@]}" -q --tb=short --no-cov
            fi
            ;;
        *)
            echo "ERROR: Unknown local-gate requirement '$requirement_id'."
            exit 1
            ;;
    esac
done

echo "[local-gate] Preflight completed successfully."
