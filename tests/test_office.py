"""DOCX, XLSX and PPTX ingestion (standard library only), local and over HTTP."""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import agentcrawl.office as office_module
from agentcrawl import AgentCrawl
from agentcrawl.exceptions import FetchError
from agentcrawl.office import office_to_markdown

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _p(text: str, style: str = "", numbered: bool = False) -> str:
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    props += "<w:numPr/>" if numbered else ""
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def docx() -> bytes:
    cell = "<w:tc><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc>"
    rows = "".join(
        "<w:tr>" + cell.format(a) + cell.format(b) + "</w:tr>"
        for a, b in [("Plan", "Price"), ("Team", "40 | EUR")]
    )
    body = (
        _p("Quarterly Report", "Title")
        + _p("Summary", "Heading1")
        + _p("Revenue grew in every region.")
        + _p("Renew contracts", numbered=True)
        + f"<w:tbl>{rows}</w:tbl>"
    )
    return _zip({"word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>"})


def xlsx() -> bytes:
    return _zip(
        {
            "xl/workbook.xml": (
                f"<workbook {S} {R}><sheets>"
                '<sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                f'<Relationships {REL}><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
            "xl/sharedStrings.xml": f"<sst {S}><si><t>Region</t></si><si><t>North</t></si>"
            "<si><t>Total</t></si></sst>",
            "xl/worksheets/sheet1.xml": (
                f"<worksheet {S}><sheetData>"
                '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>2</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="C2"><v>120</v></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )


def pptx() -> bytes:
    def slide(*lines: str) -> str:
        paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in lines)
        return f"<p:sld {P} {A}><p:cSld><p:spTree>{paragraphs}</p:spTree></p:cSld></p:sld>"

    return _zip(
        {
            "ppt/presentation.xml": f"<p:presentation {P} {R}><p:sldIdLst>"
            '<p:sldId id="1" r:id="rId2"/><p:sldId id="2" r:id="rId1"/>'
            "</p:sldIdLst></p:presentation>",
            "ppt/_rels/presentation.xml.rels": f"<Relationships {REL}>"
            '<Relationship Id="rId1" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>',
            "ppt/slides/slide1.xml": slide("Roadmap", "Ship search"),
            "ppt/slides/slide2.xml": slide("Welcome", "Agenda for today"),
        }
    )


def test_docx_keeps_headings_lists_and_tables() -> None:
    markdown, metadata = office_to_markdown(docx(), "docx")

    assert markdown.startswith("# Quarterly Report\n\n# Summary\n\nRevenue grew")
    assert "- Renew contracts" in markdown
    assert "| Plan | Price |" in markdown and "| Team | 40 \\| EUR |" in markdown
    assert metadata == {"content_format": "markdown", "document_type": "docx", "table_count": 1}


def test_xlsx_renders_one_table_per_sheet_with_gaps_filled() -> None:
    markdown, metadata = office_to_markdown(xlsx(), "xlsx")

    assert "## Sales" in markdown
    assert "| Region | Total | column_3 |" in markdown
    assert "| North |  | 120 |" in markdown
    assert metadata["sheets"] == [{"name": "Sales", "row_count": 1, "rows_omitted": 0}]


def test_pptx_follows_presentation_order() -> None:
    markdown, metadata = office_to_markdown(pptx(), "pptx")

    assert markdown.index("Slide 1: Welcome") < markdown.index("Slide 2: Roadmap")
    assert "- Agenda for today" in markdown
    assert metadata["slide_count"] == 2


def test_hostile_archives_fail_clearly(monkeypatch) -> None:
    with pytest.raises(FetchError, match="Not a valid DOCX"):
        office_to_markdown(b"not a zip", "docx")
    doctype = _zip({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "b">]><x/>'})
    with pytest.raises(FetchError, match="DOCTYPE"):
        office_to_markdown(doctype, "docx")
    monkeypatch.setattr(office_module, "_MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(FetchError, match="expands to more than"):
        office_to_markdown(docx(), "docx")


def test_local_docx_scrapes_to_markdown(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    path.write_bytes(docx())

    document = AgentCrawl().scrape(str(path))

    assert document.ok
    assert "# Summary" in document.markdown
    assert document.metadata["document_type"] == "docx"


@pytest.fixture
def server() -> Iterator[str]:
    files = {
        "/sheet": (xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "/files/deck.pptx": (pptx(), "application/octet-stream"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload, content_type = files[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_remote_office_files_by_content_type_or_extension(server: str) -> None:
    crawler = AgentCrawl(
        {"allow_private_network": True, "respect_robots_txt": False, "browser_fallback": False}
    )

    sheet = crawler.scrape(f"{server}/sheet")
    deck = crawler.scrape(f"{server}/files/deck.pptx")

    assert "| North |  | 120 |" in sheet.markdown
    assert sheet.metadata["document_type"] == "xlsx"
    assert "Slide 1: Welcome" in deck.markdown
