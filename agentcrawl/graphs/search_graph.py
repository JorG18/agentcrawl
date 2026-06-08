from __future__ import annotations

from typing import Any

from ..client import AgentCrawler
from ..config_adapter import normalize_graph_config


class SearchGraph:
    """AgentCrawl search and extraction graph."""

    def __init__(
        self,
        prompt: str,
        config: dict[str, Any] | None = None,
        query: str | None = None,
        schema: Any | None = None,
    ):
        self.prompt = prompt
        self.query = query or prompt
        self.schema = schema
        self.config = normalize_graph_config(config)

    def run(self) -> dict[str, Any]:
        return AgentCrawler(self.config).search_then_scrape(self.query, self.prompt, self.schema)
