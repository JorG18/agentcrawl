from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path
from typing import Any

from .exceptions import FetchError
from .office import OFFICE_SUFFIXES, office_to_markdown

_TEXT_SUFFIXES = {".txt", ".text"}
_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
_JSON_SUFFIXES = {".json"}
_XML_SUFFIXES = {".xml", ".rss", ".atom"}
_PDF_SUFFIXES = {".pdf"}
_CSV_SUFFIXES = {".csv", ".tsv"}
CSV_CONTENT_TYPES = frozenset({"text/csv", "text/tab-separated-values", "application/csv"})
# Rendering caps: a Markdown table is for reading, not bulk transfer. Past
# these, the cut is reported in metadata (``csv_rows_omitted``), never silent.
_MAX_CSV_ROWS = 5_000
_MAX_CSV_CELL_CHARS = 500
_MAX_PDF_BYTES = 50 * 1024 * 1024
_MAX_PDF_PAGES = 500
# Local text-like documents were read whole with no ceiling, so a multi-GB
# ``.txt``/``.json``/``.xml`` (or one pointed at by an API caller with local
# files enabled) was materialized in full before ``max_input_chars`` could
# discard almost all of it. Same ceiling as PDFs for consistency.
_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


def read_local_document(path: Path, *, ocr: bool = False) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        return _read_pdf(path, ocr=ocr)
    _reject_oversized_document(path)
    if suffix in OFFICE_SUFFIXES:
        return office_to_markdown(path.read_bytes(), OFFICE_SUFFIXES[suffix])
    if suffix in _MARKDOWN_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace"), {
            "content_format": "markdown",
            "document_type": "markdown",
        }
    if suffix in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace"), {
            "content_format": "text",
            "document_type": "text",
        }
    if suffix in _JSON_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        return _format_json_markdown(text), {
            "content_format": "markdown",
            "document_type": "json",
        }
    if suffix in _CSV_SUFFIXES:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return csv_to_markdown(text, delimiter="\t" if suffix == ".tsv" else None)
    if suffix in _XML_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        return f"```xml\n{text.strip()}\n```", {
            "content_format": "markdown",
            "document_type": "xml",
        }
    return path.read_text(encoding="utf-8", errors="replace"), {
        "content_format": "html",
        "document_type": "html",
    }


def csv_to_markdown(text: str, *, delimiter: str | None = None) -> tuple[str, dict[str, Any]]:
    """Render CSV/TSV as a Markdown table plus shape metadata.

    The delimiter is sniffed (``, ; \t |``) unless given. Pipes and newlines
    inside cells are escaped so a cell can never break the table.
    """
    text = text.lstrip("\ufeff")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(row)]
    metadata: dict[str, Any] = {
        "content_format": "markdown",
        "document_type": "csv",
        "csv_delimiter": delimiter,
    }
    if not rows:
        metadata.update({"row_count": 0, "column_count": 0, "columns": []})
        return "", metadata
    header, body = rows[0], rows[1:]
    width = max(len(row) for row in rows)
    header = header + [""] * (width - len(header))
    shown = body[:_MAX_CSV_ROWS]

    def cell(value: str) -> str:
        value = value.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
        if len(value) > _MAX_CSV_CELL_CHARS:
            value = value[: _MAX_CSV_CELL_CHARS - 1] + "…"
        return value

    lines = [
        "| "
        + " | ".join(cell(value) or f"column_{i + 1}" for i, value in enumerate(header))
        + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in shown:
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(cell(value) for value in padded) + " |")
    metadata.update(
        {
            "row_count": len(body),
            "column_count": width,
            "columns": [value.strip() for value in header],
            "csv_rows_omitted": len(body) - len(shown),
        }
    )
    return "\n".join(lines), metadata


def markdown_from_fetched_content(content: str, metadata: dict[str, Any]) -> str | None:
    content_format = metadata.get("content_format")
    if content_format in {"markdown", "text"}:
        return content.strip()
    return None


def _reject_oversized_document(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FetchError(f"Cannot read local document {path}: {exc}") from exc
    if size > _MAX_DOCUMENT_BYTES:
        raise FetchError(
            f"Local document exceeds the {_MAX_DOCUMENT_BYTES // (1024 * 1024)} MB "
            f"size limit: {path}"
        )


def _read_pdf(path: Path, *, ocr: bool = False) -> tuple[str, dict[str, Any]]:
    size = path.stat().st_size
    if size > _MAX_PDF_BYTES:
        raise FetchError(f"PDF exceeds the {_MAX_PDF_BYTES // (1024 * 1024)} MB size limit: {path}")
    fitz = _import_pymupdf()
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise FetchError(f"PDF open failed for {path}: {exc}") from exc
    return _pdf_markdown(document, size, ocr=ocr)


def pdf_bytes_to_markdown(data: bytes, *, ocr: bool = False) -> tuple[str, dict[str, Any]]:
    """Convert a downloaded PDF (HTTP response body) to Markdown."""
    if len(data) > _MAX_PDF_BYTES:
        raise FetchError(f"PDF exceeds the {_MAX_PDF_BYTES // (1024 * 1024)} MB size limit")
    fitz = _import_pymupdf()
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise FetchError(f"PDF open failed: {exc}") from exc
    return _pdf_markdown(document, len(data), ocr=ocr)


def _import_pymupdf() -> Any:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise FetchError(
            "PDF ingestion requires the docs extra. Install with: "
            "python -m pip install 'agentcrawl-ai[docs]'"
        ) from exc
    return fitz


def _pdf_markdown(document: Any, size: int, *, ocr: bool) -> tuple[str, dict[str, Any]]:
    pages: list[str] = []
    metadata: dict[str, Any] = {
        "content_format": "markdown",
        "document_type": "pdf",
        "source_bytes": size,
    }
    ocr_pages: list[int] = []
    try:
        if getattr(document, "is_encrypted", False) or getattr(document, "needs_pass", False):
            raise FetchError("Encrypted PDF documents are not supported.")
        page_count = int(getattr(document, "page_count", 0))
        if page_count > _MAX_PDF_PAGES:
            raise FetchError(
                f"PDF page count exceeds the {_MAX_PDF_PAGES} page limit: {page_count}"
            )
        metadata.update({k: v for k, v in (document.metadata or {}).items() if v})
        metadata["page_count"] = page_count
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if not text and ocr:
                text = _ocr_page(page, index)
                if text:
                    ocr_pages.append(index)
            if text:
                pages.append(f"## Page {index}\n\n{text}")
        metadata["has_text"] = bool(pages)
        if ocr_pages:
            metadata["ocr_pages"] = ocr_pages
        elif not pages and page_count and not ocr:
            # Scanned PDF: say why it is empty instead of returning nothing.
            metadata["warning"] = "PDF has no text layer; it looks scanned. Retry with ocr=true."
    finally:
        document.close()

    return "\n\n".join(pages).strip(), metadata


def _ocr_page(page: Any, index: int) -> str:
    """OCR one image-only page with PyMuPDF's Tesseract bridge."""
    try:
        textpage = page.get_textpage_ocr(full=True)
        return page.get_text("text", textpage=textpage).strip()
    except Exception as exc:
        raise FetchError(
            f"OCR failed on PDF page {index}: {exc}. OCR needs the Tesseract binary "
            "installed and TESSDATA_PREFIX set."
        ) from exc


def _format_json_markdown(text: str) -> str:
    try:
        parsed = json.loads(text)
        text = json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        text = text.strip()
    return f"```json\n{text}\n```"


def html_from_plain_text(text: str) -> str:
    return f"<pre><code>{html.escape(text)}</code></pre>"
