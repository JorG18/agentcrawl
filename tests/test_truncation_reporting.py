"""Regression tests for the 2026-09 silent-truncation findings.

Before this fix ``html_to_markdown`` cut the primary output at
``max_input_chars`` and ``chunk_text`` dropped everything past
``max_chunks``, both without any signal. The parser no longer truncates:
callers apply the budget and report what they dropped.
"""

from __future__ import annotations

from pathlib import Path

from agentcrawl import AgentCrawl
from agentcrawl.browser_retry import attempt_browser_retry
from agentcrawl.config import CrawlConfig
from agentcrawl.crawler import _markdown_to_text
from agentcrawl.parsing import (
    apply_output_budget,
    chunk_text,
    chunk_text_stats,
    html_to_markdown,
)

_BIG_PARAGRAPHS = 4000


def _big_html() -> str:
    body = "".join(
        f"<p>Paragraph {index} carries enough words to consume budget.</p>"
        for index in range(_BIG_PARAGRAPHS)
    )
    return f"<html><body><article><h1>Long document</h1>{body}</article></body></html>"


def test_apply_output_budget_reports_what_it_dropped() -> None:
    text, omitted = apply_output_budget("abcdef", 4)
    assert text == "abcd"
    assert omitted == 2


def test_apply_output_budget_is_a_noop_under_the_limit() -> None:
    text, omitted = apply_output_budget("abc", 10)
    assert text == "abc"
    assert omitted == 0


def test_html_to_markdown_no_longer_truncates() -> None:
    config = CrawlConfig()
    markdown = html_to_markdown(_big_html(), config, only_main_content=True)
    assert len(markdown) > config.max_input_chars


def test_chunk_text_stats_reports_omitted_chunks() -> None:
    config = CrawlConfig(chunk_size=200, max_chunks=2)
    text = "Frase de relleno. " * 200
    chunks, stats = chunk_text_stats(text, config)
    assert len(chunks) == 2
    assert stats["chunks_used"] == 2
    assert stats["chunks_omitted"] > 0
    assert stats["chunks_total"] == 2 + stats["chunks_omitted"]
    assert stats["chunk_chars_omitted"] > 0
    assert chunk_text(text, config) == chunks


def test_scrape_reports_truncation_in_metadata(tmp_path: Path) -> None:
    page = tmp_path / "big.html"
    page.write_text(_big_html(), encoding="utf-8")

    document = AgentCrawl(CrawlConfig(max_input_chars=1_000)).scrape(str(page))

    assert document.ok
    assert document.metadata["markdown_truncated"] is True
    assert document.metadata["chars_omitted"] > 0
    assert document.metadata["markdown_chars"] == 1_000
    assert document.metadata["markdown_chars_full"] > 1_000


def test_scrape_reports_no_truncation_for_a_small_page(tmp_path: Path) -> None:
    page = tmp_path / "small.html"
    page.write_text(
        "<html><body><article><h1>Short</h1><p>ok</p></article></body></html>",
        encoding="utf-8",
    )

    document = AgentCrawl().scrape(str(page))

    assert document.metadata["markdown_truncated"] is False
    assert document.metadata["chars_omitted"] == 0
    assert document.metadata["markdown_chars"] == document.metadata["markdown_chars_full"]


def test_browser_retry_document_carries_the_token_metrics(monkeypatch) -> None:
    html = "<html><body><article><h1>Hola</h1><p>" + "texto " * 60 + "</p></article></body></html>"
    monkeypatch.setattr(
        "agentcrawl.browser_retry.fetch_source",
        lambda source, config: (html, {"fetcher": "playwright", "final_url": source}),
    )

    document = attempt_browser_retry(
        "https://example.com/page",
        original_metadata={},
        blocked_reason="client challenge",
        original_config=CrawlConfig(),
        only_main_content=None,
        requested=["markdown"],
    )

    assert document is not None
    text = _markdown_to_text(document.markdown)
    assert document.metadata["estimated_tokens"] == max(1, len(text) // 4)
    assert document.metadata["raw_html_bytes"] == len(html.encode("utf-8"))
    assert document.metadata["raw_html_tokens_estimate"] == max(1, len(html) // 4)
    assert document.metadata["markdown_truncated"] is False
