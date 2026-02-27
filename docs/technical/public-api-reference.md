# Public API Reference

## Overview

The Public API runs on port `8443` and is intended for external applications.
It exposes tenant-scoped session and chat endpoints under `/api/v1`, plus
optional tenant-scoped YouTube endpoints.

**Base URL:** `http://<host>:8443/api/v1`

This API is distinct from the internal Skills API (`:8080`).

---

## Authentication

The API uses two auth modes:

1. `X-API-Key` for tenant control-plane calls (sessions, release markers, and YouTube routes)
2. `Authorization: Bearer zt_sess_...` for session-scoped calls (chat and analytics)

### API Key auth

Provide a tenant API key for non-chat routes:

```http
X-API-Key: sk_live_...
```

### Session token auth

Session tokens are JWTs prefixed with `zt_sess_` and default to 24-hour expiry.
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

Public health probe. No authentication required.

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

**Headers:** `X-API-Key`

**Request:**

```json
{
  "external_user_id": "user_123",
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
  "created_at": "2026-02-24T18:00:00+00:00",
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
  "created_at": "2026-02-24T18:00:00+00:00",
  "updated_at": "2026-02-24T18:05:00+00:00"
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

## Chat (Session Token)

### POST /api/v1/chat

Send one message and receive one assistant message.

**Headers:** `Authorization: Bearer zt_sess_...`

**Request:**

```json
{
  "message": "What changed in this release?",
  "metadata": {
    "channel": "web"
  }
}
```

**Response 200:**

```json
{
  "message_id": "...",
  "session_id": "...",
  "role": "assistant",
  "content": "...",
  "created_at": "2026-02-24T18:10:00+00:00",
  "model": "..."
}
```

### POST /api/v1/chat/stream

Stream assistant output via Server-Sent Events.

**Headers:** `Authorization: Bearer zt_sess_...`

Event payloads:

- `{"type":"token","content":"..."}` repeated
- `{"type":"done","message_id":"...","model":"..."}` final

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

### GET /api/v1/analytics/recommendations

List recommendation candidates for the current tenant/session context.

Query params:

- `status` (optional)
- `limit` (default `50`, max `200`)

### POST /api/v1/analytics/recommendations/{recommendation_id}/feedback

Attach feedback to one recommendation.

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
| 401 | Missing/invalid auth header or expired session token | `{"error":"Invalid or expired session token"}` |
| 403 | Tenant inactive or session/tenant mismatch | `{"error":"Tenant not found or inactive"}` |
| 404 | Tenant-scoped resource not found | `{"error":"Session not found"}` |
| 429 | Tenant rate limit exceeded | `{"error":"Rate limit exceeded","retry_after":60}` |
| 503 | Service dependency unavailable | `{"error":"Service unavailable"}` |

---

## Related Docs

- [AI Agent Integration](ai-agent-integration.md)
- [Skills API Reference](api-reference.md)
- [Architecture](architecture.md)
