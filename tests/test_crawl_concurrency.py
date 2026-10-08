"""Concurrent ``crawl``: pages fetched at the same time, bookkeeping unchanged."""

from __future__ import annotations

import threading
import time

import pytest

from agentcrawl import AgentCrawl, ScrapeDocument

ROOT = "https://example.com/"
A, B, C = ROOT + "a", ROOT + "b", ROOT + "c"
D, E, F = ROOT + "d", ROOT + "e", ROOT + "f"


def _crawler(concurrency: int, **config) -> AgentCrawl:
    return AgentCrawl(
        {
            "respect_robots_txt": False,
            "crawl_depth": 1,
            "crawl_concurrency": concurrency,
            **config,
        }
    )


def _doc(url: str, links: list[str] | None = None) -> ScrapeDocument:
    return ScrapeDocument(url=url, markdown=f"# {url}", text=url, links=links or [])


def test_crawl_fetches_pages_concurrently(monkeypatch) -> None:
    barrier = threading.Barrier(2, timeout=5)

    def fake_scrape(self, source, formats=None, only_main_content=None):
        if source == ROOT:
            return _doc(ROOT, [A, B])
        barrier.wait()  # raises BrokenBarrierError if /a and /b are fetched one at a time
        return _doc(source)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    run = _crawler(2).crawl(ROOT, max_pages=3)

    assert not run.errors
    assert len(run.documents) == 3


def test_crawl_keeps_dispatch_order(monkeypatch) -> None:
    def fake_scrape(self, source, formats=None, only_main_content=None):
        if source == ROOT:
            return _doc(ROOT, [A, B])
        if source == A:
            time.sleep(0.3)
        return _doc(source)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    run = _crawler(2).crawl(ROOT, max_pages=3)

    assert [document.url for document in run.documents] == [ROOT, A, B]


def test_crawl_concurrency_never_exceeds_max_pages(monkeypatch) -> None:
    calls: list[str] = []
    lock = threading.Lock()

    def fake_scrape(self, source, formats=None, only_main_content=None):
        with lock:
            calls.append(source)
        children = [f"{ROOT}p{index}" for index in range(10)]
        return _doc(source, children if source == ROOT else [])

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    run = _crawler(4).crawl(ROOT, max_pages=3)

    assert len(calls) == 3
    assert run.metadata["visited"] == 3


def test_crawl_cancel_keeps_in_flight_pages_queued(monkeypatch) -> None:
    release = threading.Event()
    checkpoints: list[dict] = []
    finished: list[str] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        if source == B:
            release.wait(5)
        return _doc(source, [A, B] if source == ROOT else [])

    def checkpoint(state, _progress, document):
        checkpoints.append(state)
        if document is not None:
            finished.append(document.url)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    crawler = _crawler(2)
    timer = threading.Timer(0.5, release.set)  # the executor waits for /b on shutdown
    timer.start()
    try:
        run = crawler.crawl(
            ROOT,
            max_pages=3,
            checkpoint_callback=checkpoint,
            should_cancel=lambda: len(finished) >= 2,
        )
    finally:
        release.set()
        timer.cancel()

    assert run.metadata["cancelled"] is True
    assert B not in run.visited_urls
    assert B in [item["url"] for item in checkpoints[-1]["queue"]]


def test_crawl_propagates_scrape_exception_from_worker(monkeypatch) -> None:
    def fake_scrape(self, source, formats=None, only_main_content=None):
        if source == A:
            raise RuntimeError("boom")
        return _doc(source, [A, B] if source == ROOT else [])

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    with pytest.raises(RuntimeError, match="boom"):
        _crawler(2).crawl(ROOT, max_pages=3)


def test_crawl_drops_to_one_fetch_after_rate_limit(monkeypatch) -> None:
    lock = threading.Lock()
    active = 0
    peak = {D: 0, E: 0, F: 0}
    limited: list[str] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        nonlocal active
        if source == A and not limited:
            limited.append(source)
            return ScrapeDocument(
                url=A,
                markdown="",
                text="",
                metadata={"error_type": "rate_limited"},
                errors=["429 Too Many Requests"],
            )
        with lock:
            active += 1
            if source in peak:
                peak[source] = active
        time.sleep(0.05)
        with lock:
            active -= 1
        links = {ROOT: [A, B, C], B: [D, E], C: [F]}.get(source, [])
        return _doc(source, links)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    run = _crawler(3, crawl_depth=2, crawl_retry_delay=0.0).crawl(ROOT, max_pages=10)

    assert {D, E, F} <= set(run.visited_urls)
    assert max(peak.values()) == 1


def test_crawl_concurrency_config_is_bounded() -> None:
    with pytest.raises(ValueError):
        AgentCrawl({"crawl_concurrency": 0})


def test_crawl_rate_limit_slowdown_survives_resume(monkeypatch) -> None:
    from dataclasses import asdict

    lock = threading.Lock()
    active = 0
    peak = 0
    limited: list[str] = []
    checkpoints: list[dict] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        nonlocal active, peak
        if source == A and not limited:
            limited.append(source)
            return ScrapeDocument(
                url=A,
                markdown="",
                text="",
                metadata={"error_type": "rate_limited"},
                errors=["429 Too Many Requests"],
            )
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        links = {ROOT: [A, B, C], B: [D, E], C: [F]}.get(source, [])
        return _doc(source, links)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    crawler = _crawler(3, crawl_depth=2, crawl_retry_delay=0.0)
    first = crawler.crawl(
        ROOT,
        max_pages=10,
        max_run_pages=3,  # a server job quantum: yields after root, /b, /c
        checkpoint_callback=lambda state, _progress, _document: checkpoints.append(state),
    )
    assert first.metadata["fairness_yielded"] is True

    peak = 0
    resume_state = {**checkpoints[-1], "documents": [asdict(d) for d in first.documents]}
    resumed = crawler.crawl(ROOT, max_pages=10, resume_state=resume_state)

    assert {A, D, E, F} <= set(resumed.visited_urls)
    assert peak == 1
