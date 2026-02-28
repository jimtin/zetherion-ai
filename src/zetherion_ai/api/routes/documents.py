"""Document upload/index/retrieval routes for tenant public API."""

from __future__ import annotations

import base64
import json
from html import escape
from typing import Any

from aiohttp import web

from zetherion_ai.documents.processing import infer_file_kind
from zetherion_ai.documents.service import DocumentService
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.routes.documents")

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _service(request: web.Request) -> DocumentService:
    service = request.app.get("document_service")
    if not isinstance(service, DocumentService):
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Document service unavailable"}),
            content_type="application/json",
        )
    return service


def _tenant_id(request: web.Request) -> str:
    tenant = request.get("tenant")
    if not isinstance(tenant, dict) or "tenant_id" not in tenant:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Missing tenant context"}),
            content_type="application/json",
        )
    return str(tenant["tenant_id"])


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "hex"):
            out[key] = str(value)
        else:
            out[key] = value
    return out


async def handle_create_upload(request: web.Request) -> web.Response:
    """POST /api/v1/documents/uploads."""
    tenant_id = _tenant_id(request)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    file_name = str(payload.get("file_name") or "").strip()
    mime_type = str(payload.get("mime_type") or "application/octet-stream").strip()
    size_bytes = int(payload.get("size_bytes") or 0)

    if not file_name:
        return web.json_response({"error": "file_name is required"}, status=400)

    upload = await _service(request).create_upload(
        tenant_id=tenant_id,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
    )

    upload_id = str(upload["upload_id"])
    return web.json_response(
        {
            "upload_id": upload_id,
            "tenant_id": tenant_id,
            "status": upload.get("status", "pending"),
            "expires_at": _serialise(upload).get("expires_at"),
            "complete_url": f"/api/v1/documents/uploads/{upload_id}/complete",
        },
        status=201,
    )


async def _read_upload_payload(request: web.Request) -> tuple[bytes, dict[str, Any]]:
    """Read file bytes from multipart or JSON request body."""
    content_type = request.headers.get("Content-Type", "")

    if content_type.startswith("multipart/"):
        reader = await request.multipart()
        file_bytes = b""
        meta: dict[str, Any] = {}

        async for part in reader:
            if part.name == "file":
                file_bytes = await part.read(decode=False)
            elif part.name == "metadata":
                raw = (await part.text()).strip()
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except json.JSONDecodeError:
                        pass

        if not file_bytes:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Missing multipart file part"}),
                content_type="application/json",
            )
        return file_bytes, meta

    try:
        payload = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "Invalid JSON body"}),
            content_type="application/json",
        ) from exc

    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "JSON body must be an object"}),
            content_type="application/json",
        )

    b64 = str(payload.get("file_base64") or payload.get("content_base64") or "").strip()
    if not b64:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "file_base64 is required"}),
            content_type="application/json",
        )

    try:
        data = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "file_base64 must be valid base64"}),
            content_type="application/json",
        ) from exc

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return data, metadata


async def handle_complete_upload(request: web.Request) -> web.Response:
    """POST /api/v1/documents/uploads/{upload_id}/complete."""
    tenant_id = _tenant_id(request)
    upload_id = request.match_info.get("upload_id", "")

    if not upload_id:
        return web.json_response({"error": "upload_id is required"}, status=400)

    try:
        payload, metadata = await _read_upload_payload(request)
    except web.HTTPException as exc:
        raise exc

    if len(payload) > _MAX_UPLOAD_BYTES:
        return web.json_response(
            {"error": f"Upload too large (max {_MAX_UPLOAD_BYTES} bytes)"},
            status=413,
        )

    try:
        doc = await _service(request).complete_upload(
            tenant_id=tenant_id,
            upload_id=upload_id,
            file_bytes=payload,
            metadata=metadata,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)

    return web.json_response(_serialise(doc), status=201)


async def handle_list_documents(request: web.Request) -> web.Response:
    """GET /api/v1/documents."""
    tenant_id = _tenant_id(request)
    tenant_manager = request.app["tenant_manager"]

    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    rows = await tenant_manager.list_documents(tenant_id, limit=limit)
    return web.json_response({"documents": [_serialise(row) for row in rows], "count": len(rows)})


async def handle_get_document(request: web.Request) -> web.Response:
    """GET /api/v1/documents/{document_id}."""
    tenant_id = _tenant_id(request)
    document_id = request.match_info["document_id"]
    tenant_manager = request.app["tenant_manager"]

    row = await tenant_manager.get_document(tenant_id, document_id)
    if row is None:
        return web.json_response({"error": "Document not found"}, status=404)

    return web.json_response(_serialise(row))


async def handle_preview_document(request: web.Request) -> web.StreamResponse:
    """GET /api/v1/documents/{document_id}/preview."""
    tenant_id = _tenant_id(request)
    document_id = request.match_info["document_id"]
    tenant_manager = request.app["tenant_manager"]

    row = await tenant_manager.get_document(tenant_id, document_id)
    if row is None:
        return web.json_response({"error": "Document not found"}, status=404)

    service = _service(request)
    mime_type = str(row.get("mime_type") or "application/octet-stream")
    file_name = str(row.get("file_name") or "document")
    kind = infer_file_kind(file_name, mime_type)

    if kind == "docx":
        preview_html = str(row.get("preview_html") or "").strip()
        if preview_html:
            return web.Response(
                text=preview_html,
                content_type="text/html",
                headers={"Content-Disposition": f'inline; filename="{file_name}.html"'},
            )

        extracted = str(row.get("extracted_text") or "").strip()
        if extracted:
            html = f"<html><body><pre>{escape(extracted[:100000])}</pre></body></html>"
            return web.Response(
                text=html,
                content_type="text/html",
                headers={"Content-Disposition": f'inline; filename="{file_name}.html"'},
            )

    try:
        payload = await service.get_document_payload(tenant_id=tenant_id, document_id=document_id)
    except ValueError:
        return web.json_response({"error": "Document payload not found"}, status=404)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    content_type = "application/pdf" if kind == "pdf" else mime_type
    return web.Response(
        body=payload,
        content_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


async def handle_download_document(request: web.Request) -> web.StreamResponse:
    """GET /api/v1/documents/{document_id}/download."""
    tenant_id = _tenant_id(request)
    document_id = request.match_info["document_id"]
    tenant_manager = request.app["tenant_manager"]

    row = await tenant_manager.get_document(tenant_id, document_id)
    if row is None:
        return web.json_response({"error": "Document not found"}, status=404)

    try:
        payload = await _service(request).get_document_payload(
            tenant_id=tenant_id, document_id=document_id
        )
    except ValueError:
        return web.json_response({"error": "Document payload not found"}, status=404)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    file_name = str(row.get("file_name") or "document")
    mime_type = str(row.get("mime_type") or "application/octet-stream")
    return web.Response(
        body=payload,
        content_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


async def handle_reindex_document(request: web.Request) -> web.Response:
    """POST /api/v1/documents/{document_id}/index."""
    tenant_id = _tenant_id(request)
    document_id = request.match_info["document_id"]

    try:
        doc = await _service(request).index_document(tenant_id=tenant_id, document_id=document_id)
    except ValueError:
        return web.json_response({"error": "Document not found"}, status=404)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response(_serialise(doc), status=200)


async def handle_rag_query(request: web.Request) -> web.Response:
    """POST /api/v1/rag/query."""
    tenant_id = _tenant_id(request)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    query = str(payload.get("query") or payload.get("message") or "").strip()
    top_k = payload.get("top_k", 6)
    provider = payload.get("provider")
    model = payload.get("model")

    try:
        result = await _service(request).query(
            tenant_id=tenant_id,
            query=query,
            top_k=int(top_k),
            provider=str(provider) if provider else None,
            model=str(model) if model else None,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)

    return web.json_response(
        {
            "answer": result.answer,
            "citations": result.citations,
            "provider": result.provider,
            "model": result.model,
        }
    )


async def handle_model_catalog(request: web.Request) -> web.Response:
    """GET /api/v1/models/providers."""
    return web.json_response(_service(request).provider_catalog())
