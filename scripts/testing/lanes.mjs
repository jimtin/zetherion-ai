#!/usr/bin/env node

/**
 * Canonical bounded lanes for deterministic local/CI execution.
 *
 * Note:
 * - Some legacy lane names are retained for protocol compatibility.
 * - "nextjs:*" lanes are marked unavailable in this repository.
 */

export const STALL_THRESHOLD_SECONDS = 45;

export const LANE_ORDER = [
  "check",
  "lint",
  "nextjs-only-audit",
  "nextjs:api-parity",
  "nextjs:functionality-matrix",
  "nextjs:functionality-check",
  "targeted-unit",
  "unit-full",
  "api-integration-coverage",
  "e2e-mocked",
  "e2e-fullstack-critical",
];

export const LANE_DEFINITIONS = {
  check: {
    description: "Pipeline + endpoint-doc contract checks",
    timeoutSeconds: 600,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "REPO_PYTHON=.venv/bin/python; if [ ! -x \"$REPO_PYTHON\" ]; then REPO_PYTHON=venv/bin/python; fi; if [ ! -x \"$REPO_PYTHON\" ]; then REPO_PYTHON=python3; fi; python3 scripts/local_gate_plan.py --base-ref ${LOCAL_GATE_BASE_REF:-origin/main} --head-ref HEAD --fail-on-unmapped && python3 scripts/check_pipeline_contract.py && DOCS_BUNDLE_BASE_SHA=${DOCS_BUNDLE_BASE_SHA:-origin/main} python3 scripts/check-endpoint-doc-bundle.py && scripts/check-docs-nav.py && scripts/check-docs-links.py && scripts/check-route-doc-parity.py && scripts/check-cgs-route-doc-parity.py && scripts/check-env-doc-parity.py && \"$REPO_PYTHON\" -m mkdocs build --strict && python3 scripts/check-announcement-dm-guard.py && python3 scripts/check-optional-service-guards.py && python3 scripts/check-windows-powershell-compat.py && python3 scripts/check-qdrant-scope-guard.py",
    ],
  },
  lint: {
    description: "Ruff lint pass for repository Python sources",
    timeoutSeconds: 900,
    command: [
      "bash",
      "-lc",
      "ruff check src/ tests/ updater_sidecar/ && ruff format --check src/ tests/",
    ],
  },
  "nextjs-only-audit": {
    unavailable: true,
    timeoutSeconds: 300,
    description: "Unavailable: this repository has no Next.js lane",
  },
  "nextjs:api-parity": {
    unavailable: true,
    timeoutSeconds: 300,
    description: "Unavailable: this repository has no Next.js lane",
  },
  "nextjs:functionality-matrix": {
    unavailable: true,
    timeoutSeconds: 300,
    description: "Unavailable: this repository has no Next.js lane",
  },
  "nextjs:functionality-check": {
    unavailable: true,
    timeoutSeconds: 300,
    description: "Unavailable: this repository has no Next.js lane",
  },
  "targeted-unit": {
    description: "Targeted unit suite for changed files",
    timeoutSeconds: 900,
    heartbeat: true,
    command: ["python3", "-m", "pytest", "tests/unit", "-q", "--tb=short", "--no-cov"],
  },
  "unit-full": {
    description: "Full unit test lane with coverage gate",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "python3",
      "-m",
      "pytest",
      "tests/",
      "-m",
      "not integration and not discord_e2e",
      "--cov=src/zetherion_ai",
      "--cov-report=term-missing",
      "--cov-fail-under=90",
      "-q",
      "--tb=short",
    ],
  },
  "api-integration-coverage": {
    description: "API/integration shard coverage report lane",
    timeoutSeconds: 2400,
    heartbeat: true,
    command: [
      "python3",
      "-m",
      "pytest",
      "tests/integration/test_api_http.py",
      "tests/integration/test_skills_http.py",
      "tests/integration/test_agent_skills_http.py",
      "-m",
      "integration and not optional_e2e",
      "-q",
      "--tb=short",
    ],
  },
  "e2e-mocked": {
    description: "Mocked E2E lane",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "python3",
      "-m",
      "pytest",
      "tests/integration/test_dev_watcher_e2e.py",
      "tests/integration/test_milestone_e2e.py",
      "-m",
      "integration and not optional_e2e",
      "-q",
      "--tb=short",
    ],
  },
  "e2e-fullstack-critical": {
    description: "Critical full-stack E2E lane via canonical gate",
    timeoutSeconds: 5400,
    command: ["./scripts/test-full.sh"],
  },
};
