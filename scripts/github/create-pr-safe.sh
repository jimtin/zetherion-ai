#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/github/create-pr-safe.sh --head <branch> --title <title> --body-file <path> [--base <branch>] [-- <extra gh args>]

Notes:
  - This wrapper intentionally requires --body-file to avoid shell expansion issues
    from inline markdown/backticks when creating PRs.
USAGE
}

BASE="main"
HEAD=""
TITLE=""
BODY_FILE=""
EXTRA_ARGS=()

while (($# > 0)); do
  case "$1" in
    --base)
      BASE="${2:-}"
      shift 2
      ;;
    --head)
      HEAD="${2:-}"
      shift 2
      ;;
    --title)
      TITLE="${2:-}"
      shift 2
      ;;
    --body-file)
      BODY_FILE="${2:-}"
      shift 2
      ;;
    --)
      shift
      EXTRA_ARGS=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$HEAD" || -z "$TITLE" || -z "$BODY_FILE" ]]; then
  echo "Missing required arguments." >&2
  usage >&2
  exit 2
fi

if [[ ! -f "$BODY_FILE" ]]; then
  echo "Body file not found: $BODY_FILE" >&2
  exit 2
fi

PR_ARGS=(
  --base "$BASE"
  --head "$HEAD"
  --title "$TITLE"
  --body-file "$BODY_FILE"
)

if ((${#EXTRA_ARGS[@]} > 0)); then
  PR_ARGS+=("${EXTRA_ARGS[@]}")
fi

gh pr create "${PR_ARGS[@]}"
