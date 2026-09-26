"""Adaptive crawl: relevant links first, stop when pages stop matching."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.crawler import _pop_ready_item, _queue_item

BASE = {
    "allow_private_network": True,
    "browser_fallback": False,
    "http_retries": 0,
    "respect_robots_txt": False,
    "timeout_ms": 5000,
    "crawl_depth": 3,
}


def _page(title: str, body: str, links: list[str]) -> str:
    anchors = "".join(f"<a href='{link}'>{link}</a>" for link in links)
    return f"<html><body><main><h1>{title}</h1><p>{body}</p>{anchors}</main></body></html>"


SITE = {
    "/": _page(
        "Docs home",
        "Welcome. Guides for billing and more.",
        ["/about", "/careers", "/press", "/billing/refunds"],
    ),
    "/about": _page("About", "Our company story and team.", ["/team"]),
    "/careers": _page("Careers", "Open roles in engineering.", []),
    "/press": _page("Press", "News coverage and logos.", []),
    "/team": _page("Team", "People who work here.", []),
    "/billing/refunds": _page(
        "Refunds", "Billing refunds are issued within five days of a refund request.", []
    ),
}


@pytest.fixture
def site() -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            body = SITE.get(self.path)
            payload = (body or "missing").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", hits
    finally:
        server.shutdown()
        server.server_close()


def test_query_visits_the_relevant_link_first(site) -> None:
    root, hits = site

    run = AgentCrawl(BASE).crawl(root, max_pages=2, query="billing refund")

    pages = [hit for hit in hits if hit != "/llms.txt"]
    assert pages == ["/", "/billing/refunds"]
    refunds = next(doc for doc in run.documents if doc.url.endswith("/billing/refunds"))
    assert refunds.metadata["query_relevance"] == 1.0
    assert run.metadata["query"] == "billing refund"


def test_query_stops_after_irrelevant_pages(site) -> None:
    root, hits = site

    run = AgentCrawl(BASE).crawl(
        root, max_pages=20, query="kubernetes autoscaling", stop_after_irrelevant=2
    )

    assert run.metadata["stopped_early"] is True
    assert len(run.documents) == 2
    assert run.metadata["pending"] > 0  # it stopped with pages still queued


def test_without_query_the_crawl_is_unchanged(site) -> None:
    root, hits = site

    run = AgentCrawl(BASE).crawl(root, max_pages=20)

    assert len(run.documents) == len(SITE)
    assert "stopped_early" not in run.metadata
    assert all("query_relevance" not in doc.metadata for doc in run.documents)


def test_scored_queue_pops_best_ready_item_and_keeps_fifo_without_scores() -> None:
    scored = deque(
        [
            _queue_item("a", 1, score=0.2),
            _queue_item("b", 1, score=0.9),
            _queue_item("c", 1, score=0.9, ready_at=10**12),  # not ready yet
        ]
    )
    item, _ = _pop_ready_item(scored, now=1.0)
    assert item["url"] == "b"

    plain = deque([_queue_item("a", 1), _queue_item("b", 1)])
    item, _ = _pop_ready_item(plain, now=1.0)
    assert item["url"] == "a" and "score" not in item
