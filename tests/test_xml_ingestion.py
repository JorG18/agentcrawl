"""XML served over HTTP: sitemaps become URL lists, other XML stays verbatim."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agentcrawl import AgentCrawl

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type='text/xsl' href='/sitemap.xsl'?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/es/</loc><lastmod>2026-09-09</lastmod></url>
  <url><loc>
    https://example.com/?a=1&amp;b=2
  </loc></url>
</urlset>"""

BUCKET = (
    "<?xml version='1.0' encoding='UTF-8'?><ListBucketResult><Name>static</Name>"
    "<IsTruncated>false</IsTruncated><Contents><Key>a.json</Key><Size>0</Size></Contents>"
    "</ListBucketResult>"
)

XHTML_AS_XML = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hola</p></body></html>'


def _scrape(body: str, content_type: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        return AgentCrawl(
            {"allow_private_network": True, "browser_fallback": False, "http_retries": 0}
        ).scrape(f"http://127.0.0.1:{httpd.server_address[1]}/doc")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_sitemap_becomes_a_list_of_urls() -> None:
    document = _scrape(SITEMAP, "application/xml; charset=utf-8")

    assert document.markdown == "- https://example.com/es/\n- https://example.com/?a=1&b=2"
    assert document.metadata["document_type"] == "sitemap"
    assert document.metadata["sitemap_url_count"] == 2


def test_other_xml_is_kept_verbatim_instead_of_run_together() -> None:
    document = _scrape(BUCKET, "application/xml")

    assert document.markdown.startswith("```xml\n<?xml")
    assert "<Key>a.json</Key>" in document.markdown
    assert document.metadata["document_type"] == "xml"


def test_xhtml_served_as_xml_is_still_read_as_a_page() -> None:
    document = _scrape(XHTML_AS_XML, "text/xml")

    assert document.markdown.strip() == "Hola"
    assert "document_type" not in document.metadata
