"""Citable, token-budgeted chunks of a page (the ``chunks`` output format).

An agent that retrieves over pages wants pieces that fit its budget and can be
cited back to the source. Each chunk keeps whole Markdown blocks (a table or a
code fence is never cut unless it alone exceeds the budget), carries the
heading path it sits under, and a ``cite_url`` that deep-links to its first
words with a URL text fragment (``#:~:text=``), which needs no element ids on
the page. With a query, every chunk also gets its BM25 score; the order stays
the document order so neighbouring chunks still read naturally.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .relevance import bm25_scores, split_blocks
from .utils import estimate_tokens

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"[`*_>#|\[\]()!]+|^\s*[-+]\s+|^\s*\d+[.)]\s+")
_FRAGMENT_WORDS = 8


def chunk_markdown(
    markdown: str,
    url: str,
    *,
    max_tokens: int = 400,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Split ``markdown`` into chunks of at most ``max_tokens`` (estimated)."""
    pieces: list[tuple[list[str], str]] = []  # (heading path, block)
    path: list[tuple[int, str]] = []
    for block in split_blocks(markdown):
        match = _HEADING.match(block.strip()) if "\n" not in block.strip() else None
        if match:
            level = len(match.group(1))
            path = [item for item in path if item[0] < level] + [(level, match.group(2))]
        headings = [title for _level, title in path]
        for part in _split_oversized(block, max_tokens):
            pieces.append((headings, part))

    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_path: list[str] = []
    for headings, block in pieces:
        text = "\n\n".join([*current, block])
        starts_section = bool(_HEADING.match(block.strip())) and bool(current)
        if current and (starts_section or estimate_tokens(text) > max_tokens):
            chunks.append(_chunk(current, current_path, url))
            current = []
        if not current:
            current_path = headings
        current.append(block)
    if current:
        chunks.append(_chunk(current, current_path, url))

    for index, chunk in enumerate(chunks):
        chunk["id"] = index
    if query and chunks:
        for chunk, score in zip(chunks, bm25_scores([c["text"] for c in chunks], query)):
            chunk["score"] = round(score, 4)
    return chunks


def _split_oversized(block: str, max_tokens: int) -> list[str]:
    """Cut a block larger than the budget at line boundaries (never mid-line)."""
    if estimate_tokens(block) <= max_tokens:
        return [block]
    parts: list[str] = []
    current: list[str] = []
    for line in block.split("\n"):
        if current and estimate_tokens("\n".join([*current, line])) > max_tokens:
            parts.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("\n".join(current))
    return parts


def _chunk(blocks: list[str], headings: list[str], url: str) -> dict[str, Any]:
    text = "\n\n".join(blocks)
    return {
        "id": 0,
        "text": text,
        "heading": " > ".join(headings),
        "url": url,
        "cite_url": _cite_url(url, text),
        "estimated_tokens": estimate_tokens(text),
    }


def _cite_url(url: str, text: str) -> str:
    """``url`` plus a text fragment on the chunk's first words, when it has any."""
    if not url.startswith(("http://", "https://")):
        return url
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            continue
        words = _MARKUP.sub(" ", _LINK.sub(r"\1", line)).split()
        if len(words) >= 2:
            # "-" is text-fragment syntax, so it must be percent-encoded too.
            fragment = urllib.parse.quote(" ".join(words[:_FRAGMENT_WORDS]), safe="")
            fragment = fragment.replace("-", "%2D")
            base = url.split("#", 1)[0]
            return f"{base}#:~:text={fragment}"
    return url
