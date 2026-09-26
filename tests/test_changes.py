"""Incremental fetch: validators, 304, content hashes, diffs and crawl change marks."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.cli import main as cli_main
from agentcrawl.mcp_server import check_changes

BASE = {
    "allow_private_network": True,
    "browser_fallback": False,
    "http_retries": 0,
    "respect_robots_txt": False,
    "timeout_ms": 5000,
}


class Site:
    def __init__(self) -> None:
        self.body = "<p>Price is 10 dollars.</p>"
        self.etag = '"v1"'
        self.requests: list[dict[str, str]] = []

    def page(self) -> bytes:
        return (
            "<html><body><main><h1>Pricing</h1><p>Plans for every team size.</p>"
            f"{self.body}<a href='/other'>other</a></main></body></html>"
        ).encode()


@pytest.fixture
def site() -> Iterator[tuple[str, Site]]:
    state = Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.requests.append(
                {"path": self.path, "if-none-match": self.headers.get("If-None-Match", "")}
            )
            if self.path == "/pricing" and self.headers.get("If-None-Match") == state.etag:
                self.send_response(304)
                self.end_headers()
                return
            payload = state.page() if self.path == "/pricing" else b"<p>other page text</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("ETag", state.etag)
            self.send_header("Last-Modified", "Sat, 26 Sep 2026 10:00:00 GMT")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/pricing", state
    finally:
        server.shutdown()
        server.server_close()


def test_scrape_records_hash_and_validators(site) -> None:
    url, _state = site

    document = AgentCrawl(BASE).scrape(url)

    assert len(document.metadata["markdown_sha256"]) == 64
    assert document.metadata["etag"] == '"v1"'
    assert document.metadata["last_modified"].startswith("Sat, 26 Sep 2026")


def test_diff_uses_304_when_unchanged(site) -> None:
    url, state = site
    crawler = AgentCrawl(BASE)
    first = crawler.scrape(url)

    result = crawler.diff(url, first)

    assert result == {
        "url": url,
        "previous_sha256": first.metadata["markdown_sha256"],
        "changed": False,
        "not_modified": True,
        "sha256": first.metadata["markdown_sha256"],
    }
    assert state.requests[-1]["if-none-match"] == '"v1"'


def test_diff_reports_what_changed(site) -> None:
    url, state = site
    crawler = AgentCrawl(BASE)
    first = crawler.scrape(url)
    state.body, state.etag = "<p>Price is 12 dollars.</p>", '"v2"'

    result = crawler.diff(url, first)

    assert result["changed"] is True and result["new"] is False
    assert "-Price is 10 dollars." in result["diff"]
    assert "+Price is 12 dollars." in result["diff"]
    assert result["added_lines"] == 1 and result["removed_lines"] == 1
    assert result["document"]["metadata"]["etag"] == '"v2"'


def test_diff_without_previous_is_new(site) -> None:
    url, _state = site

    result = AgentCrawl(BASE).diff(url)

    assert result["changed"] is True and result["new"] is True
    assert "diff" not in result


def test_crawl_marks_new_changed_and_unchanged(site) -> None:
    url, state = site
    crawler = AgentCrawl({**BASE, "crawl_depth": 1})
    first = crawler.crawl(url, max_pages=5)
    hashes = {doc.url: doc.metadata["markdown_sha256"] for doc in first.documents}
    other = next(key for key in hashes if key.endswith("/other"))
    del hashes[other]
    state.body = "<p>Price is 15 dollars.</p>"

    second = crawler.crawl(url, max_pages=5, previous_hashes=hashes)

    changes = {doc.url.rsplit("/", 1)[-1]: doc.metadata["change"] for doc in second.documents}
    assert changes == {"pricing": "changed", "other": "new"}
    assert second.metadata["changes"] == {"new": 1, "changed": 1, "unchanged": 0}


def test_cli_diff_round_trip(site, tmp_path: Path, capsys) -> None:
    url, state = site
    saved = tmp_path / "pricing.json"
    flags = ["--allow-private-network", "--no-robots"]

    assert cli_main(["diff", url, "--save", str(saved), *flags]) == 0
    capsys.readouterr()
    state.body, state.etag = "<p>Price is 20 dollars.</p>", '"v3"'
    assert cli_main(["diff", url, "--previous", str(saved), *flags]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["changed"] is True and "+Price is 20 dollars." in output["diff"]


def test_mcp_check_changes(site, monkeypatch) -> None:
    url, _state = site
    monkeypatch.setenv("AGENTCRAWL_ALLOW_PRIVATE_NETWORK", "true")
    monkeypatch.setenv("AGENTCRAWL_RESPECT_ROBOTS_TXT", "false")

    result = check_changes(url, etag='"v1"')

    assert result["not_modified"] is True
