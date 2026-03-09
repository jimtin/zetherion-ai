# API Error Matrix

This matrix defines the normalized error behavior for the Zetherion public API and CGS gateway.

Exposure rule:
- External clients consume CGS gateway errors only.
- `/api/v1` errors are upstream/internal and surfaced through CGS mappings.

## Maintenance Note (2026-03-09)

- Segment 3 adds sandbox-specific validation and authorization outcomes to the upstream `/api/v1` surface:
  - `400` when `test_profile_id` is supplied on `POST /api/v1/sessions` with a live API key
  - `403` when `sk_test_...` is used on unsupported API-key routes
  - `404` when sandbox profile or rule identifiers do not exist for the authenticated tenant
  - configured sandbox preview/chat errors can intentionally return structured simulated status/body pairs
- Test-mode chat and analytics keep the same JSON/SSE envelopes as live mode; their isolation changes affect persistence side effects, not the success shape.
- Tenant conversational runtime still accepts optional `memory_subject_id` on `POST /api/v1/sessions` and returns `conversation_summary` metadata on session reads; these additions do not introduce new top-level error envelopes.
- Tenant chat summary/memory persistence still runs after the assistant response and is best-effort; user-visible live `/api/v1/chat*` error semantics remain unchanged.
- Segment 2 data-plane isolation foundation added internal owner-vs-tenant
  storage and encryption routing; error envelopes and status mappings for
  `/api/v1` and `/service/ai/v1` are unchanged by this segment.
- CGS failure envelope now includes `error.retryable` on all structured failures.
- Added blog publish adapter duplicate-as-success behavior:
  - `409` with `error=null` and `data.status=duplicate`.
- Added centralized upstream error mapping policy across runtime/internal/admin/reporting route families.
- Added upstream document lifecycle error mappings for archive/delete + restore routes.
- Archive/purge execution failures are asynchronous and surface via document/job status fields, not synchronous DELETE request failures.
- Added tenant messaging policy-gated upstream/public and CGS-admin error mappings.

## Public API (`/api/v1`)

| Route Group | Status | Error Shape | Notes |
|---|---|---|---|
| Auth failures (`X-API-Key`, `Authorization`) | `401` | `{"error":"..."}` | Missing/invalid key or session token |
| Tenant/session forbidden/inactive | `403` | `{"error":"..."}` | Tenant isolation/security rule, including unsupported `sk_test_...` route access and test-mode recommendation feedback |
| Unknown resource | `404` | `{"error":"..."}` | Missing session/document/replay chunk |
| Validation failures | `400` | `{"error":"..."}` | Invalid JSON/body/query/path payload, including malformed `memory_subject_id`, sandbox preview payloads, or `test_profile_id` on live session creation |
| Messaging trust-policy deny/approval-required | `403`/`409` | `{"error":"...","code":"AI_*"}` | Trust tier, allowlist, or approval policy gates |
| Oversized document uploads | `413` | `{"error":"Upload too large ..."}` | Max upload limit in route guard |
| Upstream dependencies unavailable | `503` | `{"error":"..."}` | Inference service/object storage unavailable |
| Unexpected server failures | `500` | `{"error":"..."}` | Unhandled internal exceptions |

## CGS Gateway (`/service/ai/v1`)

CGS always wraps non-stream JSON responses in:

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

| Condition | HTTP | `error.code` |
|---|---|---|
| Missing/invalid JWT | `401` | `AI_AUTH_MISSING`, `AI_AUTH_INVALID_TOKEN` |
| Principal cannot access tenant | `403` | `AI_AUTH_FORBIDDEN` |
| Mutating internal-admin request missing step-up auth | `403` | `AI_AUTH_STEP_UP_REQUIRED` |
| Request payload validation failed | `400` | `AI_BAD_REQUEST` |
| Conversation not found | `404` | `AI_CONVERSATION_NOT_FOUND` |
| Tenant not found/inactive | `404`/`403` | `AI_TENANT_NOT_FOUND`, `AI_TENANT_INACTIVE` |
| Internal-admin high-risk action without approved ticket | `409` | `AI_APPROVAL_REQUIRED` |
| Approval ticket missing/invalid | `404`/`409` | `AI_APPROVAL_NOT_FOUND`, `AI_APPROVAL_INVALID` |
| Approval attempted by requesting operator (two-person rule) | `409` | `AI_APPROVAL_TWO_PERSON_REQUIRED` |
| Idempotency key payload mismatch | `409` | `AI_IDEMPOTENCY_CONFLICT` |
| Blog publish duplicate replay (same payload) | `409` | `none` (`error=null`, `data.status=duplicate`) |
| Blog publish token missing/invalid | `401`/`403` | `AI_AUTH_MISSING`, `AI_AUTH_FORBIDDEN` |
| Upstream unauthorized/forbidden/not found/conflict/rate limited | passthrough | `AI_UPSTREAM_401`, `AI_UPSTREAM_403`, `AI_UPSTREAM_404`, `AI_UPSTREAM_409`, `AI_UPSTREAM_429` |
| Upstream 5xx failure | `503` | `AI_UPSTREAM_5XX` |
| Generic upstream failure | `502` | `AI_UPSTREAM_ERROR` |
| Skills upstream operator failure | `502` | `AI_SKILLS_UPSTREAM_ERROR` |
| Unexpected gateway failure | `500` | `AI_INTERNAL_ERROR` |

Retryability rules:
- `error.retryable=true` for transient classes (`429`, upstream `5xx`, generic gateway `500`).
- `error.retryable=false` for validation/authz/conflict classes.

## Document + RAG Endpoint Specifics

| Endpoint | Failure Case | Result |
|---|---|---|
| `POST /api/v1/documents/uploads/{upload_id}/complete` | Upload expired/non-pending/missing | `400` |
| `GET /api/v1/documents/{document_id}` | Unknown document | `404` |
| `DELETE /api/v1/documents/{document_id}` | Unknown document | `404` |
| `DELETE /api/v1/documents/{document_id}` | Invalid lifecycle transition (for example `processing`) | `409` |
| `DELETE /api/v1/documents/{document_id}` | Archive enqueue accepted but worker later fails | `202` + inspect document/job status |
| `POST /api/v1/documents/{document_id}/restore` | Unknown document | `404` |
| `POST /api/v1/documents/{document_id}/restore` | Invalid lifecycle transition (`purged` or non-archived) | `409` |
| `GET /api/v1/documents/{document_id}/preview` | Missing payload | `404` |
| `POST /api/v1/rag/query` | Empty query/provider/model not allowed | `400` |
| `POST /api/v1/rag/query` | Matches exist only in `archiving|archived|purged` docs | `200` with no-context answer |
| `POST /service/ai/v1/documents/uploads/{upload_id}/complete` (multipart) | Missing `tenant_id` query param | `400` + `AI_BAD_REQUEST` |
| `POST /service/ai/v1/rag/query` | Tenant missing in request | `400` + `AI_BAD_REQUEST` |
| `GET /service/ai/v1/documents*` | Tenant query not supplied | `400` + `AI_BAD_REQUEST` |

## Email Admin Endpoint Specifics

| Endpoint | Failure Case | Result |
|---|---|---|
| `PUT /service/ai/v1/internal/admin/tenants/{tenant_id}/email/providers/google/oauth-app` | Missing secrets scope | `403` + `AI_AUTH_FORBIDDEN` |
| `PUT /service/ai/v1/internal/admin/tenants/{tenant_id}/email/providers/google/oauth-app` | Missing approved ticket | `409` + `AI_APPROVAL_REQUIRED` |
| `GET /service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/connect/callback` | Missing `code` or `state` query | `400` + `AI_BAD_REQUEST` |
| `DELETE /service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}` | Missing approved ticket | `409` + `AI_APPROVAL_REQUIRED` |
| `GET /service/ai/v1/internal/admin/tenants/{tenant_id}/email/calendars` | Missing `mailbox_id` query | `400` + `AI_BAD_REQUEST` |
| `POST /service/ai/v1/internal/admin/tenants/{tenant_id}/email/mailboxes/{mailbox_id}/sync` | Invalid direction/status payload | `400` + `AI_BAD_REQUEST` |

## Messaging Endpoint Specifics

| Endpoint | Failure Case | Result |
|---|---|---|
| `GET /api/v1/messaging/messages` | Missing `chat_id` query | `400` |
| `GET /api/v1/messaging/messages` | Chat not allowlisted | `403` + `AI_MESSAGING_CHAT_NOT_ALLOWLISTED` |
| `POST /api/v1/messaging/messages/{chat_id}/send` | Approval required by policy | `409` + `AI_APPROVAL_REQUIRED` |
| `POST /api/v1/messaging/messages/{chat_id}/send` | Chat not allowlisted | `403` + `AI_MESSAGING_CHAT_NOT_ALLOWLISTED` |
| `POST /service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/messages/{chat_id}/send` | High-risk send without approved ticket | `409` + `AI_APPROVAL_REQUIRED` |

## Sandbox and Test-Mode Endpoint Specifics

| Endpoint | Failure Case | Result |
|---|---|---|
| `POST /api/v1/sessions` | `test_profile_id` supplied with `sk_live_...` | `400` |
| `POST /api/v1/sessions` | `test_profile_id` does not exist for the tenant | `404` |
| Any API-key route outside `POST /api/v1/sessions` or `/api/v1/test/*` | Authenticated with `sk_test_...` | `403` |
| `GET|PATCH|DELETE /api/v1/test/profiles/{profile_id}` | Unknown profile | `404` |
| `POST /api/v1/test/profiles` | Missing `name` | `400` |
| `GET|POST /api/v1/test/profiles/{profile_id}/rules` | Unknown profile | `404` |
| `POST /api/v1/test/profiles/{profile_id}/rules` | Missing `route_pattern` | `400` |
| `POST|PATCH /api/v1/test/profiles/{profile_id}/rules*` | `match` or `response` is not an object | `400` |
| `PATCH|DELETE /api/v1/test/profiles/{profile_id}/rules/{rule_id}` | Unknown rule | `404` |
| `POST /api/v1/test/profiles/{profile_id}/preview` | `body`/`session` not an object or `history` not an array | `400` |
| `POST /api/v1/chat` or `POST /api/v1/chat/stream` in test mode | Matching sandbox rule returns `response.error` | configured status + configured JSON body |
| `POST /api/v1/analytics/recommendations/{recommendation_id}/feedback` in test mode | Any call | `403` |

Non-error sandbox behavior:
- `GET /api/v1/analytics/recommendations` in test mode returns `200` with an empty list.
- `POST /api/v1/analytics/sessions/end` in test mode returns `200` with `execution_mode="test"` but does not persist derived recommendations or funnel updates.
