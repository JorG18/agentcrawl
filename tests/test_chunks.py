"""The ``chunks`` output format: token-budgeted, citable pieces of a page."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.chunks import chunk_markdown
from agentcrawl.config import CrawlConfig
from agentcrawl.utils import estimate_tokens

URL = "https://docs.example.com/guide"

MARKDOWN = """# Guide

Intro paragraph about the product.

## Install

Run the installer with [pip](https://pypi.org) on any machine.

```python
import agentcrawl
```

## Rate limits

Each key gets sixty requests per minute. Owner keys are exempt.

### Bursts

Short bursts above the limit answer 429 with a retry-after header."""


def test_chunks_follow_sections_and_carry_their_heading_path() -> None:
    chunks = chunk_markdown(MARKDOWN, URL, max_tokens=400)

    assert [chunk["heading"] for chunk in chunks] == [
        "Guide",
        "Guide > Install",
        "Guide > Rate limits",
        "Guide > Rate limits > Bursts",
    ]
    assert [chunk["id"] for chunk in chunks] == [0, 1, 2, 3]
    # A code fence stays whole inside its section's chunk.
    assert "```python\nimport agentcrawl\n```" in chunks[1]["text"]
    assert all(chunk["url"] == URL for chunk in chunks)
    assert "score" not in chunks[0]


def test_cite_url_points_at_the_chunk_first_words() -> None:
    chunks = chunk_markdown(MARKDOWN, URL + "#old-anchor", max_tokens=400)

    # A one-word heading is skipped (too vague to match); the link target is
    # dropped; a "-" is percent-encoded because it is text-fragment syntax.
    assert chunks[1]["cite_url"] == (
        f"{URL}#:~:text=Run%20the%20installer%20with%20pip%20on%20any%20machine."
    )
    dashed = chunk_markdown("Pick a built-in step now.", URL)
    assert dashed[0]["cite_url"].endswith("text=Pick%20a%20built%2Din%20step%20now.")
    local = chunk_markdown("## A b\n\ntext", "/tmp/notes.md")
    assert local[0]["cite_url"] == "/tmp/notes.md"


def test_chunks_respect_the_token_budget() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    long_line_block = "\n".join("line " * 30 for _ in range(40))

    chunks = chunk_markdown(paragraphs + "\n\n" + long_line_block, URL, max_tokens=120)

    assert len(chunks) > 5
    assert all(chunk["estimated_tokens"] <= 120 for chunk in chunks)
    assert all(chunk["estimated_tokens"] == estimate_tokens(chunk["text"]) for chunk in chunks)
    joined = "".join(chunk["text"] for chunk in chunks)
    assert "Paragraph 11" in joined and joined.count("line") == 40 * 30


def test_query_adds_bm25_scores_in_document_order() -> None:
    chunks = chunk_markdown(MARKDOWN, URL, query="rate limit per minute")

    scores = [chunk["score"] for chunk in chunks]
    assert max(scores) == chunks[2]["score"] > 0
    assert chunks[1]["score"] == 0


def test_scrape_returns_chunks_format(tmp_path: Path) -> None:
    page = tmp_path / "guide.html"
    page.write_text(
        "<html><body><main><h1>Guide</h1><p>First part of the guide.</p>"
        "<h2>Limits</h2><p>Sixty requests per minute.</p></main></body></html>",
        encoding="utf-8",
    )

    result = AgentCrawl({"chunk_tokens": 50}).scrape(
        str(page), formats=["chunks"], query="requests per minute"
    )

    assert [chunk["heading"] for chunk in result["chunks"]] == ["Guide", "Guide > Limits"]
    assert result["chunks"][1]["score"] > 0


def test_chunk_tokens_is_validated() -> None:
    with pytest.raises(ValueError):
        CrawlConfig.from_dict({"chunk_tokens": 10})


def test_single_oversized_line_is_split() -> None:
    chunks = chunk_markdown("word " * 400, "https://e.x/", max_tokens=50)
    assert all(c["estimated_tokens"] <= 50 for c in chunks)
    assert "".join(c["text"].replace(" ", "") for c in chunks) == "word" * 400


def test_single_unbroken_token_is_split() -> None:
    chunks = chunk_markdown("a" * 5000, "https://e.x/", max_tokens=50)
    assert all(c["estimated_tokens"] <= 50 for c in chunks)
    assert "".join(c["text"] for c in chunks) == "a" * 5000
