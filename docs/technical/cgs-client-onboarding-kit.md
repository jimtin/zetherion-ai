# CGS Client Onboarding Kit (Document Intelligence)

## Audience

This kit is for external client delivery teams integrating document intelligence.
All integration is through CGS `/service/ai/v1`.

## Primary Handoff

For the full technical implementation scope CGS must deliver to go live, use:
- `.agent-handoff/CGS_GO_LIVE_IMPLEMENTATION_CHECKLIST.md`
- `docs/technical/cgs-email-monitoring-onboarding-kit.md` (tenant multi-mailbox monitoring and intelligence)

Maintenance note (2026-03-05):
- Internal trust-policy enforcement was added for sensitive internal admin actions.
- Internal tenant messaging admin routes were added under `/service/ai/v1/internal/admin/tenants/{tenant_id}/messaging/*`.
- Internal tenant security observability routes were added under `/service/ai/v1/internal/admin/tenants/{tenant_id}/security/*`.
- Internal autonomous merge execution route was added under `/service/ai/v1/internal/admin/tenants/{tenant_id}/automerge/execute`.
- Public CGS client-facing document intelligence endpoints in this kit are unchanged.

## Integration Outcome

After implementing this kit, client applications can:
- Upload documents.
- View indexed status and metadata.
- Preview PDF/DOCX/text content.
- Download original files.
- Ask questions over indexed documents with citations.

## Public API Surface (CGS Only)

- `POST /service/ai/v1/documents/uploads`
- `POST /service/ai/v1/documents/uploads/{upload_id}/complete`
- `GET /service/ai/v1/documents`
- `GET /service/ai/v1/documents/{document_id}`
- `GET /service/ai/v1/documents/{document_id}/preview`
- `GET /service/ai/v1/documents/{document_id}/download`
- `POST /service/ai/v1/documents/{document_id}/index`
- `POST /service/ai/v1/rag/query`
- `GET /service/ai/v1/models/providers`

Contract source:
- `docs/technical/openapi-cgs-gateway.yaml`

## Required Auth

Headers:
- `Authorization: Bearer <cgs_jwt>`

CGS-hosted web UI mode:
- The CGS website uses a session-cookie BFF proxy (HttpOnly session token cookie to CGS route handlers).
- Browser code does not read or store bearer tokens directly.

Tenant scoping:
- Query endpoints require `tenant_id` query parameter.
- Mutating JSON endpoints require `tenant_id` in body.
- Multipart upload completion requires `tenant_id` query parameter.

## Core Client Flows

### Flow A: Upload and Index

1. Create upload intent:
   - `POST /documents/uploads`
2. Complete upload:
   - JSON path: `POST /documents/uploads/{upload_id}/complete` with `tenant_id` + `file_base64`
   - Multipart path: `POST /documents/uploads/{upload_id}/complete?tenant_id=...` with `file`
3. Poll:
   - `GET /documents?tenant_id=...`
   - `GET /documents/{document_id}?tenant_id=...`
4. Wait for final status:
   - `indexed` (success) or `failed` (show error)

### Flow B: Preview and Download

1. Preview:
   - `GET /documents/{document_id}/preview?tenant_id=...`
2. Download:
   - `GET /documents/{document_id}/download?tenant_id=...`

### Flow C: Ask Documents

1. Fetch provider catalog:
   - `GET /models/providers?tenant_id=...`
2. Submit query:
   - `POST /rag/query`
3. Render:
   - `answer`
   - `citations[]` with document links

## Request/Response Examples

### Create Upload

```http
POST /service/ai/v1/documents/uploads
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "tenant_id": "tenant-a",
  "file_name": "proposal.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 483201,
  "metadata": {
    "source": "portal"
  }
}
```

### Complete Upload (Multipart)

```http
POST /service/ai/v1/documents/uploads/{upload_id}/complete?tenant_id=tenant-a
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

Parts:
- `file`: binary
- `metadata`: optional JSON string

### Query Documents

```json
{
  "tenant_id": "tenant-a",
  "query": "Summarize key implementation risks",
  "top_k": 6,
  "provider": "anthropic",
  "model": "claude-sonnet-4-6"
}
```

Provider values:
- Canonical: `groq`, `openai`, `anthropic`
- Alias accepted: `claude`

## UI State Model

Document status state machine:
- `uploaded` -> `processing` -> `indexed`
- `uploaded` -> `processing` -> `failed`

Client behavior:
- Disable retrieval action until `indexed`.
- Show retry action (`/index`) on `failed`.
- Surface `error_message` from detail response.

## Error Handling Matrix (Client Actions)

- `AI_BAD_REQUEST`: show validation error inline.
- `AI_AUTH_FORBIDDEN`: block action and display tenant access error.
- `AI_UPSTREAM_429`: retry with backoff and user notice.
- `AI_UPSTREAM_5XX` or `AI_INTERNAL_ERROR`: show temporary outage message.

## Environment + Secret Ownership

Client app requires only:
- CGS JWT issuance/configuration.
- CGS API base URL.

Client app must never hold:
- Zetherion API keys.
- Zetherion session tokens.
- Upstream service credentials.

## UAT Checklist

1. Upload PDF and DOCX successfully.
2. Confirm status transitions to `indexed`.
3. Validate preview behavior for both file types.
4. Validate downloaded bytes match uploaded bytes.
5. Run at least one RAG query per provider option exposed.
6. Confirm citations map to correct document IDs.
7. Verify cross-tenant access attempts return `403`.
8. Verify invalid payloads return `AI_BAD_REQUEST`.

## Go-Live Checklist

1. JWT claims include tenant context.
2. API gateway forwards `Authorization` and request IDs.
3. Retries configured for transient `429/5xx`.
4. Observability dashboards include endpoint latency/error rates.
5. On-call runbook includes ingestion failure triage path.

## Supporting Docs

- `docs/technical/cgs-public-api-endpoint-build-spec.md`
- `docs/technical/frontend-route-wiring.md`
- `docs/technical/api-auth-matrix.md`
- `docs/technical/api-error-matrix.md`
- `docs/technical/openapi-cgs-gateway.yaml`
