# Zetherion Document Archive/Delete Specification

## Scope

This document defines the **Zetherion-side** implementation contract for document delete/archive/restore behavior.

It covers:
- Public Zetherion API routes under `/api/v1`.
- Document lifecycle state model.
- Persistence/job contracts.
- Archive and purge behavior.

It does not cover external gateway/public API mapping.

## Goals

1. Add document delete capability without immediate destructive erase.
2. Remove archived content from retrieval immediately.
3. Allow restore during retention window.
4. Auto-purge raw bytes and vectors after retention period.
5. Keep tombstone metadata for auditability after purge.

## Segmented PR Execution Order

1. `seg1-zeth-delete-handoff-gate` (completed): handoff spec + docs gate scoping.
2. `seg2a-ci-docker-build-conditional` (completed): minimize CI spend by running `docker-build-test` only when Docker-related files change, while keeping scheduled/manual runs intact.
3. `seg2-zeth-document-archive-schema` (completed): schema + tenant manager lifecycle methods.
4. `seg3-zeth-delete-delete-endpoint` (completed): upstream delete/archive + restore routes, list filter wiring, lifecycle route tests, and docs bundle updates.
5. `seg5-zeth-archive-worker-guardrails` (completed): archive/purge maintenance worker logic, server lifecycle loop wiring, retrieval exclusion for `archiving|archived|purged`, and regression tests.
6. Remaining segments continue in strict one-PR-per-segment order for additional hardening only.

## Implementation Status Update (2026-03-05)

- Upstream tenant messaging endpoints were added (`/api/v1/messaging/*`) with trust-policy gating;
  document archive/delete contracts in this spec remain unchanged.
- Archive jobs are now processed by `DocumentService` maintenance methods:
  - `process_archive_jobs(...)`
  - `process_due_purges(...)`
  - `run_archive_maintenance_once(...)`
- Upstream API startup now launches a background maintenance loop when document service is configured.
- Retrieval now filters archived-state matches before prompt/citation assembly.
- Current default processing parameters:
  - retention: 90 days
  - archive batch size: 100
  - maintenance poll interval: 15 seconds

## Lifecycle States

Canonical document `status` values:
- `uploaded`
- `processing`
- `indexed`
- `failed`
- `archiving`
- `archived`
- `purged`

State transitions:
1. Active lifecycle: `uploaded -> processing -> indexed|failed`
2. Delete request: `indexed|uploaded|failed -> archiving -> archived`
3. Restore: `archived -> processing -> indexed|failed`
4. Purge: `archived -> purged`

## New/Updated API Endpoints

## 1) Delete (archive request)

`DELETE /api/v1/documents/{document_id}`

Behavior:
- Asynchronous request-accept path.
- Marks document `archiving` and enqueues archive job.
- Returns `202 Accepted` with archive metadata.

Success response (`202`):

```json
{
  "document_id": "uuid",
  "tenant_id": "uuid",
  "status": "archiving",
  "archive_job_id": "uuid",
  "archived_at": null,
  "purge_after": null,
  "message": "Archive scheduled"
}
```

Error semantics:
- `404` if document not found for tenant.
- Idempotent success if already `archiving|archived|purged`.

## 2) Restore

`POST /api/v1/documents/{document_id}/restore`

Behavior:
- Allowed only when `status=archived`.
- Clears archive markers, re-runs indexing pipeline, and returns updated record.

Success response (`200`):

```json
{
  "document_id": "uuid",
  "tenant_id": "uuid",
  "status": "processing"
}
```

Error semantics:
- `404` if unknown document.
- `409` when status is `purged` (non-restorable tombstone).
- `409` when status is not `archived`.

## 3) Document List Filter

`GET /api/v1/documents?include_archived=<bool>&limit=<n>`

Behavior:
- Default: `include_archived=false`
- When false, omit `archiving|archived|purged`.
- When true, include all statuses.

## Data Model Changes

## `tenant_documents` additions

- `archived_at TIMESTAMPTZ NULL`
- `purge_after TIMESTAMPTZ NULL`
- `purged_at TIMESTAMPTZ NULL`
- `archived_reason TEXT NULL`

## `document_archive_jobs` table

Columns:
- `job_id UUID UNIQUE NOT NULL`
- `tenant_id UUID NOT NULL`
- `document_id UUID NOT NULL`
- `status VARCHAR(20) NOT NULL` (`queued|running|succeeded|failed`)
- `retry_count INT NOT NULL DEFAULT 0`
- `next_attempt_at TIMESTAMPTZ NULL`
- `error_message TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `started_at TIMESTAMPTZ NULL`
- `completed_at TIMESTAMPTZ NULL`

## Archive and Purge Processing

## Archive worker responsibilities

1. Claim queued jobs.
2. Delete vectors from Qdrant (`tenant_id + document_id`).
3. Update document:
- `status=archived`
- `archived_at=now()`
- `purge_after=now()+interval '90 days'`
4. Mark job succeeded/failed.

## Purge worker responsibilities

For archived docs where `purge_after <= now()`:
1. Delete object storage bytes (`object_key`).
2. Re-run vector deletion as idempotent safety.
3. Update document:
- `status=purged`
- `purged_at=now()`
- clear `extracted_text`, `preview_html`
- set `chunk_count=0`
4. Preserve metadata row as tombstone.

## Retrieval Guardrail

Before assembling retrieval citations/context, exclude documents with statuses:
- `archiving`
- `archived`
- `purged`

This ensures deleted/archived documents never appear in generated answers.

## Configuration Defaults

- `DOCUMENT_ARCHIVE_RETENTION_DAYS=90`
- `DOCUMENT_ARCHIVE_JOB_BATCH_SIZE=100`
- `DOCUMENT_ARCHIVE_POLL_INTERVAL_SECONDS=15`

## Test Matrix (Required)

1. Delete request returns `202` and creates archive job.
2. Repeated delete is idempotent.
3. Archive worker transitions document to `archived` and removes vectors.
4. Archived docs excluded from retrieval answers/citations.
5. Restore works from `archived` and reindexes.
6. Restore from `purged` returns `409`.
7. Purge worker transitions to `purged` and deletes bytes/vectors.
8. List default excludes archived/purged; filter includes them.
9. Cross-tenant archive/restore attempts are denied.

## Backward Compatibility

- Existing upload/preview/download/index/query APIs remain unchanged.
- New statuses are additive; clients must tolerate unknown future status values.
