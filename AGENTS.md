# Agent Operating Rules for Test and CI Reliability

## Canonical Full Validation (Mandatory)

Before any push, release prep, or "done" status on substantial code changes, run:

```bash
./scripts/test-full.sh
```

Windows wrapper:

```powershell
./scripts/test-full.ps1
```

This is the only supported full local gate. Do not substitute ad-hoc pytest commands as equivalent proof.

## Hard Requirements

1. The full pipeline must start from a clean test environment and fail fast on any stage failure.
2. Discord E2E is required in full mode (`RUN_DISCORD_E2E_REQUIRED=true` by default).
3. Required Discord E2E defaults to Groq (`DISCORD_E2E_PROVIDER=groq`) and must fail fast when Groq credentials are missing.
4. Local-model Discord E2E is opt-in only (`RUN_DISCORD_E2E_LOCAL_MODEL=true`).
5. Canonical full gate uses cloud embeddings (`EMBEDDINGS_BACKEND=openai`, `OPENAI_EMBEDDING_MODEL=text-embedding-3-large`) and must not pull local embedding models in required mode.
6. Do not bypass pre-push checks (`git push --no-verify` is not allowed for normal workflows).
7. Delete-only pushes (remote ref deletions with no new commits/tags) are exempt and should skip the full gate automatically via `.git-hooks/pre-push`.
8. If required env vars for Discord E2E are missing, stop and surface that explicitly.
9. Local socket-bind preflight must pass; if it fails, run the canonical gate outside sandbox restrictions.
10. Background jobs (`mypy`, `pip-audit`) must never run indefinitely; canonical timeouts/heartbeat logging are mandatory.
11. Canonical validation must run against repo-local virtualenv tooling (`.venv`/`venv`), not an unrelated active shell virtualenv.
12. Pre-push validation must refuse dirty worktree/index state so checks run against the exact commit SHA being pushed.
13. Protected shared-infra and shared-runtime coverage-sensitive paths must be covered by the source-controlled local gate manifest (`.ci/local_gate_manifest.json`); unmapped protected changes fail local validation before push.
14. Canonical static analysis must run both `scripts/check_pipeline_contract.py` and `scripts/check-endpoint-doc-bundle.py` so route/doc-contract mismatches fail locally before push.
15. Unit-test coverage must remain `>=90%` in canonical runs; coverage regressions block push/release.
16. Shared trust/personal/profile/portfolio/routing/queue/model/context/storage/startup changes mapped in `.ci/local_gate_manifest.json` must run the bounded `unit-full` lane before push.
17. Critical-path integration suites must run in canonical validation, including dev-watcher onboarding paths.
18. Full end-to-end validation is mandatory for substantial delivery: Docker E2E (`test_e2e.py`) and required Discord E2E must both pass.
19. This repository is Zetherion-only. Top-level `cgs/**` website/UI files are disallowed here.
20. CI enforces a risk-classifier contract (`risk-classifier`) that sets `e2e_required=true|false`; ambiguity must fail-safe to `true`.
21. CI required-E2E gate (`required-e2e-gate`) must emit a machine-readable receipt artifact (`e2e-contract-receipt`) every run.
22. When `e2e_required=true`, CI must validate a local required-E2E receipt (`.ci/e2e-receipt.json`) for the PR head SHA; CI must not execute full E2E suites directly.
23. For isolated Discord E2E debugging, use `./scripts/run-required-discord-e2e.sh`; direct `pytest tests/integration/test_discord_e2e.py ...` invocation is not accepted as workflow evidence.

## Current CI Proof Contract

1. `./scripts/test-full.sh` remains the only supported heavy local gate for substantial delivery.
2. When `e2e_required=true`, run `bash scripts/local-required-e2e-receipt.sh` after the full gate passes and commit `.ci/e2e-receipt.json`.
3. GitHub currently validates exact-SHA receipt evidence for required E2E instead of executing the full E2E suites directly on PRs.
4. Current required branch checks are:
   - `CI Summary`
   - `Linting & Formatting`
   - `Pipeline Contract`
   - `Secret Scan (Gitleaks)`
   - `Zetherion Boundary Check`
5. Additional path-gated local-equivalent CI jobs may still run until the PR fast-path rollout lands; do not treat skipped heavy jobs as permission to skip the local heavy gate.

## API Documentation Contract (Mandatory)

1. Any new API endpoint set (public API and/or CGS gateway) must include an extensive documentation bundle before merge.
2. The required bundle includes:
   - public API reference updates
   - CGS mapping specification updates
   - OpenAPI contract updates (public + CGS)
   - API error matrix + auth matrix updates
   - example request/response updates
   - frontend wiring guide updates (route-to-screen mapping)
   - changelog entry updates
3. CI must fail if endpoint routes change without full documentation bundle updates in the same change set.
4. Documentation suite publishing must run after every `main` merge (no docs-only path filter on `main`).

## Script Policy

Legacy scripts are compatibility wrappers and must delegate to canonical behavior:

- `scripts/pre-push-tests.sh`
- `scripts/run-integration-tests.sh`
- `scripts/run-discord-e2e-tests.sh`

If behavior changes, update `scripts/test-full.sh` first and keep wrappers aligned.

Critical-path integration coverage in canonical runs must include both:

- `tests/integration/test_dev_watcher_e2e.py`
- `tests/integration/test_dev_watcher_onboarding_integration.py`

## Repository Boundary Policy (Mandatory)

- Allowed scope: `src/zetherion_ai/**` and supporting infra/docs/scripts/tests for Zetherion.
- Disallowed scope in this repo: top-level `cgs/**` website/UI artifacts.
- CI enforcement: `zetherion-boundary-check` must fail on any top-level `cgs/**` change.
- Local enforcement: pre-push must fail when a pushed commit range contains top-level `cgs/**` paths.
- Release checklist command (required before merge):
  - `scripts/check-scope-diff.sh origin/main HEAD`
- Normal workflow forbids bypass shortcuts:
  - `git push --no-verify` is prohibited.
  - `--admin` merge is prohibited unless explicit incident/break-glass authorization is documented.

## CI Cost + Attribution Policy

- Keep CI spend minimized via heavy-job gating and path-based execution.
- Every CI failure must produce attribution (`ci-failure-attribution.json`) explaining whether:
  - it should have been caught locally, or
  - it is CI-only/deferred by policy, or
  - there is a pipeline contract gap.
- Maintain `.ci/pipeline_contract.json` when adding/removing CI jobs.

## If CI Fails

1. Read the failure-attribution summary/artifact first.
2. If reason is `SHOULD_HAVE_BEEN_CAUGHT_LOCALLY`, treat as local gate process breach and fix workflow usage.
3. If reason is `PIPELINE_CONTRACT_GAP`, update contract mappings immediately.
4. Run `./scripts/require-local-gate-update.sh --sha <failed_commit_sha>` and do not proceed until it passes.
5. Re-run `./scripts/test-full.sh` after fixes before pushing again.

## Automatic Main Promotion (Mandatory)

1. Standard branch workflow is `codex/*` -> auto-promotion into `main`; do not use non-`codex/*` branches when the goal is standard delivery.
2. Direct feature pushes to `main` are non-standard; use only break-glass procedures when automation cannot be used.
3. Successful `CI/CD Pipeline` runs for `codex/*` pull requests targeting `main` are promoted by `.github/workflows/auto-merge-main.yml` using fast-forward-only rules.
4. If fast-forward is blocked, automation must stop merge attempts and open/update a PR from `codex/*` to `main` with rebase instructions.
5. If `Deploy Windows` fails for an auto-merged `main` SHA, `.github/workflows/revert-failed-main-deploy.yml` must auto-revert the merged commit range on `main`.
6. Manual merge to `main` is break-glass only and must include explicit operator justification in workflow/runbook notes.

## Break-Glass SSH Deployment (Non-Standard)

SSH-based deployment/remoting is emergency-only recovery and is not part of the standard completion path.

## Post-Deploy Promotions (Mandatory)

1. Blog/release promotion execution is owned by the Windows main machine, not GitHub Actions.
2. Promotions may run only after deployment receipt validation passes (`status=success`, SHA match, all required checks true).
3. Blog generation/publish is mandatory per deployed `main` SHA:
   - required models: `gpt-5.2` (draft) and `claude-sonnet-4-6` (refine)
   - no lower-tier fallback
   - idempotency required by deployed SHA (no duplicate publish)
4. GitHub release auto-increment is mandatory per deployed `main` SHA:
   - increment SemVer patch from latest `v*` release (bootstrap `v0.1.0` when absent)
   - bind release to deployed SHA
   - idempotency required by SHA (no duplicate release creation)
5. Promotions secrets must be machine-local DPAPI (`C:\ZetherionAI\data\secrets\promotions.bin`) and must not be stored in repo `.env` or GitHub Actions secrets for execution.
