from __future__ import annotations

from agentcrawl.crawler import _estimate_tokens, _markdown_to_text
from agentcrawl.config import CrawlConfig
from agentcrawl.parsing import html_to_markdown


def test_estimate_tokens_zero_for_empty_text() -> None:
    assert _estimate_tokens("") == 0


def test_estimate_tokens_uses_four_chars_per_token_floor() -> None:
    # 12 chars → 3 tokens (12 // 4)
    assert _estimate_tokens("a" * 12) == 3
    # 1 char → at least 1, never zero
    assert _estimate_tokens("a") == 1


def test_estimate_tokens_scales_with_text_length() -> None:
    short = _estimate_tokens("hello world")
    long = _estimate_tokens("hello world " * 100)
    assert long > short


def test_html_to_markdown_exposes_token_metadata_in_scrape() -> None:
    """End-to-end: a small HTML page should produce a document with
    estimated_tokens, raw_html_tokens_estimate, and raw_html_bytes in
    metadata."""

    html = (
        "<html><body>"
        "<main><article><h1>API Tokens Note</h1>"
        "<p>This short guide explains how to keep your tokens secret. "
        "Store tokens in env vars; never commit them to source control.</p>"
        "</article></main>"
        "</body></html>"
    )
    md = html_to_markdown(html, CrawlConfig(), only_main_content=True)
    text = _markdown_to_text(md)
    metadata = {
        "estimated_tokens": _estimate_tokens(text),
        "raw_html_tokens_estimate": _estimate_tokens(html),
        "raw_html_bytes": len(html.encode("utf-8")),
    }
    assert "estimated_tokens" in metadata
    assert "raw_html_tokens_estimate" in metadata
    assert "raw_html_bytes" in metadata
    assert metadata["estimated_tokens"] < metadata["raw_html_tokens_estimate"]
