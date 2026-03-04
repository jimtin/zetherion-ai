# Zetherion Upstream API Reference (Internal)

## Overview

The upstream API runs on port `8443` and is consumed by the CGS gateway.
It exposes tenant-scoped session/chat/document/reporting capabilities under `/api/v1`,
plus optional tenant-scoped YouTube endpoints.

**Internal Base URL:** `http://<host>:8443/api/v1`

This API is distinct from the internal Skills API (`:8080`) and is not the public
client contract. External clients must integrate through CGS `/service/ai/v1`.

Maintenance note (2026-03-04):
- Internal document upload route parsing/typing hardening was applied.
- No public API contract changes were introduced.
- Zetherion-only boundary recovery removed in-repo CGS website/UI assets; upstream API behavior remains unchanged.

### Exposure Policy (Authoritative)

- Zetherion `/api/v1` is upstream-only.
- Direct client/browser access is not supported.
- CGS `/service/ai/v1` is the only public API surface for client integrations.

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
