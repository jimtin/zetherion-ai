"""Unit tests for public API document routes."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.documents import (
    _serialise,
    _service,
    _tenant_id,
    handle_archive_document,
    handle_complete_upload,
    handle_create_upload,
    handle_download_document,
    handle_get_document,
    handle_list_documents,
    handle_model_catalog,
    handle_preview_document,
    handle_rag_query,
    handle_reindex_document,
    handle_restore_document,
)
from zetherion_ai.documents.service import (
    DocumentLifecycleError,
    DocumentQueryResult,
    DocumentService,
)


@pytest_asyncio.fixture()
async def documents_client():
    tenant = {"tenant_id": "tenant-1"}
    tenant_manager = AsyncMock()
    service = DocumentService(
        tenant_manager=tenant_manager,
        memory=AsyncMock(),
        inference_broker=AsyncMock(),
        blob_store=AsyncMock(),
    )

    @web.middleware
    async def inject_tenant(request: web.Request, handler):
        request["tenant"] = tenant
        return await handler(request)

    app = web.Application(middlewares=[inject_tenant])
    app["tenant_manager"] = tenant_manager
    app["document_service"] = service
    app.router.add_post("/api/v1/documents/uploads", handle_create_upload)
    app.router.add_post("/api/v1/documents/uploads/{upload_id}/complete", handle_complete_upload)
    app.router.add_get("/api/v1/documents", handle_list_documents)
    app.router.add_get("/api/v1/documents/{document_id}", handle_get_document)
    app.router.add_delete("/api/v1/documents/{document_id}", handle_archive_document)
    app.router.add_get("/api/v1/documents/{document_id}/preview", handle_preview_document)
    app.router.add_get("/api/v1/documents/{document_id}/download", handle_download_document)
    app.router.add_post("/api/v1/documents/{document_id}/index", handle_reindex_document)
    app.router.add_post("/api/v1/documents/{document_id}/restore", handle_restore_document)
    app.router.add_post("/api/v1/rag/query", handle_rag_query)
    app.router.add_get("/api/v1/models/providers", handle_model_catalog)

    async with TestClient(TestServer(app)) as client:
        yield client, service, tenant_manager


def test_serialise_converts_datetime_and_uuid_like_values() -> None:
    class _UUIDLike:
        hex = "abc123"

        def __str__(self) -> str:
            return "uuid-as-string"

    now = datetime.now(UTC)
    out = _serialise({"created_at": now, "id": _UUIDLike(), "count": 2})
    assert out["created_at"] == now.isoformat()
    assert out["id"] == "uuid-as-string"
    assert out["count"] == 2


def test_tenant_and_service_helpers_validate_presence() -> None:
    request = MagicMock()
    request.app = {}
    with pytest.raises(web.HTTPServiceUnavailable):
        _service(request)

    request_with_missing_tenant = MagicMock()
    request_with_missing_tenant.get.return_value = None
    with pytest.raises(web.HTTPUnauthorized):
        _tenant_id(request_with_missing_tenant)


@pytest.mark.asyncio
async def test_create_upload_validates_payload_and_returns_201(documents_client) -> None:
    client, service, _ = documents_client
    service.create_upload = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "upload_id": "up-1",
            "status": "pending",
            "expires_at": datetime.now(UTC),
        }
    )

    bad_json = await client.post(
        "/api/v1/documents/uploads",
        data="{bad",
        headers={"Content-Type": "application/json"},
    )
    assert bad_json.status == 400

    missing_name = await client.post(
        "/api/v1/documents/uploads",
        json={"mime_type": "application/pdf"},
    )
    assert missing_name.status == 400

    response = await client.post(
        "/api/v1/documents/uploads",
        json={"file_name": "report.pdf", "mime_type": "application/pdf", "size_bytes": 99},
    )
    assert response.status == 201
    body = await response.json()
    assert body["upload_id"] == "up-1"
    assert body["complete_url"].endswith("/up-1/complete")


@pytest.mark.asyncio
async def test_complete_upload_handles_json_and_multipart_payloads(documents_client) -> None:
    client, service, _ = documents_client
    service.complete_upload = AsyncMock(return_value={"document_id": "doc-1"})  # type: ignore[method-assign]

    invalid_b64 = await client.post(
        "/api/v1/documents/uploads/up-1/complete",
        json={"file_base64": "not-base64"},
    )
    assert invalid_b64.status == 400

    encoded = base64.b64encode(b"hello").decode("ascii")
    json_ok = await client.post(
        "/api/v1/documents/uploads/up-1/complete",
        json={"file_base64": encoded, "metadata": {"from": "json"}},
    )
    assert json_ok.status == 201
    json_call = service.complete_upload.call_args.kwargs
    assert json_call["file_bytes"] == b"hello"
    assert json_call["metadata"] == {"from": "json"}

    form = FormData()
    form.add_field("file", b"hello-multipart", filename="report.txt", content_type="text/plain")
    form.add_field("metadata", json.dumps({"from": "multipart"}))
    multipart_ok = await client.post("/api/v1/documents/uploads/up-2/complete", data=form)
    assert multipart_ok.status == 201
    multipart_call = service.complete_upload.call_args.kwargs
    assert multipart_call["file_bytes"] == b"hello-multipart"
    assert multipart_call["metadata"] == {"from": "multipart"}


@pytest.mark.asyncio
async def test_complete_upload_maps_service_errors(documents_client) -> None:
    client, service, _ = documents_client
    service.complete_upload = AsyncMock(side_effect=ValueError("Upload not found"))  # type: ignore[method-assign]
    value_error = await client.post(
        "/api/v1/documents/uploads/up-1/complete",
        json={"file_base64": base64.b64encode(b"hello").decode("ascii")},
    )
    assert value_error.status == 400

    service.complete_upload = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("storage unavailable")
    )
    runtime_error = await client.post(
        "/api/v1/documents/uploads/up-1/complete",
        json={"file_base64": base64.b64encode(b"hello").decode("ascii")},
    )
    assert runtime_error.status == 503


@pytest.mark.asyncio
async def test_complete_upload_rejects_missing_multipart_file(documents_client) -> None:
    client, _, _ = documents_client
    form = FormData()
    form.add_field("metadata", json.dumps({"x": 1}))
    response = await client.post("/api/v1/documents/uploads/up-1/complete", data=form)
    assert response.status == 400


@pytest.mark.asyncio
async def test_list_and_get_document_routes(documents_client) -> None:
    client, _, tenant_manager = documents_client
    tenant_manager.list_documents = AsyncMock(return_value=[{"document_id": "doc-1"}])
    tenant_manager.get_document = AsyncMock(
        side_effect=[None, {"document_id": "doc-1", "file_name": "report.pdf"}]
    )

    list_response = await client.get("/api/v1/documents?limit=bad")
    assert list_response.status == 200
    assert (await list_response.json())["count"] == 1
    assert tenant_manager.list_documents.call_args.kwargs["include_archived"] is False

    include_archived = await client.get("/api/v1/documents?include_archived=true")
    assert include_archived.status == 200
    assert tenant_manager.list_documents.call_args.kwargs["include_archived"] is True

    invalid_include_archived = await client.get("/api/v1/documents?include_archived=maybe")
    assert invalid_include_archived.status == 400

    missing = await client.get("/api/v1/documents/doc-404")
    assert missing.status == 404
    found = await client.get("/api/v1/documents/doc-1")
    assert found.status == 200


@pytest.mark.asyncio
async def test_preview_document_uses_docx_preview_or_extracted_text(documents_client) -> None:
    client, _, tenant_manager = documents_client
    tenant_manager.get_document = AsyncMock(
        side_effect=[
            {
                "document_id": "doc-1",
                "file_name": "draft.docx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "preview_html": "<html><body>Preview</body></html>",
            },
            {
                "document_id": "doc-2",
                "file_name": "draft.docx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "extracted_text": "Line 1",
            },
        ]
    )

    preview_html = await client.get("/api/v1/documents/doc-1/preview")
    assert preview_html.status == 200
    assert "Preview" in (await preview_html.text())

    fallback_html = await client.get("/api/v1/documents/doc-2/preview")
    assert fallback_html.status == 200
    assert "<pre>Line 1</pre>" in (await fallback_html.text())


@pytest.mark.asyncio
async def test_preview_and_download_stream_binary_payload(documents_client) -> None:
    client, service, tenant_manager = documents_client
    tenant_manager.get_document = AsyncMock(
        return_value={
            "document_id": "doc-1",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
        }
    )
    service.get_document_payload = AsyncMock(return_value=b"%PDF-1.7")  # type: ignore[method-assign]

    preview = await client.get("/api/v1/documents/doc-1/preview")
    assert preview.status == 200
    assert preview.headers["Content-Disposition"].startswith("inline")

    download = await client.get("/api/v1/documents/doc-1/download")
    assert download.status == 200
    assert download.headers["Content-Disposition"].startswith("attachment")
    assert await download.read() == b"%PDF-1.7"


@pytest.mark.asyncio
async def test_preview_and_download_map_payload_errors(documents_client) -> None:
    client, service, tenant_manager = documents_client
    tenant_manager.get_document = AsyncMock(
        return_value={"file_name": "report.pdf", "mime_type": "application/pdf"}
    )
    service.get_document_payload = AsyncMock(side_effect=ValueError("missing"))  # type: ignore[method-assign]
    preview_missing = await client.get("/api/v1/documents/doc-1/preview")
    assert preview_missing.status == 404
    download_missing = await client.get("/api/v1/documents/doc-1/download")
    assert download_missing.status == 404

    service.get_document_payload = AsyncMock(side_effect=RuntimeError("storage down"))  # type: ignore[method-assign]
    preview_error = await client.get("/api/v1/documents/doc-1/preview")
    assert preview_error.status == 503
    download_error = await client.get("/api/v1/documents/doc-1/download")
    assert download_error.status == 503


@pytest.mark.asyncio
async def test_reindex_document_maps_errors(documents_client) -> None:
    client, service, _ = documents_client
    service.index_document = AsyncMock(return_value={"document_id": "doc-1"})  # type: ignore[method-assign]
    success = await client.post("/api/v1/documents/doc-1/index")
    assert success.status == 200

    service.index_document = AsyncMock(side_effect=ValueError("missing"))  # type: ignore[method-assign]
    missing = await client.post("/api/v1/documents/doc-1/index")
    assert missing.status == 404

    service.index_document = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    failure = await client.post("/api/v1/documents/doc-1/index")
    assert failure.status == 500


@pytest.mark.asyncio
async def test_archive_document_route_maps_status_and_idempotency(documents_client) -> None:
    client, service, _ = documents_client
    service.request_archive = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            ValueError("Document not found"),
            {
                "document": {
                    "document_id": "doc-1",
                    "tenant_id": "tenant-1",
                    "status": "archiving",
                },
                "archive_job_id": "job-1",
                "idempotent": False,
            },
            {
                "document": {"document_id": "doc-1", "tenant_id": "tenant-1", "status": "archived"},
                "archive_job_id": None,
                "idempotent": True,
            },
        ]
    )

    not_found = await client.delete("/api/v1/documents/doc-1")
    assert not_found.status == 404

    scheduled = await client.delete(
        "/api/v1/documents/doc-1",
        json={"reason": "cleanup"},
    )
    assert scheduled.status == 202
    scheduled_body = await scheduled.json()
    assert scheduled_body["archive_job_id"] == "job-1"
    assert scheduled_body["message"] == "Archive scheduled"
    assert service.request_archive.call_args_list[1].kwargs["archived_reason"] == "cleanup"

    idempotent = await client.delete("/api/v1/documents/doc-1")
    assert idempotent.status == 202
    idempotent_body = await idempotent.json()
    assert idempotent_body["archive_job_id"] is None
    assert idempotent_body["message"] == "Archive already scheduled"


@pytest.mark.asyncio
async def test_archive_document_route_validates_reason_and_errors(documents_client) -> None:
    client, service, _ = documents_client
    service.request_archive = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            DocumentLifecycleError("Document cannot be archived from status 'processing'"),
            RuntimeError("storage down"),
        ]
    )

    invalid_reason = await client.delete(
        "/api/v1/documents/doc-1",
        json={"reason": 123},
    )
    assert invalid_reason.status == 400

    conflict = await client.delete("/api/v1/documents/doc-1")
    assert conflict.status == 409

    runtime_error = await client.delete("/api/v1/documents/doc-1")
    assert runtime_error.status == 503


@pytest.mark.asyncio
async def test_restore_document_route_maps_errors_and_success(documents_client) -> None:
    client, service, _ = documents_client
    service.restore_document = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            ValueError("Document not found"),
            DocumentLifecycleError("Purged document cannot be restored"),
            RuntimeError("upstream unavailable"),
            {"document_id": "doc-1", "status": "indexed"},
        ]
    )

    not_found = await client.post("/api/v1/documents/doc-1/restore")
    assert not_found.status == 404

    conflict = await client.post("/api/v1/documents/doc-1/restore")
    assert conflict.status == 409

    unavailable = await client.post("/api/v1/documents/doc-1/restore")
    assert unavailable.status == 503

    success = await client.post("/api/v1/documents/doc-1/restore")
    assert success.status == 200
    assert (await success.json())["status"] == "indexed"


@pytest.mark.asyncio
async def test_rag_query_and_model_catalog(documents_client) -> None:
    client, service, _ = documents_client
    service.query = AsyncMock(  # type: ignore[method-assign]
        return_value=DocumentQueryResult(
            answer="From docs",
            citations=[{"document_id": "doc-1", "file_name": "report.pdf"}],
            provider="groq",
            model="llama-3.3-70b-versatile",
        )
    )
    service.provider_catalog = MagicMock(  # type: ignore[method-assign]
        return_value={"providers": ["groq"]}
    )

    bad_json = await client.post(
        "/api/v1/rag/query",
        data="{bad",
        headers={"Content-Type": "application/json"},
    )
    assert bad_json.status == 400

    success = await client.post(
        "/api/v1/rag/query",
        json={"query": "Summarize", "provider": "groq", "model": "llama-3.3-70b-versatile"},
    )
    assert success.status == 200
    body = await success.json()
    assert body["provider"] == "groq"

    service.query = AsyncMock(side_effect=ValueError("Query cannot be empty"))  # type: ignore[method-assign]
    bad_query = await client.post("/api/v1/rag/query", json={"query": ""})
    assert bad_query.status == 400

    service.query = AsyncMock(side_effect=RuntimeError("inference unavailable"))  # type: ignore[method-assign]
    unavailable = await client.post("/api/v1/rag/query", json={"query": "hello"})
    assert unavailable.status == 503

    providers = await client.get("/api/v1/models/providers")
    assert providers.status == 200
    assert (await providers.json())["providers"] == ["groq"]
