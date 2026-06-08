from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScrapeDocument:
    url: str
    markdown: str
    text: str
    html: str = ""
    links: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(slots=True)
class MapResult:
    source: str
    urls: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class CrawlRun:
    source: str
    documents: list[ScrapeDocument]
    visited_urls: list[str]
    discovered_urls: list[str]
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class CrawlResult:
    source: str
    prompt: str
    answer: Any
    markdown: str
    raw_html: str = ""
    chunks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors
