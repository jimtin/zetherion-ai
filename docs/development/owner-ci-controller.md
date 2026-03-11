# Owner CI Controller

This document describes the repo-agnostic owner CI controller now implemented
across Zetherion AI, CGS, and the Windows worker runtime.

## Scope

- Zetherion owns repo profiles, plan snapshots, runs, shards, worker jobs, and
  reviewer state in `owner_personal`.
- CGS exposes owner-only routes for repo, plan, and run control, and it acts as
  the relay when a worker cannot reach Zetherion directly.
- The Windows worker runs only from `C:\ZetherionCI\...` and explicitly denies
  `C:\ZetherionAI`.
- GitHub remains the source of truth and the only deployment trigger.

## Core Models

- `RepoProfile`
  - repo registry entry for any onboarded app
  - defines fast local lanes, Windows certification lanes, review policy,
    promotion policy, and allowlisted repo roots
- `PlanSnapshot`
  - versioned owner plan storage
  - intended for persistent development plans and staged rollout notes
- `CiRun`
  - one validation run for a repo/ref/mode
  - stores run state, reviewer receipts, GitHub receipts, and shard state
- `CiShard`
  - one executable lane inside a run
  - becomes a worker job when the execution target is `windows_local`

## Public Interfaces

### Zetherion owner worker bridge

- `GET /owner/ci/worker/v1/health`
- `POST /owner/ci/worker/v1/bootstrap`
- `POST /owner/ci/worker/v1/nodes/register`
- `POST /owner/ci/worker/v1/nodes/{node_id}/heartbeat`
- `POST /owner/ci/worker/v1/nodes/{node_id}/jobs/claim`
- `POST /owner/ci/worker/v1/nodes/{node_id}/jobs/{job_id}/result`

### CGS owner routes

- `POST /service/ai/v1/owner/repos`
- `GET /service/ai/v1/owner/repos`
- `PATCH /service/ai/v1/owner/repos/:repoId`
- `POST /service/ai/v1/owner/ci-runs`
- `GET /service/ai/v1/owner/ci-runs/:runId`
- `POST /service/ai/v1/owner/ci-runs/:runId/promote`
- `POST /service/ai/v1/owner/dev-plans`
- `GET /service/ai/v1/owner/dev-plans/:planId`
- `GET /service/ai/v1/owner/dev-plans/:planId/versions`

### CGS relay surface

- `GET /api/ai/ci/relay/worker/v1/*`
- `POST /api/ai/ci/relay/worker/v1/*`

Worker result replays use:

- `X-API-Secret`
- `X-CI-Relay-Replay: 1`

This bypass is limited to relay replays because original worker signatures are
not reusable after timestamp/nonce expiry.

### Agent broker surface

- `POST /service/ai/v1/agent/sessions`
- `GET /service/ai/v1/agent/apps`
- `GET /service/ai/v1/agent/repos`
- `GET /service/ai/v1/agent/apps/{app_id}/manifest`
- `GET /service/ai/v1/agent/apps/{app_id}/docs`
- `GET /service/ai/v1/agent/apps/{app_id}/docs/{slug}`
- `GET /service/ai/v1/agent/apps/{app_id}/services`
- `GET /service/ai/v1/agent/apps/{app_id}/services/{service_kind}`
- `POST /service/ai/v1/agent/apps/{app_id}/workspace-bundles`
- `GET /service/ai/v1/agent/workspace-bundles/{bundle_id}`
- `POST /service/ai/v1/agent/apps/{app_id}/test-plans/compile`
- `POST /service/ai/v1/agent/apps/{app_id}/publish-candidates`
- `POST /service/ai/v1/agent/apps/{app_id}/operations/resolve`
- `GET /service/ai/v1/agent/operations/{operation_id}`
- `GET /service/ai/v1/agent/operations/{operation_id}/evidence`
- `GET /service/ai/v1/agent/operations/{operation_id}/logs`
- `GET /service/ai/v1/agent/operations/{operation_id}/incidents`

Supported brokered service views:

- GitHub: `overview`, `compare`, `pulls`, `workflows`
- Vercel: `overview`, `deployments`, `domains`, `envs`
- Clerk: `overview`, `jwks`, `openid`
- Stripe: `overview`, `products`, `prices`, `customers`, `subscriptions`,
  `invoices`, `webhook_health`

Downstream agents never receive reusable third-party credentials. External
access is brokered through owner-managed connectors stored in Zetherion, and
writeback to GitHub remains publish-candidate only.

### Provider evidence ingestion

- CGS now forwards Stripe and Clerk webhooks into the normalized operation
  evidence model after the primary business logic succeeds.
- CGS also exposes provider-specific observability webhook endpoints for
  GitHub and Vercel:
  - `POST /api/webhooks/github/{app_id}`
  - `POST /api/webhooks/vercel/{app_id}`
- Polling fallback is available through:
  - `GET /api/cron/cgs-ai-operations`
- The canonical debugging path is `resolve -> operation -> evidence`. When a
  managed operation carries a linked CI `run_id`, operation evidence also
  includes correlated CI lifecycle events, debug bundles, and Docker/container
  logs.

## Windows Provisioning

Two PowerShell scripts are provided under [scripts/windows](/Users/jameshinton/Development/zetherion-ai/scripts/windows):

- [install-ci-worker.ps1](/Users/jameshinton/Development/zetherion-ai/scripts/windows/install-ci-worker.ps1)
  - clones a dedicated checkout into `C:\ZetherionCI\agent-src`
  - creates a dedicated virtualenv in `C:\ZetherionCI\agent-runtime`
  - installs `zetherion-dev-agent` from that checkout
  - writes `%USERPROFILE%\.zetherion-dev-agent\config.toml`
  - registers the startup task `ZetherionOwnerCiWorker`
- [verify-ci-worker-connectivity.ps1](/Users/jameshinton/Development/zetherion-ai/scripts/windows/verify-ci-worker-connectivity.ps1)
  - validates direct endpoint reachability
  - validates relay reachability when configured
  - optionally runs `zetherion-dev-agent worker --once`
  - optionally forces direct-url failure to confirm relay fallback
  - records a JSON receipt

## Rollout Rules

- `catalyst-group-solutions` and `zetherion-ai` are permanent certification
  repos.
- Controller, relay, reviewer, runtime, or provisioning changes must re-certify
  both repos before promotion.
- Scheduled regression on both repos remains mandatory after rollout.
- Disconnected execution may complete a shard, but merge/promotion stays blocked
  until synced receipts are stored in Zetherion and the reviewer verdict is
  green.

## Operational Notes

- The worker spool is durable and local. If direct and relay submission both
  fail, results remain queued until the next successful sync cycle.
- The Windows verifier can only mark `claiming` and `submitting` as true when a
  queued worker job is available during `worker --once`.
- The relay outbox currently flushes on relay traffic. If you want periodic
  drain without inbound traffic, add a cron/job that calls the relay health
  endpoint.
