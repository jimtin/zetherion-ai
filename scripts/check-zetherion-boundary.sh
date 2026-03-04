#!/usr/bin/env bash
# Enforce repository boundary: top-level CGS UI files do not belong in this repo.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/check-zetherion-boundary.sh                # scan tracked files
  scripts/check-zetherion-boundary.sh <base> <head>  # scan changed files in range
EOF
}

collect_files() {
    if [ "$#" -eq 0 ]; then
        git ls-files
        return 0
    fi

    if [ "$#" -eq 2 ]; then
        git diff --name-only "$1" "$2"
        return 0
    fi

    usage >&2
    return 2
}

main() {
    local files
    files="$(collect_files "$@")"
    local forbidden
    forbidden="$(
        printf '%s\n' "$files" | awk 'NF > 0' | rg '^cgs/' || true
    )"

    if [ -n "$forbidden" ]; then
        echo "ERROR: top-level CGS UI paths are not allowed in this Zetherion repo."
        echo "Found disallowed paths:"
        printf '%s\n' "$forbidden"
        return 1
    fi

    echo "Boundary check passed: no disallowed top-level cgs/ paths detected."
    return 0
}

main "$@"
