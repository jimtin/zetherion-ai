#!/usr/bin/env bash
set -euo pipefail

cat <<'EOF'
This script is deprecated and no longer performs wiki sync.

Canonical source of truth:
  .github/workflows/sync-wiki.yml

Use one of these paths instead:
  1. Push docs changes to main (workflow runs automatically)
  2. Trigger manually with GitHub CLI:
     gh workflow run sync-wiki.yml
EOF

exit 1
