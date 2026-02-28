"""Unit tests for document ingestion/retrieval service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest

from zetherion_ai.agent.providers import Provider
from zetherion_ai.documents.service import DOCUMENT_COLLECTION, DocumentService


@pytest.fixture()
def document_service() -> tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    tenant_manager = AsyncMock()
    memory = AsyncMock()
    inference = AsyncMock()
    blob_store = AsyncMock()
    service = DocumentService(
        tenant_manager=tenant_manager,
        memory=memory,
        inference_broker=inference,
        blob_store=blob_store,
    )
    return service, tenant_manager, memory, inference, blob_store


@pytest.mark.asyncio
async def test_initialize_is_idempotent(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, _, memory, _, _ = document_service
    await service.initialize()
    await service.initialize()
    memory.ensure_collection.assert_awaited_once_with(DOCUMENT_COLLECTION)


@pytest.mark.asyncio
async def test_create_upload_normalizes_metadata(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, _, _, _ = document_service
    tenant_manager.create_document_upload.return_value = {
        "upload_id": "up-1",
        "status": "pending",
    }

    row = await service.create_upload(
        tenant_id="tenant-1",
        file_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=42,
        metadata=None,
    )

    assert row["upload_id"] == "up-1"
    assert tenant_manager.create_document_upload.call_args.kwargs["metadata"] == {}


@pytest.mark.asyncio
async def test_complete_upload_happy_path(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, _, _, blob_store = document_service
    tenant_manager.get_document_upload.return_value = {
        "status": "pending",
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
        "file_name": "proposal.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "metadata": {"origin": "portal"},
    }
    tenant_manager.create_document.return_value = {"document_id": "doc-1"}
    tenant_manager.get_document.return_value = {"document_id": "doc-1", "status": "indexed"}
    service.index_document = AsyncMock(return_value={"document_id": "doc-1"})  # type: ignore[method-assign]

    result = await service.complete_upload(
        tenant_id="tenant-1",
        upload_id="upload-1",
        file_bytes=b"hello world",
        metadata={"tag": "client-a"},
    )

    assert result["document_id"] == "doc-1"
    blob_store.put_chunk.assert_awaited_once()
    create_kwargs = tenant_manager.create_document.call_args.kwargs
    assert create_kwargs["metadata"] == {"origin": "portal", "tag": "client-a"}
    assert create_kwargs["object_key"].startswith("documents/tenant-1/")
    tenant_manager.mark_document_upload_completed.assert_awaited_once()
    service.index_document.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_complete_upload_raises_when_blob_store_missing() -> None:
    service = DocumentService(
        tenant_manager=AsyncMock(),
        memory=AsyncMock(),
        inference_broker=AsyncMock(),
        blob_store=None,
    )
    with pytest.raises(RuntimeError, match="object storage is not configured"):
        await service.complete_upload(
            tenant_id="tenant-1",
            upload_id="upload-1",
            file_bytes=b"x",
            metadata=None,
        )


@pytest.mark.asyncio
async def test_complete_upload_validates_upload_state_and_expiry(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, _, _, _ = document_service

    tenant_manager.get_document_upload.return_value = None
    with pytest.raises(ValueError, match="Upload not found"):
        await service.complete_upload(
            tenant_id="tenant-1",
            upload_id="upload-1",
            file_bytes=b"x",
            metadata=None,
        )

    tenant_manager.get_document_upload.return_value = {
        "status": "completed",
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
    }
    with pytest.raises(ValueError, match="no longer pending"):
        await service.complete_upload(
            tenant_id="tenant-1",
            upload_id="upload-2",
            file_bytes=b"x",
            metadata=None,
        )

    tenant_manager.get_document_upload.return_value = {
        "status": "pending",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    with pytest.raises(ValueError, match="expired"):
        await service.complete_upload(
            tenant_id="tenant-1",
            upload_id="upload-3",
            file_bytes=b"x",
            metadata=None,
        )


@pytest.mark.asyncio
async def test_index_document_success_updates_payload_and_vectors(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, memory, _, blob_store = document_service
    tenant_manager.get_document.side_effect = [
        {
            "document_id": "doc-1",
            "file_name": "proposal.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "object_key": "documents/tenant-1/doc-1/proposal.docx",
        },
        {"document_id": "doc-1", "status": "indexed"},
    ]
    tenant_manager.create_document_ingestion_job.return_value = {"job_id": "job-1"}
    blob_store.get_chunk.return_value = b"raw bytes"

    with (
        patch("zetherion_ai.documents.service.extract_text", return_value="Alpha Beta"),
        patch("zetherion_ai.documents.service.infer_file_kind", return_value="docx"),
        patch(
            "zetherion_ai.documents.service.build_docx_preview_html",
            return_value="<html>ok</html>",
        ),
        patch("zetherion_ai.documents.service.chunk_text", return_value=["chunk one", "chunk two"]),
    ):
        row = await service.index_document(tenant_id="tenant-1", document_id="doc-1")

    assert row["status"] == "indexed"
    memory.delete_by_filters.assert_awaited_once_with(
        DOCUMENT_COLLECTION,
        filters={"tenant_id": "tenant-1", "document_id": "doc-1"},
    )
    assert memory.store_with_payload.await_count == 2
    tenant_manager.update_document_index_payload.assert_awaited_once_with(
        "tenant-1",
        document_id="doc-1",
        extracted_text="Alpha Beta",
        preview_html="<html>ok</html>",
        chunk_count=2,
        status="indexed",
        error_message=None,
    )
    tenant_manager.update_document_ingestion_job.assert_awaited_with(
        "tenant-1",
        job_id="job-1",
        status="indexed",
        error_message=None,
    )


@pytest.mark.asyncio
async def test_index_document_failure_marks_job_failed(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, _, _, blob_store = document_service
    tenant_manager.get_document.return_value = {
        "document_id": "doc-1",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "object_key": "documents/tenant-1/doc-1/report.pdf",
    }
    tenant_manager.create_document_ingestion_job.return_value = {"job_id": "job-9"}
    blob_store.get_chunk.return_value = None

    with pytest.raises(RuntimeError, match="binary payload not found"):
        await service.index_document(tenant_id="tenant-1", document_id="doc-1")

    assert tenant_manager.update_document_status.await_count >= 2
    tenant_manager.update_document_ingestion_job.assert_any_await(
        "tenant-1",
        job_id="job-9",
        status="failed",
        error_message=ANY,
    )


@pytest.mark.asyncio
async def test_query_validations_and_empty_results(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, _, memory, inference, _ = document_service

    with pytest.raises(ValueError, match="cannot be empty"):
        await service.query(tenant_id="tenant-1", query="   ")

    memory.search_collection.return_value = []
    result = await service.query(tenant_id="tenant-1", query="What is in docs?")
    assert result.provider == "none"
    assert result.model == "none"
    inference.infer.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_calls_inference_and_deduplicates_citations(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, _, memory, inference, _ = document_service
    memory.search_collection.return_value = [
        {
            "document_id": "doc-1",
            "file_name": "a.pdf",
            "chunk_index": 0,
            "content": "first chunk",
        },
        {
            "document_id": "doc-1",
            "file_name": "a.pdf",
            "chunk_index": 1,
            "content": "second chunk",
        },
    ]
    inference.infer.return_value = SimpleNamespace(
        content="Answer from context",
        provider=Provider.GROQ,
        model="llama-3.3-70b-versatile",
    )
    settings = SimpleNamespace(
        groq_model="llama-3.3-70b-versatile",
        openai_model="gpt-5.2",
        claude_model="claude-sonnet-4-6",
        rag_allowed_providers="groq,openai,claude",
        rag_allowed_models="llama-3.3-70b-versatile,gpt-5.2,claude-sonnet-4-6",
    )
    with patch("zetherion_ai.documents.service.get_settings", return_value=settings):
        result = await service.query(
            tenant_id="tenant-1",
            query="Summarize",
            top_k=50,
            provider="groq",
            model="llama-3.3-70b-versatile",
        )

    assert result.answer == "Answer from context"
    assert result.citations == [{"document_id": "doc-1", "file_name": "a.pdf"}]
    search_call = memory.search_collection.call_args.kwargs
    assert search_call["limit"] == 20
    infer_call = inference.infer.call_args.kwargs
    assert infer_call["forced_provider"] == Provider.GROQ
    assert infer_call["forced_model"] == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
async def test_query_requires_inference_broker() -> None:
    service = DocumentService(
        tenant_manager=AsyncMock(),
        memory=AsyncMock(),
        inference_broker=None,
        blob_store=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="Inference broker is not configured"):
        await service.query(tenant_id="tenant-1", query="Hello")


@pytest.mark.asyncio
async def test_get_document_payload_success_and_failures(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, tenant_manager, _, _, blob_store = document_service
    tenant_manager.get_document.return_value = {"object_key": "documents/t-1/d-1/file.pdf"}
    blob_store.get_chunk.return_value = b"payload"
    payload = await service.get_document_payload(tenant_id="tenant-1", document_id="doc-1")
    assert payload == b"payload"

    blob_store.get_chunk.return_value = None
    with pytest.raises(ValueError, match="payload not found"):
        await service.get_document_payload(tenant_id="tenant-1", document_id="doc-1")

    tenant_manager.get_document.return_value = None
    with pytest.raises(ValueError, match="Document not found"):
        await service.get_document_payload(tenant_id="tenant-1", document_id="missing")


def test_provider_catalog_and_provider_model_resolution(
    document_service: tuple[DocumentService, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    service, _, _, _, _ = document_service
    settings = SimpleNamespace(
        groq_model="llama-3.3-70b-versatile",
        openai_model="gpt-5.2",
        claude_model="claude-sonnet-4-6",
        rag_allowed_providers="groq,openai,claude",
        rag_allowed_models="extra-model",
    )
    with patch("zetherion_ai.documents.service.get_settings", return_value=settings):
        catalog = service.provider_catalog()
        assert "extra-model" in catalog["allowed_models"]
        provider, model = service._resolve_provider_model(
            provider="groq",
            model="llama-3.3-70b-versatile",
        )
        assert provider == Provider.GROQ
        assert model == "llama-3.3-70b-versatile"

        inferred_provider, _ = service._resolve_provider_model(provider=None, model="gpt-5.2")
        assert inferred_provider == Provider.OPENAI

        with pytest.raises(ValueError, match="provider is not allowed"):
            service._resolve_provider_model(provider="unknown", model=None)
        with pytest.raises(ValueError, match="model is not allowed"):
            service._resolve_provider_model(provider="groq", model="not-allowed")


def test_parse_json_and_checksum_helpers() -> None:
    assert DocumentService.parse_json('{"k":"v"}') == {"k": "v"}
    assert DocumentService.parse_json("not-json") == {}
    assert DocumentService.parse_json(None) == {}
    checksum = DocumentService.checksum_sha256(b"hello")
    assert len(checksum) == 64
