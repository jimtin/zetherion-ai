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
  "z-unit-core",
  "z-unit-runtime",
  "z-unit-owner-ci",
  "z-int-runtime-api",
  "z-int-runtime-queue",
  "z-int-dependencies",
  "z-int-platform",
  "z-e2e-dm-sim",
  "z-e2e-channel-sim",
  "z-e2e-faults",
  "z-e2e-discord-live",
  "z-e2e-discord-security",
  "z-int-runtime",
  "z-int-faults",
  "z-e2e-discord-sim",
  "z-e2e-discord-real",
  "z-release",
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
      "python3 scripts/local_gate_plan.py --base-ref ${LOCAL_GATE_BASE_REF:-origin/main} --head-ref HEAD --fail-on-unmapped && python3 scripts/check_pipeline_contract.py && DOCS_BUNDLE_BASE_SHA=${DOCS_BUNDLE_BASE_SHA:-origin/main} python3 scripts/check-endpoint-doc-bundle.py && scripts/check-docs-nav.py && scripts/check-docs-links.py && scripts/check-route-doc-parity.py && scripts/check-cgs-route-doc-parity.py && scripts/check-env-doc-parity.py && scripts/repo-python-tool.sh -m mkdocs build --strict && python3 scripts/check-announcement-dm-guard.py && python3 scripts/check-optional-service-guards.py && python3 scripts/check-windows-powershell-compat.py && python3 scripts/check-windows-deploy-contract.py && python3 scripts/check-qdrant-scope-guard.py",
    ],
  },
  lint: {
    description: "Ruff lint pass for repository Python sources",
    timeoutSeconds: 900,
    command: [
      "bash",
      "-lc",
      "scripts/repo-python-tool.sh -m ruff check src/ tests/ updater_sidecar/ && scripts/repo-python-tool.sh -m ruff format --check src/ tests/",
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
  "z-unit-core": {
    description: "Sharded unit lane for queue, local gate, and release contracts",
    timeoutSeconds: 1200,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "scripts/docker-python-tool.sh -m pytest tests/unit/test_queue.py tests/unit/test_local_gate_plan.py tests/unit/test_local_gate_docker_fallback.py -q --tb=short --no-cov",
    ],
  },
  "z-unit-runtime": {
    description: "Sharded unit lane for Discord runtime and startup health",
    timeoutSeconds: 1200,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "scripts/docker-python-tool.sh -m pytest tests/unit/test_discord_bot_critical_paths.py tests/unit/test_discord_dm_scenarios.py tests/unit/test_scheduler_heartbeat.py tests/unit/test_skills_server.py -q --tb=short --no-cov",
    ],
  },
  "z-unit-owner-ci": {
    description: "Sharded unit lane for owner-CI planning and readiness receipts",
    timeoutSeconds: 1200,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "scripts/docker-python-tool.sh -m pytest tests/unit/test_owner_ci_skills.py tests/unit/test_check_pipeline_contract.py tests/unit/test_check_cicd_success.py tests/unit/test_automerge_orchestrator.py -q --tb=short --no-cov",
    ],
  },
  "z-int-runtime-api": {
    description: "Service-backed API and gateway runtime integration lane",
    timeoutSeconds: 2400,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-runtime-api --slot slot_a --services postgres,qdrant,zetherion-ai-skills,zetherion-ai-api,zetherion-ai-cgs-gateway --artifacts-root .artifacts/z-int-runtime-api -- -m pytest tests/integration/test_runtime_dependency_service.py -m 'service_integration and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-int-runtime-queue": {
    description: "Service-backed queue, runtime-status, and announcement integration lane",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-runtime-queue --slot slot_a --services postgres --artifacts-root .artifacts/z-int-runtime-queue -- -m pytest tests/integration/test_queue_runtime_service.py -k 'runtime_status_store or announcement_claim_probe_round_trip' -m 'service_integration and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-int-dependencies": {
    description: "Service-backed skills and Qdrant dependency integration lane",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-dependencies --slot slot_b --services postgres,qdrant,zetherion-ai-skills,zetherion-ai-api,zetherion-ai-cgs-gateway --artifacts-root .artifacts/z-int-dependencies -- -m pytest tests/integration/test_runtime_dependency_service.py -m 'service_integration and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-int-platform": {
    description: "Service-backed platform support integration lane",
    timeoutSeconds: 2400,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-platform --slot slot_b --services postgres,qdrant,zetherion-ai-skills,zetherion-ai-api,zetherion-ai-cgs-gateway,zetherion-ai-bot --artifacts-root .artifacts/z-int-platform -- -m pytest tests/integration/test_health_e2e.py tests/integration/test_update_e2e.py tests/integration/test_telemetry_e2e.py -m 'integration and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-dm-sim": {
    description: "Deterministic DM delivery canary over the real queue storage path",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-e2e-dm-sim --slot slot_a --services postgres --artifacts-root .artifacts/z-e2e-dm-sim -- -m pytest tests/integration/test_queue_runtime_service.py -k 'processes_dm_items_without_stranding' -m 'service_e2e and delivery_canary' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-channel-sim": {
    description: "Deterministic channel delivery canary over the real queue storage path",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-e2e-channel-sim --slot slot_b --services postgres --artifacts-root .artifacts/z-e2e-channel-sim -- -m pytest tests/integration/test_queue_runtime_service.py -k 'falls_back_to_channel_send' -m 'service_e2e and delivery_canary' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-faults": {
    description: "Deterministic fault-injection lane for queue retries and dead-lettering",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-e2e-faults --slot slot_b --services postgres --artifacts-root .artifacts/z-e2e-faults -- -m pytest tests/integration/test_queue_runtime_service.py -k 'dead_letters_failed_discord_work_without_processing_leaks' -m 'service_e2e and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-discord-live": {
    description: "Live Discord DM and channel canary receipt lane",
    timeoutSeconds: 3600,
    heartbeat: true,
    command: ["bash", "./scripts/local-required-e2e-receipt.sh"],
  },
  "z-e2e-discord-security": {
    description: "Live Discord security canary lane",
    timeoutSeconds: 2400,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-e2e-discord-security --slot slot_b --services postgres,qdrant,zetherion-ai-skills,zetherion-ai-api,zetherion-ai-cgs-gateway,zetherion-ai-bot --artifacts-root .artifacts/z-e2e-discord-security -- -m pytest tests/integration/test_discord_e2e.py -m 'discord_e2e and security_canary and optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-int-runtime": {
    description: "Compatibility runtime integration lane",
    timeoutSeconds: 2400,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-runtime --slot slot_a --services postgres,qdrant,zetherion-ai-skills,zetherion-ai-api,zetherion-ai-cgs-gateway --artifacts-root .artifacts/z-int-runtime -- -m pytest tests/integration/test_runtime_dependency_service.py tests/integration/test_queue_runtime_service.py -m 'service_integration and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-int-faults": {
    description: "Compatibility fault-injection lane",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-int-faults --slot slot_b --services postgres --artifacts-root .artifacts/z-int-faults -- -m pytest tests/integration/test_queue_runtime_service.py -k 'dead_letters_failed_discord_work_without_processing_leaks' -m 'service_e2e and not optional_e2e' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-discord-sim": {
    description: "Compatibility simulated Discord queue lane",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "bash",
      "-lc",
      "./scripts/run-service-lane.sh --lane z-e2e-discord-sim --slot slot_a --services postgres --artifacts-root .artifacts/z-e2e-discord-sim -- -m pytest tests/integration/test_queue_runtime_service.py -k 'processes_dm_items_without_stranding or falls_back_to_channel_send' -m 'service_e2e and delivery_canary' -q --tb=short --no-cov",
    ],
  },
  "z-e2e-discord-real": {
    description: "Compatibility live Discord canary receipt lane",
    timeoutSeconds: 3600,
    heartbeat: true,
    command: ["bash", "./scripts/local-required-e2e-receipt.sh"],
  },
  "z-release": {
    description: "Local release verification and canonical heavy gate",
    timeoutSeconds: 5400,
    command: ["bash", "./scripts/test-full.sh"],
  },
  "unit-full": {
    description: "Full unit test lane with coverage gate",
    timeoutSeconds: 1800,
    heartbeat: true,
    command: [
      "sh",
      "-lc",
      [
        "node --test scripts/testing/run-bounded.test.mjs",
        "rm -f .coverage .coverage.* .coverage-*",
        "mkdir -p .artifacts/coverage/unit-full",
        "python3 -m pytest tests/ -m 'not integration and not discord_e2e' --cov=src/zetherion_ai --cov-report=term-missing --cov-fail-under=0 -q --tb=short",
        "python3 -m coverage report > .artifacts/coverage/unit-full-coverage-report.txt",
        "python3 scripts/testing/coverage_gate.py --artifacts-dir .artifacts/coverage/unit-full --coverage-file .coverage --lane-id unit-full --minimum-statements 90 --minimum-lines 90 --minimum-branches 90 --minimum-functions 90",
      ].join(" && "),
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
      "--no-cov",
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
    heartbeat: true,
    command: ["bash", "-lc", "SKIP_LOCAL_SOCKET_PREFLIGHT=true ./scripts/test-full.sh"],
  },
};
