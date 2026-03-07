# Isolation Inventory

Segment 0 establishes a source-controlled inventory of the current storage,
vector, route, prompt, and job-flow surfaces that will be migrated into the
target trust domains:

- `owner_personal`
- `owner_portfolio`
- `tenant_raw`
- `tenant_derived`
- `control_plane`
- `worker_artifact`

This segment does not change runtime behavior. It documents the current state,
identifies the legacy compatibility surfaces that still mix domains, and gives
later segments a deterministic baseline for migration.

## Canonical Artifact

The machine-readable source of truth for this segment is:

- `.ci/isolation_compatibility_manifest.json`

Tests validate that the manifest stays structurally complete, that the current
trust-domain vocabulary remains stable, and that the explicitly allowed legacy
modules are tracked until their planned cutover segments land.

## Inventory Coverage

The manifest captures the current state of:

- relational storage families
- Qdrant collections and their current domain intent
- public, internal, CGS, and Discord route families
- prompt source files that currently assemble model context
- scheduler, queue, announcement, document, and worker job flows
- the existing mix of access control, behavioral trust, grants,
  descriptive relationship state, and derived intelligence

## Current-State Findings

### Domains that already have a clear intent

- `owner_personal`
  - `src/zetherion_ai/personal/storage.py`
  - `src/zetherion_ai/personal/operational_storage.py` now owns owner-personal commitments, blockers, active plans, and review-queue state in the owner schema
  - Gmail account/email state
  - calendar, tasks, milestones, and dev journal vector collections
- `tenant_raw`
  - tenant chat, document, messaging, and execution data in
    `src/zetherion_ai/api/tenant.py` and
    `src/zetherion_ai/admin/tenant_admin_manager.py`
- `control_plane`
  - CGS tenant mappings, audits, request logs, queueing, rollout, and
    announcement dispatch
  - CGS tenant mappings now carry first-class `isolation_stage` and
    provisioning reconciliation metadata for staged client upgrades
  - Canonical trust persistence now lives in `src/zetherion_ai/trust/storage.py` under the control-plane schema, covering trust policies, grants, scorecards, feedback, and decision audit rows for staged backfill

### Domains that are not isolated yet

- `owner_portfolio`
  - derived health/telemetry/intelligence outputs exist, but they are not yet
    split into a dedicated owner-only derived domain
- `tenant_derived`
  - recommendation, analytics, and intelligence outputs exist, but they still
    share storage and access paths with raw tenant data
- `worker_artifact`
  - worker jobs, sessions, artifacts, and bounded logs exist, but the
    surrounding prompt and storage surfaces still rely on repo-oriented grants
    and shared runtime paths

### Highest-priority legacy compatibility surfaces

- `src/zetherion_ai/memory/qdrant.py`
  - legacy generic helper retained internally, but production callers now route through scoped wrappers and a repo guard blocks new direct use
- `src/zetherion_ai/discord/user_manager.py`
  - legacy single-user store that mixes owner-personal and control-plane data
- `src/zetherion_ai/integrations/storage.py`
  - mixed raw/derived/control storage for integration workflows
- `src/zetherion_ai/agent/core.py`
- `src/zetherion_ai/agent/prompts.py`
- `src/zetherion_ai/routing/email_router.py`
  - prompt and routing surfaces that still depend on implicit caller discipline
    instead of fail-closed scope labels

## Migration Rules Set by This Baseline

- New isolation work must extend the manifest instead of silently bypassing it.
- Legacy compatibility surfaces must stay on the explicit allowlist until the
  segment named in the manifest removes them.
- Future scope-kernel work must use the six trust domains defined here; new
  domain names require an explicit manifest and documentation update.
- Route or storage changes that alter trust boundaries should update this
  inventory before or with the behavior change.
- CGS tenant migration now writes `cgs_ai_tenant_migration_receipts` in the control-plane domain and owner-safe tenant health snapshots into the `owner_portfolio` schema; later segments must preserve that split during cutover.
- Prompt isolation now starts in `src/zetherion_ai/trust/scope.py`; current
  integrations cover owner agent prompts, tenant chat prompts, email routing
  prompts, docs knowledge prompts, and tenant document QA prompts.
