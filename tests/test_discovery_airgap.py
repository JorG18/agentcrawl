"""Discovery (robots.txt, sitemaps) honours airgap and audit (2026-09 audit).

Reproduced before the fix: with ``airgap=True`` a ``Sitemap:`` line pointing at
another host was fetched anyway, and a crawl's robots.txt request never showed
up in the audit trail. Discovery used its own opener with the SSRF guard only.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentcrawl import AgentCrawl

BASE = {
    "allow_private_network": True,
    "browser_fallback": False,
    "http_retries": 0,
    "respect_robots_txt": True,
    "timeout_ms": 5000,
}


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def sites() -> Iterator[tuple[str, list[str], list[str]]]:
    """Site A (127.0.0.1) whose robots.txt announces a sitemap on host B (localhost)."""
    hits_a: list[str] = []
    hits_b: list[str] = []

    class SiteB(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits_b.append(self.path)
            body = (
                b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/'
                b'sitemap/0.9"><url><loc>http://127.0.0.1/from-b</loc></url></urlset>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    site_b = _serve(SiteB)
    port_b = site_b.server_address[1]

    class SiteA(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits_a.append(self.path)
            if self.path == "/robots.txt":
                body = f"User-agent: *\nAllow: /\nSitemap: http://localhost:{port_b}/sitemap.xml\n"
                content_type = "text/plain"
            elif self.path == "/sitemap.xml":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            else:
                body = "<html><body><article><h1>A</h1><p>x</p><a href='/p2'>p2</a></article></body></html>"
                content_type = "text/html"
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    site_a = _serve(SiteA)
    try:
        yield f"http://127.0.0.1:{site_a.server_address[1]}/", hits_a, hits_b
    finally:
        for server in (site_a, site_b):
            server.shutdown()
            server.server_close()


def test_airgap_blocks_a_sitemap_on_another_host(sites) -> None:
    root, _hits_a, hits_b = sites

    result = AgentCrawl({**BASE, "airgap": True, "audit": True}).map(root, max_urls=10)

    assert hits_b == []  # nothing was sent to the other host
    assert result.ok is True  # a refused sitemap is skipped, not a failed map
    (skipped,) = result.metadata["airgap_skipped"]
    assert skipped.startswith("http://localhost:") and skipped.endswith("/sitemap.xml")
    audit = result.metadata["discovery_audit"]
    assert audit["audit_blocked_request_count"] == 1
    assert audit["audit_third_party_request_count"] == 0


def test_audit_only_records_the_third_party_sitemap_hop(sites) -> None:
    root, _hits_a, hits_b = sites

    result = AgentCrawl({**BASE, "audit": True}).map(root, max_urls=10)

    assert hits_b == ["/sitemap.xml"]  # audit observes, it never blocks
    audit = result.metadata["discovery_audit"]
    urls = [record["url"] for record in audit["audit_records"]]
    assert any(url.endswith("/robots.txt") for url in urls)
    assert audit["audit_third_party_request_count"] == 1


def test_crawl_audits_its_robots_request_once(sites) -> None:
    root, hits_a, _hits_b = sites

    run = AgentCrawl({**BASE, "audit": True}).crawl(root, max_pages=1)

    assert hits_a.count("/robots.txt") == 1
    audit = run.metadata["discovery_audit"]
    assert [record["url"] for record in audit["audit_records"]] == [root + "robots.txt"]
    assert audit["audit_records"][0]["status"] == 200
