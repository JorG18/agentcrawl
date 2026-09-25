"""Error transparency (S2): error_message/status_code, type-first classification,
fetcher validation, and the engine's error_type surviving every surface."""

from __future__ import annotations

import socket
import ssl
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.config import CrawlConfig
from agentcrawl.errors import classify_exception, sanitize_error_message
from agentcrawl.exceptions import FetchError


@pytest.fixture
def status_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            code = int(self.path.strip("/") or 200)
            body = b"<html><body><h1>status page</h1></body></html>"
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def _crawler(**extra: object) -> AgentCrawl:
    return AgentCrawl(
        {
            "allow_private_network": True,
            "http_retries": 0,
            "browser_fallback": False,
            "respect_robots_txt": False,
            **extra,
        }
    )


@pytest.mark.parametrize(
    ("code", "error_type"),
    [(403, "blocked"), (404, "not_found"), (429, "rate_limited"), (503, "fetch_error")],
)
def test_http_status_is_reported(status_server: str, code: int, error_type: str) -> None:
    document = _crawler().scrape(f"{status_server}/{code}")
    assert document.metadata["error_type"] == error_type
    assert document.metadata["status_code"] == code
    assert str(code) in document.metadata["error_message"]


def test_error_message_is_bounded_and_single_line() -> None:
    raw = "boom\n\x00\x1b[31m" + "x" * 1000
    cleaned = sanitize_error_message(raw)
    assert len(cleaned) <= 300
    assert "\n" not in cleaned and "\x00" not in cleaned and "\x1b" not in cleaned
    assert cleaned.startswith("boom")


def test_classification_prefers_type_over_substrings() -> None:
    # The message mentions "browser" but the cause is a timeout.
    exc = FetchError("Playwright fetch failed for https://x: browser gave up")
    exc.__cause__ = socket.timeout("timed out")
    assert classify_exception(exc) == "timeout"

    tls = FetchError("HTTP fetch failed for https://x: something")
    tls.__cause__ = urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
    assert classify_exception(tls) == "tls_error"

    dns = FetchError("HTTP fetch failed for https://x: whatever")
    dns.__cause__ = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    assert classify_exception(dns) == "network_error"

    explicit = FetchError("anything at all", error_type="config_error")
    assert classify_exception(explicit) == "config_error"


def test_unknown_fetcher_is_a_config_error_not_browser_error() -> None:
    with pytest.raises(ValueError, match="fetcher"):
        CrawlConfig.from_dict({"fetcher": "chrome"})


def test_browser_is_an_alias_of_playwright() -> None:
    assert CrawlConfig.from_dict({"fetcher": "browser"}).fetcher == "playwright"


def test_challenge_error_type_survives_scrape_many(tmp_path) -> None:
    page = tmp_path / "challenge.html"
    page.write_text(
        "<html><body><p>A required part of this site couldn't load.</p></body></html>",
        encoding="utf-8",
    )
    crawler = AgentCrawl({"browser_fallback": False})
    assert crawler.scrape(str(page)).metadata["error_type"] == "client_challenge"
    (batch,) = crawler.scrape_many([str(page)])
    assert batch.metadata["error_type"] == "client_challenge"
    assert batch.metadata["error_message"]


def test_crawl_does_not_retry_a_challenge_page(tmp_path) -> None:
    page = tmp_path / "challenge.html"
    page.write_text(
        "<html><body><p>A required part of this site couldn't load.</p></body></html>",
        encoding="utf-8",
    )
    crawler = AgentCrawl({"browser_fallback": False, "crawl_retry_delay": 0.0})
    run = crawler.crawl(str(page), max_pages=1, max_depth=0)
    failures = run.metadata.get("terminal_failures") or []
    assert failures and failures[0]["error_type"] == "client_challenge"
    assert failures[0]["attempts"] == 1
