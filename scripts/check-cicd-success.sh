#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/check-cicd-success.sh --sha <sha> [--ref <ref>] [--wait-seconds <seconds>] [--poll-interval <seconds>]

Rules:
  - All refs require successful CI evidence for the target SHA.
  - main/refs/heads/main additionally require a successful "Deploy Windows" run and
    a valid deployment-receipt artifact proving runtime + resilience success.

Options:
  --wait-seconds <seconds>  Poll for pending CI/deploy evidence before failing (default: 0)
  --poll-interval <seconds> Poll interval when waiting (default: 10)
USAGE
}

SHA=""
REF=""
TARGET_SHA=""
REPO_SLUG="jimtin/zetherion-ai"
WAIT_SECONDS=0
POLL_INTERVAL=10

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
    --wait-seconds)
      WAIT_SECONDS="${2:-}"
      shift 2
      ;;
    --poll-interval)
      POLL_INTERVAL="${2:-}"
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

if ! [[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --wait-seconds must be a non-negative integer."
  exit 1
fi

if ! [[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || [[ "$POLL_INTERVAL" -le 0 ]]; then
  echo "ERROR: --poll-interval must be a positive integer."
  exit 1
fi

TARGET_SHA="$SHA"
if [[ ${#SHA} -lt 40 ]]; then
  resolved="$(git rev-parse "$SHA" 2>/dev/null || true)"
  if [[ -n "$resolved" ]]; then
    TARGET_SHA="$resolved"
  fi
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

is_main_ref() {
  local ref="$1"
  [[ "$ref" == "main" || "$ref" == "refs/heads/main" ]]
}

fetch_all_runs_json() {
  gh run list --limit 100 --json databaseId,workflowName,headSha,headBranch,status,conclusion,createdAt,url
}

filter_ci_runs_json() {
  jq -c '
    map(select(
      .workflowName == "CI/CD Pipeline"
      or .workflowName == ".github/workflows/ci.yml"
      or .workflowName == "ci.yml"
    ))
  '
}

filter_deploy_runs_json() {
  jq -c '
    map(select(
      .workflowName == "Deploy Windows"
      or .workflowName == ".github/workflows/deploy-windows.yml"
      or .workflowName == "deploy-windows.yml"
    ))
  '
}

find_successful_run_id() {
  local runs_json="$1"
  local target_sha="$2"
  local sha_short="$3"
  jq -r --arg sha "$target_sha" --arg sha_short "$sha_short" '
    map(select(
      (.headSha == $sha or (.headSha | startswith($sha_short)))
      and .status == "completed"
      and .conclusion == "success"
    ))
    | sort_by(.createdAt)
    | reverse
    | .[0].databaseId // empty
  ' <<<"$runs_json"
}

summarize_latest_run_state() {
  local runs_json="$1"
  local target_sha="$2"
  local sha_short="$3"
  jq -r --arg sha "$target_sha" --arg sha_short "$sha_short" '
    map(select(.headSha == $sha or (.headSha | startswith($sha_short))))
    | sort_by(.createdAt)
    | reverse
    | if length == 0 then "" else .[0] | "run=\(.databaseId) status=\(.status) conclusion=\(.conclusion // \"pending\") branch=\(.headBranch // \"\") url=\(.url // \"\")" end
  ' <<<"$runs_json"
}

fetch_check_runs_json() {
  local target_sha="$1"
  gh api "repos/$REPO_SLUG/commits/$target_sha/check-runs"
}

required_check_runs_success() {
  local check_runs_json="$1"
  jq -r '
    (any(.check_runs[]?; (.name == "CI Gate / CI Summary" or .name == "CI Summary") and .status == "completed" and .conclusion == "success"))
    and
    (any(.check_runs[]?; (.name == "CI Gate / Required E2E Gate" or .name == "Required E2E Gate") and .status == "completed" and .conclusion == "success"))
  ' <<<"$check_runs_json"
}

required_check_runs_pending() {
  local check_runs_json="$1"
  jq -r '
    any(.check_runs[]?;
      (
        .name == "CI Gate / CI Summary"
        or .name == "CI Summary"
        or .name == "CI Gate / Required E2E Gate"
        or .name == "Required E2E Gate"
      ) and (.status != "completed" or .conclusion == null or .conclusion == "")
    )
  ' <<<"$check_runs_json"
}

summarize_required_check_runs() {
  local check_runs_json="$1"
  jq -r '
    [ .check_runs[]?
      | select(
          .name == "CI Gate / CI Summary"
          or .name == "CI Summary"
          or .name == "CI Gate / Required E2E Gate"
          or .name == "Required E2E Gate"
        )
      | "\(.name):status=\(.status) conclusion=\(.conclusion // \"pending\")"
    ]
    | if length == 0 then "none" else join("; ") end
  ' <<<"$check_runs_json"
}

fetch_associated_prs_json() {
  local target_sha="$1"
  gh api "repos/$REPO_SLUG/commits/$target_sha/pulls" -H "Accept: application/vnd.github+json"
}

summarize_associated_pr_ci() {
  local target_sha="$1"
  local ci_runs_json="$2"
  local prs_json
  local pr_number
  local pr_head_sha
  local pr_head_ref
  local pr_ci_run_id
  local pr_ci_state

  prs_json="$(fetch_associated_prs_json "$target_sha" 2>/dev/null || printf '[]')"
  pr_number="$(jq -r --arg sha "$target_sha" 'map(select(.merged_at != null and .merge_commit_sha == $sha)) | sort_by(.merged_at) | reverse | .[0].number // empty' <<<"$prs_json")"
  pr_head_sha="$(jq -r --arg sha "$target_sha" 'map(select(.merged_at != null and .merge_commit_sha == $sha)) | sort_by(.merged_at) | reverse | .[0].head.sha // empty' <<<"$prs_json")"
  pr_head_ref="$(jq -r --arg sha "$target_sha" 'map(select(.merged_at != null and .merge_commit_sha == $sha)) | sort_by(.merged_at) | reverse | .[0].head.ref // empty' <<<"$prs_json")"

  if [[ -z "$pr_number" || -z "$pr_head_sha" ]]; then
    echo ""
    return 0
  fi

  pr_ci_run_id="$(find_successful_run_id "$ci_runs_json" "$pr_head_sha" "$pr_head_sha")"
  if [[ -n "$pr_ci_run_id" ]]; then
    echo "associated_pr=#${pr_number} head_sha=${pr_head_sha} head_ref=${pr_head_ref} pr_ci_run=${pr_ci_run_id}"
    return 0
  fi

  pr_ci_state="$(summarize_latest_run_state "$ci_runs_json" "$pr_head_sha" "$pr_head_sha")"
  echo "associated_pr=#${pr_number} head_sha=${pr_head_sha} head_ref=${pr_head_ref}${pr_ci_state:+ ${pr_ci_state}}"
}

validate_deploy_receipt() {
  local deploy_run_id="$1"
  local target_sha="$2"
  local sha_short="$3"
  local tmp_dir
  local receipt_path
  local is_valid

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' RETURN

  if ! gh run download "$deploy_run_id" --name "deployment-receipt" --dir "$tmp_dir" >/dev/null 2>&1; then
    echo "ERROR: failed to download deployment-receipt artifact from run $deploy_run_id."
    return 1
  fi

  receipt_path="$(find "$tmp_dir" -type f -name 'deployment-receipt.json' | head -n 1)"
  if [[ -z "$receipt_path" ]]; then
    echo "ERROR: deployment-receipt.json not found in downloaded artifact."
    return 1
  fi

  is_valid="$({
    jq -r --arg sha "$target_sha" --arg sha_short "$sha_short" '
      .status == "success"
      and (.target_sha == $sha or (.target_sha | startswith($sha_short)))
      and (.deployed_sha == $sha or (.deployed_sha | startswith($sha_short)))
      and .checks.containers_healthy == true
      and .checks.bot_startup_markers == true
      and .checks.postgres_model_keys == true
      and .checks.fallback_probe == true
      and .checks.recovery_tasks_registered == true
      and .checks.runner_service_persistent == true
      and .checks.docker_service_persistent == true
    ' "$receipt_path"
  } || true)"

  if [[ "$is_valid" != "true" ]]; then
    echo "ERROR: deployment receipt did not satisfy success contract:"
    cat "$receipt_path"
    return 1
  fi

  echo "Deployment success verified: run_id=$deploy_run_id receipt=$receipt_path"
}

START_EPOCH="$(date +%s)"

while :; do
  ALL_RUNS_JSON="$(fetch_all_runs_json)"
  CI_RUNS_JSON="$(filter_ci_runs_json <<<"$ALL_RUNS_JSON")"
  DEPLOY_RUNS_JSON="$(filter_deploy_runs_json <<<"$ALL_RUNS_JSON")"

  CI_RUN_ID="$(find_successful_run_id "$CI_RUNS_JSON" "$TARGET_SHA" "$SHA")"
  CI_SOURCE="workflow"
  CI_BRANCH=""
  CI_OK="false"
  CI_PENDING="false"
  CI_PENDING_REASON=""
  CI_ASSOCIATED_PR_SUMMARY=""

  if [[ -n "$CI_RUN_ID" ]]; then
    CI_OK="true"
    CI_BRANCH="$({
      jq -r --arg id "$CI_RUN_ID" '
        map(select((.databaseId | tostring) == $id))
        | .[0].headBranch // ""
      ' <<<"$CI_RUNS_JSON"
    } || true)"
  fi

  EFFECTIVE_REF="$REF"
  if [[ -z "$EFFECTIVE_REF" || "$EFFECTIVE_REF" == "HEAD" ]]; then
    EFFECTIVE_REF="$CI_BRANCH"
  fi

  if is_main_ref "$EFFECTIVE_REF"; then
    CHECK_RUNS_JSON="$(fetch_check_runs_json "$TARGET_SHA")"
    if [[ "$CI_OK" != "true" && "$(required_check_runs_success "$CHECK_RUNS_JSON")" == "true" ]]; then
      CI_OK="true"
      CI_SOURCE="check-runs"
    elif [[ "$CI_OK" != "true" ]]; then
      CI_PENDING_REASON="$(summarize_required_check_runs "$CHECK_RUNS_JSON")"
      CI_ASSOCIATED_PR_SUMMARY="$(summarize_associated_pr_ci "$TARGET_SHA" "$CI_RUNS_JSON")"
      if [[ "$(required_check_runs_pending "$CHECK_RUNS_JSON")" == "true" ]]; then
        CI_PENDING="true"
      elif [[ -n "$CI_ASSOCIATED_PR_SUMMARY" ]]; then
        CI_PENDING="true"
      fi
    fi
  else
    if [[ "$CI_OK" != "true" ]]; then
      CI_PENDING_REASON="$(summarize_latest_run_state "$CI_RUNS_JSON" "$TARGET_SHA" "$SHA")"
      if [[ -n "$CI_PENDING_REASON" && "$CI_PENDING_REASON" == *"status=in_progress"* ]]; then
        CI_PENDING="true"
      elif [[ -n "$CI_PENDING_REASON" && "$CI_PENDING_REASON" == *"status=queued"* ]]; then
        CI_PENDING="true"
      fi
    fi
  fi

  DEPLOY_RUN_ID=""
  DEPLOY_PENDING="false"
  DEPLOY_PENDING_REASON=""

  if is_main_ref "$EFFECTIVE_REF"; then
    DEPLOY_RUN_ID="$(find_successful_run_id "$DEPLOY_RUNS_JSON" "$TARGET_SHA" "$SHA")"
    if [[ -z "$DEPLOY_RUN_ID" ]]; then
      DEPLOY_PENDING_REASON="$(summarize_latest_run_state "$DEPLOY_RUNS_JSON" "$TARGET_SHA" "$SHA")"
      if [[ -n "$DEPLOY_PENDING_REASON" && "$DEPLOY_PENDING_REASON" == *"status=in_progress"* ]]; then
        DEPLOY_PENDING="true"
      elif [[ -n "$DEPLOY_PENDING_REASON" && "$DEPLOY_PENDING_REASON" == *"status=queued"* ]]; then
        DEPLOY_PENDING="true"
      fi
    fi
  fi

  if [[ "$CI_OK" == "true" ]]; then
    echo "CI success verified: source=$CI_SOURCE sha=$TARGET_SHA ref=$EFFECTIVE_REF${CI_RUN_ID:+ run_id=$CI_RUN_ID}"
    if ! is_main_ref "$EFFECTIVE_REF"; then
      exit 0
    fi

    if [[ -n "$DEPLOY_RUN_ID" ]]; then
      validate_deploy_receipt "$DEPLOY_RUN_ID" "$TARGET_SHA" "$SHA"
      exit 0
    fi
  fi

  NOW_EPOCH="$(date +%s)"
  ELAPSED=$((NOW_EPOCH - START_EPOCH))
  SHOULD_WAIT="false"
  if [[ "$WAIT_SECONDS" -gt 0 && "$ELAPSED" -lt "$WAIT_SECONDS" ]]; then
    if [[ "$CI_PENDING" == "true" || "$DEPLOY_PENDING" == "true" ]]; then
      SHOULD_WAIT="true"
    fi
  fi

  if [[ "$SHOULD_WAIT" == "true" ]]; then
    echo "Verification pending for sha=$TARGET_SHA ref=$EFFECTIVE_REF; waiting ${POLL_INTERVAL}s (elapsed ${ELAPSED}s/${WAIT_SECONDS}s)."
    if [[ -n "$CI_PENDING_REASON" ]]; then
      echo "  CI pending: $CI_PENDING_REASON"
    fi
    if [[ -n "$CI_ASSOCIATED_PR_SUMMARY" ]]; then
      echo "  CI associated PR: $CI_ASSOCIATED_PR_SUMMARY"
    fi
    if [[ -n "$DEPLOY_PENDING_REASON" ]]; then
      echo "  Deploy pending: $DEPLOY_PENDING_REASON"
    fi
    sleep "$POLL_INTERVAL"
    continue
  fi

  if [[ "$CI_OK" != "true" ]]; then
    if [[ "$CI_PENDING" == "true" ]]; then
      echo "ERROR: CI verification is still pending for commit $TARGET_SHA."
      if [[ -n "$CI_PENDING_REASON" ]]; then
        echo "Required main check-runs: $CI_PENDING_REASON"
      fi
      if [[ -n "$CI_ASSOCIATED_PR_SUMMARY" ]]; then
        echo "Associated PR context: $CI_ASSOCIATED_PR_SUMMARY"
      fi
    else
      echo "ERROR: No successful CI evidence found for commit $TARGET_SHA."
      echo "$CI_RUNS_JSON" | jq -r '.[] | "- run=\(.databaseId) branch=\(.headBranch) sha=\(.headSha) status=\(.status) conclusion=\(.conclusion)"'
      if is_main_ref "$EFFECTIVE_REF"; then
        echo "Main fallback requires successful check-runs named \"CI Gate / CI Summary\" and \"CI Gate / Required E2E Gate\"."
        if [[ -n "$CI_PENDING_REASON" ]]; then
          echo "Observed main check-runs: $CI_PENDING_REASON"
        fi
        if [[ -n "$CI_ASSOCIATED_PR_SUMMARY" ]]; then
          echo "Associated PR context: $CI_ASSOCIATED_PR_SUMMARY"
        fi
      fi
    fi
    exit 1
  fi

  if [[ -z "$DEPLOY_RUN_ID" ]]; then
    if [[ "$DEPLOY_PENDING" == "true" ]]; then
      echo "ERROR: Deploy Windows verification is still pending for commit $TARGET_SHA."
      echo "Deploy state: $DEPLOY_PENDING_REASON"
    else
      echo "ERROR: main requires successful Deploy Windows run for commit $TARGET_SHA."
      echo "$DEPLOY_RUNS_JSON" | jq -r '.[] | "- run=\(.databaseId) branch=\(.headBranch) sha=\(.headSha) status=\(.status) conclusion=\(.conclusion)"'
      if [[ -n "$DEPLOY_PENDING_REASON" ]]; then
        echo "Observed Deploy Windows state: $DEPLOY_PENDING_REASON"
      fi
    fi
    exit 1
  fi
done
