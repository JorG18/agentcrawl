"""LangChain document loader backed by AgentCrawl.

    from agentcrawl.integrations.langchain import AgentCrawlLoader

    docs = AgentCrawlLoader(["https://docs.example.com/"], mode="crawl",
                            chunk_tokens=400, crawl_options={"max_pages": 20}).load()

Needs ``langchain-core``. Each page (or chunk) becomes a ``Document`` whose
metadata carries ``source``, ``title`` and, for chunks, ``heading`` and a
``cite_url`` that deep-links to the passage.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

try:
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.documents import Document
except ImportError as exc:  # pragma: no cover - exercised via the error message
    raise ImportError(
        "AgentCrawlLoader needs LangChain: python -m pip install langchain-core"
    ) from exc

from ._records import iter_records


class AgentCrawlLoader(BaseLoader):
    def __init__(
        self,
        sources: str | Sequence[str],
        *,
        mode: str = "scrape",
        config: dict[str, Any] | None = None,
        chunk_tokens: int | None = None,
        query: str | None = None,
        crawl_options: dict[str, Any] | None = None,
    ) -> None:
        self.sources = sources
        self.mode = mode
        self.config = config
        self.chunk_tokens = chunk_tokens
        self.query = query
        self.crawl_options = crawl_options
        self.errors: list[str] = []

    def lazy_load(self) -> Iterator[Document]:
        for text, metadata in iter_records(
            self.sources,
            mode=self.mode,
            config=self.config,
            chunk_tokens=self.chunk_tokens,
            query=self.query,
            crawl_options=self.crawl_options,
            errors=self.errors,
        ):
            yield Document(page_content=text, metadata=metadata)
