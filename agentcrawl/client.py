from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, Iterable

from .airgap import AuditTrail
from .config import CrawlConfig
from .graph import CrawlGraph
from .models import CrawlResult, SearchResult
from .search import search_web


class AgentCrawler:
    """Programmatic entry point for prompt-driven scraping."""

    def __init__(self, config: dict[str, Any] | CrawlConfig | None = None):
        self.config = CrawlConfig.from_dict(config)

    def extract(self, source: str, prompt: str, schema: Any | None = None) -> CrawlResult:
        return CrawlGraph(self.config).run(source, prompt, schema)

    def markdown(
        self, source: str, prompt: str = "Extract the main content as clean markdown."
    ) -> CrawlResult:
        # ``replace`` copies fields shallowly; ``asdict`` deep-copied the
        # caller's ``llm`` client, which fails for any client holding a lock.
        config = replace(self.config, output_format="markdown")
        return CrawlGraph(config).run(source, prompt, None)

    def scrape_many(
        self,
        sources: Iterable[str],
        prompt: str,
        schema: Any | None = None,
    ) -> list[CrawlResult]:
        source_list = list(sources)
        if not source_list:
            return []
        max_workers = max(1, min(self.config.parallelism, len(source_list)))
        results_by_index: dict[int, CrawlResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.extract, source, prompt, schema): index
                for index, source in enumerate(source_list)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results_by_index[index] = future.result()
                except Exception as exc:
                    results_by_index[index] = CrawlResult(
                        source=source_list[index],
                        prompt=prompt,
                        answer=None,
                        markdown="",
                        errors=[str(exc)],
                    )
        return [results_by_index[index] for index in range(len(source_list))]

    def search(self, query: str, audit_trail: AuditTrail | None = None) -> list[SearchResult]:
        """Search the web. Pass ``audit_trail`` to record the request."""
        return search_web(query, self.config, audit_trail)

    def search_then_scrape(
        self,
        query: str,
        prompt: str,
        schema: Any | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        # With ``audit=True`` the search request must show up in the trail like
        # any other request; it used to be invisible because search bypassed
        # the guarded opener entirely.
        trail = AuditTrail() if self.config.audit else None
        results = self.search(query, trail)
        airgap_skipped: list[str] = []
        if self.config.airgap:
            # Each scrape trusts its own target host, so without this filter a
            # search engine — not the caller — chose which hosts an airgapped
            # run contacted. Under airgap only allowlisted result hosts run.
            allowed = []
            for result in results:
                if _allowlisted(result.url, self.config.allowlist_domains):
                    allowed.append(result)
                else:
                    airgap_skipped.append(result.url)
            results = allowed
        if limit is not None:
            results = results[:limit]
        crawls = self.scrape_many([result.url for result in results], prompt, schema)
        payload: dict[str, Any] = {
            "query": query,
            "search_results": results,
            "results": crawls,
            "answers": [crawl.answer for crawl in crawls if crawl.ok],
        }
        if airgap_skipped:
            payload["airgap_skipped"] = airgap_skipped
        if trail is not None:
            payload["audit"] = trail.to_metadata()
        return payload


def _allowlisted(url: str, allowlist: Iterable[str]) -> bool:
    from urllib.parse import urlsplit

    from .airgap import _match

    host = (urlsplit(url).hostname or "").lower()
    return bool(host) and any(_match(host, entry) for entry in allowlist)
