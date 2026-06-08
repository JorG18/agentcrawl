from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any, Iterable

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
        config = CrawlConfig.from_dict({**asdict(self.config), "output_format": "markdown"})
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

    def search(self, query: str) -> list[SearchResult]:
        return search_web(query, self.config)

    def search_then_scrape(
        self,
        query: str,
        prompt: str,
        schema: Any | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        results = self.search(query)
        if limit is not None:
            results = results[:limit]
        crawls = self.scrape_many([result.url for result in results], prompt, schema)
        return {
            "query": query,
            "search_results": results,
            "results": crawls,
            "answers": [crawl.answer for crawl in crawls if crawl.ok],
        }
