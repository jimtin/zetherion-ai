# Frontend Route-to-Screen Wiring Guide

This guide maps CGS `/service/ai/v1` routes to expected frontend flows.

## Maintenance Note (2026-03-01)

- Internal document route hardening introduced no route, payload, or screen-mapping changes.
- Existing frontend upload/list/detail/preview/download wiring remains valid.

## Document Center Screens

| Screen | Route(s) | Method | Notes |
|---|---|---|---|
| Document list | `/service/ai/v1/documents?tenant_id={tenant_id}` | `GET` | Shows status (`uploaded`, `processing`, `indexed`, `failed`) |
| Upload modal - start | `/service/ai/v1/documents/uploads` | `POST` | Returns `upload_id` + complete route |
| Upload modal - complete | `/service/ai/v1/documents/uploads/{upload_id}/complete` | `POST` | Send `tenant_id`, `file_base64`, optional metadata |
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

## Conversation Screens (Existing)

| Screen | Route | Method |
|---|---|---|
| New chat session | `/service/ai/v1/conversations` | `POST` |
| Send message | `/service/ai/v1/conversations/{conversation_id}/messages` | `POST` |
| Stream response | `/service/ai/v1/conversations/{conversation_id}/messages/stream` | `POST` |
| History | `/service/ai/v1/conversations/{conversation_id}/messages` | `GET` |

## Expected Request Envelope Behavior

- Success responses return `request_id`, `data`, `error: null`.
- Failure responses return `request_id`, `data: null`, and typed `error` with `code`.
- Non-JSON preview/download routes stream raw bytes and preserve upstream `Content-Type` + `Content-Disposition` headers.

## Upload UX Sequence

1. User selects file in browser.
2. Frontend calls create upload route with file metadata.
3. Frontend reads file bytes and calls complete upload route.
4. Poll document list/detail until status transitions to `indexed` or `failed`.
5. On `indexed`, enable retrieval and citation experience.

## Download UX Sequence

1. Frontend calls download route for selected document.
2. Browser receives attachment headers.
3. Client saves file using filename from `Content-Disposition`.

## Error Handling UX Hints

- `AI_BAD_REQUEST`: show inline validation errors (tenant missing, query empty, payload invalid).
- `AI_UPSTREAM_429`: show retry/backoff indicator.
- `AI_UPSTREAM_5XX` or `AI_INTERNAL_ERROR`: show transient service outage and retry CTA.
- Document status `failed`: surface `error_message` from document detail endpoint.
