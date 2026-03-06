# Worker Compromise Response Runbook

This runbook defines containment, credential rotation, and staged re-enable
for compromised or suspected-compromised sub-worker nodes.

## Triggers

Run this flow when any of the following is true:

- unexpected worker claims/results from a known node ID
- repeated signature/nonce validation failures from one node
- worker health score collapse with repeated failed job submissions
- host compromise indicator on a worker machine

## Immediate Containment

1. Stop new dispatch globally by setting `WORKER_DISPATCH_KILL_SWITCH=true`.
2. Stop result acceptance globally by setting `WORKER_RESULT_ACCEPT_KILL_SWITCH=true`.
3. Quarantine the suspected node:
   - `POST /admin/tenants/{tenant_id}/workers/nodes/{node_id}/quarantine`
4. Confirm the node cannot claim jobs:
   - `POST /worker/v1/nodes/{node_id}/jobs/claim` should return policy/eligibility denial.

Quarantine revokes active sessions server-side. A quarantined node cannot be used
for new claims until status is restored.

## Credential Rotation

1. Keep the node quarantined during rotation.
2. Rotate worker credentials with a fresh bootstrap/register cycle:
   - `POST /worker/v1/bootstrap`
   - `POST /worker/v1/nodes/register`
3. Replace local worker secrets on the node host:
   - session token
   - signing secret
4. Verify old credentials are rejected (401/403) and new credentials are accepted.

## Canary Re-Enable

Use staged rollout controls instead of direct full re-enable.

1. Set worker rollout to canary:
   - `security.worker_rollout_stage = canary`
2. Enable canary dispatch:
   - `security.worker_canary_enabled = true`
3. Allowlist only known-safe nodes:
   - `security.worker_canary_node_ids`
   - `security.worker_canary_node_groups`
4. Keep suspected node quarantined until a clean host attestation is complete.
5. Run no-op and low-risk jobs first, then progressively increase scope.
6. Promote to general rollout only after stable canary results:
   - `security.worker_rollout_stage = general`

## Health/Quarantine Guardrails

Dispatch and claims enforce:

- minimum health score (`security.worker_claim_min_health_score`)
- stale heartbeat cutoff (`security.worker_heartbeat_stale_seconds`)
- auto-quarantine policy:
  - `security.worker_auto_quarantine_enabled`
  - `security.worker_auto_quarantine_score_threshold`
  - `security.worker_auto_quarantine_consecutive_failures`

If a node crosses threshold, it is auto-quarantined and active sessions are revoked.

## Recovery Verification

Before clearing the incident:

1. `GET /admin/tenants/{tenant_id}/workers/nodes/{node_id}` shows expected status/health.
2. Worker claim path succeeds only for intended canary nodes.
3. Worker result submissions are accepted only from active sessions.
4. Worker event feed contains quarantine, credential rotation, and re-enable evidence:
   - `GET /admin/tenants/{tenant_id}/workers/events`
