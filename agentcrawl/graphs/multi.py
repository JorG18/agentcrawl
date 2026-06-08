from __future__ import annotations

from typing import Any

from ..client import AgentCrawler
from ..config_adapter import normalize_graph_config


class MultiExtractionGraph:
    """AgentCrawl multi-source extraction graph."""

    def __init__(
        self,
        prompt: str,
        sources: list[str],
        config: dict[str, Any] | None = None,
        schema: Any | None = None,
    ):
        self.prompt = prompt
        self.sources = sources
        self.schema = schema
        self.config = normalize_graph_config(config)

    def run(self) -> list[Any]:
        results = AgentCrawler(self.config).scrape_many(self.sources, self.prompt, self.schema)
        return [
            result.answer if not result.errors else {"error": "; ".join(result.errors)}
            for result in results
        ]
