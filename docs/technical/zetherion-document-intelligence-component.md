# Zetherion Document Intelligence Component (Internal)

## Purpose

This document defines the internal Zetherion component behind CGS document intelligence.
It is implementation-level documentation for backend and operations teams.

Public exposure rule:
- External clients must not call Zetherion `/api/v1` directly.
- CGS `/service/ai/v1` is the only supported public API for this capability.

## Maintenance Note (2026-03-19)

- Upstream session chat routes (`/api/v1/chat*`) now accept optional runtime
  provider/model selection hints and return provider/usage/selection metadata.
- That change stays inside the session chat runtime and does not alter document
  intelligence route contracts, retrieval authorization, storage layout, or
  archive lifecycle behavior.
- Previous 2026-03-10 maintenance updates:

- Segment 6 adds upstream tenant notification routes under `/api/v1/notifications/*`
  backed by the generalized announcement core.
- Those notification routes stay in tenant live API-key auth, remain outside the
  document ingestion/retrieval surface, and do not change document intelligence
  contracts, object storage layout, Qdrant collection design, or retrieval authorization.

- Upstream tenant sandbox support now adds `sk_test_...` API keys plus `/api/v1/test/*` profile/rule management for chat simulation.
- Test-mode sessions, messages, and analytics rows are tagged `execution_mode=test` and are excluded from CRM extraction, funnel, and recommendation persistence in this segment.
- Document intelligence routes remain live-only API-key routes in Segment 3. `sk_test_...` keys do not access `/api/v1/documents*`, `/api/v1/rag/query`, or model-catalog routes.
- Upstream tenant conversation routes still support tenant-scoped `memory_subject_id` and rolling `conversation_summary` metadata for `/api/v1/sessions` and `/api/v1/chat*`.
- Those conversation changes stay in `tenant_raw` and do not alter document intelligence route contracts, storage partitions, or retrieval authorization.
- Segment 2 data-plane isolation foundation added scoped object-storage prefixes,
  additive PostgreSQL isolation schemas, and owner-vs-tenant Qdrant/encryption
  routing beneath document intelligence storage surfaces.
- Document intelligence routes and external CGS-facing contracts remain unchanged
  in this segment.
- Added upstream tenant messaging routes (`/api/v1/messaging/*`) for policy-gated message read/send.
- Document intelligence contracts remain unchanged by messaging additions.
- Added upstream document lifecycle APIs:
  - `DELETE /api/v1/documents/{document_id}` (archive schedule)
  - `POST /api/v1/documents/{document_id}/restore` (restore + reindex)
- Added archive lifecycle status support and archive job persistence.
- Added background archive/purge maintenance loop wiring in upstream API server lifecycle.
- Added retrieval guardrail to exclude `archiving|archived|purged` from RAG context assembly.

## Component Boundaries

Zetherion component responsibilities:
- Accept tenant-scoped document upload intents and completed payloads.
- Persist raw document bytes to object storage.
- Persist metadata and ingestion lifecycle records in Postgres.
- Extract/chunk/embed content and upsert vectors into Qdrant.
- Serve preview/download payloads and retrieval responses.

CGS responsibilities:
- Authenticate client principals.
- Enforce tenant access at API boundary.
- Map CGS request/response envelope and errors.
- Proxy document and retrieval requests to Zetherion upstream.

## Data Model

Primary tables:
- `tenant_documents`
- `tenant_document_uploads`
- `document_ingestion_jobs`
- `document_archive_jobs`

`tenant_documents` key fields:
- `tenant_id`
- `document_id`
- `file_name`
- `mime_type`
- `object_key`
- `status` (`uploaded|processing|indexed|failed|archiving|archived|purged`)
- `size_bytes`
- `checksum_sha256`
- `chunk_count`
- `extracted_text`
- `preview_html`
- `archived_at`
- `purge_after`
- `purged_at`
- `archived_reason`
- `error_message`
- `created_at`
- `updated_at`

`tenant_document_uploads` key fields:
- `tenant_id`
- `upload_id`
- `file_name`
- `mime_type`
- `size_bytes`
- `metadata_json`
- `status` (`pending|completed|expired|failed`)
- `document_id` (nullable until complete)
- `expires_at`
- `created_at`
- `updated_at`

`document_ingestion_jobs` key fields:
- `tenant_id`
- `job_id`
- `document_id`
- `status` (`processing|indexed|failed`)
- `error_message`
- `created_at`
- `updated_at`

`document_archive_jobs` key fields:
- `tenant_id`
- `job_id`
- `document_id`
- `status` (`queued|running|succeeded|failed`)
- `retry_count`
- `next_attempt_at`
- `error_message`
- `created_at`
- `updated_at`

## Object Storage Contract

Storage backend: S3-compatible blob store via replay/object abstraction.

Object key format:
- `documents/{tenant_id}/{document_id}/{safe_filename}`

Integrity:
- SHA-256 checksum stored as `checksum_sha256`.

## Vector Store Contract

Backend: Qdrant collection `tenant_documents`.

Payload keys per chunk:
- `tenant_id`
- `document_id`
- `file_name`
- `mime_type`
- `chunk_index`
- `content`
- `indexed_at`

Tenant isolation:
- Every search and delete operation is filtered by `tenant_id`.

## Ingestion Pipeline

1. `POST /api/v1/documents/uploads`
   - Creates pending upload intent with expiry.
2. `POST /api/v1/documents/uploads/{upload_id}/complete`
   - Validates upload token/status/expiry.
   - Accepts JSON base64 or multipart file.
   - Stores raw bytes in object storage.
   - Creates `tenant_documents` row with `uploaded` status.
3. Indexing phase
   - Creates ingestion job (`processing`).
   - Extracts text from PDF/DOCX/text.
   - Chunks text.
   - Upserts vectors into Qdrant.
 - Updates document/job to `indexed` or `failed`.

Archive/delete phase:
1. `DELETE /api/v1/documents/{document_id}`
   - validates lifecycle state
   - updates document to `archiving`
   - enqueues `document_archive_jobs`
2. Archive maintenance loop claims jobs, removes vectors, and marks document `archived` with retention window.
3. Purge maintenance loop removes raw bytes + vectors after retention and marks `purged`.

Restore phase:
1. `POST /api/v1/documents/{document_id}/restore`
2. validates `status=archived`
3. clears archive markers, transitions to processing, re-runs indexing flow.

Failure behavior:
- Upload validation failures return `400`.
- Object/inference dependency errors return `503`.
- Indexing errors set persistent `failed` state with `error_message`.

## Preview/Reader Behavior

Route: `GET /api/v1/documents/{document_id}/preview`

By file type:
- PDF: inline PDF bytes.
- DOCX: sanitized HTML preview if available.
- DOCX fallback: escaped extracted text in HTML `<pre>`.
- Other: inline raw payload with source mime type.

Route: `GET /api/v1/documents/{document_id}/download`
- Returns raw bytes with attachment disposition.

## Retrieval Orchestration

Route: `POST /api/v1/rag/query`

Execution:
1. Tenant-scoped vector search in `tenant_documents`.
2. Status guardrail excludes `archiving|archived|purged` matches before context assembly.
3. Context assembly from active matches only.
4. Inference call with optional provider/model override.
5. Citation assembly (`document_id`, `file_name`).

Provider override:
- Canonical providers: `groq`, `openai`, `anthropic`.
- `claude` is accepted as alias for `anthropic`.
- Model overrides are allowlist-validated.

Embeddings default:
- `EMBEDDINGS_BACKEND=openai`
- `OPENAI_EMBEDDING_MODEL=text-embedding-3-large`

## Security and Tenancy

- Tenant identity is resolved before route handlers.
- Document rows are always tenant-filtered.
- Vector operations are always tenant-filtered.
- Raw object keys are tenant-partitioned.
- Segment 3 test-mode keys are intentionally blocked from document and retrieval routes.
- No tenant API key or session token is exposed to browsers.

## Operations and Support

Health indicators:
- Qdrant collection availability.
- Object storage read/write availability.
- Inference broker availability for retrieval generation.

Operational tasks:
- Re-index via `POST /api/v1/documents/{document_id}/index`.
- Archive/delete request via `DELETE /api/v1/documents/{document_id}`.
- Restore via `POST /api/v1/documents/{document_id}/restore`.
- Triage failed ingestions by `error_message`.
- Validate preview fallbacks for unsupported/broken files.

## Source of Truth

- `src/zetherion_ai/api/routes/documents.py`
- `src/zetherion_ai/documents/service.py`
- `src/zetherion_ai/documents/processing.py`
- `src/zetherion_ai/api/tenant.py`
