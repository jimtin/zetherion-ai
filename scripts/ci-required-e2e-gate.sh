#!/usr/bin/env bash
# CI entrypoint: validate local required-E2E receipt contract.

set -euo pipefail

python3 scripts/ci_required_e2e_gate.py
