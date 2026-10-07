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
    """Cut a block larger than the budget at line boundaries.

    A single line over the budget (minified code, a one-line table) is cut
    at spaces, or mid-word when one word alone is too long.
    """
    if estimate_tokens(block) <= max_tokens:
        return [block]
    parts: list[str] = []
    current: list[str] = []
    for line in (piece for raw in block.split("\n") for piece in _wrap(raw, max_tokens)):
        if current and estimate_tokens("\n".join([*current, line])) > max_tokens:
            parts.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("\n".join(current))
    return parts


def _wrap(line: str, max_tokens: int) -> list[str]:
    limit = max(1, max_tokens) * 4  # estimate_tokens counts 4 characters a token
    pieces: list[str] = []
    while len(line) > limit:
        cut = line.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        pieces.append(line[:cut])
        line = line[cut:].lstrip(" ")
    pieces.append(line)
    return pieces


def _chunk(blocks: list[str], headings: list[str], url: str) -> dict[str, Any]:
    text = "\n\n".join(blocks)
    return {
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


def _sections(markdown: str) -> list[dict[str, Any]]:
    """The page cut at every heading: id, heading path, level and Markdown."""
    sections: list[dict[str, Any]] = []
    path: list[tuple[int, str]] = []
    current: dict[str, Any] = {"heading": [], "level": 0, "blocks": []}
    for block in split_blocks(markdown):
        stripped = block.strip()
        match = _HEADING.match(stripped) if "\n" not in stripped else None
        if match:
            if current["blocks"]:
                sections.append(current)
            level = len(match.group(1))
            path = [item for item in path if item[0] < level] + [(level, match.group(2))]
            current = {"heading": [title for _l, title in path], "level": level, "blocks": []}
        current["blocks"].append(block)
    if current["blocks"]:
        sections.append(current)
    for number, section in enumerate(sections, 1):
        section["id"] = f"s{number}"
        section["markdown"] = "\n\n".join(section.pop("blocks"))
    return sections


def _section_end(sections: list[dict[str, Any]], start: int) -> int:
    """Index after ``start``'s last subsection (text before any heading has none)."""
    level = sections[start]["level"]
    end = start + 1
    if level:
        while end < len(sections) and sections[end]["level"] > level:
            end += 1
    return end


def outline_markdown(markdown: str) -> list[dict[str, Any]]:
    """What a long page holds, without its text: read the outline, then the
    one section that answers the question (``section=``). ``tokens`` is the
    size of what that section returns, subsections included."""
    sections = _sections(markdown)
    return [
        {
            "id": section["id"],
            "heading": " > ".join(section["heading"]) or "(before the first heading)",
            "level": section["level"],
            "tokens": sum(
                estimate_tokens(s["markdown"]) for s in sections[i : _section_end(sections, i)]
            ),
        }
        for i, section in enumerate(sections)
    ]


def select_section(markdown: str, wanted: str) -> str | None:
    """A section with its subsections, by outline id (``s3``) or heading text
    (exact last heading first, then any heading path containing it)."""
    sections = _sections(markdown)
    key = wanted.strip().casefold()
    matchers = (
        lambda s: s["id"] == key,
        lambda s: bool(s["heading"]) and s["heading"][-1].casefold() == key,
        lambda s: key in " > ".join(s["heading"]).casefold(),
    )
    for matches in matchers:
        start = next((i for i, s in enumerate(sections) if matches(s)), None)
        if start is not None:
            end = _section_end(sections, start)
            return "\n\n".join(s["markdown"] for s in sections[start:end])
    return None
