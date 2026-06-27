"""Tests for browser_fallback retry on 200-OK challenge pages.

These tests use a fake HTML file and patch fetchers.fetch_source so we
can exercise the retry path without touching the real network.
"""

from __future__ import annotations

from pathlib import Path

from agentcrawl import AgentCrawl
from agentcrawl.browser_retry import attempt_browser_retry
from agentcrawl.config import CrawlConfig
from agentcrawl.fetchers import FetchError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests" / "fixtures" / "quality" / "fake_challenge.html").as_uri()


def _challenge_html() -> str:
    return """<!doctype html><html><body>
<div class="cf-challenge">client challenge please enable javascript and disable any ad blockers</div>
</body></html>"""


def _clean_html() -> str:
    return (ROOT / "tests" / "fixtures" / "quality" / "fake_challenge.html").read_text()


def test_retry_returns_none_when_source_is_not_remote() -> None:
    cfg = CrawlConfig(browser_fallback=True)
    out = attempt_browser_retry(
        FIXTURE,
        original_metadata={},
        blocked_reason="client challenge",
        original_config=cfg,
        only_main_content=True,
        requested=["markdown"],
    )
    assert out is None


def test_scrape_with_browser_fallback_off_returns_challenge_error(monkeypatch) -> None:
    challenge = _challenge_html()
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source", lambda *a, **kw: (challenge, {"fetcher": "http"})
    )
    doc = AgentCrawl({"fetcher": "http", "browser_fallback": False}).scrape(
        "https://example.test/challenge"
    )
    assert doc.ok is False
    assert doc.metadata.get("error_type") == "client_challenge"


def test_scrape_with_browser_fallback_on_succeeds_when_browser_fetches_clean(monkeypatch) -> None:
    challenge = _challenge_html()
    clean = _clean_html()
    calls: list[str] = []

    def fake_fetch(source, config):
        calls.append(config.fetcher)
        if config.fetcher == "http":
            return (challenge, {"fetcher": "http", "final_url": source})
        return (clean, {"fetcher": config.fetcher, "final_url": source})

    monkeypatch.setattr("agentcrawl.crawler.fetch_source", fake_fetch)
    monkeypatch.setattr("agentcrawl.browser_retry.fetch_source", fake_fetch)
    doc = AgentCrawl(
        {"fetcher": "http", "browser_fallback": True, "browser_backend": "playwright"}
    ).scrape("https://example.test/challenge")
    assert doc.ok is True
    assert doc.metadata.get("browser_retry") is True
    assert "Released Package Page" in doc.markdown
    assert calls == ["http", "playwright"]


def test_scrape_falls_back_to_original_challenge_when_browser_also_challenges(monkeypatch) -> None:
    challenge = _challenge_html()

    def fake_fetch(source, config):
        return (challenge, {"fetcher": config.fetcher, "final_url": source})

    monkeypatch.setattr("agentcrawl.crawler.fetch_source", fake_fetch)
    doc = AgentCrawl(
        {"fetcher": "http", "browser_fallback": True, "browser_backend": "playwright"}
    ).scrape("https://example.test/challenge")
    assert doc.ok is False
    assert "browser_retry" not in doc.metadata


def test_scrape_falls_back_when_browser_raises(monkeypatch) -> None:
    challenge = _challenge_html()

    def fake_fetch(source, config):
        if config.fetcher == "http":
            return (challenge, {"fetcher": "http", "final_url": source})
        raise FetchError("browser backend unavailable")

    monkeypatch.setattr("agentcrawl.crawler.fetch_source", fake_fetch)
    doc = AgentCrawl(
        {"fetcher": "http", "browser_fallback": True, "browser_backend": "playwright"}
    ).scrape("https://example.test/challenge")
    assert doc.ok is False
    assert doc.metadata.get("error_type") == "client_challenge"


def test_browser_retry_path_not_triggered_when_already_using_browser(monkeypatch) -> None:
    """If the original fetcher is already a browser, retry would be redundant
    and could re-trigger the same challenge. We must leave the original
    challenge path alone."""
    challenge = _challenge_html()
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source", lambda *a, **kw: (challenge, {"fetcher": "playwright"})
    )
    doc = AgentCrawl({"fetcher": "playwright", "browser_fallback": True}).scrape(
        "https://example.test/challenge"
    )
    assert doc.ok is False
    assert doc.metadata.get("error_type") == "client_challenge"
