#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/require-local-gate-update.sh --sha <sha>

Behavior:
  - Downloads ci-failure-attribution artifact for the most recent failed
    CI/CD Pipeline run for the commit.
  - If attribution includes SHOULD_HAVE_BEEN_CAUGHT_LOCALLY, requires
    local gate script updates plus AGENTS/docs alignment in the same fix.
  - If attribution includes PIPELINE_CONTRACT_GAP, requires
    .ci/pipeline_contract.json update plus AGENTS/docs alignment.
USAGE
}

SHA=""
TARGET_SHA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      SHA="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SHA" ]]; then
  echo "ERROR: --sha is required."
  usage
  exit 1
fi

TARGET_SHA="$SHA"
if [[ ${#SHA} -lt 40 ]]; then
  resolved="$(git rev-parse "$SHA" 2>/dev/null || true)"
  if [[ -n "$resolved" ]]; then
    TARGET_SHA="$resolved"
  fi
fi

for tool in gh jq git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: required tool missing: $tool"
    exit 1
  fi
done

ALL_RUNS_JSON="$(gh run list --limit 30 --json databaseId,workflowName,headSha,status,conclusion,createdAt,url)"

RUNS_JSON="$({
  jq -c '
    map(select(
      .workflowName == "CI/CD Pipeline"
      or .workflowName == ".github/workflows/ci.yml"
      or .workflowName == "ci.yml"
    ))
  ' <<<"$ALL_RUNS_JSON"
})"

FAILED_RUN_ID="$({
  jq -r --arg sha "$TARGET_SHA" --arg sha_short "$SHA" '
    map(select(
      (.headSha == $sha or (.headSha | startswith($sha_short)))
      and .status == "completed"
      and (.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral")
    ))
    | sort_by(.createdAt)
    | reverse
    | .[0].databaseId // empty
  ' <<<"$RUNS_JSON"
})"

if [[ -z "$FAILED_RUN_ID" ]]; then
  echo "No failed CI/CD Pipeline run found for $TARGET_SHA. No local gate update required."
  exit 0
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

if ! gh run download "$FAILED_RUN_ID" --name "ci-failure-attribution" --dir "$tmp_dir" >/dev/null 2>&1; then
  echo "ERROR: could not download ci-failure-attribution artifact for run $FAILED_RUN_ID."
  exit 1
fi

ATTR_PATH="$(find "$tmp_dir" -type f -name 'ci-failure-attribution.json' | head -n 1)"
if [[ -z "$ATTR_PATH" ]]; then
  echo "ERROR: ci-failure-attribution.json not found in artifact."
  exit 1
fi

NEEDS_LOCAL_GATE_UPDATE="$({
  jq -r '
    [.failures[].reason_code] | any(. == "SHOULD_HAVE_BEEN_CAUGHT_LOCALLY" or startswith("LOCAL_GATE_BREACH_"))
  ' "$ATTR_PATH"
})"
NEEDS_CONTRACT_UPDATE="$({
  jq -r '
    [.failures[].reason_code] | any(. == "PIPELINE_CONTRACT_GAP")
  ' "$ATTR_PATH"
})"

if [[ "$NEEDS_LOCAL_GATE_UPDATE" != "true" && "$NEEDS_CONTRACT_UPDATE" != "true" ]]; then
  echo "Attribution does not require local gate updates for $TARGET_SHA."
  exit 0
fi

CHANGED_FILES="$({
  {
    git diff --name-only
    git diff --cached --name-only
    if git rev-parse --verify HEAD >/dev/null 2>&1; then
      git show --name-only --pretty=format: HEAD
    fi
  } | sed '/^$/d' | sort -u
})"

has_changed() {
  local target="$1"
  grep -Fxq "$target" <<<"$CHANGED_FILES"
}

missing=()

if [[ "$NEEDS_LOCAL_GATE_UPDATE" == "true" ]]; then
  if ! has_changed "scripts/test-full.sh" \
    && ! has_changed "scripts/pre-push-tests.sh" \
    && ! has_changed ".git-hooks/pre-push" \
    && ! has_changed "scripts/run-local-gate-preflight.sh" \
    && ! has_changed "scripts/local_gate_plan.py" \
    && ! has_changed ".ci/local_gate_manifest.json"; then
    missing+=("expected local gate automation change (scripts/test-full.sh, scripts/pre-push-tests.sh, .git-hooks/pre-push, scripts/run-local-gate-preflight.sh, scripts/local_gate_plan.py, or .ci/local_gate_manifest.json)")
  fi
fi

if [[ "$NEEDS_CONTRACT_UPDATE" == "true" ]]; then
  if ! has_changed ".ci/pipeline_contract.json"; then
    missing+=("expected pipeline contract update (.ci/pipeline_contract.json)")
  fi
fi

if [[ "$NEEDS_LOCAL_GATE_UPDATE" == "true" || "$NEEDS_CONTRACT_UPDATE" == "true" ]]; then
  if ! has_changed "AGENTS.md" && ! has_changed "docs/development/canonical-test-gate-and-ci-cost-plan.md"; then
    missing+=("expected policy/doc alignment change (AGENTS.md or docs/development/canonical-test-gate-and-ci-cost-plan.md)")
  fi
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: CI attribution requires local gate updates that are not present:"
  for item in "${missing[@]}"; do
    echo "  - $item"
  done
  echo
  echo "Current detected changed files:"
  if [[ -n "$CHANGED_FILES" ]]; then
    echo "$CHANGED_FILES" | sed 's/^/  - /'
  else
    echo "  - (none)"
  fi
  echo
  echo "Attribution source: $ATTR_PATH (run_id=$FAILED_RUN_ID)"
  exit 1
fi

echo "Local gate update requirements satisfied for failed CI run $FAILED_RUN_ID."
