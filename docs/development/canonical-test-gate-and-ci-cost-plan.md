# Canonical Test Gate and CI Cost Controls

## Purpose

This document defines the single supported local full-test procedure and the CI failure-attribution policy used to explain why a CI failure was or was not caught locally before deployment.

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

## Compatibility Wrappers

These scripts are retained for compatibility only and delegate to canonical full behavior:

- `scripts/run-integration-tests.sh`
- `scripts/run-discord-e2e-tests.sh`

## Git Hook Enforcement

`.git-hooks/pre-push` executes `./scripts/test-full.sh` directly. This blocks pushes when full validation fails.

## CI Cost Strategy

The CI workflow applies maximum reduction defaults:

1. heavy jobs run in `schedule`/`workflow_dispatch` only
2. docs and integration jobs are path-gated
3. in-progress runs are canceled on new commits via `concurrency`

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

## CI/CD Proof of Completion

After pushing, completion must be proven by CI/CD results for the exact commit SHA:

```bash
./scripts/check-cicd-success.sh --sha <commit_sha> --ref <ref>
```

Rules:

1. All refs require successful `CI/CD Pipeline`.
2. `main` additionally requires successful `Deploy Windows` and a valid `deployment-receipt.json` artifact.

If CI failure attribution includes `SHOULD_HAVE_BEEN_CAUGHT_LOCALLY` or `PIPELINE_CONTRACT_GAP`, enforce local gate updates in the same fix cycle:

```bash
./scripts/require-local-gate-update.sh --sha <failed_commit_sha>
```
