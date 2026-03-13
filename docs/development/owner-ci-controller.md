# Owner CI Controller

This document describes the owner-controlled CI/CD model implemented across
Zetherion AI, CGS, and the Windows worker runtime.

## Scope

- Zetherion owns repo profiles, runs, shards, receipts, evidence, incidents,
  and release verification.
- CGS exposes owner-only routes and dashboards for repo, plan, run, health,
  and blocker visibility.
- The Windows worker runs from `C:\ZetherionCI\agent-src`.
- GitHub is an event source and manual helper boundary only.
- `./scripts/test-full.sh` remains the canonical local heavy gate.

## Core Models

- `CiOperation`
- `CiShardReceipt`
- `MergeReadinessReceipt`
- `ReleaseVerificationReceipt`
- `DependencyStatus`
- `QueueHealth`
- `SchemaIntegrityStatus`
- `AuthFlowStatus`

## External Statuses

GitHub should consume these Zetherion-owned contexts:

- `zetherion/merge-readiness`
- `zetherion/deploy-readiness`

GitHub workflow names are no longer the authoritative readiness signal.

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

### Receipt and incident flow

Default debugging path:

`resolve -> operation -> evidence -> incident`

Every healthy deploy should end with a green `ReleaseVerificationReceipt`.

## Windows Provisioning

The worker runtime stays isolated from the live deployed tree:

- automation workspace: `C:\ZetherionCI\agent-src`
- deployed runtime: `C:\ZetherionAI`

Host execution is limited to bootstrap, checkout/worktree prep, Docker
orchestration, and cleanup.
