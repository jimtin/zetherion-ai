"""Unit tests for document processing helpers."""

from __future__ import annotations

import builtins
import sys
import types
import xml.etree.ElementTree as ElementTree
from io import BytesIO
from zipfile import ZipFile

from zetherion_ai.documents import processing


def _build_docx_bytes(xml_payload: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w") as archive:
        archive.writestr("word/document.xml", xml_payload)
    return buffer.getvalue()


def _install_defusedxml_stub() -> None:
    """Provide a lightweight defusedxml stub for import-time resolution."""
    pkg = types.ModuleType("defusedxml")
    pkg.ElementTree = ElementTree
    sys.modules["defusedxml"] = pkg
    sys.modules["defusedxml.ElementTree"] = ElementTree


def test_infer_file_kind_uses_mime_and_extension() -> None:
    assert processing.infer_file_kind("report.pdf", None) == "pdf"
    assert processing.infer_file_kind("report.bin", "application/pdf") == "pdf"
    assert processing.infer_file_kind("notes.docx", None) == "docx"
    assert processing.infer_file_kind("note.txt", "text/plain") == "text"
    assert processing.infer_file_kind("blob.bin", "application/octet-stream") == "binary"


def test_extract_docx_text_reads_paragraphs() -> None:
    _install_defusedxml_stub()
    payload = _build_docx_bytes(
        """
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>Hello</w:t></w:r></w:p>
            <w:p><w:r><w:t>World</w:t></w:r></w:p>
          </w:body>
        </w:document>
        """.strip()
    )

    text = processing._extract_docx_text(payload)
    assert text == "Hello\n\nWorld"


def test_extract_docx_text_returns_empty_when_document_xml_missing() -> None:
    _install_defusedxml_stub()
    buffer = BytesIO()
    with ZipFile(buffer, mode="w") as archive:
        archive.writestr("word/styles.xml", "<xml/>")
    assert processing._extract_docx_text(buffer.getvalue()) == ""


def test_extract_docx_text_returns_empty_for_invalid_xml() -> None:
    _install_defusedxml_stub()
    payload = _build_docx_bytes("<w:document><broken></w:document>")
    assert processing._extract_docx_text(payload) == ""


def test_extract_docx_text_returns_empty_when_defusedxml_unavailable() -> None:
    original_import = builtins.__import__

    def _fake_import(
        name: str,
        globals_obj: object | None = None,
        locals_obj: object | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("defusedxml"):
            raise ModuleNotFoundError(name)
        return original_import(name, globals_obj, locals_obj, fromlist, level)

    payload = _build_docx_bytes("<w:document/>")
    builtins.__import__ = _fake_import
    try:
        assert processing._extract_docx_text(payload) == ""
    finally:
        builtins.__import__ = original_import


def test_extract_pdf_text_optional_uses_decode_fallback() -> None:
    text = processing._extract_pdf_text_optional(b"Hello\x00\tWorld")
    assert "Hello" in text
    assert "World" in text


def test_extract_text_dispatches_to_kind_handlers() -> None:
    _install_defusedxml_stub()
    assert processing.extract_text("readme.txt", "text/plain", b"hello") == "hello"
    assert processing.extract_text("blob.bin", "application/octet-stream", b"abc") == ""


def test_build_docx_preview_html_escapes_and_limits() -> None:
    html = processing.build_docx_preview_html("line1\n<script>alert(1)</script>", max_paragraphs=1)
    assert "<!doctype html>" in html
    assert "<p>line1</p>" in html
    assert "alert(1)" not in html


def test_chunk_text_deduplicates_adjacent_identical_chunks() -> None:
    chunks = processing.chunk_text("aaaaa", chunk_size=3, overlap=2)
    assert chunks == ["aaa"]
    assert processing.chunk_text("   ", chunk_size=10, overlap=2) == []


def test_safe_filename_component_and_normalize_metadata() -> None:
    assert processing.safe_filename_component("Q1 Report (final).pdf") == "Q1-Report-final-.pdf"
    assert processing.safe_filename_component("////") == "document"
    assert processing.normalize_metadata({"source": "upload"}) == {"source": "upload"}
    assert processing.normalize_metadata(["nope"]) == {}
