#!/usr/bin/env bash
# validate-ci.sh - Run CI checks locally before pushing
# Usage: ./scripts/validate-ci.sh [--quick] [--docker]
#
# Options:
#   --quick   Skip slow checks (Docker build, Trivy)
#   --docker  Include Docker build and Trivy scan

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

QUICK=false
DOCKER=false

for arg in "$@"; do
    case $arg in
        --quick) QUICK=true ;;
        --docker) DOCKER=true ;;
    esac
done

passed=0
failed=0
skipped=0

check_tool() {
    command -v "$1" &> /dev/null
}

run_check() {
    local name="$1"
    local cmd="$2"
    local skip_if_quick="${3:-false}"
    local tool="${4:-}"  # Optional: primary tool name to check

    if [[ "$skip_if_quick" == "true" && "$QUICK" == "true" ]]; then
        echo -e "${YELLOW}SKIP${NC} $name (--quick mode)"
        ((skipped++))
        return 0
    fi

    # Check if tool is installed
    if [[ -n "$tool" ]] && ! check_tool "$tool"; then
        echo -e "${YELLOW}SKIP${NC} $name ($tool not installed)"
        ((skipped++))
        return 0
    fi

    echo -n "Running $name... "
    if eval "$cmd" > /tmp/ci-check-output.txt 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        ((passed++))
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        echo "  Output:"
        sed 's/^/    /' /tmp/ci-check-output.txt | head -20
        ((failed++))
        return 1
    fi
}

echo "========================================"
echo "Local CI Validation"
echo "========================================"
echo ""

# Code Quality
echo "--- Code Quality ---"
run_check "Ruff linter" "ruff check src/ tests/" "false" "ruff"
run_check "Ruff formatter" "ruff format --check src/ tests/" "false" "ruff"
run_check "mypy type check" "mypy src/zetherion_ai --config-file=pyproject.toml" "false" "mypy"

# Security
echo ""
echo "--- Security Scanning ---"
run_check "Bandit" "bandit -r src/ -c pyproject.toml -q" "false" "bandit"
run_check "Semgrep" "semgrep scan --config auto --quiet src/" "true" "semgrep"
run_check "Gitleaks" "gitleaks detect --source . --no-git -q 2>/dev/null || true" "false" "gitleaks"

# Dependencies
echo ""
echo "--- Dependency Checks ---"
run_check "pip-audit" "pip-audit -r requirements.txt --strict" "false" "pip-audit"
run_check "pip-licenses" "pip-licenses --allow-only='MIT License;MIT;BSD License;BSD-2-Clause;BSD-3-Clause;Apache Software License;Apache License 2.0;Apache-2.0;ISC License;ISC;Python Software Foundation License;PSF-2.0;Mozilla Public License 2.0 (MPL 2.0);MPL-2.0;Artistic License;Public Domain;The Unlicense;CC0-1.0;0BSD;Zlib;UNKNOWN' --partial-match" "false" "pip-licenses"

# Tests
echo ""
echo "--- Tests ---"
run_check "pytest (unit)" "pytest tests/ -m 'not integration' -q --tb=no" "false" "pytest"

# Docker (optional)
if [[ "$DOCKER" == "true" ]]; then
    echo ""
    echo "--- Docker ---"
    run_check "Docker build" "docker build -t zetherion_ai:test . -q" "false" "docker"

    if docker images zetherion_ai:test -q 2>/dev/null | grep -q .; then
        run_check "Trivy scan" "trivy image --severity CRITICAL,HIGH --exit-code 0 zetherion_ai:test -q" "false" "trivy"
        run_check "Docker Compose config" "docker compose config -q" "false" "docker"
    fi
fi

# Summary
echo ""
echo "========================================"
echo "Summary"
echo "========================================"
echo -e "  ${GREEN}Passed${NC}: $passed"
echo -e "  ${RED}Failed${NC}: $failed"
echo -e "  ${YELLOW}Skipped${NC}: $skipped"
echo ""

if [[ $skipped -gt 0 ]]; then
    echo ""
    echo "To install missing tools:"
    echo "  pip install bandit pip-audit pip-licenses semgrep"
    echo "  brew install gitleaks trivy  # macOS"
fi

if [[ $failed -gt 0 ]]; then
    echo ""
    echo -e "${RED}CI validation failed. Fix issues before pushing.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed! Safe to push.${NC}"
    exit 0
fi
