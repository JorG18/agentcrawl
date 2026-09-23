"""Query-aware selection of what fits in a budget (BM25, stdlib only).

When a document is larger than the budget (``max_chunks`` for LLM extraction,
``max_input_chars`` for scrape output), the old behaviour kept the *head* of the
document. For a question about something on page 40 that is exactly the wrong
half. With a query, the budget is spent on the passages that score best for it
and the kept passages stay in document order so the text still reads naturally.

BM25 is deliberately simple and dependency-free: no embeddings, no model, fully
deterministic — the same query over the same page selects the same passages.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"\w+", re.UNICODE)

# Small bilingual stop list: the product's users write queries in English and
# Spanish. Stop words only change ranking weight, never correctness.
_STOP = frozenset(
    """
    a an and are as at be by for from has have how in is it its of on or that the
    this to was were what when where which who why will with does do you your
    el la los las un una unos unas y o de del al en es son que qué por para con
    como cómo cuál cuáles cuando dónde se su sus lo le les mi tu
    """.split()
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


# Tokens are accent-folded, so the stop list must be too ("cuál" -> "cual").
_STOP = frozenset(_fold(word) for word in _STOP)


def tokenize(text: str) -> list[str]:
    return [token for token in _WORD.findall(_fold(text)) if len(token) > 1 and token not in _STOP]


def bm25_scores(
    passages: list[str], query: str, *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """BM25 score of every passage for ``query`` (0.0 when nothing matches)."""
    terms = list(dict.fromkeys(tokenize(query)))
    if not passages or not terms:
        return [0.0] * len(passages)
    tokenized = [tokenize(passage) for passage in passages]
    lengths = [len(tokens) for tokens in tokenized]
    average = (sum(lengths) / len(lengths)) or 1.0
    document_frequency = {
        term: sum(1 for tokens in tokenized if term in set(tokens)) for term in terms
    }
    total = len(passages)
    scores: list[float] = []
    for tokens, length in zip(tokenized, lengths):
        counts = Counter(tokens)
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            score += idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length / average))
        scores.append(score)
    return scores


def select_top_passages(passages: list[str], query: str, keep: int) -> tuple[list[int], bool]:
    """Indices of the ``keep`` best passages, in document order.

    Returns ``(indices, ranked)``. ``ranked`` is False when the query matched
    nothing: the head of the document is kept, exactly as before, so a query
    can never make the result worse than no query.
    """
    if keep >= len(passages):
        return list(range(len(passages))), False
    scores = bm25_scores(passages, query)
    if not any(scores):
        return list(range(keep)), False
    order = sorted(range(len(passages)), key=lambda index: (-scores[index], index))
    return sorted(order[:keep]), True


def split_blocks(markdown: str) -> list[str]:
    """Markdown blocks (paragraphs, list runs, tables, code fences) for selection."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def select_within_budget(markdown: str, query: str, limit: int) -> tuple[str, dict[str, object]]:
    """Keep the most relevant blocks of ``markdown`` that fit in ``limit`` chars.

    Headings travel with the section they introduce: a kept block also keeps
    the nearest heading above it, so the output still says *where* each passage
    came from. Returns ``(text, info)``; ``info["ranked"]`` is False when the
    query matched nothing (caller falls back to the head cut).
    """
    blocks = split_blocks(markdown)
    scores = bm25_scores(blocks, query)
    if not any(scores):
        return markdown[:limit], {"ranked": False}
    heading_for: list[int | None] = []
    last_heading: int | None = None
    for index, block in enumerate(blocks):
        if block.lstrip().startswith("#"):
            last_heading = index
        heading_for.append(last_heading)

    chosen: set[int] = set()
    used = 0
    for index in sorted(range(len(blocks)), key=lambda i: (-scores[i], i)):
        if scores[index] <= 0:
            break
        additions = [index]
        heading = heading_for[index]
        if heading is not None and heading not in chosen and heading != index:
            additions.insert(0, heading)
        cost = sum(len(blocks[i]) + 2 for i in additions if i not in chosen)
        if used + cost > limit:
            continue
        chosen.update(additions)
        used += cost
    if not chosen:
        return markdown[:limit], {"ranked": False}
    kept = [blocks[i] for i in sorted(chosen)]
    return "\n\n".join(kept), {
        "ranked": True,
        "blocks_total": len(blocks),
        "blocks_kept": len(chosen),
    }
