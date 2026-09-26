"""LlamaIndex reader backed by AgentCrawl.

    from agentcrawl.integrations.llama_index import AgentCrawlReader

    documents = AgentCrawlReader(chunk_tokens=400).load_data(["https://example.com/"])

Needs ``llama-index-core``. Each page (or chunk) becomes a ``Document`` with
``source``, ``title`` and, for chunks, ``heading`` and ``cite_url`` metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

try:
    from llama_index.core.readers.base import BaseReader
    from llama_index.core.schema import Document
except ImportError as exc:  # pragma: no cover - exercised via the error message
    raise ImportError(
        "AgentCrawlReader needs LlamaIndex: python -m pip install llama-index-core"
    ) from exc

from ._records import iter_records


class AgentCrawlReader(BaseReader):
    def __init__(
        self,
        *,
        mode: str = "scrape",
        config: dict[str, Any] | None = None,
        chunk_tokens: int | None = None,
        query: str | None = None,
        crawl_options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._config = config
        self._chunk_tokens = chunk_tokens
        self._query = query
        self._crawl_options = crawl_options
        self.errors: list[str] = []

    def load_data(self, urls: str | Sequence[str]) -> list[Document]:
        return [
            Document(text=text, metadata=metadata)
            for text, metadata in iter_records(
                urls,
                mode=self._mode,
                config=self._config,
                chunk_tokens=self._chunk_tokens,
                query=self._query,
                crawl_options=self._crawl_options,
                errors=self.errors,
            )
        ]
