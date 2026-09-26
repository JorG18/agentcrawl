"""Shared page-to-record logic for the framework adapters."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from ..chunks import chunk_markdown
from ..crawler import AgentCrawl
from ..models import ScrapeDocument

# Flat, vector-store friendly metadata; everything else stays out.
_KEEP = ("title", "description", "final_url", "markdown_sha256", "language", "document_type")


def iter_records(
    sources: str | Sequence[str],
    *,
    mode: str = "scrape",
    config: dict[str, Any] | AgentCrawl | None = None,
    chunk_tokens: int | None = None,
    query: str | None = None,
    crawl_options: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(text, metadata)`` for each page, or each chunk when ``chunk_tokens`` is set.

    Failed pages are not yielded as empty documents; their errors are appended
    to ``errors`` so the caller can see what was skipped.
    """
    if mode not in {"scrape", "crawl"}:
        raise ValueError("mode must be 'scrape' or 'crawl'")
    crawler = config if isinstance(config, AgentCrawl) else AgentCrawl(config or {})
    for source in [sources] if isinstance(sources, str) else list(sources):
        if mode == "crawl":
            run = crawler.crawl(source, query=query, **(crawl_options or {}))
            documents = run.documents
            if errors is not None:
                errors.extend(run.errors)
        else:
            documents = [crawler.scrape(source)]
        for document in documents:
            if not isinstance(document, ScrapeDocument):
                continue
            if not document.ok or not document.markdown.strip():
                if errors is not None:
                    errors.extend(document.errors or [f"{document.url}: empty page"])
                continue
            yield from _records(document, chunk_tokens, query)


def _records(
    document: ScrapeDocument, chunk_tokens: int | None, query: str | None
) -> Iterator[tuple[str, dict[str, Any]]]:
    base = {"source": document.url}
    base.update(
        {key: document.metadata[key] for key in _KEEP if document.metadata.get(key) is not None}
    )
    if not chunk_tokens:
        yield document.markdown, base
        return
    cite = str(document.metadata.get("final_url") or document.url)
    for chunk in chunk_markdown(document.markdown, cite, max_tokens=chunk_tokens, query=query):
        metadata = {
            **base,
            "chunk_id": chunk["id"],
            "heading": chunk["heading"],
            "cite_url": chunk["cite_url"],
        }
        if "score" in chunk:
            metadata["score"] = chunk["score"]
        yield chunk["text"], metadata
