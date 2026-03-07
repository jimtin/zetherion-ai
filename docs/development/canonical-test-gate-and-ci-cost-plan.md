# Canonical Test Gate and CI Cost Controls

## Purpose

This document defines the single supported local full-test procedure, the exact-SHA receipt proof model, and the CI failure-attribution policy used to explain why a CI failure was or was not caught locally before deployment. Segment-level rollout metadata now lives in `.ci/ci_hardening_workstream_manifest.json`, and the reviewed rejection baseline is recorded in `docs/development/ci-hardening-baseline.md`.

## Canonical Local Gate

Run this command for full validation:

```bash
./scripts/test-full.sh
```

Windows wrapper:

```powershell
./scripts/test-full.ps1
```

The canonical gate delegates to `scripts/pre-push-tests.sh` and enforces:

1. strict required tests
2. required Discord E2E
3. fail-fast stage exits
4. automatic test-environment teardown

## Bounded Lane Protocol

Long-running validation lanes can be executed through the bounded harness:

```bash
node scripts/testing/run-bounded.mjs --lane check
```

Quiet lanes can be wrapped with heartbeat logging:

```bash
node scripts/testing/run-with-heartbeat.mjs --heartbeat-seconds 30 -- python3 scripts/check_pipeline_contract.py
```

Stall/timeout diagnostics capture:

```bash
node scripts/testing/test-hang-diagnostics.mjs --output-dir artifacts/testing/hang-diagnostics/manual
```

Defaults:

1. stall threshold: 45 seconds without process output
2. stalled/timeout lanes are terminated and diagnostics are captured
3. lane executions are appended to `docs/migration/test-execution-log.md` by default
4. exact-SHA local-gate preflight lanes write to `artifacts/testing/local-gate-preflight-log.md` so validation receipts do not dirty the commit being validated

Lane order:

1. `check`, `lint`, `nextjs-only-audit`, `nextjs:api-parity`, `nextjs:functionality-matrix`, `nextjs:functionality-check`
2. `targeted-unit`
3. `unit-full`
4. `api-integration-coverage`
5. `e2e-mocked`
6. `e2e-fullstack-critical`

## Compatibility Wrappers

These scripts are retained for compatibility only and delegate to canonical full behavior:

- `scripts/run-integration-tests.sh`
- `scripts/run-discord-e2e-tests.sh`

## Git Hook Enforcement

`.git-hooks/pre-push` now enforces a commit-state preflight before the expensive full gate:

1. refuse dirty worktree/index state
2. refuse pushes for a non-checked-out commit SHA
3. run `scripts/run-local-gate-preflight.sh` against the exact `<base, head>` push range
4. then execute `./scripts/test-full.sh`

The preflight is driven by the source-controlled manifest at `.ci/local_gate_manifest.json`. Its bounded lane receipts are written to `artifacts/testing/local-gate-preflight-log.md` so exact-SHA validation does not mutate tracked files. It currently requires local fast-fail coverage for:

- endpoint docs bundle changes on API/CGS route files
- strict `mypy src/zetherion_ai --config-file=pyproject.toml` for runtime Python changes
- bounded `unit-full` for shared trust/personal/profile/portfolio/routing/queue/model/context/storage/startup paths that can move the repo-wide coverage floor
- targeted Qdrant/data-plane regression tests
- targeted replay-store regression tests
- targeted receipt/workflow-support regression suites for receipt validation, local gate enforcement, and CI failure-attribution changes
- targeted Windows deploy-preflight regression suites for deployment-receipt validation and optional-service guard changes

## Current GitHub Proof and Cost Contract

The active PR contract today is:

1. current required branch checks are `CI Summary`, `Linting & Formatting`, `Pipeline Contract`, `Secret Scan (Gitleaks)`, and `Zetherion Boundary Check`
2. required E2E on PRs is enforced by exact-SHA local receipt validation, not by running full E2E suites directly in GitHub Actions
3. PR fast path is limited to `detect-changes`, `risk-classifier`, `lint`, `secret-scan`, `pipeline-contract`, `zetherion-boundary-check`, `required-e2e-gate`, `CI Summary`, and `CI Failure Attribution`
4. heavy local-equivalent jobs such as unit, type-check, security, docs-contract, and docker-build are deferred off PRs to push or weekly/manual verification runs
5. weekly/manual runs remain the place for the heaviest independent verification lanes
6. in-progress runs are canceled on new commits via `concurrency`

## Failure Attribution

On every CI run, `scripts/ci_failure_attribution.py` emits:

- Step Summary table
- `ci-failure-attribution.json` artifact

Reason codes:

- `SHOULD_HAVE_BEEN_CAUGHT_LOCALLY`
- `CI_ONLY_ENVIRONMENT_DIFF`
- `PIPELINE_CONTRACT_GAP`

Contract source:

- `.ci/pipeline_contract.json`
- validated by `scripts/check_pipeline_contract.py`

## Operating Rule

When CI fails, inspect attribution first. If attribution says a failure should have been caught locally, treat that as a process breach and enforce canonical local gate usage before the next push.

Protected shared-infra and shared-runtime coverage-sensitive paths must stay mapped in `.ci/local_gate_manifest.json`; local validation fails fast when a protected path changes without an explicit local gate mapping.

## CI/CD Proof of Completion

After pushing, completion must be proven by CI/CD results for the exact commit SHA:

```bash
./scripts/check-cicd-success.sh --sha <commit_sha> --ref <ref>
```

When verifying a freshly promoted `main` commit immediately after merge, optional polling is available so pending `CI Gate` or `Deploy Windows` evidence is reported clearly instead of as a missing-evidence failure:

```bash
./scripts/check-cicd-success.sh --sha <commit_sha> --ref main --wait-seconds 180
```

Rules:

1. Non-`main` refs require successful `CI/CD Pipeline` for the exact SHA.
2. `main` accepts either successful `CI/CD Pipeline` for the exact SHA or successful `CI Gate / CI Summary` plus `CI Gate / Required E2E Gate` check-runs for that exact SHA.
3. `main` additionally requires successful `Deploy Windows` and a valid `deployment-receipt.json` artifact.
4. `main` receipt checks must all be true:
   - `containers_healthy`
   - `bot_startup_markers`
   - `postgres_model_keys`
   - `fallback_probe`
   - `recovery_tasks_registered`
   - `runner_service_persistent`
   - `docker_service_persistent`

If CI failure attribution includes `SHOULD_HAVE_BEEN_CAUGHT_LOCALLY` or `PIPELINE_CONTRACT_GAP`, enforce local gate updates in the same fix cycle:

```bash
./scripts/require-local-gate-update.sh --sha <failed_commit_sha>
```
