# CGS Public API Endpoint Build Specification

## 1. Purpose

This document is the implementation handoff for Catalyst Group Solutions (CGS) to build and wire the public app-facing API endpoints that integrate with Zetherion AI through the CGS gateway layer.

It is based on the code currently implemented in this repository (not only draft design docs).

## Maintenance Note (2026-03-04)

- Added internal blog publish adapter route: `POST /service/ai/v1/internal/blog/publish`.
- Added gateway envelope field `error.retryable` for all failure responses.
- Added CGS route-doc parity gate for lifecycle/reporting/admin/blog surfaces.
- Added tenant email admin control-plane routes for OAuth app setup, mailbox linking, sync, critical message triage, calendar binding, and insight reindex.
- Zetherion-only boundary recovery removed in-repo CGS website/UI assets; endpoint contracts stay CGS-first and unchanged.

## 2. Scope

In scope:

- CGS app-facing API contract at `/service/ai/v1`
- Auth and authorization model for CGS tokens
- Request/response envelope standard
- Idempotency behavior
- Tenant lifecycle/internal operator endpoints
- Tenant email admin control plane endpoints (operator-only)
- Tenant reporting endpoints
- SDK integration mode for CGS web observer analytics
- Deployment and readiness checklist

Out of scope:

- Re-defining Zetherion upstream `/api/v1` contracts
- Replacing Zetherion backend logic
- Frontend UI design details in CGS apps
- Direct client access to Zetherion `/api/v1`

Canonical route inventory (implemented):
- Runtime conversations + analytics + recommendations:
  - `/service/ai/v1/conversations/*`
- Document intelligence:
  - `/service/ai/v1/documents/*`
  - `/service/ai/v1/rag/query`
  - `/service/ai/v1/models/providers`
- Internal lifecycle:
  - `/service/ai/v1/internal/tenants/*`
- Internal tenant-admin:
  - `/service/ai/v1/internal/admin/tenants/{tenant_id}/*`
- Tenant reporting:
  - `/service/ai/v1/tenants/{tenant_id}/*`
- Internal publish adapter:
  - `/service/ai/v1/internal/blog/publish`

## 3. Architecture Overview

### 3.1 Runtime path

1. CGS app calls `/service/ai/v1/*`
2. CGS gateway validates CGS JWT (`Authorization: Bearer ...`) against JWKS
3. Gateway resolves `cgs_tenant_id -> zetherion_tenant_id + encrypted API key`
4. Gateway proxies to Zetherion upstream:
   - Public API (`/api/v1/...`)
   - Skills API (`/handle`) for internal tenant lifecycle actions
5. Gateway normalizes all responses into:
   - `{ request_id, data, error }`

### 3.2 Data persistence (gateway-owned)

Gateway persists and uses:

- `cgs_ai_tenants`
- `cgs_ai_conversations`
- `cgs_ai_idempotency`
- `cgs_ai_request_log`
- `cgs_ai_admin_changes`
- `cgs_ai_blog_publish_receipts`

Session tokens and API keys are encrypted at rest.

## 4. Base Path and Versioning

- Base path: `/service/ai/v1`
- Health endpoint: `GET /service/ai/v1/health` (public)
- All non-health endpoints require bearer auth

## 5. Authentication and Authorization

### 5.1 JWT verification

Gateway verifies CGS JWTs using:

- `CGS_AUTH_JWKS_URL` (required)
- `CGS_AUTH_ISSUER` (optional but recommended)
- `CGS_AUTH_AUDIENCE` (optional but recommended)

Token requirements:

- `sub` claim is required
- Tenant claim may be in `tenant_id` or `cgs_tenant_id`
- Roles may be in `roles` (string or array)
- Scopes may be in `scope` (space-separated) and/or `scopes` (array)

### 5.2 Tenant scoping

- If token includes `tenant_id` (or `cgs_tenant_id`), it must match tenant in route/body.
- Mismatch returns `403 AI_AUTH_FORBIDDEN`.
- Internal operator endpoints require elevated role/scope.

### 5.3 Internal operator access

Operator access is allowed when either is true:

- Role includes one of: `operator`, `admin`, `owner`
- Scope includes one of: `cgs:internal`, `cgs:operator`, `cgs:admin`

## 6. Common API Contract

### 6.1 Headers

Required on protected routes:

- `Authorization: Bearer <cgs_jwt>`

Optional:

- `X-Request-Id: <client_request_id>`
- `Idempotency-Key: <opaque_key>` (for mutating endpoints)

### 6.2 Response envelope

Success:

```json
{
  "request_id": "req_...",
  "data": {},
  "error": null
}
```

Failure:

```json
{
  "request_id": "req_...",
  "data": null,
  "error": {
    "code": "AI_*",
    "message": "human readable",
    "retryable": false,
    "details": {}
  }
}
```

### 6.3 Error code families

- Auth:
  - `AI_AUTH_MISSING`
  - `AI_AUTH_INVALID_TOKEN`
  - `AI_AUTH_FORBIDDEN`
- Validation/tenant/conversation:
  - `AI_BAD_REQUEST`
  - `AI_TENANT_NOT_FOUND`
  - `AI_TENANT_INACTIVE`
  - `AI_CONVERSATION_NOT_FOUND`
- Idempotency:
  - `AI_IDEMPOTENCY_CONFLICT`
- Upstream mapping:
  - `AI_UPSTREAM_401`
  - `AI_UPSTREAM_403`
  - `AI_UPSTREAM_404`
  - `AI_UPSTREAM_409`
  - `AI_UPSTREAM_429`
  - `AI_UPSTREAM_5XX`
  - `AI_UPSTREAM_ERROR`
- Internal:
  - `AI_INTERNAL_ERROR`
  - `AI_SKILLS_UPSTREAM_ERROR`

## 7. Idempotency Semantics

Applies to mutating endpoints when `Idempotency-Key` is present.

- Record uniqueness key:
  - `(cgs_tenant_id, endpoint, method, idempotency_key)`
- Request body fingerprint:
  - SHA256 of canonicalized JSON payload
- Behavior:
  - Same key + same payload => cached response replayed
  - Same key + different payload => `409 AI_IDEMPOTENCY_CONFLICT`
- Replay responses include header:
  - `X-Idempotent-Replay: true`

## 8. Endpoint Specification

### 8.1 Health

### `GET /service/ai/v1/health`

- Auth: no
- Response:

```json
{
  "status": "healthy",
  "service": "cgs-gateway"
}
```

### 8.2 Runtime Conversation Endpoints

### `POST /service/ai/v1/conversations`

Creates gateway conversation and upstream Zetherion session.

Request body:

```json
{
  "tenant_id": "tenant-a",
  "app_user_id": "app-user-1",
  "external_user_id": "ext-user-1",
  "metadata": {
    "source": "portal"
  }
}
```

Returns (201):

```json
{
  "request_id": "req_...",
  "data": {
    "conversation_id": "cgs_conv_...",
    "tenant_id": "tenant-a",
    "session_id": "uuid",
    "created_at": "iso",
    "expires_at": "iso"
  },
  "error": null
}
```

### `GET /service/ai/v1/conversations/{conversation_id}`

Returns conversation/session metadata and close status.

### `DELETE /service/ai/v1/conversations/{conversation_id}`

Closes conversation and deletes upstream session. Upstream `404` is treated as successful close.

### `POST /service/ai/v1/conversations/{conversation_id}/messages`

Request body:

```json
{
  "message": "string (1..10000 chars)",
  "metadata": {}
}
```

Returns assistant message payload from upstream `/api/v1/chat`, envelope-wrapped.

### `POST /service/ai/v1/conversations/{conversation_id}/messages/stream`

- SSE proxy to upstream `/api/v1/chat/stream`
- Content type: `text/event-stream`
- This is streamed passthrough behavior

### `GET /service/ai/v1/conversations/{conversation_id}/messages`

Query params forwarded:

- `limit`
- `before`

Mapped upstream endpoint:

- `/api/v1/chat/history`

### `POST /service/ai/v1/conversations/{conversation_id}/analytics/events`

Forwards to:

- `/api/v1/analytics/events`

### `POST /service/ai/v1/conversations/{conversation_id}/analytics/replay/chunks`

Forwards to:

- `/api/v1/analytics/replay/chunks`

### `GET /service/ai/v1/conversations/{conversation_id}/analytics/replay/chunks/{web_session_id}/{sequence_no}`

Forwards to:

- `/api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}`

Query params are passed through.

### `POST /service/ai/v1/conversations/{conversation_id}/analytics/end`

Forwards to:

- `/api/v1/analytics/sessions/end`

### `GET /service/ai/v1/conversations/{conversation_id}/recommendations`

Forwards to:

- `/api/v1/analytics/recommendations`

Query params are passed through.

### `POST /service/ai/v1/conversations/{conversation_id}/recommendations/{recommendation_id}/feedback`

Forwards to:

- `/api/v1/analytics/recommendations/{recommendation_id}/feedback`

### 8.3 Document Intelligence Endpoints

All document endpoints require bearer auth and explicit tenant-scoping.
For list/detail/preview/download/providers endpoints, `tenant_id` is required as a query param.
For mutating endpoints, `tenant_id` is required in request JSON body.

### `POST /service/ai/v1/documents/uploads`

Create upload intent (maps to `POST /api/v1/documents/uploads`).

Request:

```json
{
  "tenant_id": "tenant-a",
  "file_name": "proposal.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 123456,
  "metadata": {
    "source": "portal"
  }
}
```

Response envelope status `201` with upstream payload in `data`.

### `POST /service/ai/v1/documents/uploads/{upload_id}/complete`

Complete upload (maps to `POST /api/v1/documents/uploads/{upload_id}/complete`).

JSON request:

```json
{
  "tenant_id": "tenant-a",
  "file_base64": "<base64>",
  "metadata": {
    "customer": "acme"
  }
}
```

Multipart request (browser-friendly):

- Query param: `tenant_id=tenant-a`
- Content type: `multipart/form-data`
- Parts:
  - `file` (required binary file)
  - `metadata` (optional JSON string)

Response envelope status `201`.

### `GET /service/ai/v1/documents?tenant_id=...`

Maps to `GET /api/v1/documents`.
Response envelope contains `{documents,count}` payload.

### `GET /service/ai/v1/documents/{document_id}?tenant_id=...`

Maps to `GET /api/v1/documents/{document_id}`.
Response envelope contains document metadata and status lifecycle:
`uploaded`, `processing`, `indexed`, `failed`.

### `GET /service/ai/v1/documents/{document_id}/preview?tenant_id=...`

Binary passthrough to `GET /api/v1/documents/{document_id}/preview`.
Gateway preserves relevant content headers:
- `Content-Type`
- `Content-Disposition`
- `Cache-Control`

### `GET /service/ai/v1/documents/{document_id}/download?tenant_id=...`

Binary passthrough to `GET /api/v1/documents/{document_id}/download` with same header behavior.

### `POST /service/ai/v1/documents/{document_id}/index`

Request:

```json
{
  "tenant_id": "tenant-a"
}
```

Maps to `POST /api/v1/documents/{document_id}/index`.

### `POST /service/ai/v1/rag/query`

Request:

```json
{
  "tenant_id": "tenant-a",
  "query": "Summarize implementation risks in the proposal",
  "top_k": 6,
  "provider": "groq",
  "model": "llama-3.3-70b-versatile"
}
```

Maps to `POST /api/v1/rag/query`.
Response envelope includes `answer`, `citations`, `provider`, `model`.
Provider contract: `groq`, `openai`, `anthropic` (`claude` is accepted as an alias).

### `GET /service/ai/v1/models/providers?tenant_id=...`

Maps to `GET /api/v1/models/providers`.
Used by frontend provider/model selector UI.

### 8.4 Internal Operator Endpoints

Prefix: `/service/ai/v1/internal`

### `GET /internal/tenants`

Query:

- `include_inactive=true|false` (default false)

Returns mapped CGS tenant list.

### `POST /internal/tenants`

Creates tenant in Zetherion via Skills API intent `client_create`, then upserts mapping.

Request body:

```json
{
  "cgs_tenant_id": "tenant-a",
  "name": "Tenant A",
  "domain": "tenant-a.example",
  "config": {}
}
```

Returns (201) with `api_key` and mapping details.

### `PATCH /internal/tenants/{tenant_id}`

Updates tenant profile/config via Skills intent `client_configure` and mapping metadata.

### `POST /internal/tenants/{tenant_id}/deactivate`

Deactivates tenant via Skills intent `client_deactivate` and local mapping.

### `POST /internal/tenants/{tenant_id}/keys/rotate`

Rotates upstream API key via Skills intent `client_rotate_key` and stores encrypted new key.

### `POST /internal/tenants/{tenant_id}/release-markers`

Forwards release marker payload to upstream:

- `/api/v1/releases/markers`

Default payload fields:

- `source` (default `cgs-deploy`)
- `environment` (default `production`)
- `commit_sha`, `branch`, `tag_name`, `deployed_at`, `metadata`

### 8.5 Internal Tenant Admin Endpoints

Prefix: `/service/ai/v1/internal/admin/tenants/{tenant_id}`

Security requirements:
- operator role/scope required
- `cgs:zetherion-admin` scope required
- mutating operations require step-up claim (`step_up=true` or MFA AMR/ACR claims)
- secrets endpoints additionally require `cgs:zetherion-secrets-admin`

High-risk approval workflow:
- required for secret create/rotate/delete
- required for owner role grants
- required for email OAuth app credential writes
- required for mailbox disconnect actions
- submit/review/apply represented by `change_ticket_id` and `/changes` workflow routes

Tenant admin route set:
- `GET|POST /discord-users`
- `DELETE /discord-users/{discord_user_id}`
- `PATCH /discord-users/{discord_user_id}/role`
- `GET /discord-bindings`
- `PUT /discord-bindings/guilds/{guild_id}`
- `PUT|DELETE /discord-bindings/channels/{channel_id}`
- `GET /settings`
- `PUT|DELETE /settings/{namespace}/{key}`
- `GET /secrets`
- `PUT|DELETE /secrets/{name}`
- `GET /audit`
- `GET|PUT /email/providers/{provider}/oauth-app`
- `POST /email/mailboxes/connect/start`
- `GET /email/mailboxes/connect/callback`
- `GET /email/mailboxes`
- `PATCH|DELETE /email/mailboxes/{mailbox_id}`
- `POST /email/mailboxes/{mailbox_id}/sync`
- `GET /email/critical/messages`
- `GET /email/calendars`
- `PUT /email/mailboxes/{mailbox_id}/calendar-primary`
- `GET /email/insights`
- `POST /email/insights/reindex`
- `POST|GET /changes`
- `POST /changes/{change_id}/approve`
- `POST /changes/{change_id}/reject`

Upstream mapping:
- CGS tenant ID maps to `zetherion_tenant_id`
- gateway calls Skills REST tenant-admin routes under:
  - `/admin/tenants/{zetherion_tenant_id}/...`
- gateway sends signed actor envelope headers:
  - `X-Admin-Actor`
  - `X-Admin-Signature`

Email admin route behavior notes:
- provider scope for initial rollout is `google`.
- OAuth app reads never return client secret values.
- mailbox status lifecycle: `pending|connected|degraded|revoked|disconnected`.
- sync job status lifecycle: `queued|running|succeeded|failed|retrying`.
- critical severity lifecycle: `critical|high|normal`; triage state `open|resolved|dismissed`.

### 8.6 Tenant Reporting Endpoints

Prefix: `/service/ai/v1/tenants/{tenant_id}`

### `GET /crm/contacts`

Forwards to `/api/v1/crm/contacts`

### `GET /crm/interactions`

Forwards to `/api/v1/crm/interactions`

### `GET /analytics/funnel`

Forwards to `/api/v1/analytics/funnel`

### `GET /analytics/recommendations`

Forwards to `/api/v1/analytics/recommendations/tenant`

## 9. Upstream Mapping Summary

Runtime endpoints map to Zetherion Public API using:

- `X-API-Key` (tenant-level operations: sessions, documents, RAG, reporting, release markers)
- `Authorization: Bearer <session_token>` (session-scoped chat/analytics paths)

Internal lifecycle maps to Skills API `/handle` with intents:

- `client_create`
- `client_configure`
- `client_deactivate`
- `client_rotate_key`

Internal tenant-admin maps to Skills REST endpoints:
- `/admin/tenants/{tenant_id}/discord-users*`
- `/admin/tenants/{tenant_id}/discord-bindings*`
- `/admin/tenants/{tenant_id}/settings*`
- `/admin/tenants/{tenant_id}/secrets*`
- `/admin/tenants/{tenant_id}/audit`
- `/admin/tenants/{tenant_id}/email/providers/{provider}/oauth-app`
- `/admin/tenants/{tenant_id}/email/oauth/{provider}/start`
- `/admin/tenants/{tenant_id}/email/oauth/{provider}/exchange`
- `/admin/tenants/{tenant_id}/email/accounts*`
- `/admin/tenants/{tenant_id}/email/critical`
- `/admin/tenants/{tenant_id}/email/calendars`
- `/admin/tenants/{tenant_id}/email/insights*`

Actor attribution to Skills tenant-admin endpoints is mandatory:
- signed actor envelope includes `actor_sub`, `actor_roles`, `request_id`, `timestamp`, `nonce`
- replay-protection is enforced upstream (nonce/timestamp validation)

## 10. Gateway Environment Variables

Required:

- `POSTGRES_DSN`
- `ENCRYPTION_PASSPHRASE`
- `CGS_AUTH_JWKS_URL`
- `ZETHERION_SKILLS_API_SECRET` or fallback `SKILLS_API_SECRET`

Recommended:

- `CGS_AUTH_ISSUER`
- `CGS_AUTH_AUDIENCE`
- `CGS_GATEWAY_ALLOWED_ORIGINS`
- `CGS_BLOG_PUBLISH_URL` (Windows promotions secret)
- `CGS_BLOG_PUBLISH_TOKEN` (Windows promotions secret)
- `GITHUB_PROMOTION_TOKEN` (Windows promotions secret)
- `BLOG_MODEL_PRIMARY=gpt-5.2` (Windows promotions secret)
- `BLOG_MODEL_SECONDARY=claude-sonnet-4-6` (Windows promotions secret)
- `BLOG_PUBLISH_ENABLED=true` (Windows promotions secret)
- `RELEASE_AUTO_INCREMENT_ENABLED=true` (Windows promotions secret)

Defaults:

- `CGS_GATEWAY_HOST=0.0.0.0`
- `CGS_GATEWAY_PORT=8743` (standalone default)
- In compose, gateway currently runs on internal port `8443`

Upstream defaults:

- `ZETHERION_PUBLIC_API_BASE_URL=http://zetherion-ai-traefik:8443`
- `ZETHERION_SKILLS_API_BASE_URL=http://zetherion-ai-traefik:8080`

## 11. Network and Routing Requirements

Traefik routing contract in this repo:

- Requests with `PathPrefix('/service/ai/v1')` must route to CGS gateway service
- Catch-all `/` routes to existing public API

For CGS website integration, ensure external ingress preserves:

- Path (`/service/ai/v1/...`)
- `Authorization` header
- `X-Request-Id` (optional pass-through)

## 12. SDK Integration for CGS Mode

`sdk/zetherion-web-observer` supports two modes:

- `provider: "zetherion"` (legacy direct `/api/v1`)
- `provider: "cgs"` (gateway `/service/ai/v1`)

CGS mode requirements:

- `authToken` (CGS JWT bearer)
- `conversationId`

CGS mode analytics paths:

- `/service/ai/v1/conversations/{conversationId}/analytics/events`
- `/service/ai/v1/conversations/{conversationId}/analytics/replay/chunks`
- `/service/ai/v1/conversations/{conversationId}/analytics/end`

## 13. CGS Implementation Checklist

1. Implement JWT issuing service for app and operator tokens.
2. Publish JWKS endpoint and configure gateway env.
3. Add tenant claims (`tenant_id` or `cgs_tenant_id`) in app tokens.
4. Add operator roles/scopes for internal endpoints.
5. Build backend wrappers for all endpoint groups in Section 8.
6. Require and reuse `Idempotency-Key` on retryable POST/DELETE calls.
7. Thread `X-Request-Id` from CGS frontend -> CGS backend -> gateway.
8. Build tenant bootstrap flow using `/internal/tenants` before runtime calls.
9. Switch web observer SDK to `provider: "cgs"` for analytics paths.
10. Validate all responses using the envelope contract.

## 14. Validation and Test Scenarios

Minimum acceptance coverage:

1. Auth
   - Missing bearer -> `401 AI_AUTH_MISSING`
   - Bad token -> `401 AI_AUTH_INVALID_TOKEN`
   - Cross-tenant access -> `403 AI_AUTH_FORBIDDEN`
2. Runtime
   - Create conversation -> message -> stream -> history -> end analytics -> recommendations
3. Idempotency
   - Same key + same body replay
   - Same key + different body conflict
4. Internal
   - Create tenant -> rotate key -> update -> deactivate
5. Reporting
   - Contacts/interactions/funnel/recommendations with tenant scoping
6. Email admin control plane
   - Configure tenant Google OAuth app (approval-gated)
   - Link 5+ mailboxes through connect start/callback
   - Trigger sync and verify critical message records + insight records
   - List calendars and set mailbox primary calendar
   - Verify mailbox delete is approval-gated and idempotent

## 15. Operational Notes

- Gateway writes request logs to `cgs_ai_request_log` keyed by `request_id`.
- API keys/session tokens are encrypted in DB.
- Error handling is normalized regardless of upstream response format.
- `X-Request-Id` is generated when omitted and returned on every response.

## 16. Source of Truth Files

- `src/zetherion_ai/cgs_gateway/server.py`
- `src/zetherion_ai/cgs_gateway/middleware.py`
- `src/zetherion_ai/cgs_gateway/routes/runtime.py`
- `src/zetherion_ai/cgs_gateway/routes/internal.py`
- `src/zetherion_ai/cgs_gateway/routes/internal_admin.py`
- `src/zetherion_ai/cgs_gateway/routes/reporting.py`
- `src/zetherion_ai/cgs_gateway/storage.py`
- `src/zetherion_ai/cgs_gateway/models.py`
- `sdk/zetherion-web-observer/src/index.ts`
- `sdk/zetherion-web-observer/src/types.ts`

## 17. Client Integration Kit

Use these docs together as the external onboarding pack:

- `docs/technical/cgs-client-onboarding-kit.md`
- `docs/technical/frontend-route-wiring.md`
- `docs/technical/api-auth-matrix.md`
- `docs/technical/api-error-matrix.md`
- `docs/technical/openapi-cgs-gateway.yaml`
