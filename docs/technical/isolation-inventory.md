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
  - tenant session recall state now includes `chat_sessions.memory_subject_id`,
    `chat_sessions.conversation_summary`, and `tenant_subject_memories`, all of
    which remain tenant-local and are not shared with owner-personal runtime paths
  - tenant chat, message, web-session, and web-event rows now carry
    `execution_mode` so sandbox traffic remains explicitly tagged inside the
    tenant runtime instead of silently mixing with live flows
- `control_plane`
  - CGS tenant mappings, audits, request logs, queueing, rollout, and
    announcement dispatch
  - CGS tenant mappings now carry first-class `isolation_stage` and
    provisioning reconciliation metadata for staged client upgrades
  - Canonical trust persistence now lives in `src/zetherion_ai/trust/storage.py` under the control-plane schema, covering trust policies, grants, scorecards, feedback, and decision audit rows for staged backfill
  - tenant API-key registry plus sandbox profile/rule configuration in
    `src/zetherion_ai/api/tenant.py` now provide a control-plane layer for
    tenant test-mode setup without crossing into owner-personal storage

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

### Segment 2 tenant conversation additions

- `src/zetherion_ai/api/conversation_runtime.py`
  - tenant-only prompt context assembler for `/api/v1/chat*`; reads and writes
    `tenant_raw` session summaries plus durable subject memories
- `src/zetherion_ai/skills/client_chat.py`
  - now accepts tenant-scoped context notes from the public API runtime and
    wraps them in `tenant_raw` scope labels before inference
- `src/zetherion_ai/api/routes/sessions.py`
  - now derives `memory_subject_id` from explicit client input or
    `external_user_id`; the identifier remains tenant-local metadata rather
    than an auth or cross-domain join key

### Segment 3 tenant sandbox additions

- `src/zetherion_ai/api/test_runtime.py`
  - deterministic sandbox responder for tenant test-mode chat and stream
    requests; synthesizes replies from tenant-owned sandbox profiles/rules and
    built-in presets without calling live providers
- `src/zetherion_ai/api/routes/test_mode.py`
  - tenant-scoped sandbox profile, rule, and preview routes under `/api/v1/test/*`
- `src/zetherion_ai/api/middleware.py`
  - now distinguishes `sk_live_...` from `sk_test_...`, restricts test keys to
    `/api/v1/sessions` plus `/api/v1/test/*`, and propagates `execution_mode`
    from API key or session token into downstream request handling
- `src/zetherion_ai/api/routes/chat.py`
  - uses `execution_mode=test` to route chat and stream through the sandbox
    runtime while keeping assistant messages inside `tenant_raw`
- `src/zetherion_ai/api/routes/analytics.py`
  - records tagged test analytics rows but suppresses tenant-derived writes such
    as recommendations, funnel updates, CRM interactions, and feedback mutation

### Segment 4 sandbox control-plane isolation additions

- `src/zetherion_ai/admin/tenant_admin_manager.py`
  - tenant execution plans, steps, retries, artifacts, and worker jobs now
    persist `execution_mode`, so test-mode control-plane execution remains
    explicitly tagged from plan creation through worker-job inspection
  - `plan_continuation` queue payloads now carry `execution_mode`, and
    test-mode worker artifact scopes use `worker_artifact:test:...` instead of
    the live `worker_artifact:...` namespace
  - live worker claims now exclude `execution_mode=test` jobs, preventing
    sandbox requests from consuming real worker leases or node capacity
- `src/zetherion_ai/queue/plan_executor.py`
  - test-mode execution plans now synthesize deterministic receipts for both
    `windows_local` and worker-targeted steps, and they skip live agent prompts,
    live worker dispatch, and owner review-summary enqueue paths
- `src/zetherion_ai/skills/server.py`
  - internal execution-plan creation now accepts `execution_mode`, and worker
    claim responses surface that mode explicitly to downstream runtimes
- `zetherion-dev-agent/src/zetherion_dev_agent/worker_runtime.py`
  - defensive last-resort short-circuit for any stray test-mode claimed job,
    returning a simulated success receipt instead of executing Codex commands

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
  prompts, docs knowledge prompts, tenant document QA prompts, and the tenant
  public-chat conversation context path.
- Test-mode tenant runtime additions must keep sandbox rows and controls tagged
  inside `tenant_raw` plus `control_plane`, and must exclude those tagged rows
  from `tenant_derived`/`owner_portfolio` pipelines unless a later segment
  explicitly documents that crossover.
- Test-mode control-plane execution additions must keep queued continuations,
  execution artifacts, and worker-facing receipts tagged `execution_mode=test`,
  must avoid live worker claims and owner review notifications, and must use
  the dedicated `worker_artifact:test:...` scope when a worker-style artifact
  reference is needed.
