"""Tests for backend/app/extractors.py, in particular the .docx
decompression-bomb guard found during adversarial testing: a small
(~1.5MB) crafted .docx with an extreme compression ratio expanded to
~445MB when parsed by python-docx, which exhausted memory and crashed the
server outright (confirmed by hand before this fix; not a theoretical
concern).
"""
from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from docx import Document

from app.extractors import UnsupportedDocumentError, extract_text


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_zip_bomb_docx_bytes(repeat: int) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        huge_text = ("<w:t>" + ("A" * 80) + "</w:t>") * repeat
        doc_xml = (
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r>{huge_text}</w:r></w:p></w:body></w:document>"
        )
        z.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def test_normal_docx_extracts_correctly():
    raw = _make_docx_bytes(["Prva linija nalaza.", "CRP povišen na 45."])
    text = extract_text("lab.docx", raw)
    assert "Prva linija nalaza." in text
    assert "CRP povišen na 45." in text


def test_normal_pdf_and_txt_still_work():
    assert extract_text("note.txt", "Obican tekstualni nalaz.".encode("utf-8")) == "Obican tekstualni nalaz."
    assert extract_text("note.md", "# Nalaz\nOpis.".encode("utf-8")) == "# Nalaz\nOpis."


def test_unsupported_extension_rejected():
    with pytest.raises(UnsupportedDocumentError):
        extract_text("file.exe", b"binary content")


def test_decompression_bomb_docx_is_rejected_quickly():
    # This exact construction (small file, huge declared uncompressed size)
    # is what crashed the server before the fix. It must be rejected based
    # on the zip's declared sizes, without ever asking python-docx to parse
    # the archive.
    bomb = _make_zip_bomb_docx_bytes(repeat=3_000_000)
    assert len(bomb) < 2 * 1024 * 1024  # a couple MB compressed
    with pytest.raises(UnsupportedDocumentError, match="too large once decompressed"):
        extract_text("bomb.docx", bomb)


def test_corrupt_docx_is_rejected_not_crashed():
    with pytest.raises(UnsupportedDocumentError):
        extract_text("not-a-real-docx.docx", b"this is not a valid zip file at all")


def test_extracted_text_is_capped_even_for_legitimate_large_documents():
    from app import extractors
    raw = _make_docx_bytes(["x" * 1000] * 200)  # a large but legitimate, non-bomb document
    text = extract_text("big.docx", raw)
    assert len(text) <= extractors.MAX_EXTRACTED_TEXT_CHARS
