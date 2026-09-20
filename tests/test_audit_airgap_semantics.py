"""Regression tests for the 2026-09 audit / airgap findings.

Two behaviours are pinned here:

1. ``audit=True`` observes; it must never enforce the airgap allowlist. It used
   to refuse every cross-host redirect (apex -> www being the common case) that
   the identical config followed with ``audit=False``.
2. The trail counts requests that were actually sent. A request the airgap
   refused was never sent, so it is reported as blocked instead of inflating
   the request and third-party counts.
"""

from __future__ import annotations

import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.airgap import AirgapViolation, AuditTrail
from agentcrawl.config import CrawlConfig
from agentcrawl.search import _fetch_search_body, _open_search_request

PAGE = b"<html><body><article><h1>Final</h1><p>contenido</p></article></body></html>"

# Local servers, so the private-network guard has to be off for these tests.
BASE = {
    "fetcher": "http",
    "browser_fallback": False,
    "http_retries": 0,
    "allow_private_network": True,
    "timeout_ms": 5000,
}


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args: object) -> None:  # keep pytest output clean
        pass


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def local_page() -> Iterator[str]:
    server = _serve(_PageHandler)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/page"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def cross_host_redirect() -> Iterator[str]:
    """A server that 302s to a *different hostname* pointing at the same IP.

    ``127.0.0.1`` -> ``localhost`` is the local shape of the everyday
    ``example.com`` -> ``www.example.com`` redirect.
    """
    target = _serve(_PageHandler)
    target_url = f"http://localhost:{target.server_address[1]}/final"

    class _Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    source = _serve(_Redirecting)
    try:
        yield f"http://127.0.0.1:{source.server_address[1]}/start"
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()


def test_audit_alone_does_not_enforce_the_airgap(cross_host_redirect: str) -> None:
    document = AgentCrawl({**BASE, "audit": True, "airgap": False}).scrape(cross_host_redirect)
    assert document.ok is True, document.errors
    assert "Final" in document.markdown


def test_airgap_still_blocks_a_cross_host_redirect(cross_host_redirect: str) -> None:
    document = AgentCrawl({**BASE, "airgap": True, "audit": True}).scrape(cross_host_redirect)
    assert document.ok is False
    assert "airgap blocked" in document.errors[0]
    # The refusal was recorded, but it was never sent: it must not be counted
    # as a request, and definitely not as a third-party request.
    assert document.metadata["audit_blocked_request_count"] == 1
    assert document.metadata["audit_third_party_request_count"] == 0


def test_one_real_request_is_recorded_once(local_page: str) -> None:
    document = AgentCrawl({**BASE, "audit": True}).scrape(local_page)
    assert document.ok is True
    assert document.metadata["audit_request_count"] == 1
    assert len(document.metadata["audit_records"]) == 1
    record = document.metadata["audit_records"][0]
    assert record["status"] == 200
    assert record["bytes"] == len(PAGE)
    assert record["blocked"] is False


def test_blocked_requests_are_counted_separately_in_the_trail() -> None:
    trail = AuditTrail()
    trail.record(
        "GET", "https://example.com/", status=200, bytes_count=10, target_host="example.com"
    )
    trail.record(
        "GET", "https://cdn.example.org/", status=0, target_host="example.com", blocked=True
    )

    metadata = trail.to_metadata()

    assert metadata["audit_request_count"] == 1
    assert metadata["audit_third_party_request_count"] == 0
    assert metadata["audit_blocked_request_count"] == 1
    assert metadata["audit_total_bytes"] == 10
    assert metadata["audit_records"][1]["blocked"] is True


def test_search_request_is_refused_under_airgap_without_an_allowlist(local_page: str) -> None:
    config = CrawlConfig(airgap=True, allow_private_network=True, timeout_ms=5000)
    request = urllib.request.Request(local_page)
    with pytest.raises(AirgapViolation) as excinfo:
        _open_search_request(request, config, None)
    assert "allowlist_domains" in str(excinfo.value)


def test_search_request_is_allowed_when_its_host_is_allowlisted(local_page: str) -> None:
    host = urllib.parse.urlsplit(local_page).hostname or ""
    config = CrawlConfig(
        airgap=True,
        allow_private_network=True,
        timeout_ms=5000,
        allowlist_domains=(host,),
    )
    request = urllib.request.Request(local_page)
    with _open_search_request(request, config, None) as response:
        assert getattr(response, "status", 200) == 200


def test_search_request_is_recorded_when_audit_is_on(local_page: str) -> None:
    trail = AuditTrail()
    config = CrawlConfig(audit=True, allow_private_network=True, timeout_ms=5000)
    body = _fetch_search_body(urllib.request.Request(local_page), config, trail)

    assert body == PAGE
    metadata = trail.to_metadata()
    assert metadata["audit_request_count"] == 1
    assert metadata["audit_records"][0]["status"] == 200
    assert metadata["audit_records"][0]["bytes"] == len(PAGE)
