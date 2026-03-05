# Frontend Route-to-Screen Wiring Guide

This guide maps CGS `/service/ai/v1` routes to expected frontend flows.

## Maintenance Note (2026-03-04)

- Added internal lifecycle/reporting route mappings and operator screen coverage.
- Added explicit failure envelope handling for `error.retryable`.
- Added blog publish adapter route mapping for operator tooling and promotion worker integration.
- Clarified that CGS website/UI is owned outside this repository; this document only maps CGS API routes to UI screens.
- Added tenant email admin route mapping for OAuth app setup, mailbox linking, sync/triage, and calendar selection.
- Added tenant messaging admin route mapping for provider config, chat policy management, chat/message views, and policy-gated sends.
- Added trust-policy enforcement note for sensitive internal admin actions; UI should handle deny/approval-required outcomes consistently.

## Document Center Screens

| Screen | Route(s) | Method | Notes |
|---|---|---|---|
| Document list | `/service/ai/v1/documents?tenant_id={tenant_id}` | `GET` | Shows status (`uploaded`, `processing`, `indexed`, `failed`) |
| Upload modal - start | `/service/ai/v1/documents/uploads` | `POST` | Returns `upload_id` + complete route |
| Upload modal - complete (JSON) | `/service/ai/v1/documents/uploads/{upload_id}/complete` | `POST` | Send `tenant_id`, `file_base64`, optional metadata |
| Upload modal - complete (multipart) | `/service/ai/v1/documents/uploads/{upload_id}/complete?tenant_id={tenant_id}` | `POST` | Send `multipart/form-data` with `file` and optional JSON-string `metadata` part |
| Document detail | `/service/ai/v1/documents/{document_id}?tenant_id={tenant_id}` | `GET` | Metadata + ingestion/index state |
| Preview pane | `/service/ai/v1/documents/{document_id}/preview?tenant_id={tenant_id}` | `GET` | Render inline PDF/HTML/text response |
| Download action | `/service/ai/v1/documents/{document_id}/download?tenant_id={tenant_id}` | `GET` | Browser file download |
| Re-index button | `/service/ai/v1/documents/{document_id}/index` | `POST` | Body requires `tenant_id` |

## Retrieval Assistant Panel

| Screen Element | Route | Method | Notes |
|---|---|---|---|
| Provider/model selector preload | `/service/ai/v1/models/providers?tenant_id={tenant_id}` | `GET` | Build provider + model dropdown options |
| Ask-on-documents submit | `/service/ai/v1/rag/query` | `POST` | Body: `tenant_id`, `query`, optional `top_k`, `provider`, `model` |
| Citation click-through | `/service/ai/v1/documents/{document_id}?tenant_id={tenant_id}` | `GET` | Resolve source document metadata |

## Admin Control Plane Screens (Operator UI)

| Screen | Route(s) | Method | Notes |
|---|---|---|---|
| Tenant access users list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-users` | `GET` | Operator + `cgs:zetherion-admin` required |
| Add approved user | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-users` | `POST` | Mutating route requires step-up auth |
| Remove approved user | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-users/{discord_user_id}` | `DELETE` | Mutating route requires step-up auth |
| Role editor | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-users/{discord_user_id}/role` | `PATCH` | Owner grants require approved `change_ticket_id` |
| Binding list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-bindings` | `GET` | Includes guild defaults + channel overrides |
| Upsert guild default | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-bindings/guilds/{guild_id}` | `PUT` | Mutating route requires step-up auth |
| Upsert channel override | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-bindings/channels/{channel_id}` | `PUT` | Body requires `guild_id` |
| Delete channel override | `/service/ai/v1/internal/admin/tenants/{tenant_id}/discord-bindings/channels/{channel_id}` | `DELETE` | Mutating route requires step-up auth |
| Settings list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/settings` | `GET` | Allowlist-controlled keys only |
| Settings write/reset | `/service/ai/v1/internal/admin/tenants/{tenant_id}/settings/{namespace}/{key}` | `PUT`/`DELETE` | Mutating route requires step-up auth |
| Secret metadata list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/secrets` | `GET` | Requires `cgs:zetherion-secrets-admin` |
| Secret rotate/delete | `/service/ai/v1/internal/admin/tenants/{tenant_id}/secrets/{name}` | `PUT`/`DELETE` | Requires approved `change_ticket_id` |
| Audit timeline | `/service/ai/v1/internal/admin/tenants/{tenant_id}/audit` | `GET` | Immutable upstream audit trail |
| Messaging provider config | `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/providers/{provider}/config` | `GET`/`PUT` | Mutating route requires step-up auth |
| Messaging chat policy | `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/chats/{chat_id}/policy` | `GET`/`PUT` | Controls read/send allowlist and retention |
| Messaging chat list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/chats` | `GET` | Supports `provider`, `include_inactive`, `limit` |
| Messaging message list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/messages` | `GET` | Requires `chat_id` query |
| Messaging send queue | `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/messages/{chat_id}/send` | `POST` | May require approval (`AI_APPROVAL_REQUIRED`) |
| Autonomous merge execute | `/service/ai/v1/internal/admin/tenants/{tenant_id}/automerge/execute` | `POST` | Requires trust-policy branch/risk guards + required checks |
| Change queue | `/service/ai/v1/internal/admin/tenants/{tenant_id}/changes` | `GET` | Shows pending/approved/applied/rejected |
| Submit change | `/service/ai/v1/internal/admin/tenants/{tenant_id}/changes` | `POST` | Creates pending review ticket |
| Approve/reject change | `/service/ai/v1/internal/admin/tenants/{tenant_id}/changes/{change_id}/approve` or `/reject` | `POST` | Two-person approval enforced |

## Email Admin Screens (Operator UI)

| Screen | Route(s) | Method | Notes |
|---|---|---|---|
| OAuth app config view | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/providers/google/oauth-app` | `GET` | Requires `cgs:zetherion-secrets-admin`; secret values are never returned |
| OAuth app config save | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/providers/google/oauth-app` | `PUT` | Requires step-up + secrets scope + approved `change_ticket_id` |
| Mailbox connect start | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/connect/start` | `POST` | Returns provider `auth_url` and OAuth `state` |
| Mailbox connect callback | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/connect/callback` | `GET` | Browser callback with `code`, `state`, optional `provider` |
| Mailbox list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes` | `GET` | Optional `provider` filter (default `google`) |
| Mailbox patch | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}` | `PATCH` | Mutating route requires step-up auth |
| Mailbox disconnect | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}` | `DELETE` | Requires approved `change_ticket_id` |
| Sync now | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}/sync` | `POST` | Optional direction/idempotency/calendar operation fields |
| Critical inbox list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/critical/messages` | `GET` | Supports `status`, `severity`, `limit` filters |
| Calendars list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/calendars` | `GET` | Requires `mailbox_id` query |
| Primary calendar set | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}/calendar-primary` | `PUT` | Mutating route requires step-up auth |
| Insights list | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/insights` | `GET` | Supports `insight_type`, `min_confidence`, `limit` filters |
| Insights reindex | `/service/ai/v1/internal/admin/tenants/{tenant_id}/email/insights/reindex` | `POST` | Rebuilds vector index records for stored insights |

## Internal Lifecycle + Reporting Screens

| Screen | Route(s) | Method | Notes |
|---|---|---|---|
| Tenant registry | `/service/ai/v1/internal/tenants` | `GET` | Supports `include_inactive=true` |
| Tenant create | `/service/ai/v1/internal/tenants` | `POST` | Operator-only lifecycle onboarding |
| Tenant profile edit | `/service/ai/v1/internal/tenants/{tenant_id}` | `PATCH` | Enforces operator tenant-claim policy |
| Tenant deactivate | `/service/ai/v1/internal/tenants/{tenant_id}/deactivate` | `POST` | Operator tenant authorization required |
| Tenant key rotate | `/service/ai/v1/internal/tenants/{tenant_id}/keys/rotate` | `POST` | Operator tenant authorization required |
| Release marker | `/service/ai/v1/internal/tenants/{tenant_id}/release-markers` | `POST` | Publishes deployment markers via mapped tenant key |
| CRM contacts report | `/service/ai/v1/tenants/{tenant_id}/crm/contacts` | `GET` | Tenant-scoped reporting |
| CRM interactions report | `/service/ai/v1/tenants/{tenant_id}/crm/interactions` | `GET` | Tenant-scoped reporting |
| Analytics funnel report | `/service/ai/v1/tenants/{tenant_id}/analytics/funnel` | `GET` | Tenant-scoped reporting |
| Analytics recommendations report | `/service/ai/v1/tenants/{tenant_id}/analytics/recommendations` | `GET` | Tenant-scoped reporting |

## Promotions/Publish Adapter

| Integration Surface | Route(s) | Method | Notes |
|---|---|---|---|
| Windows promotions publish callback | `/service/ai/v1/internal/blog/publish` | `POST` | Auth uses static bearer token (`CGS_BLOG_PUBLISH_TOKEN`) and strict `Idempotency-Key=blog-<sha>` validation |

## Conversation Screens (Existing)

| Screen | Route | Method |
|---|---|---|
| New chat session | `/service/ai/v1/conversations` | `POST` |
| Send message | `/service/ai/v1/conversations/{conversation_id}/messages` | `POST` |
| Stream response | `/service/ai/v1/conversations/{conversation_id}/messages/stream` | `POST` |
| History | `/service/ai/v1/conversations/{conversation_id}/messages` | `GET` |

## Expected Request Envelope Behavior

- Success responses return `request_id`, `data`, `error: null`.
- Failure responses return `request_id`, `data: null`, and typed `error` with `code` + `retryable`.
- Non-JSON preview/download routes stream raw bytes and preserve upstream `Content-Type` + `Content-Disposition` headers.

## Upload UX Sequence

1. User selects file in browser.
2. Frontend calls create upload route with file metadata.
3. Frontend completes upload using either:
   - JSON `file_base64` payload, or
   - multipart form upload to reduce payload overhead.
4. Poll document list/detail until status transitions to `indexed` or `failed`.
5. On `indexed`, enable retrieval and citation experience.

## Download UX Sequence

1. Frontend calls download route for selected document.
2. Browser receives attachment headers.
3. Client saves file using filename from `Content-Disposition`.

## Email Mailbox Onboarding Sequence

1. Operator opens OAuth app screen and validates current config.
2. Operator saves tenant Google OAuth app (`PUT /email/providers/google/oauth-app`) using step-up and approved `change_ticket_id`.
3. Operator starts mailbox connect (`POST /email/mailboxes/connect/start`) and opens returned `auth_url`.
4. Google redirects to callback (`GET /email/mailboxes/connect/callback?code=...&state=...`), then mailbox list is refreshed.
5. Operator triggers initial sync (`POST /email/mailboxes/{mailbox_id}/sync`) and verifies critical/insight feeds.

## Email Monitoring + Calendar Sequence

1. Pull critical inbox list (`GET /email/critical/messages`) and render severity/reason/confidence.
2. Pull insights list (`GET /email/insights`) for structured downstream extraction usage.
3. Load mailbox calendars (`GET /email/calendars?mailbox_id=...`) and set primary calendar (`PUT /calendar-primary`).
4. Surface mailbox statuses `pending|connected|degraded|revoked|disconnected` and hide write-sync actions when revoked/disconnected.

## Error Handling UX Hints

- `AI_BAD_REQUEST`: show inline validation errors (tenant missing, query empty, payload invalid).
- `AI_UPSTREAM_429`: show retry/backoff indicator.
- `AI_UPSTREAM_5XX` or `AI_INTERNAL_ERROR`: show transient service outage and retry CTA.
- Document status `failed`: surface `error_message` from document detail endpoint.
- `AI_AUTH_STEP_UP_REQUIRED`: trigger MFA/step-up flow, then retry mutation.
- `AI_APPROVAL_REQUIRED`: surface `change_ticket_id`, move action into approval queue UI.
- `AI_APPROVAL_TWO_PERSON_REQUIRED`: block self-approval in reviewer UI.
- Mailbox status `degraded`/`revoked`: show reconnect CTA and suspend calendar write actions.
