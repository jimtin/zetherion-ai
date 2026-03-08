# Changelog

All notable changes to Zetherion AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Note: percentage/count metrics in historical phase sections are snapshots from
> the date of that phase. Current policy is enforced by test configuration:
> overall coverage must remain `>=90%`.

---

## [Unreleased]

### Fixed - Trust storage schema bootstrap concurrency in container startup (2026-03-08)
- Serialized canonical trust schema creation with a PostgreSQL advisory transaction lock so concurrent service startup cannot race while creating `control_plane.trust_*` tables.
- Added unit coverage for schema-specific advisory lock selection and trust-storage bootstrap sequencing so this startup failure is caught locally before the full containerized gate.

### Fixed - Windows shell wrapper line endings for canary runs (2026-03-08)
- Added `.gitattributes` to force `*.sh` files to check out with LF endings so Git Bash on Windows does not choke on carriage returns in the Discord E2E wrapper.
- Hardened `scripts/windows/discord-canary.py` to stage an LF-normalized copy of the Discord E2E wrapper under the deploy root before invoking Bash, so the canary remains runnable even if an existing Windows checkout still has CRLF shell scripts.

### Fixed - Windows Discord canary wrapper path on host (2026-03-08)
- Switched the Windows canary launcher to invoke `scripts/run-required-discord-e2e.sh` relative to the deploy root instead of passing a raw `C:\...` path into Git Bash, which was being mangled into an invalid shell path on the Windows host.
- Added unit coverage to assert the canary subprocess uses the repo-relative wrapper command while keeping the deploy root as the working directory.

### Fixed - Windows PowerShell compatibility for resilience scripts (2026-03-08)
- Replaced PowerShell 7-only null-coalescing syntax in host-facing Windows scripts so manual host execution and scheduled-task bootstrap work under Windows PowerShell 5.1 as well as `pwsh`.
- Added `scripts/check-windows-powershell-compat.py` plus unit coverage and wired it into the bounded `check` lane and deploy-preflight local-gate regressions so incompatible syntax is rejected before push.

### Changed - Segment CI-07 Windows Full-Parity Production Discord Canary (2026-03-08)

- Impacted capability IDs:
  - `ci.e2e.discord_isolation`
  - `ci.weekly.independent_verification`
- Impacted workflow scenario IDs:
  - `e2e.windows_discord_canary`
- Added `scripts/windows/discord-canary.py` and `scripts/windows/discord-canary-runner.ps1` so the Windows host can execute the blessed isolated Discord E2E wrapper in `windows_prod_canary` mode, persist run receipts/state/logs under `C:\ZetherionAI\data\discord-canary`, and classify results as success, cleanup degradation, lease contention, timeout, or runner failure.
- Extended the Windows resilience task scripts plus `.ci/local_gate_manifest.json` so the canary runner is registered, verified, and covered by a dedicated local regression suite before push.
- Updated `scripts/verify-windows-host.ps1` and `scripts/windows/announcement-emit.py` so host verification surfaces stale/failed canaries and Windows can emit `discord_canary` health announcements without turning canary degradation into a deploy rollback.

### Changed - Segment CI-06 Discord E2E Channel Isolation, Target Lease, and Synthetic Cleanup (2026-03-08)

- Impacted capability IDs:
  - `ci.e2e.discord_isolation`
- Impacted workflow scenario IDs:
  - `e2e.concurrent_discord_runs_isolated`
  - `e2e.target_lease_unavailable`
- Added `src/zetherion_ai/discord/e2e_lease.py`, `scripts/discord_e2e_run_manager.py`, and `scripts/discord_e2e_run_manager.sh` so required Discord E2E runs now create one ephemeral channel per run, encode target-bot lease metadata in the channel topic, and janitor stale channels before the next invocation.
- Updated `scripts/run-required-discord-e2e.sh`, `scripts/pre-push-tests.sh`, and `scripts/local-required-e2e-receipt.sh` so canonical Discord E2E always runs through the isolated channel/thread wrapper, writes machine-readable run metadata, and records cleanup/lease status in the local E2E receipt. Standalone wrapper use is documented as diagnostic-only; merge evidence comes from `bash scripts/local-required-e2e-receipt.sh`.
- Added narrow bot-side synthetic-test rate-limit bypass checks in `src/zetherion_ai/discord/bot.py` and `src/zetherion_ai/config.py`, scoped to allowlisted authors plus active leased channels only.
- Updated `tests/integration/test_discord_e2e.py` to run-tag mutating artifacts and write cleanup prompts, and expanded `.ci/local_gate_manifest.json` with Discord E2E isolation regression coverage so wrapper/bot changes must prove the lease contract locally before push.

### Changed - Segment CI-05 Containerized E2E Run Isolation (2026-03-08)

- Impacted capability IDs:
  - `ci.e2e.run_isolation`
  - `ci.local_preflight`
- Impacted workflow scenario IDs:
  - `e2e.concurrent_docker_runs_isolated`
  - `e2e.stale_stack_janitor_cleanup`
- Added `scripts/e2e_run_manager.py` and `scripts/e2e_run_manager.sh` so local Docker-backed E2E runs now allocate a unique run id, Compose project, artifact root, host-port map, and cleanup manifest per invocation.
- Converted `docker-compose.test.yml` to env-driven host ports and per-run stack roots, and removed fixed `container_name` declarations so concurrent local runs can coexist.
- Updated `scripts/pre-push-tests.sh`, `scripts/local-required-e2e-receipt.sh`, and the Docker-backed integration tests to consume runtime metadata instead of assuming one global `zetherion-ai-test` stack.
- Expanded `.ci/local_gate_manifest.json` with an isolated E2E harness regression suite so future harness or wrapper changes must prove the run manager/runtime contract locally before push.

### Changed - Segment CI-04 Heavy Verification Offload and Workflow Noise Removal (2026-03-08)

- Impacted capability IDs:
  - `ci.weekly.independent_verification`
  - `ci.pr.fast_path`
- Impacted workflow scenario IDs:
  - `weekly.full_independent_verification`
  - `pr.docs_only_fast_path`
- Removed `push` and `pull_request` triggers from `.github/workflows/codeql.yml` so CodeQL runs only on its weekly cadence or by manual dispatch, eliminating skipped PR check noise.
- Narrowed `.github/workflows/docs.yml` so docs deployment runs on `main` only when docs-site sources change, and removed duplicate docs-contract validation from that publish workflow.
- Extended `scripts/check_pipeline_contract.py` and its regression coverage so weekly-heavy verification cadence, PR-run cancellation, and workflow-trigger spend controls are now enforced in-repo.
- Added Docker/Discord E2E smoke preflight to `scripts/pre-push-tests.sh` and richer Docker E2E failure diagnostics so a not-yet-stable test stack fails fast instead of burning the full concurrent E2E tranche.

### Changed - Segment CI-03 PR CI Fast Path and Ruleset Alignment (2026-03-08)

- Impacted capability IDs:
  - `ci.pr.fast_path`
  - `ci.receipt.validation`
- Impacted workflow scenario IDs:
  - `pr.docs_only_fast_path`
  - `pr.receipt_sha_mismatch_rejected`
- Slimmed pull-request CI to the fast-path contract: `detect-changes`, `risk-classifier`, `lint`, `secret-scan`, `pipeline-contract`, `zetherion-boundary-check`, `required-e2e-gate`, `CI Summary`, and `CI Failure Attribution`.
- Deferred heavy local-equivalent jobs (`type-check`, `security`, `docs-contract`, `unit-test`, and `docker-build-test`) off PRs to push or scheduled/manual verification without changing the required branch-check contexts.
- Updated the workflow summary and pipeline contract metadata so PR evaluation matches the slim contract while push and scheduled/manual runs keep the broader job graph.

### Changed - Segment CI-02 Script and Receipt Regression Packs (2026-03-08)

- Impacted capability IDs:
  - `ci.receipt.validation`
  - `ci.failure_attribution`
- Impacted workflow scenario IDs:
  - `pr.receipt_script_change_requires_regression_pack`
  - `pr.receipt_sha_mismatch_rejected`
- Added targeted regression coverage for `scripts/check-cicd-success.sh`, `scripts/validate-deployment-receipt.py`, and `scripts/require-local-gate-update.sh` so receipt parsing and deployment-receipt contract changes fail locally before burning CI minutes.
- Expanded `.ci/local_gate_manifest.json` and `scripts/run-local-gate-preflight.sh` so workflow, receipt, failure-attribution, and deploy-preflight helper changes now require the matching regression suites.
- Updated `scripts/ci_failure_attribution.py` and `scripts/require-local-gate-update.sh` so local-gate misses now emit explicit `LOCAL_GATE_BREACH_*` reason codes instead of collapsing everything into one generic bucket.

### Changed - Segment CI-01 Local Gate Expansion for Shared Runtime Changes (2026-03-08)

- Impacted capability IDs:
  - `ci.local_preflight`
- Impacted workflow scenario IDs:
  - `pr.shared_runtime_requires_unit_full`
  - `pr.vector_regression_pack_required`
- Expanded `.ci/local_gate_manifest.json` so shared-runtime directories under trust, personal, profile, portfolio, routing, queueing, and `security/trust_policy.py` now require bounded `unit-full` in addition to the existing type-check and Bandit requirements.
- Added focused local-gate planner coverage for trust runtime, profile builder, routing policy, and queue manager changes so the expanded shared-runtime guard cannot regress silently.
- Updated operator-facing gate docs to reflect the widened shared-runtime coverage floor before the later PR-fast-path workflow changes land.

### Changed - Segment CI-00 Contract Alignment and Failure Baseline (2026-03-08)

- Impacted capability IDs:
  - `ci.contract.alignment`
- Impacted workflow scenario IDs:
  - `pr.docs_only_fast_path`
  - `pr.receipt_sha_mismatch_rejected`
- Added `.ci/ci_hardening_workstream_manifest.json` so the CI hardening rollout now has one source-controlled manifest for segment contract rules, capability IDs, workflow scenario IDs, and the current required-check inventory.
- Added `docs/development/ci-hardening-baseline.md` to record the reviewed CI rejection classes and the current `main` ruleset checks before any workflow behavior changes land.
- Updated `AGENTS.md`, `docs/development/canonical-test-gate-and-ci-cost-plan.md`, and `docs/development/ci-cd.md` so the documented contract matches the current receipt-driven PR model: heavy validation remains local and exact-SHA receipts are the proof path for required E2E.

### Changed - CI/CD Receipt Verification Pending-State Handling (2026-03-08)

- Impacted capability IDs:
  - `ci.receipt.pending-state-diagnostics`
  - `ci.receipt.main-post-merge-wait-support`
- Impacted workflow scenario IDs:
  - `verification.post-merge.main-check-runs-pending`
  - `verification.post-merge.deploy-windows-pending`
- Updated `scripts/check-cicd-success.sh` so it distinguishes pending evidence from missing evidence, surfaces associated merged-PR context for `main`, and supports optional `--wait-seconds` polling during post-merge verification.
- Preserved the existing proof contract: non-`main` refs still require exact-SHA `CI/CD Pipeline` success, and `main` still requires exact-SHA `CI Gate / CI Summary` plus `CI Gate / Required E2E Gate` and a valid `Deploy Windows` deployment receipt.

### Added - Segment 11 Worker Delegation Hardening and Codex Control (2026-03-07)

- Impacted capability IDs:
  - `worker.delegation.control-plane-routes`
  - `worker.delegation.trust-policy-approval-gate`
  - `worker.delegation.cgs-route-contract`
- Impacted workflow scenario IDs:
  - `isolation.segment11.manage-worker-delegation-grants-via-skills-admin`
  - `isolation.segment11.manage-worker-delegation-grants-via-cgs-admin`
  - `isolation.segment11.require-two-person-approval-for-worker-delegation-grants`
- Added Skills and CGS internal-admin route surfaces for listing, upserting, and revoking worker delegation grants, using scoped resource payloads instead of path-encoded repo or Codex identifiers.
- Added explicit `worker.delegation.grant` trust-policy gating so high-risk worker repo/Codex grant changes reuse the same approval-required and two-person workflow as other sensitive worker mutations.
- Updated worker control route tests, OpenAPI, and operator-facing route documentation so delegation grants are covered by the existing docs bundle and validation suites before push.

### Added - Segment 14 Legacy Cutover and Safety Cleanup (2026-03-07)

- Impacted capability IDs:
  - `trust.engine.canonical-decision-entrypoint`
  - `trust.storage.canonical-decision-audit`
  - `trust.compat.shadow-alias-bridge`
  - `ci.deploy-receipt.main-check-run-fallback`
- Impacted workflow scenario IDs:
  - `isolation.segment14.cutover-canonical-decision-recording`
  - `isolation.segment14.preserve-legacy-shadow-compatibility`
  - `isolation.segment14.verify-main-ci-gate-receipt`
- Updated `src/zetherion_ai/trust/engine.py`, `src/zetherion_ai/trust/storage.py`, and `src/zetherion_ai/trust/runtime.py` so production code records canonical trust decisions through `record_decision` / `record_decision_audit`, while keeping compatibility aliases in place for legacy shadow-mode imports and tests.
- Updated trust decision call sites in personal actions, tenant trust-policy evaluation, Gmail, GitHub, and YouTube so live autonomy surfaces now call the canonical decision recorder instead of the legacy shadow-only entrypoint.
- Repaired `scripts/check-cicd-success.sh` so post-merge verification on `main` recognizes the current `CI Gate / CI Summary` and `CI Gate / Required E2E Gate` check-run topology before validating the Windows deployment receipt.
- Updated `scripts/run-local-gate-preflight.sh` so exact-SHA bounded lanes write receipts to `artifacts/testing/local-gate-preflight-log.md` instead of mutating the tracked append-only execution ledger during pre-push validation.

### Added - Segment 10 Personal Action Migration (2026-03-07)

- Impacted capability IDs:
  - `trust.runtime.personal-action-canonical-audit`
  - `trust.runtime.personal-policy-feedback-sync`
  - `trust.runtime.routing-canonical-audit`
  - `startup.skills-routing-trust-storage-wiring`
- Impacted workflow scenario IDs:
  - `isolation.segment10.audit-personal-action-decisions`
  - `isolation.segment10.sync-personal-policy-feedback`
  - `isolation.segment10.audit-email-task-calendar-routing`
  - `isolation.segment10.bootstrap-routing-trust-storage`
- Added `src/zetherion_ai/trust/runtime.py` so personal-action and routing decisions can be normalized into canonical trust audits without widening live autonomy behavior.
- Updated `src/zetherion_ai/personal/actions.py` to persist canonical trust audits for decisions, record canonical feedback outcomes, and keep owner-personal policy/scorecard state synchronized with the legacy personal policy store.
- Updated `src/zetherion_ai/routing/task_calendar_router.py`, `src/zetherion_ai/routing/email_router.py`, and `src/zetherion_ai/skills/server.py` so email/task/calendar routing decisions now emit canonical trust audits through the control-plane trust store while preserving the existing route behavior.
- Added focused unit coverage for the new runtime helper mappings plus the personal-action and routing audit hooks to restore the repo-wide `unit-full` coverage floor locally before push.

### Added - Segment 8 Personal Intelligence Foundation (2026-03-07)

- Impacted capability IDs:
  - `personal.owner-operational-state.persistence`
  - `personal.owner-review-state.persistence`
  - `personal.context.operational-review-state`
  - `startup.owner-personal-intelligence-schema-bootstrap`
- Impacted workflow scenario IDs:
  - `isolation.segment8.persist-owner-commitments-blockers-plans`
  - `isolation.segment8.persist-owner-review-queue`
  - `isolation.segment8.include-owner-operational-context`
  - `isolation.segment8.bootstrap-owner-personal-intelligence-schema`
- Added `src/zetherion_ai/personal/operational_storage.py` with owner-personal relational persistence for commitments, projects, deadlines, routines, waiting-fors, blockers, active plans, and canonical review items.
- Extended `src/zetherion_ai/personal/models.py` and `src/zetherion_ai/personal/context.py` so owner operational state and pending review items can be carried into personal decision context safely from the `owner_personal` trust domain.
- Bootstrapped the owner-personal intelligence schema during app and skills startup and recorded the new owner-personal relational storage family in the isolation compatibility baseline.
- Added focused unit coverage for owner-personal operational/review storage, prompt-fragment rendering, and startup bootstrap wiring.


### Added - Segment 7 Canonical Trust Persistence and Grants (2026-03-07)

- Impacted capability IDs:
  - `trust.persistence.canonical-store`
  - `trust.grants.generic-resource-scope`
  - `trust.backfill.legacy-source-normalization`
  - `ci.endpoint-doc-bundle.route-sensitive-diff`
  - `ci.local-gate.bandit-src`
- Impacted workflow scenario IDs:
  - `isolation.segment7.bootstrap-control-plane-trust-tables`
  - `isolation.segment7.backfill-personal-gmail-github-youtube-worker-trust`
  - `isolation.segment7.persist-shadow-decision-audit`
  - `ci.segment7.ignore-nonroute-api-server-bootstrap-edits`
  - `ci.segment7.catch-bandit-before-push`
- Added `src/zetherion_ai/trust/storage.py` with canonical control-plane tables for trust policies, grants, scorecards, feedback events, and decision audit rows.
- Added `src/zetherion_ai/trust/backfill.py` so current personal, Gmail, GitHub, YouTube, and worker messaging trust records can be mapped into the canonical store without changing live enforcement.
- Bootstrapped the canonical trust schema during app/API/skills startup and extended the isolation inventory baseline to declare the new control-plane trust tables explicitly.
- Narrowed `scripts/check-endpoint-doc-bundle.py` so docs-bundle enforcement still catches route-surface edits, but no longer false-positives on bootstrap-only server wiring changes.
- Added a Bandit exact-SHA local-gate requirement for Python source changes so security-scan regressions fail before push instead of burning CI minutes.

### Added - Segment 6 Unified Trust Engine Shadow Mode (2026-03-07)

- Impacted capability IDs:
  - `trust.engine.shadow-mode`
  - `trust.engine.adapter-parity-logging`
  - `trust.engine.cross-surface-normalization`
- Impacted workflow scenario IDs:
  - `isolation.segment6.shadow-evaluate-tenant-trust-policy`
  - `isolation.segment6.shadow-evaluate-personal-actions`
  - `isolation.segment6.shadow-evaluate-gmail-github-youtube-trust`
- Added `src/zetherion_ai/trust/engine.py` and `src/zetherion_ai/trust/adapters.py` to normalize legacy autonomy decisions into one canonical trust decision model without changing live enforcement.
- Wired non-blocking shadow recording into tenant trust policy, personal actions, Gmail trust, GitHub autonomy, and YouTube trust so each decision path now emits comparable `trust_shadow_decision` telemetry.
- Added focused unit coverage for the new engine/adapters plus hook-level contract tests to keep shadow-mode wiring from regressing silently.

### Added - Segment 12 Owner Portfolio Intelligence Pipeline (2026-03-07)

- Impacted capability IDs:
  - `isolation.owner-portfolio.tenant-derived-dataset-pipeline`
  - `isolation.owner-portfolio.cgs-mirrored-snapshot-provenance`
  - `isolation.owner-portfolio.client-insights-derived-only-analysis`
- Impacted workflow scenario IDs:
  - `isolation.segment12.derive-tenant-health-dataset-before-owner-snapshot`
  - `isolation.segment12.cgs-reconcile-mirrors-owner-portfolio-summary`
  - `isolation.segment12.cross-tenant-analysis-uses-derived-summaries-only`
- Added `src/zetherion_ai/portfolio/derivation.py` and `src/zetherion_ai/portfolio/storage.py` so tenant health summaries are normalized into `tenant_derived` datasets before owner-facing portfolio snapshots are stored.
- Added `src/zetherion_ai/portfolio/pipeline.py` so the raw-to-derived-to-owner transformation now lives behind one explicit portfolio pipeline with stored provenance.
- Updated `ClientInsightsSkill` so owner-facing summary, health-check, and cross-tenant analysis paths read stored `owner_portfolio` snapshots by default; refresh from raw tenant interactions is now an explicit pipeline action instead of implicit prompt-time derivation.
- Updated CGS internal tenant create/reconcile flows to request an explicit portfolio refresh and mirror the returned owner-portfolio summary plus provenance references into the existing CGS owner snapshot record without changing the public route contract.

### Added - Segment 4 CGS Provisioning and Reconciliation Orchestrator (2026-03-07)

- Impacted capability IDs:
  - `isolation.cgs.idempotent-provisioning`
  - `isolation.cgs.mapping-isolation-stage`
  - `isolation.cgs.reconciliation-candidates`
- Impacted workflow scenario IDs:
  - `isolation.segment4.cgs-create-tenant-idempotent-retry`
  - `isolation.segment4.cgs-existing-tenant-reconcile-without-duplicate-upstream-create`
  - `isolation.segment4.cgs-track-isolation-stage-and-provisioning-baseline`
- Added `CGSTenantProvisioningOrchestrator` so repeated internal tenant create requests reuse existing mappings instead of creating duplicate upstream tenants.
- Added first-class `isolation_stage` persistence plus provisioning/reconciliation metadata on `cgs_ai_tenants` to support staged upgrades of existing clients.
- Added reconciliation candidate helpers and route/test coverage for idempotent create responses that now report `isolation_stage` and `provisioning_status`.
- Added staged tenant migration receipts plus owner-portfolio tenant snapshots so existing CGS clients can reconcile into `shadow`, `dual_write`, `cutover_ready`, or rollback states without remapping credentials.
- Extended `PATCH /service/ai/v1/internal/tenants/{tenant_id}` to trigger bounded tenant document reindex backfill, release-marker capture, and owner-safe health snapshots during migration.

### Added - Segment 2 Data Plane Isolation Foundation (2026-03-06)

- Impacted capability IDs:
  - `isolation.data-plane.owner-tenant-qdrant-routing`
  - `isolation.data-plane.object-storage-domain-prefixes`
  - `isolation.data-plane.postgres-schema-bootstrap`
  - `security.encryption.owner-tenant-domain-keys`
- Impacted workflow scenario IDs:
  - `isolation.segment2.owner-runtime-uses-owner-qdrant-config`
  - `isolation.segment2.tenant-runtime-uses-tenant-qdrant-config`
  - `isolation.segment2.object-storage-domain-prefix-and-legacy-fallback`
  - `isolation.segment2.postgres-schema-bootstrap-additive`
- Added owner-vs-tenant Qdrant configuration fallbacks, runtime routing, and
  trust-domain logging so owner and tenant memory surfaces can be pointed at
  separate vector planes without forcing an immediate data cutover.
- Added trust-domain object-storage prefix enforcement with legacy read/delete
  fallback so new tenant blob writes land under domain-scoped paths while
  existing replay/document payloads remain readable during migration.
- Added additive PostgreSQL schema bootstrap helpers and owner/tenant encryption
  domain helpers, including wrapped tenant-key primitives for later per-tenant
  key rollout work.

### Fixed - Windows Optional Service Deploy Guard (2026-03-06)

- Impacted capability IDs:
  - `deploy.windows.optional-service-profiles`
  - `deploy.windows.remove-orphaned-optional-services`
  - `testing.static.optional-service-guard`
- Impacted workflow scenario IDs:
  - `deploy.windows.skip-unconfigured-cloudflared`
  - `deploy.windows.skip-unconfigured-whatsapp-bridge`
  - `testing.local.optional-service-profile-guard`
- Added compose profiles for `cloudflared` and `zetherion-ai-whatsapp-bridge`
  so unconfigured optional services are no longer started during routine Windows
  deployments.
- Updated `scripts/windows/deploy-runner.ps1` to compute active optional service
  profiles from `.env` and run `docker compose up -d --build --remove-orphans`
  with only the enabled profiles.
- Added `scripts/check-optional-service-guards.py` and unit coverage so the
  static `check` lane and CI pipeline fail if those optional service guards are
  removed.
- Aligned `scripts/verify-windows-host.ps1` with runtime verification by only
  expecting the WhatsApp bridge when it is both enabled and fully configured.

### Added - Segment 1 Scope Kernel and Prompt Isolation (2026-03-06)

- Impacted capability IDs:
  - `trust.scope.kernel`
  - `trust.scope.prompt-isolation`
  - `trust.scope.violation-logging`
- Impacted workflow scenario IDs:
  - `isolation.segment1.owner-agent-scope-labelling`
  - `isolation.segment1.tenant-chat-scope-labelling`
  - `isolation.segment1.scope-violation-rejection`
  - `isolation.segment1.docs-and-email-prompt-labelling`
- Added `src/zetherion_ai/trust/scope.py` with canonical trust-domain scope types,
  scope-labelled prompt fragments, fail-closed composition rules, and structured
  `prompt_scope_violation` logging.
- Routed current owner and tenant prompt builders through the scope kernel for:
  - owner agent core and router prompts
  - tenant `client_chat` system prompts
  - email classification and personality extraction prompts
  - docs knowledge and tenant document QA prompts
- Added unit coverage in `tests/unit/test_scope_kernel.py` for allowed and denied
  scope combinations, including worker-artifact and owner-portfolio boundaries.

### Added - Segment 0 Current-State Isolation Inventory and Compatibility Baseline (2026-03-06)

- Impacted capability IDs:
  - `isolation.inventory.current-state-map`
  - `trust.domains.canonical-vocabulary`
  - `migration.compatibility.allowlist`
  - `testing.bounded.pytest-venv-resolution`
- Impacted workflow scenario IDs:
  - `isolation.segment0.capture-current-storage-and-route-scope`
  - `isolation.segment0.classify-trust-and-access-mechanisms`
  - `isolation.segment0.validate-compatibility-manifest`
  - `testing.local.targeted-unit.pytest-override-heartbeat`
- Added `.ci/isolation_compatibility_manifest.json` to capture the current
  trust-domain baseline across:
  - relational storage families
  - Qdrant collections
  - route families
  - prompt sources
  - scheduler/queue/worker job flows
  - legacy compatibility surfaces
- Added [Isolation Inventory](../technical/isolation-inventory.md) technical
  documentation describing the six target trust domains and the current-state
  findings that later isolation segments will migrate.
- Added unit coverage in `tests/unit/test_isolation_compatibility_manifest.py`
  so the manifest remains complete, file-backed, and explicit about known
  legacy compatibility modules.
- Updated `scripts/testing/run-bounded.mjs` so pytest invocations wrapped by
  `run-with-heartbeat` still rewrite to the repo-local `.venv/bin/python`,
  preventing false `No module named pytest` failures during bounded targeted
  runs.

### Changed - Segment C2 Repo Hygiene Before Relocation (2026-03-06)

- Impacted capability IDs:
  - `repo.cleanup.zero-byte-tracked-files`
  - `repo.checkout.canonical-local-path`
  - `deploy.mac.remote-path`
- Impacted workflow scenario IDs:
  - `cleanup.c2.remove-zero-byte-tracked-files`
  - `cleanup.c2.normalize-canonical-clone-docs`
  - `cleanup.c2.update-remote-deploy-helper-path`
- Removed tracked zero-byte files:
  - `memory/phase7-github-management.md`
  - `zetherion-dev-agent/src/zetherion_dev_agent/watchers/__init__.py`
- Updated macOS/Linux setup docs and quick-start snippets to use the canonical
  checkout path `~/Developer/PersonalBot`.
- Updated Windows setup docs to use the stable checkout path `C:\ZetherionAI`.
- Updated the Mac deployment helper default remote path to `~/Developer/PersonalBot`.

### Changed - Segment 17 CI/E2E Contract Enforcement (2026-03-06)

- Added CI risk classifier job (`risk-classifier`) with fail-safe defaults:
  - emits `e2e_required=true|false`
  - classifies changed-path policy and defaults ambiguous cases to `true`
  - uploads `e2e-risk-classifier` receipt artifact
- Added required E2E gate job (`required-e2e-gate`) that always emits
  `e2e-contract-receipt` and validates local receipt evidence only:
  - CI never runs full E2E suites directly
  - when `e2e_required=true`, `.ci/e2e-receipt.json` must be present and match PR head SHA
  - receipt contract requires `docker_e2e=passed` and `discord_required_e2e=passed`
- Added local receipt runner `scripts/local-required-e2e-receipt.sh` to execute required
  Docker/Discord E2E suites and write `.ci/e2e-receipt.json`.
- Updated CI summary contract so merge cannot promote when required E2E gate fails.
- Updated CI failure attribution taxonomy:
  - `required-e2e-gate` failures now surface
    `AGENTS_POLICY_BREACH_REQUIRED_E2E`.
- Added risk/E2E gate mappings to `.ci/pipeline_contract.json`.
- Added script-level unit coverage for:
  - `scripts/ci_e2e_risk_classifier.py`
  - `scripts/ci_failure_attribution.py` reason-code handling.

### Changed - Segment 16 Multi-Sub-Worker Scale Hardening (2026-03-06)

- Added capability-aware worker dispatch targeting for `execution_target=any_worker`
  with explicit `target_node_id` assignment and eligibility checks before enqueue.
- Added worker rollout/canary gating for claim/result trust-policy evaluation contexts
  (`node_canary_enabled`) and rollout-stage enforcement for worker actions.
- Added worker health-scoring controls in tenant-admin storage and runtime:
  - `health_score`, `consecutive_job_failures` on worker nodes
  - stale-heartbeat and minimum-score dispatch guards
  - auto-quarantine thresholds with session revocation on trigger
- Extended worker node status operations to maintain health score/failure counters
  and revoke active sessions when quarantined.
- Added contention/idempotency unit coverage for multi-node safety:
  - lease-collision handling between worker nodes
  - duplicate result submission idempotency across nodes
- Added operator runbook for compromise response, key rotation, and canary re-enable:
  - `docs/development/worker-compromise-response.md`

### Changed - Segment 15 Worker WhatsApp Boundary Controls (2026-03-06)

- Added worker messaging grant persistence in tenant-admin storage:
  - `tenant_worker_messaging_grants` table with per-node/per-provider/per-chat scope
  - permission flags (`allow_read`, `allow_draft`, `allow_send`)
  - optional `redacted_payload` mode
  - TTL expiry and revoke metadata (`expires_at`, `revoked_at`, `revoked_by`)
- Added tenant-admin grant APIs on Skills:
  - `GET /admin/tenants/{tenant_id}/workers/messaging/grants`
  - `PUT /admin/tenants/{tenant_id}/workers/nodes/{node_id}/messaging/grants/{provider}/{chat_id}`
  - `DELETE /admin/tenants/{tenant_id}/workers/messaging/grants/{grant_id}`
- Added CGS internal admin wrappers for worker messaging grant list/upsert/revoke under:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/workers/...`
- Added trust-policy action `worker.messaging.grant` with two-person approval semantics.
- Extended worker messaging grants to support provider-scoped draft access:
  - `whatsapp:{read,draft}`
  - `email:{draft}`
  - unsupported permission/provider combinations now fail closed before dispatch, admin upsert, and trust backfill.
- Worker dispatch claim now enforces deny-by-default for `messaging.read*`/`messaging.draft*`/`messaging.send*`/`email.draft*`
  jobs unless an active scoped grant exists; denied attempts are logged as tenant
  security events (`worker_messaging_access_denied`).
- Worker grant TTL cleanup now runs in the existing messaging cleanup loop to purge
  expired grants automatically.

### Changed - Additional CI Cost Controls (2026-03-06)

- Added path-gating for code-change lanes:
  - `type-check` now runs only for code changes plus weekly/manual runs.
  - `security` (Bandit) now runs only for code changes plus weekly/manual runs.
  - `unit-test` now runs only for code changes plus weekly/manual runs.
- Added dedicated `code` changed-path filter in CI to drive deterministic
  gating for code-related jobs.
- Updated CI summary pass logic to treat path-gated `type-check`, `security`,
  and `unit-test` as policy-success when intentionally skipped.
- Updated CodeQL workflow to execute analysis in weekly/manual mode only.
- Updated pipeline contract notes to reflect local-first + path-gated policy
  for type/security/unit lanes.

### Changed - CI Cost Reduction and Secret Scanning Policy (2026-03-06)

- Switched CI schedule from daily to weekly (`Sunday 02:30 UTC`) to reduce recurring run volume.
- Reduced default PR matrix cost:
  - `unit-test` now runs Python `3.12` on PR/push by default.
  - Python `3.13` unit shard runs in weekly/manual heavy mode.
  - `integration-test` now runs in weekly/manual heavy mode.
  - `dependency-audit` now runs in weekly/manual heavy mode.
- Added explicit secrets scanning lane:
  - `Secret Scan (Gitleaks)` diff-only scan on PRs (`base..head` range).
  - Full-history gitleaks scan on weekly/manual runs.
- Updated CI summary and failure attribution wiring to include `secret-scan`
  and to treat intentionally skipped heavy-mode jobs as successful by policy.
- Updated pipeline contract governance artifacts for the new `secret-scan`
  job mapping and revised heavy-mode notes.

### Changed - Segment 14 Worker Operator Control Surface (2026-03-06)

- Added worker operator control routes across Skills and CGS internal admin surfaces:
  - node quarantine/unquarantine
  - node capability updates
  - worker job list/get
  - worker job retry/cancel
  - worker lifecycle/event listing
- Added Discord worker operator command handling for status, pending approvals,
  quarantine/unquarantine, and retry/cancel flows with tenant-aware admin checks.
- Expanded unit coverage for worker operator paths in:
  - Skills client/server routing and payload handling
  - CGS internal admin route branches and validation models
  - tenant admin manager helper behavior
  - Discord command routing and failure messaging
- Updated CGS route docs/OpenAPI and frontend route-wiring mappings for new
  worker operator endpoints.
- Fixed CI pipeline-contract doc bundle drift by updating required CGS
  onboarding/spec docs when CGS route files change.
- Updated bounded test lane defaults so pytest-heavy lanes run under heartbeat
  wrappers (`targeted-unit`, `unit-full`, `api-integration-coverage`,
  `e2e-mocked`) to prevent false stall failures.

### Changed - Segment 13 Announcement DM Guardrails (2026-03-06)

- Added `scripts/check-announcement-dm-guard.py` guardrail to block direct `user.send(...)`
  calls in announcement-producing paths, with allowlist restricted to
  `src/zetherion_ai/announcements/discord_adapter.py`.
- Added unit coverage in `tests/unit/test_check_announcement_dm_guard.py` for
  allowlisted and violation paths.
- Wired the guard into:
  - bounded local `check` lane (`scripts/testing/lanes.mjs`)
  - CI `pipeline-contract` fast-fail checks (`.github/workflows/ci.yml`)
- Updated bounded local `lint` lane to run `ruff format --check` in addition to
  `ruff check`, matching CI lint behavior and preventing format-only CI failures.
- Updated CI/CD documentation to include announcement DM guardrail enforcement in
  pipeline-contract checks.

### Changed - CI Conservation Guardrails for Local Validation (2026-03-06)

- Expanded bounded `check` lane to include documentation contract checks:
  - `check-docs-nav.py`
  - `check-docs-links.py`
  - `check-route-doc-parity.py`
  - `check-cgs-route-doc-parity.py`
  - `check-env-doc-parity.py`
- Updated `scripts/testing/run-bounded.mjs` to auto-prefer `.venv/bin/python` for local pytest lanes
  when available, preventing repeated `python3: No module named pytest` failures.
- Added `scripts/github/create-pr-safe.sh` wrapper that requires `--body-file` for PR creation
  to avoid shell backtick expansion regressions.

### Changed - Windows Announcement Plane Integration for Deploy/Promotions (2026-03-06)

- Replaced Windows deploy completion notification step to emit internal announcement events via
  `scripts/windows/announcement-emit.py` instead of direct Discord DM calls.
- Added `scripts/windows/announcements-flush.ps1` to replay spooled announcement events and wired it into:
  - deploy workflow completion path
  - promotions runner pre/post emit path
  - promotions watch pre/post cycle path
- Updated promotions runner notification path to `announcement-emit.py` and removed runtime dependency on
  direct DM notifier calls for deploy/promotions status announcements.
- Extended Windows promotions secret bootstrap/validation scripts with announcement-specific settings:
  - `ANNOUNCEMENT_EMIT_ENABLED`
  - `ANNOUNCEMENT_API_URL`
  - `ANNOUNCEMENT_API_SECRET`
  - `ANNOUNCEMENT_TARGET_USER_ID`
- Updated Windows promotions runbook/docs to validate announcement emit + flush behavior and queue-first
  retry semantics when announcement API is unavailable.

### Added - Segment 7 Security Hardening, Observability, and Rollout (2026-03-05)

- Added tenant security event persistence + aggregation in tenant admin storage:
  - `tenant_security_events` table and supporting indexes
  - high-severity (`high|critical`) alert logging on ingest
  - dashboard aggregation API support (`window`, severity totals, top event types, recent events)
- Added Skills internal hardening routes:
  - `GET /admin/tenants/{tenant_id}/messaging/messages/export`
  - `DELETE /admin/tenants/{tenant_id}/messaging/messages`
  - `GET /admin/tenants/{tenant_id}/security/events`
  - `GET /admin/tenants/{tenant_id}/security/dashboard`
- Added CGS internal admin wrappers for those hardening routes:
  - `GET /service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/messages/export`
  - `DELETE /service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/messages`
  - `GET /service/ai/v1/internal/admin/tenants/{tenant_id}/security/events`
  - `GET /service/ai/v1/internal/admin/tenants/{tenant_id}/security/dashboard`
- Added trust-policy rollout-stage gating for sensitive action namespaces:
  - messaging actions check `security.messaging_rollout_stage` (`disabled|canary|general`) and optional `security.messaging_canary_enabled`
  - automerge actions check `security.automerge_rollout_stage` (`disabled|canary|general`) and optional `security.automerge_canary_enabled`
- Added `messaging.delete` trust-policy action with approval/two-person semantics.
- Added security-event telemetry wiring for bridge signature failures/replay detection and policy-denied ingest/send/automerge paths.
- Added key-rotation runbook coverage for bridge signing and admin actor secrets in the security model docs.

### Changed - Trust Policy Gate for Internal Admin Actions (2026-03-05)

- Added a centralized trust-policy evaluator for sensitive/critical actions across messaging and autonomous merge control paths.
- Wired CGS internal admin tenant routes through trust-policy enforcement before upstream apply calls.
- Added global trust controls and kill switches:
  - `MESSAGING_INGESTION_KILL_SWITCH`
  - `MESSAGING_SEND_KILL_SWITCH`
  - `AUTO_MERGE_EXECUTION_KILL_SWITCH`
  - `AUTO_MERGE_POLICY_ENABLED`
  - `SECURITY_DEFAULT_TRUST_TIER`
- No external endpoint shape changes; contract docs bundle updated to reflect policy and configuration coverage.

### Added - Tenant Messaging Persistence + Skills Internal Control Plane (2026-03-05)

- Added tenant messaging persistence tables:
  - `tenant_messaging_provider_configs`
  - `tenant_messaging_accounts`
  - `tenant_messaging_chat_policies`
  - `tenant_messaging_messages` (encrypted full-text bodies with expiry timestamps)
  - `tenant_messaging_action_queue` (queued send actions with request/change refs)
- Added Skills internal tenant messaging routes:
  - `GET/PUT /admin/tenants/{tenant_id}/messaging/providers/{provider}/config`
  - `GET/PUT /admin/tenants/{tenant_id}/messaging/chats/{chat_id}/policy`
  - `GET /admin/tenants/{tenant_id}/messaging/chats`
  - `GET /admin/tenants/{tenant_id}/messaging/messages`
  - `POST /admin/tenants/{tenant_id}/messaging/messages/{chat_id}/send`
  - `POST /admin/tenants/{tenant_id}/messaging/ingest`
- Bridge ingest now stores signed inbound events into encrypted tenant message storage and enforces chat allowlist policy.
- Added TTL cleanup loop in Skills server to continuously purge expired tenant messaging payloads.

### Added - Tenant Messaging CGS + Public API Surfaces (2026-03-05)

- Added CGS internal admin messaging route family under:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/...`
  - includes provider config, chat policy CRUD, chat/message list, and send queue endpoints.
- Added tenant public upstream messaging routes:
  - `GET /api/v1/messaging/chats`
  - `GET /api/v1/messaging/messages`
  - `POST /api/v1/messaging/messages/{chat_id}/send`
- Added trust-policy/approval propagation for CGS messaging send:
  - high-risk send requests return `AI_APPROVAL_REQUIRED` with generated `change_ticket_id` when required.
- Added public messaging route trust-policy checks for read/send decisions and policy-coded error responses.
- Updated OpenAPI and docs bundles for both upstream and CGS gateway contracts.

### Added - Execution Ledger + Overnight Continuation Loop (2026-03-05)

- Added tenant execution-ledger persistence tables:
  - `tenant_execution_plans`
  - `tenant_execution_steps`
  - `tenant_execution_step_retries`
  - `tenant_execution_artifacts`
  - `tenant_execution_transitions`
- Added queue-integrated continuation task type:
  - `plan_continuation`
  - dispatched by `QueueProcessors` and executed via `PlanContinuationExecutor`.
- Added lease-based plan claiming and step claim/re-run semantics in tenant admin domain, including:
  - idempotent completed-step skip behavior
  - stale running-step reclaim
  - explicit failure category capture + retry backoff scheduling.
- Added Skills internal tenant execution-plan routes:
  - `POST /admin/tenants/{tenant_id}/execution/plans`
  - `GET /admin/tenants/{tenant_id}/execution/plans`
  - `GET /admin/tenants/{tenant_id}/execution/plans/{plan_id}`
  - `POST /admin/tenants/{tenant_id}/execution/plans/{plan_id}/pause`
  - `POST /admin/tenants/{tenant_id}/execution/plans/{plan_id}/resume`
  - `POST /admin/tenants/{tenant_id}/execution/plans/{plan_id}/cancel`

### Added - Autonomous PR + Auto-Merge Guardrail Engine (2026-03-05)

- Added deterministic autonomous merge orchestration module:
  - branch ensure/create (`codex/*`)
  - pull request create/reuse
  - guardrail evaluation (allowed paths, diff thresholds, forbidden actions)
  - required check-run gating before merge
  - merge execution + rollback-required escalation state
- Added Skills internal route:
  - `POST /admin/tenants/{tenant_id}/automerge/execute`
  - applies trust-policy gate (`automerge.execute`) and records admin audit events.
- Added CGS internal admin route:
  - `POST /service/ai/v1/internal/admin/tenants/{tenant_id}/automerge/execute`
  - forwards signed actor context and propagates change-ticket apply/failed status.
- Extended GitHub API client with orchestration primitives:
  - git ref get/create + branch ensure
  - pull request create/find + changed-files listing
  - commit check-run listing for required-check gating.
- Extended execution-step normalization to preserve per-step metadata (`steps[].metadata`) for deterministic executor routing in plan-ledger workflows.

### Changed - Windows Deploy Resilience Registration Signal Cleanup (2026-03-05)

- Updated `Deploy Windows` resilience registration step behavior to remain explicitly non-blocking without
  emitting a failed-step annotation when task registration is access-denied.
- Added explicit registration outputs for downstream diagnostics:
  - `registration_status`
  - `failure_code`
  - `bootstrap_required`
- Expanded promotions-task warning summary to include resilience registration status/failure details so
  operators can distinguish warning-state bootstrap requirements from true deployment failures.

### Added - Document Archive/Delete API Lifecycle Routes (2026-03-05)

- Added upstream document lifecycle routes:
  - `DELETE /api/v1/documents/{document_id}` (archive/delete request, async enqueue)
  - `POST /api/v1/documents/{document_id}/restore` (restore from archived state + reindex)
- Added `include_archived` query support to `GET /api/v1/documents` with default exclusion of
  `archiving|archived|purged`.
- Added document lifecycle domain methods:
  - `DocumentService.request_archive(...)`
  - `DocumentService.restore_document(...)`
  - `TenantManager.mark_document_restoring(...)`
- Added/updated unit coverage for route wiring, lifecycle status/error mapping, and tenant-manager
  lifecycle transitions.
- Updated upstream OpenAPI and technical docs bundle to include archive/delete/restore behavior and
  expanded lifecycle statuses.

### Changed - Document Archive Worker + Retrieval Guardrails (2026-03-05)

- Added archive/purge maintenance execution inside `DocumentService`:
  - claims archive jobs
  - deletes vectors
  - marks archived state + retention window
  - purges bytes/vectors after retention and marks `purged`
- Added upstream API lifecycle wiring in `PublicAPIServer` to run document maintenance loop on startup and cancel cleanly on shutdown.
- Updated RAG query path to exclude `archiving|archived|purged` documents before context/citation assembly.
- Added unit coverage for maintenance loop retry behavior and archive/purge processing paths.

### Changed - Windows Promotions Hardening + Runbook (2026-03-04)

- Enforced strict CGS blog publish response contract handling in Windows promotions pipeline:
  - `201/published` success
  - `409/duplicate` idempotent success
  - `409/AI_IDEMPOTENCY_CONFLICT` non-retryable failure
  - `400/401/403` non-retryable failure
  - `429/5xx` retryable failure
- Added promotions retry taxonomy and exit code contract:
  - `0` success
  - `2` retryable failure (queue + retry)
  - `3` non-retryable failure (no requeue)
- Added Discord DM completion notifications for deploy + promotions outcomes with idempotent dedupe keys.
- Improved resilience task registration diagnostics with explicit fields:
  - `bootstrap_required`
  - `failure_code`
  - `registration_actor`
  - `is_elevated`
- Added deterministic host-side resilience tooling:
  - `scripts/windows/bootstrap-resilience-tasks.ps1`
  - `scripts/windows/verify-resilience-tasks.ps1`
  - validates `ZetherionStartupRecover`, `ZetherionRuntimeWatchdog`, `ZetherionPostDeployPromotions`
- Added workflow strictness toggle in `deploy-windows.yml`:
  - `WINDOWS_REQUIRE_PROMOTIONS_TASK` (default `false`)
  - warning summary when promotions task is missing
  - blocking gate only when strict mode is enabled
- Expanded CI/CD docs with an operator runbook for:
  - promotions secret setup/validation
  - resilience bootstrap/verification
  - DM notification verification
  - CGS publish contract troubleshooting

### Changed - Zetherion-Only Repository Boundary Recovery (2026-03-04)

- Removed top-level `cgs/**` website/UI source from this repository to restore Zetherion-only scope.
- Removed CGS UI-specific CI jobs and local gate wiring:
  - removed `cgs-lint`, `cgs-typecheck`, `cgs-test`, `cgs-build`
  - removed `scripts/check-cgs-ui.sh` integration from local validation flows
- Added boundary enforcement to prevent reintroduction of top-level UI code:
  - CI `zetherion-boundary-check`
  - local diff guard `scripts/check-zetherion-boundary.sh`
  - scope audit helper `scripts/check-scope-diff.sh`
  - pre-push guard integration
- Kept internal Zetherion CGS gateway code under `src/zetherion_ai/cgs_gateway/**` unchanged as in-scope integration logic.
- Removed Windows deploy/rollback/startup hooks that created `cgs/.env.local`; runtime now operates without top-level CGS UI filesystem assumptions.

### Added - CGS-First Tenant Multi-Email Monitoring Control Plane (2026-03-04)

- Added tenant-scoped email admin persistence and intelligence model:
  - `tenant_email_provider_configs`
  - `tenant_email_oauth_states`
  - `tenant_email_accounts`
  - `tenant_email_sync_jobs`
  - `tenant_email_message_cache`
  - `tenant_email_critical_items`
  - `tenant_email_insights`
  - `tenant_email_events`
- Added Skills internal tenant email admin endpoints:
  - OAuth app config read/write
  - OAuth connect start/exchange
  - mailbox list/patch/delete
  - sync trigger
  - critical list
  - calendar list + primary calendar set
  - insights list + reindex
- Added model-assisted critical classification stage for synced emails:
  - deterministic rules + inference refinement
  - severity/reason/confidence outputs persisted for triage
- Added insight vector indexing for tenant email insights (Qdrant-backed when configured).
- Added retention enforcement in email sync path:
  - message body cache purge at 90 days
  - critical/insight purge at 365 days
- Added CGS internal admin email route family under:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/...`
- Added high-risk approval enforcement in CGS routes for:
  - tenant OAuth app credential writes
  - mailbox disconnect actions
- Added CGS docs coverage for email control-plane:
  - OpenAPI updates
  - auth/error matrix updates
  - frontend route wiring updates
  - new `docs/technical/cgs-email-monitoring-onboarding-kit.md`

### Added - CGS Tenant Admin Control Plane (2026-03-03)

- Added tenant-scoped internal admin persistence and audit model:
  - `tenant_discord_users`
  - `tenant_discord_bindings`
  - `tenant_settings_overrides`
  - `tenant_setting_versions`
  - `tenant_secrets`
  - `tenant_secret_versions`
  - `tenant_admin_audit_log`
- Added Skills internal tenant-admin API endpoints under:
  - `/admin/tenants/{tenant_id}/...`
  - includes Discord allowlist/roles, bindings, settings, secrets, and audit readout
- Added signed actor-envelope enforcement on Skills tenant-admin routes:
  - `X-Admin-Actor` + `X-Admin-Signature`
  - nonce/timestamp replay protection
- Added CGS internal tenant-admin route family:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/...`
- Added CGS approval workflow storage + routes for high-risk admin mutations:
  - pending change create/list
  - approve/reject (two-person guard)
  - apply status tracking for secret mutations
- Added tenant-aware runtime retrieval helpers:
  - `get_dynamic_for_tenant(...)`
  - `get_secret_for_tenant(...)`
- Wired Discord runtime allowlist checks to tenant-scoped controls when enabled via dynamic security settings.

### Changed - CGS-First Document Intelligence Client Kit (2026-03-02)

- Clarified exposure policy across docs and contracts: Zetherion `/api/v1` is upstream-only and CGS `/service/ai/v1` is the only public client API.
- Added internal component spec:
  - `docs/technical/zetherion-document-intelligence-component.md`
- Added external onboarding pack:
  - `docs/technical/cgs-client-onboarding-kit.md`
- Expanded CGS docs for browser upload completion options:
  - JSON completion (`tenant_id` in body)
  - multipart completion (`tenant_id` in query)
- Implemented CGS multipart passthrough for upload completion in runtime gateway route.
- Normalized document RAG provider naming to canonical `anthropic` (with `claude` alias compatibility retained).
- Updated OpenAPI specs and route wiring/auth/error docs to align with the above behavior.
- Tightened endpoint-doc bundle enforcement to include the new component spec + onboarding kit files.

### Changed - Windows Merge-Intelligence Promotions Authority (2026-03-02)

- Removed GitHub Actions `post-deploy-promotions.yml`; blog/release promotions are now executed on the Windows deployment host.
- Added Windows promotion runtime components:
  - `scripts/windows/promotions-runner.ps1`
  - `scripts/windows/promotions-watch.ps1`
  - `scripts/windows/promotions-pipeline.py`
  - `scripts/windows/set-promotions-secrets.ps1`
  - `scripts/windows/test-promotions-secrets.ps1`
- Deployment workflow now persists machine-local deployment receipts at `C:\ZetherionAI\data\deployment-receipts\<sha>.json` and invokes the local promotions runner after successful receipt build.
- Added scheduled task registration for `ZetherionPostDeployPromotions` (startup + periodic execution) through `scripts/windows/register-resilience-tasks.ps1`.
- Added per-SHA local promotions artifacts:
  - `C:\ZetherionAI\data\promotions\analysis\<sha>.json`
  - `C:\ZetherionAI\data\promotions\receipts\<sha>.json`
  - `C:\ZetherionAI\data\promotions\state.json`
- Promotions pipeline now enforces:
  - deployment receipt validation before any publish/release action
  - merge-intelligence evidence mapping across the promotion window
  - high-tier model only content generation (`gpt-5.2` -> `claude-sonnet-4-6`)
  - SEO + GEO gate checks and claim-to-evidence validation
  - idempotent blog publish + idempotent release increment with partial-failure retry behavior
- `scripts/check-cicd-success.sh` no longer requires a GitHub `Post-Deploy Promotions` workflow run for `main`.
- CI push trigger no longer runs full pipeline on `codex/**` (PR-to-main flow remains the quality gate path).

### Changed - Document Route Internal Hardening (2026-03-01)

- Internal typing and multipart parsing hardening in `src/zetherion_ai/api/routes/documents.py`.
- No API surface changes: no path/method/auth/error/schema contract changes for document endpoints.

### Changed - Windows Deploy Resilience Fallback (2026-03-01)

- Added controlled fallback in Windows resilience readiness checks for hardened runners where scheduled task registration is access-denied.
- Deployment receipt now treats persistent runner/docker service recovery as valid fallback when recovery tasks are missing and explicit fallback is enabled.

### Added - Document Intelligence + Post-Deploy Promotions (2026-02-28)

- Tenant-scoped document intelligence API endpoints:
  - `POST /api/v1/documents/uploads`
  - `POST /api/v1/documents/uploads/{upload_id}/complete`
  - `GET /api/v1/documents`
  - `GET /api/v1/documents/{document_id}`
  - `GET /api/v1/documents/{document_id}/preview`
  - `GET /api/v1/documents/{document_id}/download`
  - `POST /api/v1/documents/{document_id}/index`
  - `POST /api/v1/rag/query`
  - `GET /api/v1/models/providers`
- New tenant document persistence schema:
  - `tenant_documents`
  - `tenant_document_uploads`
  - `document_ingestion_jobs`
- Document ingestion pipeline for PDF/DOCX/text extraction, chunking, embedding, and indexing into `tenant_documents` Qdrant collection.
- CGS gateway runtime routes for document upload/list/detail/preview/download/re-index/RAG/provider catalog under `/service/ai/v1`.
- Provider/model override support in inference broker call path to support Groq/OpenAI/Claude routing for document RAG.
- Security and reliability hardening for Discord message alignment:
  - intent-aware memory-store handling in Tier 2 security path to reduce benign false positives
  - deterministic recent-context retrieval ordering in Qdrant
  - queue serialization guard for `discord_message` tasks by `(user_id, channel_id)`
  - strict reply-reference matching in Discord E2E waiter logic
- CI docs-contract enforcement additions:
  - endpoint doc-bundle checker (`scripts/check-endpoint-doc-bundle.py`)
  - pipeline contract job now runs endpoint docs bundle checks with full git history
- Documentation deployment now republishes on every `main` push (no docs-only path filter).
- Post-deploy promotions workflow (`.github/workflows/post-deploy-promotions.yml`) added:
  - gated by successful `Deploy Windows` completion and deployment receipt validation
  - mandatory SemVer patch release auto-increment/idempotency per deployed SHA
  - mandatory CGS blog generation/publish using `gpt-5.2` + `claude-sonnet-4-6` only
  - publishes release/blog receipts as workflow artifacts

### Added - Phase 9 (2026-02-08)

#### Phase 9: Personal Understanding System

- PostgreSQL-backed personal model with 4 data models: `PersonalProfile`, `PersonalContact`, `PersonalPolicy`, `PersonalLearning`
- asyncpg-based `PersonalStorage` with full CRUD operations for all models
- Communication style dimensions (formality, verbosity, directness, proactivity on 0-1 scale)
- Contact graph with relationship tracking and metadata
- Policy system with 5 domains and 4 modes (`AUTO`, `DRAFT`, `ASK`, `NEVER`)
- Integration with Gmail for contact-aware responses
- See [`../technical/personal-understanding.md`](../technical/personal-understanding.md) for data model details

### Added - Phase 8 (2026-02-07)

#### Phase 8: Gmail Integration

- 12-file Gmail module with complete email management capabilities
- Two-dimensional trust system (per-contact + per-type)
- Trust formula: `effective_trust = min(type_trust, contact_trust, reply_type_ceiling)`
- Progressive autonomy: read-only -> draft with approval -> auto-draft -> auto-send
- Auto-send threshold: `effective_trust >= 0.85 AND confidence >= 0.85`
- Reply type ceilings across 8 types from `ACKNOWLEDGMENT` (0.95) to `SENSITIVE` (0.30)
- Trust evolution deltas: +0.05 approval, -0.20 rejection, `GLOBAL_CAP` 0.95
- OAuth account management with encrypted token storage (AES-256-GCM)
- Unified inbox aggregation across multiple accounts
- Reply draft pipeline (7-step process from classification to send/queue)
- Digest generation (morning, evening, weekly summaries)
- Email analytics with relationship scoring
- See [`../technical/gmail-architecture.md`](../technical/gmail-architecture.md) for full design

#### Phase 8A: Observation Pipeline

- Tiered extraction: Tier 1 (regex patterns), Tier 2 (LLM-based)
- 6 learning categories for implicit knowledge extraction
- 5 learning sources with confidence scoring
- Background profile extraction after responses
- PostgreSQL storage for personal learnings
- See [`../technical/observation-pipeline.md`](../technical/observation-pipeline.md) for pipeline details

### Added - Phase 7 (2026-02-07)

#### Phase 7: GitHub Integration

- GitHub skill with 18 intents for full repository management
- Issue management: list, view, create, close, reopen, label, comment
- PR management: list, view, diff, merge
- Workflow and repo info queries
- 3 autonomy levels: `Autonomous`, `Ask`, `Always Ask`
- Per-action autonomy defaults with safety-first design
- GitHub token management via environment variables
- See [`../user/github-integration.md`](../user/github-integration.md) for usage guide

### Added - Phase 6 (2026-02-07)

#### Phase 6: Docker Hardening

- Distroless base images for bot and skills containers (~50MB runtime)
- Read-only root filesystem with tmpfs for `/tmp`
- `no-new-privileges` security option on ALL containers
- Resource limits (CPU and memory) for all 6 services
- Dual Ollama architecture (separate router + generation containers)
- PostgreSQL 17 Alpine service for personal understanding data
- Health checks on all 6 services with Python-based probes
- Network isolation between services
- See [`../technical/docker.md`](../technical/docker.md) for architecture details

### Added - Phase 5 (2026-02-06)

#### Phase 5A: Encryption Layer

- AES-256-GCM field-level encryption for sensitive Qdrant payloads
- PBKDF2-HMAC-SHA256 key derivation with 600,000 iterations and 32-byte salt
- TLS support for Qdrant connections (optional)
- `FieldEncryptor` and `KeyManager` classes in `zetherion_ai.security`

#### Phase 5B: InferenceBroker

- Smart multi-provider LLM routing based on task type
- Provider capability matrix: Claude (code), OpenAI (reasoning), Gemini (long docs), Ollama (lightweight)
- 16 TaskTypes for granular routing decisions
- Automatic fallback chains when primary provider unavailable
- See [`../technical/architecture.md`](../technical/architecture.md) for routing design

#### Phase 5B.1: Model Registry and Cost Tracking

- Dynamic model discovery via provider APIs
- Tier-based model selection (quality, balanced, fast)
- SQLite cost tracking with per-request logging
- Daily and monthly cost aggregation and reporting
- Discord notifications for budget alerts
- See [`../technical/cost-tracking.md`](../technical/cost-tracking.md) for budget management details

#### Phase 5C: User Profile System

- 8 profile categories: identity, preferences, schedule, projects, relationships, skills, goals, habits
- Tiered inference: Tier 1 (regex), Tier 2 (Ollama), Tier 3 (embeddings), Tier 4 (cloud)
- 5 signal engines for implicit extraction
- TTL-based caching with confidence scoring
- Background profile extraction after responses

#### Phase 5C.1: Employment Profile

- Bot identity and relationship modeling
- Trust levels: `MINIMAL` -> `BUILDING` -> `ESTABLISHED` -> `HIGH` -> `FULL`
- 10 relationship milestones tracking
- Communication style adaptation (formality, verbosity, proactivity)

#### Phase 5D: Skills Framework

- Abstract Skill interface with permission-based access control
- 15 granular permissions (read/write profile, memories, messages, etc.)
- `SkillRegistry` for intent routing and heartbeat coordination
- Separate Docker container for skills service isolation
- REST API with authentication on port 8080 for bot-skills communication
- See [`../technical/skills-framework.md`](../technical/skills-framework.md) for the framework design

#### Phase 5E: Built-in Skills

- **Task Manager**: CRUD operations, priority levels, deadlines, heartbeat reminders
- **Calendar Awareness**: Event types, recurrence patterns, availability checking
- **Profile Manager**: GDPR-style view/update/delete/export, confidence reports

#### Phase 5F: Heartbeat Scheduler

- Configurable interval (default 5 min) with quiet hours support
- Rate limiting (3 messages/hour per user)
- `ActionExecutor` with handlers for all skill action types
- Scheduled event handling for one-time triggers

#### Phase 5G: Router Enhancement

- 3 new skill intents: `TASK_MANAGEMENT`, `CALENDAR_QUERY`, `PROFILE_QUERY`
- Skill intent examples in router prompts
- Agent integration for skill intent handling

### Changed

- **Test suite**: Expanded to **3,000+ tests** (historical snapshot at the time:
  93%+ coverage across 89 test files and 91 source files)
- **Docker services**: Expanded to **6 services** (bot, skills, qdrant, postgres, ollama, ollama-router)
- **Ollama models**: Updated from Qwen to Meta Llama (llama3.2:3b for router, llama3.1:8b for generation)
- **Default cloud models**: claude-sonnet-4-5-20250929, gpt-5.2, gemini-2.5-flash
- **Project renamed** from `secureclaw` to `zetherion-ai`; all imports updated to `zetherion_ai`
- Docker container names now use hyphens (`zetherion-ai-*`)
- Integration tests updated for Phase 5-9 features

### Fixed

- All test failures resolved across all phases
- Config tests with environment variable isolation using monkeypatch
- Docker integration tests with proper service startup
- Type checking errors in async Qdrant client
- GitHub push protection issues with example tokens in documentation
- Chainguard ENTRYPOINT compatibility for CMD and healthchecks
- `load_dotenv` moved to fixture scope to prevent env pollution in test suite
- asyncpg mypy import issues resolved
- Security hardening and cleanup for skills server and Qdrant connections

---

## [1.0.0] - Initial Release

### Added

- Discord bot with dual LLM backends (Gemini + Ollama)
- Message routing with intent classification
- Claude / OpenAI integration for complex tasks (claude-sonnet-4-5-20250929, gpt-5.2)
- Gemini / Ollama integration for simple queries (gemini-2.5-flash, llama3.2:3b)
- Qdrant vector database for long-term memory
- Google Gemini embeddings for semantic search
- Docker containerization with Compose orchestration
- Basic security controls (rate limiting, user allowlist)
- Comprehensive error handling and retry logic

### Security

- Pydantic `SecretStr` for all credentials
- Gitleaks secret scanning in pre-commit hooks
- Bandit security scanning in CI/CD
- CodeQL weekly analysis
- Pinned dependencies with Dependabot
- Prompt injection detection (17 regex patterns)
- User allowlist for Discord interactions

---

## Version History Details

### Phase 1: Test Fixes (Coverage: ~42% -> 78%)

**Phase 1A: Agent Core Tests** -- Fixed 13 test failures

- Resolved Docker service dependency issues
- Fixed async Qdrant client usage
- Improved retry logic testing
- **Result**: 41 tests passing, 94.76% module coverage

**Phase 1B: Security Tests** -- Fixed 3 test failures

- Fixed prompt injection detection tests
- Corrected allowlist and rate limiter tests
- **Result**: 37 tests passing, 94.12% module coverage

**Phase 1C: Config Tests** -- Fixed 10 test failures

- Implemented environment variable isolation with monkeypatch
- Fixed `allowed_user_ids` parsing tests
- Resolved Docker integration test errors (14 tests)
- **Result**: All config tests passing, 96.88% module coverage

### Phase 2: Coverage Improvements (Coverage: 78% -> 87.58%)

**Phase 2A: Router Factory Tests** -- 26% -> 100% coverage

- Added 12 comprehensive tests for factory functions
- Tested async/sync router creation, health checks, and fallback logic

**Phase 2B: Discord Bot Edge Cases** -- 68.55% -> 89.92% coverage

- Added 11 edge case tests for uncovered code paths
- `/channels` command tests (6 tests): unauthorized user handling, DM vs guild context, channel listing, long response splitting
- Agent readiness tests (1 test)
- `_send_long_message` helper tests (4 tests)

### Phase 3-4: Refactoring and CI Hardening

- Project renamed from SecureClaw to Zetherion AI
- Pre-commit hooks consolidated (Ruff, Mypy, Bandit, Gitleaks, Hadolint)
- CI/CD pipeline with 6 parallel jobs
- Windows deployment scripts (PowerShell)
- MkDocs documentation site with wiki sync

### Phase 5: Skills and Routing (Historical snapshot: 87.58% -> 93%+)

- Complete InferenceBroker with multi-provider routing
- Encryption layer with AES-256-GCM
- User profile system with tiered inference
- Skills framework with 3 built-in skills
- Heartbeat scheduler with quiet hours
- Router enhancement with skill intents

### Phase 6: Docker Hardening

- Distroless images, read-only filesystems, resource limits
- Dual Ollama architecture for router and generation separation
- PostgreSQL 17 Alpine for persistent data
- Health checks and network isolation across all 6 services

### Phase 7: GitHub Integration

- 18 intents for full repository management
- 3 autonomy levels with safety-first defaults

### Phase 8: Gmail Integration

- 12-file module with trust-based progressive autonomy
- OAuth management, inbox aggregation, reply drafting, digest generation
- Observation pipeline for implicit knowledge extraction

### Phase 9: Personal Understanding

- PostgreSQL-backed personal model (4 data models)
- Communication style dimensions, contact graphs, policy system

### Historical Test Statistics (Snapshot)

| Category | Count | Status |
|----------|-------|--------|
| **Test Files** | 89 (snapshot) | All passing |
| **Source Files** | 91 (snapshot) | Covered |
| **Total Tests** | 3,000+ | All passing |
| **Overall Coverage** | 93%+ (snapshot) | Target exceeded at that time |

### Docker Services

| Service | Image | Purpose |
|---------|-------|---------|
| bot | Distroless | Discord bot and agent core |
| skills | Distroless | Skills REST API on port 8080 |
| qdrant | qdrant/qdrant | Vector memory storage |
| postgres | PostgreSQL 17 Alpine | Personal understanding data |
| ollama | ollama/ollama | Generation model (llama3.1:8b) |
| ollama-router | ollama/ollama | Router model (llama3.2:3b) |

---

## CI/CD Pipeline

### Pipeline Stages

1. **Lint** -- Ruff linting and formatting
2. **Type Check** -- Mypy strict mode type checking
3. **Security** -- Bandit security scanning
4. **Tests** -- Unit tests on Python 3.12 and 3.13
5. **Docker Build** -- Container build verification
6. **Integration** -- Full integration tests with Docker services

### Pre-Commit Hooks

- Ruff (linting and formatting)
- Mypy (type checking)
- Gitleaks (secret scanning)
- Bandit (security scanning)
- Hadolint (Dockerfile linting)
- File checks (trailing whitespace, EOF, merge conflicts)

---

## Breaking Changes

None yet. This project has not yet published a stable public API.

---

## Migration Guide

### From Development to Production

1. **Update `.env` file**:
   - Set `ENVIRONMENT=production`
   - Set `LOG_LEVEL=INFO`
   - Configure `ALLOWED_USER_IDS` for production users

2. **Review Security Settings**:
   - Ensure all API keys are properly set
   - Verify user allowlist is configured
   - Check rate limiting settings

3. **Deploy with Docker Compose**:
   ```bash
   docker compose up -d
   ```

4. **Monitor Logs**:
   ```bash
   tail -f logs/zetherion_ai.log | jq .
   ```

See [`../technical/configuration.md`](../technical/configuration.md) for full configuration reference.

---

## Known Issues

None currently reported. See [GitHub Issues](https://github.com/jimtin/zetherion-ai/issues) for the latest.

---

## Contributors

- James Hinton ([@jimtin](https://github.com/jimtin)) -- Project creator and maintainer
- Claude Sonnet 4.5 / Claude Opus 4.6 -- AI pair programming

See [Setup and Contributing](setup.md) for contribution guidelines.

---

## License

This project is licensed under the MIT License. See the [LICENSE](https://github.com/jimtin/zetherion-ai/blob/main/LICENSE) file for details.

---

## Acknowledgments

- Anthropic for the Claude API
- Google for the Gemini API
- OpenAI for GPT models
- Discord.py community
- Qdrant team for the vector database
- Ollama team for the local LLM runtime
- Meta for the Llama model family
