from __future__ import annotations

from typing import Any, TypedDict


class CrawlState(TypedDict, total=False):
    source: str
    prompt: str
    schema: Any
    config: Any
    html: str
    markdown: str
    chunks: list[str]
    answer: Any
    validation_error: str | None
    reasoning: str | None
    attempt: int
    empty: bool
    errors: list[str]
    metadata: dict[str, Any]
