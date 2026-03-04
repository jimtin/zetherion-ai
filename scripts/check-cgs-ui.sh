#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CGS_DIR="${ROOT_DIR}/cgs"

if [[ ! -f "${CGS_DIR}/package.json" ]]; then
  echo "[check-cgs-ui] skip: cgs/package.json not found"
  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[check-cgs-ui] error: npm is required"
  exit 1
fi

cd "${CGS_DIR}"

if [[ ! -d node_modules ]]; then
  echo "[check-cgs-ui] installing dependencies (npm ci)"
  npm ci --no-audit --no-fund
fi

echo "[check-cgs-ui] lint"
npm run lint

echo "[check-cgs-ui] typecheck"
npm run typecheck

echo "[check-cgs-ui] test"
npm run test

echo "[check-cgs-ui] build"
npm run build
