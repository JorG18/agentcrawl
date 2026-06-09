from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .exceptions import FetchError

_TEXT_SUFFIXES = {".txt", ".text"}
_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
_JSON_SUFFIXES = {".json"}
_XML_SUFFIXES = {".xml", ".rss", ".atom"}
_PDF_SUFFIXES = {".pdf"}
_MAX_PDF_BYTES = 50 * 1024 * 1024
_MAX_PDF_PAGES = 500


def read_local_document(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        return _read_pdf(path)
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


def markdown_from_fetched_content(content: str, metadata: dict[str, Any]) -> str | None:
    content_format = metadata.get("content_format")
    if content_format in {"markdown", "text"}:
        return content.strip()
    return None


def _read_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    size = path.stat().st_size
    if size > _MAX_PDF_BYTES:
        raise FetchError(
            f"PDF exceeds the {_MAX_PDF_BYTES // (1024 * 1024)} MB size limit: {path}"
        )

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise FetchError(
            "PDF ingestion requires the docs extra. Install with: "
            "python -m pip install 'agentcrawl[docs]'"
        ) from exc

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise FetchError(f"PDF open failed for {path}: {exc}") from exc

    pages: list[str] = []
    metadata: dict[str, Any] = {
        "content_format": "markdown",
        "document_type": "pdf",
        "source_bytes": size,
    }
    try:
        if getattr(document, "is_encrypted", False) or getattr(document, "needs_pass", False):
            raise FetchError("Encrypted PDF documents are not supported.")
        page_count = int(getattr(document, "page_count", 0))
        if page_count > _MAX_PDF_PAGES:
            raise FetchError(f"PDF page count exceeds the {_MAX_PDF_PAGES} page limit: {page_count}")
        metadata.update({k: v for k, v in (document.metadata or {}).items() if v})
        metadata["page_count"] = page_count
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append(f"## Page {index}\n\n{text}")
        metadata["has_text"] = bool(pages)
    finally:
        document.close()

    return "\n\n".join(pages).strip(), metadata


def _format_json_markdown(text: str) -> str:
    try:
        parsed = json.loads(text)
        text = json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        text = text.strip()
    return f"```json\n{text}\n```"


def html_from_plain_text(text: str) -> str:
    return f"<pre><code>{html.escape(text)}</code></pre>"
