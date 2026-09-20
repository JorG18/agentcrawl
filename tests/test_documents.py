from pathlib import Path
import sys
import types

import pytest

import agentcrawl.documents as documents_module
from agentcrawl.documents import _read_pdf, read_local_document
from agentcrawl.exceptions import FetchError


def test_local_text_document_over_the_size_limit_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """Regression (2026-09 audit, second pass): PDFs had a 50 MB cap but every
    other local document type (txt/md/json/xml/html) was read whole, so a
    multi-GB file was materialized before ``max_input_chars`` could discard
    almost all of it. A 60 MB ``.txt`` read back in full with no error."""
    document = tmp_path / "big.txt"
    document.write_text("y" * 4096, encoding="utf-8")
    monkeypatch.setattr(documents_module, "_MAX_DOCUMENT_BYTES", 1024)

    with pytest.raises(FetchError) as excinfo:
        read_local_document(document)

    assert "size limit" in str(excinfo.value)


def test_local_document_within_the_size_limit_still_reads(tmp_path: Path) -> None:
    document = tmp_path / "notes.txt"
    document.write_text("Plain document text.", encoding="utf-8")

    content, metadata = read_local_document(document)

    assert content == "Plain document text."
    assert metadata["document_type"] == "text"


class FakePage:
    def __init__(self, text: str):
        self.text = text

    def get_text(self, _format: str) -> str:
        return self.text


class FakeDocument:
    metadata = {"title": "Sample PDF"}
    page_count = 1
    is_encrypted = False
    needs_pass = False

    def __iter__(self):
        return iter([FakePage("First page text")])

    def close(self) -> None:
        pass


class FakeEncryptedDocument(FakeDocument):
    is_encrypted = True
    needs_pass = True


class FakeLargeDocument(FakeDocument):
    page_count = 501


def test_read_pdf_rejects_files_over_size_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"x" * (51 * 1024 * 1024))

    with pytest.raises(FetchError, match="PDF exceeds"):
        _read_pdf(pdf)


def test_read_pdf_rejects_encrypted_documents(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "encrypted.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(
        sys.modules, "fitz", types.SimpleNamespace(open=lambda _path: FakeEncryptedDocument())
    )

    with pytest.raises(FetchError, match="Encrypted PDF"):
        _read_pdf(pdf)


def test_read_pdf_rejects_too_many_pages(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "too-many-pages.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(
        sys.modules, "fitz", types.SimpleNamespace(open=lambda _path: FakeLargeDocument())
    )

    with pytest.raises(FetchError, match="PDF page count exceeds"):
        _read_pdf(pdf)


def test_read_pdf_marks_scanned_or_empty_pdf(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(
        sys.modules, "fitz", types.SimpleNamespace(open=lambda _path: FakeDocument())
    )

    markdown, metadata = _read_pdf(pdf)

    assert "First page text" in markdown
    assert metadata["has_text"] is True
