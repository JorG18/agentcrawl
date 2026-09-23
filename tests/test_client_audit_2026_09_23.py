"""``AgentCrawler`` / ``AgentCrawl.extract`` regressions (2026-09-23 audit)."""

from __future__ import annotations

import threading
from pathlib import Path

from agentcrawl import AgentCrawl
from agentcrawl.client import AgentCrawler
from agentcrawl.models import SearchResult


class LockHoldingLLM:
    """Real LLM clients hold locks/connections; ``deepcopy`` cannot copy them."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return '{"title": "Hola"}'


def test_extract_accepts_an_llm_client_that_cannot_be_copied(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1>Hola</h1></body></html>", encoding="utf-8")
    llm = LockHoldingLLM()

    result = AgentCrawl({"llm": llm}).extract(str(page), "title")

    assert result.errors == []
    assert result.answer == {"title": "Hola"}
    assert llm.prompts  # the very same object was used, not a copy


def test_markdown_keeps_the_same_llm_object() -> None:
    llm = LockHoldingLLM()
    crawler = AgentCrawler({"llm": llm})
    seen = []
    import agentcrawl.client as client_module

    class Graph:
        def __init__(self, config):
            seen.append(config)

        def run(self, *args):
            return None

    original = client_module.CrawlGraph
    client_module.CrawlGraph = Graph
    try:
        crawler.markdown("https://example.com/")
    finally:
        client_module.CrawlGraph = original
    assert seen[0].llm is llm and seen[0].output_format == "markdown"


def test_airgapped_search_only_scrapes_allowlisted_result_hosts(monkeypatch) -> None:
    crawler = AgentCrawler(
        {"airgap": True, "allowlist_domains": ["html.duckduckgo.com", "*.example.com"]}
    )
    monkeypatch.setattr(
        crawler,
        "search",
        lambda query, trail=None: [
            SearchResult(title="a", url="https://docs.example.com/a", snippet=""),
            SearchResult(title="b", url="https://tracker.example.org/b", snippet=""),
        ],
    )
    scraped: list[list[str]] = []
    monkeypatch.setattr(
        crawler, "scrape_many", lambda urls, prompt, schema=None: scraped.append(urls) or []
    )

    payload = crawler.search_then_scrape("q", "title")

    assert scraped == [["https://docs.example.com/a"]]
    assert payload["airgap_skipped"] == ["https://tracker.example.org/b"]
