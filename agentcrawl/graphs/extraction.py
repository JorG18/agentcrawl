from __future__ import annotations

import warnings
from typing import Any

from ..client import AgentCrawler
from ..config_adapter import normalize_graph_config


class ExtractionGraph:
    """AgentCrawl single-source extraction graph."""

    def __init__(
        self,
        prompt: str,
        source: str,
        config: dict[str, Any] | None = None,
        schema: Any | None = None,
    ):
        warnings.warn(
            "ExtractionGraph is deprecated and will be removed in 0.6; use AgentCrawl(...).extract instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.prompt = prompt
        self.source = source
        self.schema = schema
        self.config = normalize_graph_config(config)

    def run(self) -> Any:
        result = AgentCrawler(self.config).extract(self.source, self.prompt, self.schema)
        if result.errors:
            return {"error": "; ".join(result.errors)}
        return result.answer
