"""Tenant document ingestion, indexing, and retrieval orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from zetherion_ai.agent.inference import InferenceBroker
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.analytics.replay_store import ReplayStore
from zetherion_ai.config import get_settings
from zetherion_ai.documents.processing import (
    build_docx_preview_html,
    chunk_text,
    extract_text,
    infer_file_kind,
    normalize_metadata,
    safe_filename_component,
)
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.documents.service")

DOCUMENT_COLLECTION = "tenant_documents"


@dataclass
class DocumentQueryResult:
    """Result payload for document retrieval queries."""

    answer: str
    citations: list[dict[str, Any]]
    provider: str
    model: str


class DocumentService:
    """Coordinates document storage, indexing, and RAG responses."""

    def __init__(
        self,
        *,
        tenant_manager: Any,
        memory: QdrantMemory,
        inference_broker: InferenceBroker | None,
        blob_store: ReplayStore | None,
    ) -> None:
        self._tenant_manager = tenant_manager
        self._memory = memory
        self._inference = inference_broker
        self._blob_store = blob_store
        self._initialized = False

    async def initialize(self) -> None:
        """Ensure required vector collections exist."""
        if self._initialized:
            return
        await self._memory.ensure_collection(DOCUMENT_COLLECTION)
        self._initialized = True
        log.info("document_service_ready")

    async def create_upload(
        self,
        *,
        tenant_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Create a pending document upload intent."""
        await self.initialize()
        upload_id = str(uuid4())
        expires_at = datetime.now(UTC) + timedelta(hours=1)

        upload = await self._tenant_manager.create_document_upload(
            tenant_id,
            upload_id=upload_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=max(0, int(size_bytes or 0)),
            metadata=normalize_metadata(metadata),
            expires_at=expires_at,
        )
        return upload

    async def complete_upload(
        self,
        *,
        tenant_id: str,
        upload_id: str,
        file_bytes: bytes,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Finalize upload, persist binary, and run initial indexing."""
        await self.initialize()

        if self._blob_store is None:
            raise RuntimeError("Document object storage is not configured")

        upload = await self._tenant_manager.get_document_upload(tenant_id, upload_id)
        if upload is None:
            raise ValueError("Upload not found")

        status = str(upload.get("status", "")).lower()
        if status != "pending":
            raise ValueError("Upload is no longer pending")

        expires_at = upload.get("expires_at")
        if hasattr(expires_at, "isoformat"):
            expires_dt = expires_at
        else:
            expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expires_dt < datetime.now(UTC):
            raise ValueError("Upload token has expired")

        file_name = str(upload.get("file_name") or "document")
        mime_type = str(upload.get("mime_type") or "application/octet-stream")
        document_id = str(uuid4())
        object_key = self._build_object_key(
            tenant_id=tenant_id,
            document_id=document_id,
            name=file_name,
        )
        checksum = hashlib.sha256(file_bytes).hexdigest()

        await self._blob_store.put_chunk(object_key, file_bytes)

        document = await self._tenant_manager.create_document(
            tenant_id,
            document_id=document_id,
            file_name=file_name,
            mime_type=mime_type,
            object_key=object_key,
            status="uploaded",
            size_bytes=len(file_bytes),
            checksum_sha256=checksum,
            metadata={
                **normalize_metadata(upload.get("metadata")),
                **normalize_metadata(metadata),
            },
        )
        await self._tenant_manager.mark_document_upload_completed(
            tenant_id,
            upload_id=upload_id,
            document_id=document_id,
        )

        await self.index_document(tenant_id=tenant_id, document_id=document_id)

        latest = await self._tenant_manager.get_document(tenant_id, document_id)
        return latest or document

    async def index_document(self, *, tenant_id: str, document_id: str) -> dict[str, Any]:
        """Extract text and index document chunks into vector store."""
        await self.initialize()

        if self._blob_store is None:
            raise RuntimeError("Document object storage is not configured")

        document = await self._tenant_manager.get_document(tenant_id, document_id)
        if document is None:
            raise ValueError("Document not found")

        job = await self._tenant_manager.create_document_ingestion_job(
            tenant_id,
            document_id=document_id,
            status="processing",
            error_message=None,
        )

        await self._tenant_manager.update_document_status(
            tenant_id,
            document_id=document_id,
            status="processing",
            error_message=None,
        )

        object_key = str(document.get("object_key") or "")
        if not object_key:
            raise RuntimeError("Document object key is missing")

        try:
            payload = await self._blob_store.get_chunk(object_key)
            if payload is None:
                raise RuntimeError("Document binary payload not found")

            file_name = str(document.get("file_name") or "document")
            mime_type = str(document.get("mime_type") or "application/octet-stream")
            extracted = extract_text(file_name, mime_type, payload)
            if not extracted.strip():
                # Fall back to simple decoded text snapshot.
                extracted = payload.decode("utf-8", errors="ignore").strip()

            kind = infer_file_kind(file_name, mime_type)
            preview_html = None
            if kind == "docx" and extracted:
                preview_html = build_docx_preview_html(extracted)

            chunks = chunk_text(extracted)
            if not chunks:
                chunks = [f"Document content for {file_name}"]

            await self._memory.delete_by_filters(
                DOCUMENT_COLLECTION,
                filters={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                },
            )

            for idx, chunk in enumerate(chunks):
                point_id = str(uuid4())
                await self._memory.store_with_payload(
                    collection_name=DOCUMENT_COLLECTION,
                    point_id=point_id,
                    payload={
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "chunk_index": idx,
                        "content": chunk,
                        "indexed_at": datetime.now(UTC).isoformat(),
                    },
                    text=chunk,
                )

            await self._tenant_manager.update_document_index_payload(
                tenant_id,
                document_id=document_id,
                extracted_text=extracted[:200000],
                preview_html=preview_html,
                chunk_count=len(chunks),
                status="indexed",
                error_message=None,
            )

            await self._tenant_manager.update_document_ingestion_job(
                tenant_id,
                job_id=str(job["job_id"]),
                status="indexed",
                error_message=None,
            )
        except Exception as exc:
            await self._tenant_manager.update_document_status(
                tenant_id,
                document_id=document_id,
                status="failed",
                error_message=str(exc),
            )
            await self._tenant_manager.update_document_ingestion_job(
                tenant_id,
                job_id=str(job["job_id"]),
                status="failed",
                error_message=str(exc),
            )
            log.exception("document_index_failed", tenant_id=tenant_id, document_id=document_id)
            raise

        latest = await self._tenant_manager.get_document(tenant_id, document_id)
        return latest or document

    async def query(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int = 6,
        provider: str | None = None,
        model: str | None = None,
    ) -> DocumentQueryResult:
        """Run tenant-scoped retrieval augmented generation query."""
        await self.initialize()

        if not query.strip():
            raise ValueError("Query cannot be empty")

        if self._inference is None:
            raise RuntimeError("Inference broker is not configured")

        matches = await self._memory.search_collection(
            DOCUMENT_COLLECTION,
            query=query,
            filters={"tenant_id": tenant_id},
            limit=max(1, min(int(top_k), 20)),
        )

        if not matches:
            return DocumentQueryResult(
                answer="I could not find relevant document context for that query.",
                citations=[],
                provider="none",
                model="none",
            )

        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        context_lines: list[str] = []
        for match in matches:
            doc_id = str(match.get("document_id", ""))
            file_name = str(match.get("file_name", "document"))
            key = (doc_id, file_name)
            if key not in seen:
                citations.append({"document_id": doc_id, "file_name": file_name})
                seen.add(key)
            chunk_index = match.get("chunk_index", 0)
            content = str(match.get("content", ""))[:1600]
            context_lines.append(f"[{file_name}#{chunk_index}] {content}")

        forced_provider, forced_model = self._resolve_provider_model(provider=provider, model=model)

        system_prompt = "\n".join(
            [
                "You answer questions using tenant documents only.",
                "Use concise answers and cite source file names inline when possible.",
                "If context is insufficient, say so clearly.",
                "Do not fabricate facts.",
            ]
        )

        prompt = (
            f"Question: {query}\n\n"
            "Context:\n"
            + "\n".join(context_lines)
            + "\n\nProvide an answer with short source citations."
        )

        result = await self._inference.infer(
            prompt=prompt,
            task_type=TaskType.LONG_DOCUMENT,
            system_prompt=system_prompt,
            max_tokens=900,
            temperature=0.2,
            forced_provider=forced_provider,
            forced_model=forced_model,
        )

        return DocumentQueryResult(
            answer=result.content,
            citations=citations,
            provider=result.provider.value,
            model=result.model,
        )

    async def get_document_payload(self, *, tenant_id: str, document_id: str) -> bytes:
        """Load raw bytes for a tenant document."""
        if self._blob_store is None:
            raise RuntimeError("Document object storage is not configured")

        doc = await self._tenant_manager.get_document(tenant_id, document_id)
        if doc is None:
            raise ValueError("Document not found")

        object_key = str(doc.get("object_key") or "")
        payload = await self._blob_store.get_chunk(object_key)
        if payload is None:
            raise ValueError("Document payload not found")
        return payload

    def provider_catalog(self) -> dict[str, Any]:
        """Return supported providers/models for client-side selector UIs."""
        settings = get_settings()
        return {
            "providers": ["groq", "openai", "claude"],
            "defaults": {
                "groq": settings.groq_model,
                "openai": settings.openai_model,
                "claude": settings.claude_model,
            },
            "allowed_models": sorted(self._allowed_models()),
        }

    def _build_object_key(self, *, tenant_id: str, document_id: str, name: str) -> str:
        safe_name = safe_filename_component(name)
        return f"documents/{tenant_id}/{document_id}/{safe_name}"

    def _allowed_providers(self) -> set[str]:
        settings = get_settings()
        raw = (settings.rag_allowed_providers or "groq,openai,claude").strip()
        values = {p.strip().lower() for p in raw.split(",") if p.strip()}
        return values or {"groq", "openai", "claude"}

    def _allowed_models(self) -> set[str]:
        settings = get_settings()
        defaults = {settings.groq_model, settings.openai_model, settings.claude_model}
        extra = {m.strip() for m in (settings.rag_allowed_models or "").split(",") if m.strip()}
        return defaults | extra

    def _resolve_provider_model(
        self,
        *,
        provider: str | None,
        model: str | None,
    ) -> tuple[Provider | None, str | None]:
        provider_map = {
            "groq": Provider.GROQ,
            "openai": Provider.OPENAI,
            "claude": Provider.CLAUDE,
            "anthropic": Provider.CLAUDE,
        }

        forced_provider: Provider | None = None
        if provider:
            key = provider.strip().lower()
            if key not in self._allowed_providers() or key not in provider_map:
                raise ValueError("Requested provider is not allowed")
            forced_provider = provider_map[key]

        forced_model = model.strip() if isinstance(model, str) and model.strip() else None
        if forced_model and forced_model not in self._allowed_models():
            raise ValueError("Requested model is not allowed")

        if forced_model and forced_provider is None:
            # Infer provider from known model defaults.
            settings = get_settings()
            if forced_model == settings.groq_model:
                forced_provider = Provider.GROQ
            elif forced_model == settings.openai_model:
                forced_provider = Provider.OPENAI
            elif forced_model == settings.claude_model:
                forced_provider = Provider.CLAUDE

        return forced_provider, forced_model

    @staticmethod
    def checksum_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def parse_json(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
