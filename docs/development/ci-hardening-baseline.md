# CI Hardening Failure Baseline

This document records the current CI/CD contract and the rejection classes reviewed before the segmented CI hardening and spend-reduction rollout begins.

## Current Repo Contract

As of 2026-03-08, the current merge contract is:

1. Run `./scripts/test-full.sh` for substantial local validation.
2. When the CI risk classifier requires E2E evidence, run `bash scripts/local-required-e2e-receipt.sh` after the full local gate passes and commit the resulting `.ci/e2e-receipt.json`.
3. GitHub validates exact-SHA receipt evidence for required E2E instead of executing the full E2E suites directly on PRs.
4. GitHub PRs now run the slim fast path: `detect-changes`, `risk-classifier`, `lint`, `secret-scan`, `pipeline-contract`, `zetherion-boundary-check`, `required-e2e-gate`, `CI Summary`, and `CI Failure Attribution`. Heavy local-equivalent jobs are deferred off PRs to push or scheduled/manual runs.

Current required branch checks from the active `main` ruleset (`Main branch protection`, GitHub ruleset `12504326`):

- `CI Summary`
- `Linting & Formatting`
- `Pipeline Contract`
- `Secret Scan (Gitleaks)`
- `Zetherion Boundary Check`

Supporting source of truth for the rollout contract lives in `.ci/ci_hardening_workstream_manifest.json`.

## Reviewed Rejection Classes

| Run / PR context | Failure class | What actually happened | Required corrective direction |
| --- | --- | --- | --- |
| `22754322441` | Real product regression | Qdrant/data-plane tests failed after runtime wiring changes; local changed-path coverage was too narrow. | Expand local preflight mappings for vector/data-plane changes. |
| `22786725474` | Real product regression | Shared personal-profile behavior regressed in unit tests. | Force shared-runtime changes through bounded `unit-full` before push. |
| `22788521476` | Local gate miss | Repo-wide coverage fell to `89.89%`; this was not a runtime defect, it was a missing local heavy-lane requirement. | Fail closed on shared-runtime path changes and coverage-sensitive modules. |
| `#124` / `#125` follow-up fixes | CI support-script regression | `check-cicd-success.sh` and related receipt logic regressed because workflow-support code was not treated like production code in local gates. | Add deterministic regression packs for receipt and workflow-support scripts. |
| `22788789492`, `22789235995` | Deploy contract mismatch | Windows deploy rolled back because `cloudflared` and `whatsapp-bridge` were misconfigured, even though the reviewed application changes were not targeting those optional services. | Split deploy health into core vs auxiliary service groups. |
| `22737551043` | GitHub-native workflow noise | Automatic dependency submission failed with a GitHub-side server error. | Remove or de-emphasize non-actionable workflow noise from PR-time required paths. |

## Baseline Decisions

- Segment 0 is documentation and manifest alignment only. It does not change workflow behavior.
- Segment 1 will widen local preflight coverage for shared-runtime and coverage-sensitive paths.
- Segment 2 will add deterministic regression coverage for receipt, shell, and CI-support code.
- Segment 3 slims PR CI to the fast-path contract; the required ruleset contexts remain unchanged because they already matched the target required-check set.
