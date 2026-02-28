"""Document parsing and chunking helpers."""

from __future__ import annotations

import re
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile


def infer_file_kind(filename: str, mime_type: str | None = None) -> str:
    """Infer coarse file kind from mime type and extension."""
    mime = (mime_type or "").lower().strip()
    suffix = Path(filename).suffix.lower()

    if "pdf" in mime or suffix == ".pdf":
        return "pdf"
    if "word" in mime or "docx" in mime or suffix == ".docx":
        return "docx"
    if "text" in mime or suffix in {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}:
        return "text"
    return "binary"


def _extract_pdf_text_optional(data: bytes) -> str:
    """Extract PDF text using optional pypdf, with safe fallback."""
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]

        reader = PdfReader(BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text.strip())
        return "\n\n".join(chunks)
    except Exception:
        # Last-resort fallback: decode printable bytes.
        text = data.decode("utf-8", errors="ignore")
        text = re.sub(r"\s+", " ", text)
        return text.strip()


def _extract_docx_text(data: bytes) -> str:
    """Extract text from DOCX without external dependencies."""
    try:
        import defusedxml.ElementTree as ElementTree  # type: ignore[import-not-found]
    except Exception:
        return ""

    with ZipFile(BytesIO(data)) as zf:
        if "word/document.xml" not in zf.namelist():
            return ""
        xml_data = zf.read("word/document.xml")

    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        return ""

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for para in root.findall(".//w:p", ns):
        runs = [node.text or "" for node in para.findall(".//w:t", ns)]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_text(filename: str, mime_type: str | None, data: bytes) -> str:
    """Extract best-effort plain text from a document payload."""
    kind = infer_file_kind(filename, mime_type)
    if kind == "pdf":
        return _extract_pdf_text_optional(data)
    if kind == "docx":
        return _extract_docx_text(data)
    if kind == "text":
        return data.decode("utf-8", errors="ignore")
    return ""


def build_docx_preview_html(extracted_text: str, *, max_paragraphs: int = 80) -> str:
    """Build a simple sanitized HTML preview from extracted text."""
    paragraphs = [p.strip() for p in extracted_text.split("\n") if p.strip()]
    body = "\n".join(f"<p>{escape(p)}</p>" for p in paragraphs[:max_paragraphs])
    return "\n".join(
        [
            "<!doctype html>",
            '<html><head><meta charset="utf-8"><title>Document Preview</title></head>',
            f"<body>{body}</body></html>",
        ]
    )


def chunk_text(text: str, *, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    """Chunk text with overlap for semantic retrieval."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []

    chunks: list[str] = []
    start = 0
    length = len(clean)
    while start < length:
        end = min(length, start + chunk_size)
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(0, end - overlap)

    # De-duplicate adjacent identical chunks.
    deduped: list[str] = []
    for chunk in chunks:
        if not deduped or deduped[-1] != chunk:
            deduped.append(chunk)
    return deduped


def safe_filename_component(value: str) -> str:
    """Normalize filename components for object-key safety."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", value)
    return re.sub(r"-+", "-", sanitized).strip("-") or "document"


def normalize_metadata(metadata: Any) -> dict[str, Any]:
    """Ensure metadata is a JSON-compatible object."""
    if isinstance(metadata, dict):
        return metadata
    return {}
