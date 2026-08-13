from __future__ import annotations

import zipfile
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


class UnsupportedDocumentError(ValueError):
    pass


# A .docx is a zip archive; a small file with extreme compression can
# expand to hundreds of megabytes when parsed, which is enough to exhaust
# server memory and crash the process on a single upload (confirmed: a
# ~1.5MB crafted .docx expanded to ~445MB and killed the server during
# testing, taking well over a minute to get there). The raw upload size
# limit alone (15MB, enforced at the API layer) does not catch this, since
# it checks the compressed size, not what the archive expands to. Checking
# each member's *declared* uncompressed size from the zip's central
# directory is cheap (no decompression happens yet) and lets us reject an
# oversized archive before ever handing it to python-docx.
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB is generous for a real clinical document
MAX_EXTRACTED_TEXT_CHARS = 5_000_000  # second line of defense regardless of source format
MAX_PDF_OCR_PAGES = 5
OCR_TIMEOUT_SECONDS = 45


def _ocr_image(raw: bytes, suffix: str) -> str:
    """Run local Tesseract with bounded input and execution time.

    The program is optional so a minimal deployment can still accept textual
    PDFs/DOCX files. We never upload a clinical image to a cloud OCR service.
    """
    binary = shutil.which("tesseract")
    if not binary:
        raise UnsupportedDocumentError("OCR is not installed. Install local Tesseract to read scanned images and PDFs.")
    try:
        completed = subprocess.run(
            [binary, "stdin", "stdout", "-l", "eng"], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=OCR_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UnsupportedDocumentError("OCR timed out. Use a smaller or clearer scan.") from exc
    if completed.returncode != 0:
        raise UnsupportedDocumentError("The image could not be read by local OCR.")
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _ocr_pdf(raw: bytes) -> tuple[str, list[int]]:
    converter = shutil.which("pdftoppm")
    if not converter:
        raise UnsupportedDocumentError("Scanned PDF OCR requires local Poppler (pdftoppm) and Tesseract.")
    try:
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > MAX_PDF_OCR_PAGES:
            raise UnsupportedDocumentError(f"Scanned PDF OCR is limited to {MAX_PDF_OCR_PAGES} pages per upload.")
    except UnsupportedDocumentError:
        raise
    except Exception as exc:
        raise UnsupportedDocumentError("The PDF could not be read.") from exc
    with tempfile.TemporaryDirectory(prefix="clinic-ocr-") as temp:
        input_path = Path(temp) / "source.pdf"
        output_prefix = Path(temp) / "page"
        input_path.write_bytes(raw)
        try:
            completed = subprocess.run(
                [converter, "-f", "1", "-l", str(MAX_PDF_OCR_PAGES), "-r", "150", "-png", str(input_path), str(output_prefix)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=OCR_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UnsupportedDocumentError("PDF rendering for OCR timed out. Use a smaller scan.") from exc
        if completed.returncode != 0:
            raise UnsupportedDocumentError("The scanned PDF could not be prepared for local OCR.")
        pages = sorted(Path(temp).glob("page-*.png"))[:MAX_PDF_OCR_PAGES]
        if not pages:
            raise UnsupportedDocumentError("No pages could be prepared for OCR.")
        page_texts = [_ocr_image(page.read_bytes(), ".png") for page in pages]
        return "\n\n".join(page_texts).strip(), _offsets_from_page_texts(page_texts)


def extract_text_with_method(filename: str, raw: bytes) -> tuple[str, str, list[int]]:
    """Returns (text, method, page_offsets). page_offsets[i] is the character
    offset in `text` where page i (0-indexed; page number = i+1) begins --
    this is what lets a lab result parsed from the text be traced back to
    the exact page of the original PDF for the document-viewer citation
    feature (see laboratory.py:extract_lab_candidates and the
    /original#page=N link built from source_page in the frontend). For
    formats without a real page concept (.docx, .txt/.md, a single image),
    the whole document is treated as one page -- page_offsets=[0] -- which
    is honest (there's no finer citation to offer) rather than fabricating
    page numbers that don't correspond to anything.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(raw))
        page_texts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(page_texts).strip()
        method = "text"
        page_offsets = _offsets_from_page_texts(page_texts)
        if not text:
            text, page_offsets = _ocr_pdf(raw)
            method = "ocr"
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                total_uncompressed = sum(info.file_size for info in archive.infolist())
        except zipfile.BadZipFile as exc:
            raise UnsupportedDocumentError("The .docx file could not be read (not a valid archive).") from exc
        if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise UnsupportedDocumentError("This .docx file is too large once decompressed to process safely.")
        doc = Document(BytesIO(raw))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
        method = "text"
        page_offsets = [0]
    elif suffix in {".txt", ".md"}:
        text = raw.decode("utf-8", errors="replace").strip()
        method = "text"
        page_offsets = [0]
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        text = _ocr_image(raw, suffix)
        method = "ocr"
        page_offsets = [0]
    else:
        raise UnsupportedDocumentError("Supported formats: PDF, DOCX, TXT, MD, PNG, JPG, TIFF and BMP.")
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        text = text[:MAX_EXTRACTED_TEXT_CHARS]
        page_offsets = [o for o in page_offsets if o <= len(text)] or [0]
    return text, method, page_offsets


def _offsets_from_page_texts(page_texts: list[str]) -> list[int]:
    offsets = []
    running = 0
    for t in page_texts:
        offsets.append(running)
        running += len(t) + 1  # +1 for the "\n" join separator
    return offsets or [0]


def extract_text(filename: str, raw: bytes) -> str:
    """Backward-compatible text-only API used by integrations and older tests."""
    return extract_text_with_method(filename, raw)[0]
