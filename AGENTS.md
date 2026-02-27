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

## Script Policy

Legacy scripts are compatibility wrappers and must delegate to canonical behavior:

- `scripts/pre-push-tests.sh`
- `scripts/run-integration-tests.sh`
- `scripts/run-discord-e2e-tests.sh`

If behavior changes, update `scripts/test-full.sh` first and keep wrappers aligned.

Critical-path integration coverage in canonical runs must include both:

- `tests/integration/test_dev_watcher_e2e.py`
- `tests/integration/test_dev_watcher_onboarding_integration.py`

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
4. Re-run `./scripts/test-full.sh` after fixes before pushing again.

## Post-Push Windows Deployment (Mandatory)

Every successful GitHub push must be followed by a Windows host update and runtime verification.

1. Use the local runbook at `.agent-handoff/GITHUB_PUSH_AND_WINDOWS_DEPLOY_RUNBOOK.md`.
2. SSH to `james@<WINDOWS_HOST_IP>`, update `C:\ZetherionAI` to the pushed ref, and record the resulting commit hash.
3. Apply the Docker credential fix from the runbook when needed, then run `docker compose up -d --build`.
4. Verify deployment health with:
   - `docker compose ps`
   - bot startup logs (`settings_manager_initialized`, `provider_issue_alerts_wired`, `provider_probe_task_started`)
   - model settings rows in Postgres (`models` namespace keys)
   - fallback behavior check and related log signals.
5. Do not consider work complete until Windows deploy + verification has succeeded, or a concrete blocker is reported.
