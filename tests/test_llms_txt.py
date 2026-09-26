"""llms.txt: read it during map(), and generate one for a site (llmstxt.org)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.cli import main as cli_main

BASE = {
    "allow_private_network": True,
    "browser_fallback": False,
    "http_retries": 0,
    "respect_robots_txt": False,
    "timeout_ms": 5000,
}

PAGES = {
    "/": (
        "<html><head><title>Example Docs</title>"
        "<meta name='description' content='Docs for the Example API.'></head>"
        "<body><main><h1>Home</h1><p>Welcome to the docs.</p>"
        "<a href='/guide'>Guide</a><a href='/broken'>Broken</a></main></body></html>"
    ),
    "/guide": (
        "<html><head><title>Guide [v2]</title>"
        "<meta name='description' content='How to\n use it.'></head>"
        "<body><main><h1>Guide</h1><p>Step one.</p></main></body></html>"
    ),
}


def _serve(llms_txt: str | None) -> ThreadingHTTPServer:
    class Site(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/llms.txt" and llms_txt is not None:
                body, content_type, status = llms_txt, "text/plain", 200
            elif self.path in PAGES:
                body, content_type, status = PAGES[self.path], "text/html", 200
            else:
                body, content_type, status = "not found", "text/plain", 404
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def site(request) -> Iterator[str]:
    server = _serve(getattr(request, "param", None))
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "site",
    [
        "# Example\n\n> Docs\n\n## Docs\n\n"
        "- [API](/api/reference): the API\n"
        '- [Auth](<https://example.invalid/elsewhere> "off-site")\n'
        "- [Changelog](changelog.md)\n"
    ],
    indirect=True,
)
def test_map_reads_llms_txt_links(site: str) -> None:
    result = AgentCrawl(BASE).map(site, max_urls=50)

    assert result.metadata["llms_txt"] == site + "llms.txt"
    assert site + "api/reference" in result.urls
    assert site + "changelog.md" in result.urls
    assert not any("example.invalid" in url for url in result.urls)  # same-domain only


def test_map_ignores_a_missing_llms_txt(site: str) -> None:
    result = AgentCrawl(BASE).map(site, max_urls=50)

    assert "llms_txt" not in result.metadata
    assert site + "guide" in result.urls


@pytest.mark.parametrize("site", ["<html><body>[x](/soft-404)</body></html>"], indirect=True)
def test_map_ignores_html_served_as_llms_txt(site: str) -> None:
    result = AgentCrawl(BASE).map(site, max_urls=50)

    assert "llms_txt" not in result.metadata
    assert site + "soft-404" not in result.urls


def test_generate_llms_txt_lists_readable_pages_only(site: str) -> None:
    result = AgentCrawl(BASE).llms_txt(site, max_pages=5)

    text = result["llms_txt"]
    assert text.startswith("# Example Docs\n\n> Docs for the Example API.\n\n## Pages\n")
    assert f"- [Example Docs]({site}): Docs for the Example API." in text
    assert f"- [Guide \\[v2\\]]({site}guide): How to use it." in text
    assert "broken" not in text  # a 404 page is reported, not listed
    assert result["pages"] == 2
    assert any("broken" in error for error in result["errors"])


def test_cli_llms_txt_writes_the_file(site: str, tmp_path: Path) -> None:
    output = tmp_path / "llms.txt"

    code = cli_main(
        ["llms-txt", site, "--max-pages", "5", "--allow-private-network", "--output", str(output)]
    )

    assert code == 0
    assert output.read_text("utf-8").startswith("# Example Docs")
