# Zetherion Upstream API Reference (Internal)

## Overview

The upstream API runs on port `8443` and is consumed by the CGS gateway.
It exposes tenant-scoped session/chat/document/reporting capabilities under `/api/v1`,
plus optional tenant-scoped YouTube endpoints.

**Internal Base URL:** `https://<host>:8443/api/v1`

This API is distinct from the internal Skills API (`:8080`) and is not the public
client contract. External clients must integrate through CGS `/service/ai/v1`.

Maintenance note (2026-03-19):
- Upstream session chat now accepts optional runtime selection hints on both
  `POST /api/v1/chat` and `POST /api/v1/chat/stream`:
  - `selection_mode`: `auto|prefer|lock` (defaults to `auto`)
  - `provider`
  - `model`
  - `task_type`
  - `agent_profile_id`
  - `fallback_allowed`
- Sync chat responses now expose `provider`, `usage`, and `selection` metadata.
- Streaming chat responses now emit those same fields on the final `done` SSE
  payload.
- Session bearer auth, tenant scoping, and the underlying chat route paths are
  unchanged; invalid runtime selection payloads now fail with `400`.
- Document/archive and notification route contracts remain unchanged by this
  chat runtime update.
- Previous 2026-03-10 maintenance updates:
- Segment 6 adds tenant notification routes on top of the shared announcement core:
  - `GET /api/v1/notifications/channels`
  - `POST /api/v1/notifications/events`
  - `GET /api/v1/notifications/subscriptions`
  - `POST /api/v1/notifications/subscriptions`
  - `PATCH /api/v1/notifications/subscriptions/{subscription_id}`
  - `DELETE /api/v1/notifications/subscriptions/{subscription_id}`
- Notification channels are registry-driven. Webhook delivery is always available; email delivery uses a tenant-connected Google mailbox when configured.
- `sk_test_...` keys remain intentionally blocked from `/api/v1/notifications/*`.
- Segment 3 adds tenant sandbox execution to the upstream runtime without changing the live chat wire format.
- `POST /api/v1/sessions` now accepts optional `test_profile_id`, returns `execution_mode` plus `test_profile_id`, and mints session tokens that carry `execution_mode=live|test`.
- Tenant API key auth now supports both `sk_live_...` and `sk_test_...`. In this segment, `sk_test_...` keys are intentionally limited to `POST /api/v1/sessions` plus `/api/v1/test/*`.
- Added deterministic sandbox control routes:
  - `GET /api/v1/test/profiles`
  - `POST /api/v1/test/profiles`
  - `GET /api/v1/test/profiles/{profile_id}`
  - `PATCH /api/v1/test/profiles/{profile_id}`
  - `DELETE /api/v1/test/profiles/{profile_id}`
  - `GET /api/v1/test/profiles/{profile_id}/rules`
  - `POST /api/v1/test/profiles/{profile_id}/rules`
  - `PATCH /api/v1/test/profiles/{profile_id}/rules/{rule_id}`
  - `DELETE /api/v1/test/profiles/{profile_id}/rules/{rule_id}`
  - `POST /api/v1/test/profiles/{profile_id}/preview`
- Test-mode session chat and analytics remain tenant-scoped but are tagged `execution_mode=test`; they skip CRM extraction and tenant-derived recommendation/funnel writes by default.
- Segment 2 tenant conversational runtime still lets `POST /api/v1/sessions` accept an optional `memory_subject_id`, derives it from `external_user_id` when omitted, and returns tenant-local conversation metadata (`memory_subject_id`, `conversation_summary`) on session reads.
- `POST /api/v1/chat` and `POST /api/v1/chat/stream` still assemble tenant-scoped context from the active session summary plus durable tenant subject memories. That context remains inside `tenant_raw`; no owner-personal memory is shared with the public API runtime.
- Previous 2026-03-05 data-plane isolation work added owner-vs-tenant Qdrant routing,
  scoped object-storage prefixes, additive PostgreSQL isolation schemas, and
  owner-vs-tenant encryption domains behind the upstream runtime.
- Added tenant messaging upstream routes:
  - `GET /api/v1/messaging/chats`
  - `GET /api/v1/messaging/messages`
  - `POST /api/v1/messaging/messages/{chat_id}/send`
- Added document archive/delete and restore upstream routes:
  - `DELETE /api/v1/documents/{document_id}`
  - `POST /api/v1/documents/{document_id}/restore`
- Added `include_archived` query support for document list.
- Document lifecycle statuses now include archive states (`archiving|archived|purged`).
- Archive/purge job processing now runs via an upstream background maintenance loop.
- `POST /api/v1/rag/query` excludes `archiving|archived|purged` documents before context assembly.

### Exposure Policy (Authoritative)

- Zetherion `/api/v1` is upstream-only.
- Direct client/browser access is not supported.
- CGS `/service/ai/v1` is the only public API surface for client integrations.

---

## Authentication

The API uses two auth modes:

1. `X-API-Key` for tenant control-plane calls (sessions, sandbox controls, notifications, release markers, and YouTube routes)
2. `Authorization: Bearer zt_sess_...` for session-scoped calls (chat and analytics)

### API Key auth

Provide a tenant API key for non-chat routes:

```http
X-API-Key: sk_live_...
```

Supported key families in this segment:

- `sk_live_...`
  - full upstream API-key surface
- `sk_test_...`
  - only `POST /api/v1/sessions` and `/api/v1/test/*`
  - intended to create test-mode sessions and manage deterministic sandbox profiles/rules

### Session token auth

Session tokens are JWTs prefixed with `zt_sess_` and default to 24-hour expiry.
They now carry `execution_mode`, which must match the stored session mode.
Use them for:

- `POST /api/v1/chat`
- `POST /api/v1/chat/stream`
- `GET /api/v1/chat/history`
- `POST /api/v1/analytics/events`
- `POST /api/v1/analytics/replay/chunks`
- `GET /api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}`
- `POST /api/v1/analytics/sessions/end`
- `GET /api/v1/analytics/recommendations`
- `POST /api/v1/analytics/recommendations/{recommendation_id}/feedback`

```http
Authorization: Bearer zt_sess_...
```

---

## Health

### GET /api/v1/health

Upstream health probe. No authentication required.

**Response 200:**

```json
{
  "status": "healthy",
  "service": "public-api"
}
```

---

## Sessions (API Key)

### POST /api/v1/sessions

Create a tenant session and return a session token.

Use `memory_subject_id` when the client already has a stable tenant-local user identifier.
If it is omitted, the runtime derives it from `external_user_id` when available.
Use `test_profile_id` only when creating a session with `sk_test_...`; if omitted, the tenant default sandbox profile is used when one exists.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "external_user_id": "user_123",
  "memory_subject_id": "user_123",
  "test_profile_id": "sandbox-profile-1",
  "metadata": {
    "source": "web"
  }
}
```

**Response 201:**

```json
{
  "session_id": "...",
  "tenant_id": "...",
  "external_user_id": "user_123",
  "memory_subject_id": "user_123",
  "execution_mode": "test",
  "test_profile_id": "sandbox-profile-1",
  "conversation_summary": "",
  "created_at": "2026-02-24T18:00:00+00:00",
  "expires_at": "2026-02-25T18:00:00+00:00",
  "session_token": "zt_sess_..."
}
```

### GET /api/v1/sessions/{session_id}

Get session metadata for the authenticated tenant.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "session_id": "...",
  "tenant_id": "...",
  "external_user_id": "user_123",
  "memory_subject_id": "user_123",
  "execution_mode": "test",
  "test_profile_id": "sandbox-profile-1",
  "conversation_summary": "Recent user requests: asked about bathroom pricing",
  "created_at": "2026-02-24T18:00:00+00:00",
  "last_active": "2026-02-24T18:05:00+00:00",
  "expires_at": "2026-02-25T18:00:00+00:00"
}
```

### DELETE /api/v1/sessions/{session_id}

Delete a session for the authenticated tenant.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "ok": true
}
```

---

## Sandbox Control (API Key)

These routes manage tenant-scoped sandbox profiles and deterministic response rules.
They are the only new API-key routes available to `sk_test_...` keys in this segment.

Sandbox responses keep the same live chat wire format but never call live providers.
When no rule matches, the runtime falls back to deterministic built-in presets such as
`pricing`, `availability`, `booking`, `urgent_support`, `follow_up`, or `default`.

### GET /api/v1/test/profiles

List sandbox profiles for the authenticated tenant.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "profiles": [
    {
      "profile_id": "...",
      "tenant_id": "...",
      "name": "Default sandbox",
      "description": "Primary test profile",
      "is_default": true,
      "is_active": true,
      "created_at": "2026-03-09T00:00:00+00:00",
      "updated_at": "2026-03-09T00:00:00+00:00"
    }
  ],
  "count": 1
}
```

### POST /api/v1/test/profiles

Create one sandbox profile.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "name": "Default sandbox",
  "description": "Primary test profile",
  "is_default": true
}
```

### GET /api/v1/test/profiles/{profile_id}

Fetch one sandbox profile.

**Headers:** `X-API-Key`

### PATCH /api/v1/test/profiles/{profile_id}

Patch one sandbox profile.

**Headers:** `X-API-Key`

Supported fields:

- `name`
- `description`
- `is_default`
- `is_active`

### DELETE /api/v1/test/profiles/{profile_id}

Delete one sandbox profile.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "ok": true
}
```

### GET /api/v1/test/profiles/{profile_id}/rules

List rules for one sandbox profile.

**Headers:** `X-API-Key`

### POST /api/v1/test/profiles/{profile_id}/rules

Create one sandbox rule.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "priority": 10,
  "method": "POST",
  "route_pattern": "/api/v1/chat*",
  "enabled": true,
  "match": {
    "body_contains": ["pricing"],
    "metadata_contains": {
      "channel": "web"
    },
    "conversation_state": "ongoing"
  },
  "response": {
    "preset_id": "pricing",
    "json_body": {
      "content": "Simulated pricing reply",
      "model": "sandbox-simulated"
    }
  },
  "latency_ms": 50
}
```

Supported `match` keys:

- `body_contains`
- `metadata_contains`
- `tool_name`
- `conversation_state` (`new|returning|ongoing`)

Supported `response` keys:

- `preset_id`
- `json_body`
- `sse_events`
- `error`

### PATCH /api/v1/test/profiles/{profile_id}/rules/{rule_id}

Patch one sandbox rule.

**Headers:** `X-API-Key`

### DELETE /api/v1/test/profiles/{profile_id}/rules/{rule_id}

Delete one sandbox rule.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "ok": true
}
```

### POST /api/v1/test/profiles/{profile_id}/preview

Preview rule matching and deterministic output for a hypothetical request without creating a session or spending inference budget.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "route": "/api/v1/chat",
  "method": "POST",
  "body": {
    "message": "Can you help with pricing?",
    "metadata": {
      "channel": "web"
    }
  },
  "session": {
    "memory_subject_id": "visitor-42",
    "conversation_summary": ""
  },
  "history": [
    {
      "role": "user",
      "content": "I need a quote"
    }
  ]
}
```

**Response 200:**

```json
{
  "profile_id": "...",
  "matched_rule_id": "...",
  "preset_id": "pricing",
  "latency_ms": 50,
  "response": {
    "preset_id": "pricing",
    "json_body": {
      "content": "Simulated pricing reply",
      "model": "sandbox-simulated"
    }
  },
  "chat_result": {
    "content": "Simulated pricing reply",
    "model": "sandbox-simulated",
    "metadata": {
      "sandbox_profile_id": "...",
      "sandbox_rule_id": "...",
      "sandbox_preset_id": "pricing"
    }
  },
  "stream_events": [
    {
      "type": "token",
      "content": "Simulated pricing reply"
    },
    {
      "type": "done",
      "model": "sandbox-simulated"
    }
  ]
}
```

---

## Notifications (API Key)

Tenant notification routes are live-key only in this segment. `sk_test_...` keys remain
restricted to `/api/v1/sessions` plus `/api/v1/test/*`.

Webhook delivery is always available. Email delivery is available when the tenant has at
least one connected Google mailbox; notifications use that mailbox to send outbound email.

### GET /api/v1/notifications/channels

List the public notification channels exposed by the shared announcement registry.

**Headers:** `X-API-Key`

**Response 200:**

```json
{
  "channels": [
    {
      "channel_id": "webhook",
      "display_name": "Webhook",
      "description": "POST a structured notification payload to a tenant webhook endpoint.",
      "config_fields": ["webhook_url"],
      "status": "available",
      "metadata": {}
    },
    {
      "channel_id": "email",
      "display_name": "Email",
      "description": "Send a notification email via a tenant-connected Google mailbox.",
      "config_fields": ["email", "account_id"],
      "status": "available",
      "metadata": {
        "account_ids": ["acct-1"]
      }
    }
  ],
  "count": 2
}
```

### GET /api/v1/notifications/subscriptions

List tenant notification subscriptions.

**Headers:** `X-API-Key`

### POST /api/v1/notifications/subscriptions

Create one tenant notification subscription.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "source_app": "checkout",
  "event_types": ["order.failed"],
  "channel_id": "webhook",
  "channel_config": {
    "webhook_url": "https://example.com/hooks/orders"
  },
  "template": {
    "title": "Checkout alert: {title}",
    "body": "{body}\n\nPayload: {payload_json}"
  },
  "status": "active"
}
```

**Response 201:**

```json
{
  "subscription_id": "...",
  "tenant_id": "...",
  "source_app": "checkout",
  "event_types": ["order.failed"],
  "channel_id": "webhook",
  "channel_config": {
    "webhook_url": "https://example.com/hooks/orders"
  },
  "template": {
    "title": "Checkout alert: {title}",
    "body": "{body}\n\nPayload: {payload_json}"
  },
  "status": "active",
  "created_at": "2026-03-10T10:00:00+00:00",
  "updated_at": "2026-03-10T10:00:00+00:00"
}
```

### PATCH /api/v1/notifications/subscriptions/{subscription_id}

Patch one subscription. Channel type is immutable in this segment; patch event types,
source app, channel config, template, or status.

**Headers:** `X-API-Key`

### DELETE /api/v1/notifications/subscriptions/{subscription_id}

Delete one subscription.

**Headers:** `X-API-Key`

### POST /api/v1/notifications/events

Publish one tenant event and fan it out to all matching subscriptions through the shared
announcement core.

**Headers:** `X-API-Key`

**Request:**

```json
{
  "source_app": "checkout",
  "event_type": "order.failed",
  "severity": "high",
  "title": "Payment failed",
  "body": "Card charge was declined.",
  "payload": {
    "order_id": "ord-1"
  },
  "dedupe_key": "order:ord-1",
  "occurred_at": "2026-03-10T10:01:00+00:00"
}
```

**Response 202:**

```json
{
  "matched_subscriptions": 1,
  "deliveries": [
    {
      "subscription_id": "sub-1",
      "channel_id": "webhook",
      "receipt": {
        "status": "scheduled",
        "event_id": "evt-1",
        "scheduled_for": "2026-03-10T10:01:00+00:00",
        "reason_code": "recipient_channel_immediate_default"
      }
    }
  ]
}
```

---

## Chat (Session Token)

### POST /api/v1/chat

Send one message and receive one assistant message.

The runtime now prepends tenant-scoped context from:
- the session's rolling `conversation_summary`
- durable memories linked to the session's `memory_subject_id`

Those context notes remain in `tenant_raw` and are never hydrated from owner-personal memory.
When the session token carries `execution_mode=test`, chat uses the sandbox runtime instead of live inference but returns the same response shape.

**Headers:** `Authorization: Bearer zt_sess_...`

**Request:**

```json
{
  "message": "What changed in this release?",
  "selection_mode": "prefer",
  "provider": "openai",
  "model": "gpt-4o",
  "task_type": "conversation",
  "agent_profile_id": "support-default",
  "fallback_allowed": true,
  "metadata": {
    "channel": "web"
  }
}
```

Request field notes:
- `selection_mode=auto` lets the runtime choose the provider/model.
- `selection_mode=prefer` tries the requested route first and can fall back
  when `fallback_allowed=true`.
- `selection_mode=lock` requires the requested provider/model exactly and
  disables fallback.
- `provider` or `model` is required when `selection_mode` is `prefer` or `lock`.
- If only `model` is supplied, the runtime accepts it only when it maps to a
  known runtime default provider.

**Response 200:**

```json
{
  "message_id": "...",
  "session_id": "...",
  "role": "assistant",
  "content": "...",
  "created_at": "2026-02-24T18:10:00+00:00",
  "model": "gpt-4o",
  "provider": "openai",
  "usage": {
    "input_tokens": 321,
    "output_tokens": 118,
    "total_tokens": 439,
    "estimated_cost_usd": 0.0047,
    "model": "gpt-4o",
    "provider": "openai"
  },
  "selection": {
    "selection_mode": "prefer",
    "requested_provider": "openai",
    "requested_model": "gpt-4o",
    "task_type": "conversation",
    "agent_profile_id": "support-default",
    "fallback_allowed": true,
    "effective_provider": "openai",
    "effective_model": "gpt-4o"
  }
}
```

### POST /api/v1/chat/stream

Stream assistant output via Server-Sent Events.

**Headers:** `Authorization: Bearer zt_sess_...`

Request body matches `POST /api/v1/chat`, including the optional runtime
selection fields.

Event payloads:

- `{"type":"token","content":"..."}` repeated
- `{"type":"done","message_id":"...","model":"...","provider":"...","usage":{...},"selection":{...}}` final

In test mode, the final `model` is usually `sandbox-simulated` unless the matching sandbox rule overrides it. Live and test mode both keep the same final `done` envelope shape.

### GET /api/v1/chat/history

Retrieve conversation history for the authenticated session.

**Headers:** `Authorization: Bearer zt_sess_...`

**Query params:**

- `limit` (default `50`, max `100`)
- `before` (optional cursor)

**Response 200:**

```json
{
  "session_id": "...",
  "messages": [
    {
      "message_id": "...",
      "role": "user",
      "content": "Hello",
      "created_at": "2026-02-24T18:09:00+00:00"
    }
  ]
}
```

---

## Analytics (Session Token)

These routes power web behavior analytics and app-watcher recommendation feedback.
All analytics routes require `Authorization: Bearer zt_sess_...`.
When the session token is test-mode, analytics rows are still tagged to the tenant/session but are excluded from funnel, recommendation, CRM, and other tenant-derived writes in this segment.

### POST /api/v1/analytics/events

Ingest batched browser/app events for the active session.

**Response 201:**

```json
{
  "ok": true,
  "web_session_id": "...",
  "ingested": 12,
  "replay_enabled": true,
  "replay_sampled": true
}
```

### POST /api/v1/analytics/replay/chunks

Upload replay chunk metadata when replay capture is enabled and consented.

**Response 201 (accepted):**

```json
{
  "ok": true,
  "accepted": true,
  "chunk": {}
}
```

When replay object storage is configured, you can optionally include `chunk_base64`
in the request body to upload the actual chunk payload alongside metadata.

### GET /api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}

Get replay chunk metadata. Add `?include_data=true` to include `data_base64`
when object storage is configured.

### POST /api/v1/analytics/sessions/end

Close a tracked web session, compute behavior summary, and generate recommendations.

Test-mode behavior:

- still returns a session summary
- includes `execution_mode: "test"` in the response
- does not persist recommendations, funnel rows, CRM interactions, or contact custom-field updates

### GET /api/v1/analytics/recommendations

List recommendation candidates for the current tenant/session context.

Query params:

- `status` (optional)
- `limit` (default `50`, max `200`)

Test-mode behavior:

- returns `{"recommendations":[],"count":0}`

### POST /api/v1/analytics/recommendations/{recommendation_id}/feedback

Attach feedback to one recommendation.

Test-mode behavior:

- returns `403` because sandbox sessions cannot mutate recommendation feedback state

### GET /api/v1/analytics/recommendations/tenant

Tenant-level recommendation list for reporting/control-plane use.

**Headers:** `X-API-Key`

Query params:

- `status` (optional)
- `limit` (default `50`, max `200`)

### GET /api/v1/analytics/funnel

Tenant-level funnel rows for reporting/control-plane use.

**Headers:** `X-API-Key`

Query params:

- `metric_date` (optional, `YYYY-MM-DD`)
- `limit` (default `200`, max `500`)

---

## CRM Read API (API Key)

Tenant-scoped CRM read endpoints for reporting/control-plane use.

### GET /api/v1/crm/contacts

List contacts for the authenticated tenant.

**Headers:** `X-API-Key`

Query params:

- `email` (optional exact-match filter)
- `limit` (default `50`, max `200`)

### GET /api/v1/crm/interactions

List interactions for the authenticated tenant.

**Headers:** `X-API-Key`

Query params:

- `contact_id` (optional)
- `session_id` (optional)
- `interaction_type` (optional)
- `limit` (default `50`, max `200`)

---

## Messaging API (API Key + Trust Policy)

Tenant-scoped messaging routes for policy-governed read/send operations.

### GET /api/v1/messaging/chats

List tenant messaging chat policy snapshots and message counters.

**Headers:** `X-API-Key`

Query params:

- `provider` (optional, default `whatsapp`)
- `include_inactive` (optional, default `true`)
- `limit` (default `200`, max `500`)

### GET /api/v1/messaging/messages

List stored encrypted/decrypted messaging records for one chat.

**Headers:** `X-API-Key`

Query params:

- `chat_id` (required)
- `provider` (optional, default `whatsapp`)
- `direction` (optional, `inbound|outbound`)
- `limit` (default `200`, max `500`)

Policy behavior:

- Evaluates `messaging.read`.
- Non-allowlisted chats return `403` with `code=AI_MESSAGING_CHAT_NOT_ALLOWLISTED`.

### POST /api/v1/messaging/messages/{chat_id}/send

Queue an outbound message action for a policy-allowlisted chat.

**Headers:** `X-API-Key`

Request:

```json
{
  "provider": "whatsapp",
  "text": "Confirming the deployment is complete.",
  "metadata": {
    "source": "tenant-ui"
  },
  "explicitly_elevated": false
}
```

Policy behavior:

- Evaluates `messaging.send`.
- Returns `409` `AI_APPROVAL_REQUIRED` when high-risk send requires approval.
- Returns `403` when trust tier/allowlist rules deny the send.

**Response 202:**

```json
{
  "ok": true,
  "queued_action": {},
  "message": {}
}
```

---

## Release Markers (API Key)

### POST /api/v1/releases/markers

Store deployment/release markers for tenant-scoped regression analysis.

**Headers:** `X-API-Key`

Optional hardened signing headers (required when `RELEASE_MARKER_SIGNING_SECRET` is configured):

- `X-Release-Timestamp`: unix epoch seconds
- `X-Release-Nonce`: unique nonce per request
- `X-Release-Signature`: hex HMAC-SHA256 of  
  `<tenant_id>.<timestamp>.<nonce>.<raw_json_body>`

**Response 201:**

```json
{
  "ok": true,
  "marker": {}
}
```

---

## Document Intelligence API (API Key)

Tenant-scoped document upload, preview/download, indexing, and retrieval endpoints.
All routes below require `X-API-Key`.

### POST /api/v1/documents/uploads

Create an upload intent and return an `upload_id` token for completion.

**Request:**

```json
{
  "file_name": "proposal.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 481230,
  "metadata": {
    "source": "client-portal"
  }
}
```

**Response 201:**

```json
{
  "upload_id": "uuid",
  "tenant_id": "uuid",
  "status": "pending",
  "expires_at": "2026-02-28T22:00:00+00:00",
  "complete_url": "/api/v1/documents/uploads/{upload_id}/complete"
}
```

### POST /api/v1/documents/uploads/{upload_id}/complete

Complete upload with either:
- `application/json` body containing `file_base64`, or
- `multipart/form-data` with a `file` part and optional `metadata`.

**JSON request:**

```json
{
  "file_base64": "<base64-bytes>",
  "metadata": {
    "customer": "acme"
  }
}
```

**Response 201:** returns created document metadata record.

### GET /api/v1/documents

List tenant documents.

Query params:
- `limit` (default `50`, max `200`)
- `include_archived` (default `false`; accepts `true|false|1|0|yes|no|on|off`)
  - when false, omits `archiving|archived|purged`
  - when true, includes all lifecycle states

### GET /api/v1/documents/{document_id}

Get one tenant document metadata/status row.

### GET /api/v1/documents/{document_id}/preview

Inline preview behavior by file type:
- PDF: `application/pdf` inline stream
- DOCX: sanitized HTML preview
- fallback: extracted text preview as HTML

### GET /api/v1/documents/{document_id}/download

Raw file download stream with attachment disposition.

### POST /api/v1/documents/{document_id}/index

Re-index an existing document into vector collection `tenant_documents`.

### DELETE /api/v1/documents/{document_id}

Schedule document archive (delete-intent) asynchronously.

Behavior:
- transitions active document to `archiving`
- creates a `document_archive_jobs` queue record
- worker loop claims archive jobs, deletes vectors, and marks document `archived`
- purge loop removes bytes/vectors after retention and marks document `purged`
- returns `202 Accepted`
- idempotent when already `archiving|archived|purged`

Optional request body:

```json
{
  "reason": "user-request"
}
```

Optional query:
- `reason=<text>` (used if JSON body is not provided)

### POST /api/v1/documents/{document_id}/restore

Restore an archived document and re-run indexing.

Behavior:
- allowed only from `archived`
- transitions to processing and re-indexes through normal ingestion/index flow
- returns updated document record

Errors:
- `404` unknown document
- `409` for invalid lifecycle (`purged` or non-archived states)

### POST /api/v1/rag/query

Query indexed tenant document chunks and generate an answer with citations.

**Request:**

```json
{
  "query": "What implementation risks are called out in the proposal?",
  "top_k": 6,
  "provider": "groq",
  "model": "llama-3.3-70b-versatile"
}
```

**Response 200:**

```json
{
  "answer": "...",
  "citations": [
    {
      "document_id": "uuid",
      "file_name": "proposal.pdf"
    }
  ],
  "provider": "groq",
  "model": "llama-3.3-70b-versatile"
}
```

### GET /api/v1/models/providers

Return retrieval provider/model catalog for UI selectors.

**Response 200:**

```json
{
  "providers": ["groq", "openai", "anthropic"],
  "defaults": {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-5.2",
    "anthropic": "claude-sonnet-4-5-20250929"
  },
  "allowed_models": ["..."]
}
```

### Document Lifecycle States

- `uploaded`
- `processing`
- `indexed`
- `failed`
- `archiving`
- `archived`
- `purged`

---

## YouTube API (API Key)

YouTube routes are registered only when YouTube storage/skills initialize.
All routes require `X-API-Key` and enforce tenant ownership checks.

Base prefix: `/api/v1/youtube`

### Channels

- `POST /api/v1/youtube/channels`
- `GET /api/v1/youtube/channels`

### Ingestion

- `POST /api/v1/youtube/channels/{channel_id}/videos`
- `POST /api/v1/youtube/channels/{channel_id}/comments`
- `POST /api/v1/youtube/channels/{channel_id}/stats`
- `POST /api/v1/youtube/channels/{channel_id}/documents`

### Intelligence

- `POST /api/v1/youtube/channels/{channel_id}/intelligence/analyze`
- `GET /api/v1/youtube/channels/{channel_id}/intelligence`
- `GET /api/v1/youtube/channels/{channel_id}/intelligence/history`

### Management

- `GET /api/v1/youtube/channels/{channel_id}/management`
- `POST /api/v1/youtube/channels/{channel_id}/management/configure`
- `GET /api/v1/youtube/channels/{channel_id}/management/replies`
- `PATCH /api/v1/youtube/channels/{channel_id}/management/replies/{reply_id}`
- `GET /api/v1/youtube/channels/{channel_id}/management/tags`
- `GET /api/v1/youtube/channels/{channel_id}/management/health`

### Strategy

- `POST /api/v1/youtube/channels/{channel_id}/strategy/generate`
- `GET /api/v1/youtube/channels/{channel_id}/strategy`
- `GET /api/v1/youtube/channels/{channel_id}/strategy/history`

### Assumptions

- `GET /api/v1/youtube/channels/{channel_id}/assumptions`
- `PATCH /api/v1/youtube/channels/{channel_id}/assumptions/{assumption_id}`
- `POST /api/v1/youtube/channels/{channel_id}/assumptions/validate`

---

## Limits and Validation

- Tenant rate limit is token-bucket based, default `60` requests/minute.
- Chat message size max is `10,000` characters.
- Chat history max page size is `100`.
- Over limit responses return `429` with a retry hint.

---

## Error Responses

| Status | Meaning | Example |
|---|---|---|
| 400 | Invalid request body or parameters | `{"error":"Invalid JSON body"}` |
| 413 | Upload payload exceeds configured limit | `{"error":"Upload too large ... "}` |
| 401 | Missing/invalid auth header or expired session token | `{"error":"Invalid or expired session token"}` |
| 403 | Tenant inactive or session/tenant mismatch | `{"error":"Tenant not found or inactive"}` |
| 404 | Tenant-scoped resource not found | `{"error":"Session not found"}` |
| 429 | Tenant rate limit exceeded | `{"error":"Rate limit exceeded","retry_after":60}` |
| 500 | Unexpected processing failure | `{"error":"...internal..."}` |
| 503 | Service dependency unavailable | `{"error":"Service unavailable"}` |

---

## Related Docs

- [AI Agent Integration](ai-agent-integration.md)
- [Skills API Reference](api-reference.md)
- [Architecture](architecture.md)
- [API Auth Matrix](api-auth-matrix.md)
- [API Error Matrix](api-error-matrix.md)
- [OpenAPI Contracts](openapi-contracts.md)
