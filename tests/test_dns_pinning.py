"""Regression tests for DNS pinning (2026-09-23 audit).

``validate_remote_url`` used to resolve the host, and urllib then resolved it a
second time to connect. A DNS server answering "public" first and "127.0.0.1"
second (DNS rebinding) walked straight past the SSRF guard. The pinned
connections resolve once at connect time and connect to what they validated.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import fetch_source

PUBLIC = "93.184.216.34"


@pytest.fixture
def local_server():
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            body = b"<html><body><h1>internal</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


def _rebinding_dns(monkeypatch, port: int) -> list[str]:
    """First lookup of ``rebind.test`` is public, every later one is loopback."""
    answers: list[str] = []
    real = socket.getaddrinfo

    def fake(host, request_port, *args, **kwargs):
        if str(host) != "rebind.test":
            return real(host, request_port, *args, **kwargs)
        address = PUBLIC if not answers else "127.0.0.1"
        answers.append(address)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, request_port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return answers


def test_dns_rebinding_cannot_reach_loopback(monkeypatch, local_server) -> None:
    port, hits = local_server
    answers = _rebinding_dns(monkeypatch, port)
    config = CrawlConfig(fetcher="http", http_retries=0, browser_fallback=False)

    with pytest.raises(FetchError, match="127.0.0.1"):
        fetch_source(f"http://rebind.test:{port}/secret", config)

    assert hits == []  # nothing ever reached the internal server
    assert answers[0] == PUBLIC and "127.0.0.1" in answers


def test_pinning_is_off_when_private_network_is_allowed(monkeypatch, local_server) -> None:
    port, hits = local_server
    config = CrawlConfig(
        fetcher="http", http_retries=0, browser_fallback=False, allow_private_network=True
    )

    html, _metadata = fetch_source(f"http://127.0.0.1:{port}/ok", config)

    assert "internal" in html
    assert hits == ["/ok"]


def test_pinned_connection_uses_the_validated_address(monkeypatch) -> None:
    from agentcrawl import security

    connected: list[tuple] = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def close(self):
            pass

    monkeypatch.setattr(security.socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port))
        ],
    )

    security._pinned_create_connection(("example.com", 443), timeout=5)

    assert connected == [(PUBLIC, 443)]
