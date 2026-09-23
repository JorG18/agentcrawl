"""CSV/TSV ingestion (new in 0.2.0): local files and ``text/csv`` URLs."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentcrawl import AgentCrawl
from agentcrawl.documents import csv_to_markdown

CSV = '﻿producto;precio;nota\nLámpara Olé;12500;"con | pipe"\nEstante Roble;9900;"dos\nlíneas"\n'


def test_csv_becomes_a_markdown_table_with_shape_metadata() -> None:
    markdown, metadata = csv_to_markdown(CSV)

    assert markdown.splitlines() == [
        "| producto | precio | nota |",
        "| --- | --- | --- |",
        "| Lámpara Olé | 12500 | con \\| pipe |",
        "| Estante Roble | 9900 | dos líneas |",
    ]
    assert metadata["csv_delimiter"] == ";"
    assert metadata["row_count"] == 2 and metadata["column_count"] == 3
    assert metadata["columns"] == ["producto", "precio", "nota"]
    assert metadata["csv_rows_omitted"] == 0


def test_large_csv_reports_the_rows_it_did_not_render() -> None:
    text = "a,b\n" + "".join(f"{i},{i}\n" for i in range(5_010))
    _markdown, metadata = csv_to_markdown(text)
    assert metadata["row_count"] == 5_010 and metadata["csv_rows_omitted"] == 10


def test_local_csv_and_tsv_files_scrape_as_tables(tmp_path: Path) -> None:
    csv_file = tmp_path / "precios.csv"
    csv_file.write_text(CSV, encoding="utf-8")
    tsv_file = tmp_path / "precios.tsv"
    tsv_file.write_text("a\tb\n1\t2\n", encoding="utf-8")

    csv_doc = AgentCrawl().scrape(str(csv_file))
    tsv_doc = AgentCrawl().scrape(str(tsv_file))

    assert "| Estante Roble | 9900 |" in csv_doc.markdown
    assert csv_doc.metadata["document_type"] == "csv"
    assert "| 1 | 2 |" in tsv_doc.markdown


def test_text_csv_urls_are_rendered_as_tables() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = "sku,stock\nA1,3\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        document = AgentCrawl(
            {"allow_private_network": True, "browser_fallback": False, "http_retries": 0}
        ).scrape(f"http://127.0.0.1:{httpd.server_address[1]}/stock.csv")
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert "| A1 | 3 |" in document.markdown
    assert document.metadata["row_count"] == 1
