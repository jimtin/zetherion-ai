# API Error Matrix

This matrix defines the normalized error behavior for the Zetherion public API and CGS gateway.

## Public API (`/api/v1`)

| Route Group | Status | Error Shape | Notes |
|---|---|---|---|
| Auth failures (`X-API-Key`, `Authorization`) | `401` | `{"error":"..."}` | Missing/invalid key or session token |
| Tenant/session forbidden/inactive | `403` | `{"error":"..."}` | Tenant isolation/security rule |
| Unknown resource | `404` | `{"error":"..."}` | Missing session/document/replay chunk |
| Validation failures | `400` | `{"error":"..."}` | Invalid JSON/body/query/path payload |
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
    "details": {}
  }
}
```

| Condition | HTTP | `error.code` |
|---|---|---|
| Missing/invalid JWT | `401` | `AI_AUTH_MISSING`, `AI_AUTH_INVALID_TOKEN` |
| Principal cannot access tenant | `403` | `AI_AUTH_FORBIDDEN` |
| Request payload validation failed | `400` | `AI_BAD_REQUEST` |
| Conversation not found | `404` | `AI_CONVERSATION_NOT_FOUND` |
| Tenant not found/inactive | `404`/`403` | `AI_TENANT_NOT_FOUND`, `AI_TENANT_INACTIVE` |
| Idempotency key payload mismatch | `409` | `AI_IDEMPOTENCY_CONFLICT` |
| Upstream unauthorized/forbidden/not found/conflict/rate limited | passthrough | `AI_UPSTREAM_401`, `AI_UPSTREAM_403`, `AI_UPSTREAM_404`, `AI_UPSTREAM_409`, `AI_UPSTREAM_429` |
| Upstream 5xx failure | `503` | `AI_UPSTREAM_5XX` |
| Generic upstream failure | `502` | `AI_UPSTREAM_ERROR` |
| Skills upstream operator failure | `502` | `AI_SKILLS_UPSTREAM_ERROR` |
| Unexpected gateway failure | `500` | `AI_INTERNAL_ERROR` |

## Document + RAG Endpoint Specifics

| Endpoint | Failure Case | Result |
|---|---|---|
| `POST /api/v1/documents/uploads/{upload_id}/complete` | Upload expired/non-pending/missing | `400` |
| `GET /api/v1/documents/{document_id}` | Unknown document | `404` |
| `GET /api/v1/documents/{document_id}/preview` | Missing payload | `404` |
| `POST /api/v1/rag/query` | Empty query/provider/model not allowed | `400` |
| `POST /service/ai/v1/rag/query` | Tenant missing in request | `400` + `AI_BAD_REQUEST` |
| `GET /service/ai/v1/documents*` | Tenant query not supplied | `400` + `AI_BAD_REQUEST` |
