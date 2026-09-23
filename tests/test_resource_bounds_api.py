"""Resource bounds on the API and on body reads (2026-09-23 audit).

- ``/v1/crawl`` with ``wait=true`` ran up to 10 000 pages inside the request
  thread and cost one rate-limit unit; a few such calls starved the server.
- ``/v1/extract`` accepted an unbounded prompt and schema (an operator-paid LLM
  cost amplifier).
- ``timeout_ms`` is per socket operation, so a body dripped one byte at a time
  held a worker indefinitely.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agentcrawl.server as server_module
from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import fetch_source
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore

KEY = "resource-bounds-key"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    server.store = SQLiteStore(tmp_path / "bounds.db")
    server.auth_enabled = True
    server.api_keys = {KEY}
    server.rate_limit_per_minute = 10
    server._rate_windows = {}
    monkeypatch.setattr(server, "sync_crawl_max_pages", 25)
    monkeypatch.setattr(server_module, "_run_crawl", lambda payload, **_: {"documents": []})
    return TestClient(app)


def _crawl(client: TestClient, max_pages: int):
    return client.post(
        "/v1/crawl",
        json={"url": "https://example.com/", "max_pages": max_pages, "wait": True},
        headers={"Authorization": f"Bearer {KEY}"},
    )


def test_sync_crawl_above_the_cap_must_use_a_job(client: TestClient) -> None:
    response = _crawl(client, 26)
    assert response.status_code == 400
    assert "wait=false" in response.json()["detail"]


def test_sync_crawl_pays_rate_limit_units_per_page(client: TestClient) -> None:
    assert _crawl(client, 6).status_code == 200  # 6 of 10 units used
    assert _crawl(client, 5).status_code == 429  # would need 11


def test_extract_prompt_and_schema_are_bounded(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {KEY}"}
    long_prompt = client.post(
        "/v1/extract",
        json={"url": "https://example.com/", "prompt": "x" * 8_001},
        headers=headers,
    )
    huge_schema = client.post(
        "/v1/extract",
        json={
            "url": "https://example.com/",
            "prompt": "title",
            "schema": {"properties": {f"f{i}": {"type": "string"} for i in range(4_000)}},
        },
        headers=headers,
    )
    assert long_prompt.status_code == 422
    assert huge_schema.status_code == 422


def test_slow_drip_body_hits_the_total_read_deadline() -> None:
    class Drip(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "200")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    config = CrawlConfig(
        fetcher="http",
        http_retries=0,
        browser_fallback=False,
        allow_private_network=True,
        timeout_ms=300,  # per socket op; every drip arrives well inside it
    )
    try:
        started = time.monotonic()
        with pytest.raises(FetchError, match="read deadline"):
            fetch_source(f"http://127.0.0.1:{httpd.server_address[1]}/", config)
        # 200 bytes x 50 ms = 10 s without the deadline; 3 x 300 ms with it.
        assert time.monotonic() - started < 5
    finally:
        httpd.shutdown()
        httpd.server_close()
