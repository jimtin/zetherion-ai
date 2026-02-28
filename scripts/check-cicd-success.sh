#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/check-cicd-success.sh --sha <sha> [--ref <ref>]

Rules:
  - All refs require a successful "CI/CD Pipeline" run for the target SHA.
  - main/refs/heads/main also require a successful "Deploy Windows" run and
    a valid deployment-receipt artifact proving runtime verification success.
EOF
}

SHA=""
REF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      SHA="${2:-}"
      shift 2
      ;;
    --ref)
      REF="${2:-}"
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

if [[ -z "$REF" ]]; then
  REF="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI is required."
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required."
  exit 1
fi

CI_RUNS_JSON="$(gh run list \
  --workflow "CI/CD Pipeline" \
  --commit "$SHA" \
  --limit 30 \
  --json databaseId,headSha,headBranch,status,conclusion,createdAt,url)"

CI_RUN_ID="$(
  jq -r --arg sha "$SHA" '
    map(select(
      .headSha == $sha
      and .status == "completed"
      and .conclusion == "success"
    ))
    | sort_by(.createdAt)
    | reverse
    | .[0].databaseId // empty
  ' <<<"$CI_RUNS_JSON"
)"

if [[ -z "$CI_RUN_ID" ]]; then
  echo "ERROR: No successful CI/CD Pipeline run found for commit $SHA."
  echo "$CI_RUNS_JSON" | jq -r '.[] | "- run=\(.databaseId) branch=\(.headBranch) status=\(.status) conclusion=\(.conclusion)"'
  exit 1
fi

CI_BRANCH="$(
  jq -r --arg id "$CI_RUN_ID" '
    map(select((.databaseId | tostring) == $id))
    | .[0].headBranch // ""
  ' <<<"$CI_RUNS_JSON"
)"

EFFECTIVE_REF="$REF"
if [[ -z "$EFFECTIVE_REF" || "$EFFECTIVE_REF" == "HEAD" ]]; then
  EFFECTIVE_REF="$CI_BRANCH"
fi

echo "CI success verified: run_id=$CI_RUN_ID sha=$SHA ref=$EFFECTIVE_REF"

if [[ "$EFFECTIVE_REF" != "main" && "$EFFECTIVE_REF" != "refs/heads/main" ]]; then
  exit 0
fi

DEPLOY_RUNS_JSON="$(gh run list \
  --workflow "Deploy Windows" \
  --commit "$SHA" \
  --limit 30 \
  --json databaseId,headSha,headBranch,status,conclusion,createdAt,url)"

DEPLOY_RUN_ID="$(
  jq -r --arg sha "$SHA" '
    map(select(
      .headSha == $sha
      and .status == "completed"
      and .conclusion == "success"
    ))
    | sort_by(.createdAt)
    | reverse
    | .[0].databaseId // empty
  ' <<<"$DEPLOY_RUNS_JSON"
)"

if [[ -z "$DEPLOY_RUN_ID" ]]; then
  echo "ERROR: main requires successful Deploy Windows run for commit $SHA."
  echo "$DEPLOY_RUNS_JSON" | jq -r '.[] | "- run=\(.databaseId) branch=\(.headBranch) status=\(.status) conclusion=\(.conclusion)"'
  exit 1
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

if ! gh run download "$DEPLOY_RUN_ID" --name "deployment-receipt" --dir "$tmp_dir" >/dev/null 2>&1; then
  echo "ERROR: failed to download deployment-receipt artifact from run $DEPLOY_RUN_ID."
  exit 1
fi

RECEIPT_PATH="$(find "$tmp_dir" -type f -name 'deployment-receipt.json' | head -n 1)"
if [[ -z "$RECEIPT_PATH" ]]; then
  echo "ERROR: deployment-receipt.json not found in downloaded artifact."
  exit 1
fi

IS_VALID="$(
  jq -r --arg sha "$SHA" '
    .status == "success"
    and .target_sha == $sha
    and .deployed_sha == $sha
    and .checks.containers_healthy == true
    and .checks.bot_startup_markers == true
    and .checks.postgres_model_keys == true
    and .checks.fallback_probe == true
  ' "$RECEIPT_PATH"
)"

if [[ "$IS_VALID" != "true" ]]; then
  echo "ERROR: deployment receipt did not satisfy success contract:"
  cat "$RECEIPT_PATH"
  exit 1
fi

echo "Deployment success verified: run_id=$DEPLOY_RUN_ID receipt=$RECEIPT_PATH"
