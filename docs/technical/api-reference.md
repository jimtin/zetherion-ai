# Skills API Reference (Internal)

## Overview

The Skills API runs on port `8080` and is designed for trusted internal callers
(bot service, internal operators, and routed internal tooling).

**Base URL:** `http://zetherion-ai-skills:8080` (or the routed internal URL set by `SKILLS_SERVICE_URL`)

For external app integrations, use the [Public API Reference](public-api-reference.md).

---

## Authentication

All endpoints require `X-API-Secret` except:

- `GET /health`
- `GET /oauth/{provider}/callback`
- `GET /gmail/callback`
- `GET /worker/v1/health`
- `POST /worker/v1/bootstrap`
- Worker signed endpoints under `/worker/v1/nodes/*`

```http
X-API-Secret: <skills-shared-secret>
```

If `SKILLS_API_SECRET` is unset, auth is effectively disabled for this internal service.

---

## Health and Registry

### GET /health

Service health probe.

**Response 200:**

```json
{
  "status": "healthy",
  "skills_ready": 8,
  "skills_total": 12
}
```

### GET /status

Registry status summary.

**Response 200:**

```json
{
  "total_skills": 12,
  "total_intents": 80,
  "by_status": {
    "ready": ["task_manager", "calendar", "email"]
  },
  "ready_count": 12,
  "error_count": 0
}
```

### GET /skills

List registered skills.

**Response 200:**

```json
{
  "skills": [
    {
      "name": "task_manager",
      "description": "...",
      "version": "1.0.0",
      "author": "Zetherion AI",
      "permissions": ["READ_MEMORIES"],
      "collections": [],
      "intents": ["create_task", "list_tasks"]
    }
  ]
}
```

### GET /skills/{name}

Get one skill metadata entry.

**Response 200:** skill metadata object

**Response 404:**

```json
{
  "error": "Skill not found"
}
```

### GET /intents

List intent-to-skill mappings.

**Response 200:**

```json
{
  "intents": {
    "create_task": "task_manager",
    "email_route": "email"
  }
}
```

### GET /prompt-fragments?user_id=<id>

Get aggregated skill prompt fragments for one user.

**Response 200:**

```json
{
  "fragments": [
    "[Tasks: 3 open, 1 overdue]"
  ]
}
```

---

## Skill Execution

### POST /handle

Dispatch one request to the mapped skill.

**Request:**

```json
{
  "id": "optional-uuid",
  "user_id": "123456789",
  "intent": "email_route",
  "message": "route unread email",
  "context": {
    "skill_name": "email",
    "provider": "google",
    "limit": 20
  }
}
```

**Response 200:**

```json
{
  "request_id": "...",
  "success": true,
  "message": "Processed 2 unread email(s) via google.",
  "data": {},
  "error": null,
  "actions": []
}
```

**Response 400:** invalid JSON or request shape

**Response 500:** unexpected internal failure

### POST /heartbeat

Run heartbeat actions for provided users.

**Request:**

```json
{
  "user_ids": ["123456789", "987654321"]
}
```

**Response 200:**

```json
{
  "actions": [
    {
      "skill_name": "gmail",
      "action_type": "send_message",
      "user_id": "123456789",
      "data": {
        "message": "Email digest is ready."
      },
      "priority": 3
    }
  ]
}
```

Actions are returned in descending priority order (higher number first).

---

## OAuth Endpoints

### GET /oauth/{provider}/authorize?user_id=<id>

Create an OAuth authorization URL for configured providers.

**Response 200:**

```json
{
  "ok": true,
  "provider": "google",
  "user_id": 123456789,
  "auth_url": "https://accounts.google.com/...",
  "state": "..."
}
```

### GET /oauth/{provider}/callback

Provider callback endpoint.

**Response 200:**

```json
{
  "ok": true,
  "provider": "google",
  "user_id": 123456789,
  "account_email": "user@example.com",
  "account_id": 42
}
```

### GET /gmail/callback

Backward-compatible alias for the Google provider callback
(`GET /oauth/{provider}/callback` with `provider=google`).

---

## Bridge Ingest

### POST /bridge/v1/tenants/{tenant_id}/messaging/ingest

Bridge-only tenant messaging ingest endpoint.

- Requires `X-API-Secret`.
- Requires signed bridge headers:
  - `X-Bridge-Timestamp`
  - `X-Bridge-Nonce`
  - `X-Bridge-Signature`
- Rejects replayed nonce values.

Tenant-admin alias for bridge ingest is available under
`/admin/tenants/{tenant_id}/messaging/ingest` and is bridge-signature
authenticated (no admin actor envelope).

Internal tenant messaging management is available under
`/admin/tenants/{tenant_id}/messaging/*`, including provider config, chat
policies, message listing, and queued send operations.

---

## Announcement Control Plane

Internal announcement producers should emit events via the announcement API.

### POST /announcements/events

Emit one announcement event and receive an ingestion receipt.

### POST /announcements/events/batch

Emit multiple announcement events in one request (for spool replay or bulk ingest).

### GET /announcements/users/{user_id}/preferences

Read effective announcement preferences for one user.

### PUT /announcements/users/{user_id}/preferences

Update announcement preferences for one user.

### POST /announcements/dispatch/flush

Trigger one manual dispatch tick for due deliveries.

---

## Worker Bridge Control Plane

Worker endpoints are intended for registered sub-worker nodes and are
authenticated with worker session credentials (not `X-API-Secret`).

Signed worker request headers:

- `Authorization: Bearer <worker-session-token>`
- `X-Worker-Session-Id`
- `X-Worker-Timestamp`
- `X-Worker-Nonce`
- `X-Worker-Signature`

### GET /worker/v1/health

Worker bridge health probe.

### POST /worker/v1/bootstrap

Bootstrap a worker node session.

- Requires `X-Worker-Bootstrap-Secret`.
- Returns initial worker session credentials and expiry.

### POST /worker/v1/nodes/register

Complete worker registration and optionally rotate session credentials.

### POST /worker/v1/nodes/{node_id}/heartbeat

Submit node heartbeat and health/capability metadata.

### POST /worker/v1/nodes/{node_id}/jobs/claim

Claim next eligible worker job based on capability policy.

### POST /worker/v1/nodes/{node_id}/jobs/{job_id}/result

Submit terminal status and artifacts for a claimed worker job.

---

## Tenant Admin Worker Operator API

Tenant-admin worker control routes are available for trusted internal callers
using `X-API-Secret` plus signed admin actor headers.

### GET /admin/tenants/{tenant_id}/workers/nodes

List registered worker nodes for one tenant.

### GET /admin/tenants/{tenant_id}/workers/nodes/{node_id}

Get one tenant worker node.

### POST /admin/tenants/{tenant_id}/workers/nodes/{node_id}/quarantine

Quarantine a worker node and optionally attach metadata.

### POST /admin/tenants/{tenant_id}/workers/nodes/{node_id}/unquarantine

Restore a quarantined worker node to active status.

### PUT /admin/tenants/{tenant_id}/workers/nodes/{node_id}/capabilities

Replace allowlisted capabilities for one worker node.

### GET /admin/tenants/{tenant_id}/workers/jobs

List worker jobs (optional filters: `status`, `node_id`, `plan_id`, `limit`).

### GET /admin/tenants/{tenant_id}/workers/jobs/{job_id}

Get one worker job.

### POST /admin/tenants/{tenant_id}/workers/jobs/{job_id}/retry

Expire/reset a worker job and re-queue plan continuation.

### POST /admin/tenants/{tenant_id}/workers/jobs/{job_id}/cancel

Cancel a worker job and mark the associated step/plan blocked/failed.

### GET /admin/tenants/{tenant_id}/workers/events

List worker lifecycle/job events (optional filters: `node_id`, `job_id`, `limit`).

### GET /admin/tenants/{tenant_id}/workers/messaging/grants

List scoped worker messaging grants (filters: `node_id`, `provider`, `chat_id`,
`include_expired`, `include_revoked`, `limit`).

### PUT /admin/tenants/{tenant_id}/workers/nodes/{node_id}/messaging/grants/{provider}/{chat_id}

Create/update one TTL-limited worker messaging grant for a specific node/chat with
permission scope (`allow_read`, `allow_send`) and optional `redacted_payload`.

### DELETE /admin/tenants/{tenant_id}/workers/messaging/grants/{grant_id}

Revoke one worker messaging grant immediately.

---

## User Management

### GET /users

List users, optional `role` query filter.

### POST /users

Add a user.

**Request:**

```json
{
  "user_id": "123456789",
  "role": "user",
  "added_by": "987654321"
}
```

**Response 201:**

```json
{
  "ok": true
}
```

### DELETE /users/{user_id}?removed_by=<id>

Remove a user.

### PATCH /users/{user_id}/role

Change a user role.

**Request:**

```json
{
  "role": "admin",
  "changed_by": "987654321"
}
```

**Response 200:**

```json
{
  "ok": true
}
```

### GET /users/audit?limit=<n>

Retrieve recent RBAC/settings audit entries.

---

## Runtime Settings

### GET /settings

List runtime settings, optional `namespace` filter.

### GET /settings/{namespace}/{key}

Read one setting.

**Response 200:**

```json
{
  "namespace": "security",
  "key": "block_threshold",
  "value": 0.7
}
```

### PUT /settings/{namespace}/{key}

Create/update one setting.

**Request:**

```json
{
  "value": 0.7,
  "changed_by": "123456789",
  "data_type": "float"
}
```

**Response 200:**

```json
{
  "ok": true
}
```

### DELETE /settings/{namespace}/{key}?deleted_by=<id>

Delete one runtime override.

**Response 200:**

```json
{
  "ok": true,
  "existed": true
}
```

---

## Runtime Secrets

### GET /secrets

List secret metadata only (never returns secret values).

### PUT /secrets/{name}

Set one encrypted secret.

**Request:**

```json
{
  "value": "secret-value",
  "changed_by": "123456789",
  "description": "Google OAuth client secret"
}
```

**Response 200:**

```json
{
  "ok": true
}
```

### DELETE /secrets/{name}?deleted_by=<id>

Delete one stored secret.

**Response 200:**

```json
{
  "ok": true,
  "existed": true
}
```

---

## Error Model

| Status | Meaning | Typical shape |
|---|---|---|
| 400 | Invalid input | `{"error":"..."}` |
| 401 | Missing/invalid `X-API-Secret` | `{"error":"Unauthorized"}` |
| 403 | Permission/role failure | `{"ok":false,"error":"..."}` |
| 404 | Resource not found | `{"error":"..."}` |
| 500 | Internal server error | `{"error":"Internal server error"}` |
| 501 | Feature not configured | `{"error":"... not configured"}` |

---

## Related Docs

- [Public API Reference](public-api-reference.md)
- [Skills Framework](skills-framework.md)
- [Architecture](architecture.md)
