#!/usr/bin/env bash
# Print top-level scope touched by a diff range and fail on forbidden folders.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/check-scope-diff.sh [<base> <head>]
Defaults to origin/main...HEAD when origin/main exists, else HEAD~1..HEAD.
EOF
}

resolve_range() {
    if [ "$#" -eq 2 ]; then
        printf '%s %s\n' "$1" "$2"
        return 0
    fi
    if [ "$#" -ne 0 ]; then
        usage >&2
        return 2
    fi

    if git rev-parse --verify origin/main >/dev/null 2>&1; then
        printf '%s %s\n' "origin/main" "HEAD"
    else
        printf '%s %s\n' "HEAD~1" "HEAD"
    fi
}

main() {
    local base head
    read -r base head <<<"$(resolve_range "$@")"

    local changed
    changed="$(git diff --name-only "$base" "$head")"
    if [ -z "$changed" ]; then
        echo "No changed files in range $base..$head"
        return 0
    fi

    echo "Changed top-level paths ($base..$head):"
    printf '%s\n' "$changed" | awk -F/ 'NF {print $1}' | sort -u

    local forbidden
    forbidden="$(printf '%s\n' "$changed" | rg '^cgs/' || true)"
    if [ -n "$forbidden" ]; then
        echo ""
        echo "ERROR: disallowed top-level cgs/ changes detected:"
        printf '%s\n' "$forbidden"
        return 1
    fi

    return 0
}

main "$@"
