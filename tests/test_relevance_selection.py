"""Query-aware budget selection (new in 0.2.0).

v0.2.0 made truncation *honest* (``markdown_truncated``, ``chunks_omitted``) but
still kept the head of a long document, so a fact past the cut never reached
the agent or the model. With a query, the budget now goes to the passages that
match it (BM25), in document order; without a match nothing changes.
"""

from __future__ import annotations

from pathlib import Path

from agentcrawl import AgentCrawl
from agentcrawl.config import CrawlConfig
from agentcrawl.graph import CrawlGraph
from agentcrawl.parsing import budget_markdown, chunk_text_stats
from agentcrawl.relevance import bm25_scores, select_top_passages, tokenize

FILLER = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod. "


def _long_markdown(sections: int = 60) -> str:
    parts = []
    for index in range(sections):
        parts.append(f"## Sección {index}\n\n" + FILLER * 8)
    parts.insert(
        50, "## Política de devoluciones\n\nEl plazo de devolución es de 30 días corridos."
    )
    return "\n\n".join(parts)


def test_tokenize_folds_case_and_accents_and_drops_stop_words() -> None:
    assert tokenize("¿Cuál es la POLÍTICA de devolución?") == ["politica", "devolucion"]


def test_bm25_prefers_the_passage_that_matches() -> None:
    scores = bm25_scores(
        ["gatos y perros", "precio del envío: gratis", "otra cosa"], "precio envio"
    )
    assert scores[1] > 0 and scores[1] == max(scores)
    assert scores[0] == scores[2] == 0


def test_no_match_keeps_the_head_exactly_as_before() -> None:
    indices, ranked = select_top_passages(["a b", "c d", "e f"], "zzz", 2)
    assert (indices, ranked) == ([0, 1], False)


def test_budget_markdown_keeps_the_relevant_section_with_its_heading() -> None:
    markdown = _long_markdown()
    head, _, head_info = budget_markdown(markdown, 4_000)
    ranked, omitted, info = budget_markdown(markdown, 4_000, "plazo de devolución")

    assert "30 días" not in head and head_info == {"markdown_selection": "head"}
    assert "30 días corridos" in ranked
    assert "## Política de devoluciones" in ranked
    assert info["markdown_selection"] == "bm25"
    assert omitted == len(markdown) - len(ranked) and len(ranked) <= 4_000


def test_chunks_past_the_cap_reach_the_model_when_they_match() -> None:
    config = CrawlConfig(chunk_size=500, max_chunks=2)
    markdown = _long_markdown()

    head_chunks, head_stats = chunk_text_stats(markdown, config)
    chunks, stats = chunk_text_stats(markdown, config, query="plazo devolución")

    assert not any("30 días" in chunk for chunk in head_chunks)
    assert head_stats["chunk_selection"] == "head"
    assert any("30 días" in chunk for chunk in chunks)
    assert stats["chunk_selection"] == "bm25" and stats["chunks_used"] == 2


def test_relevance_can_be_turned_off() -> None:
    config = CrawlConfig(chunk_size=500, max_chunks=2, relevance_chunking=False)
    chunks, stats = chunk_text_stats(_long_markdown(), config, query="plazo devolución")
    assert stats["chunk_selection"] == "head"
    assert not any("30 días" in chunk for chunk in chunks)


def test_scrape_query_keeps_the_matching_passage(tmp_path: Path) -> None:
    html = "<html><body><main>" + "".join(
        f"<h2>Sección {i}</h2><p>{FILLER * 8}</p>" for i in range(60)
    )
    html += "<h2>Política de devoluciones</h2><p>El plazo de devolución es de 30 días.</p>"
    html += "</main></body></html>"
    page = tmp_path / "long.html"
    page.write_text(html, encoding="utf-8")
    crawler = AgentCrawl({"max_input_chars": 3_000})

    plain = crawler.scrape(str(page))
    focused = crawler.scrape(str(page), query="plazo de devolución")

    assert "30 días" not in plain.markdown and plain.metadata["markdown_selection"] == "head"
    assert "30 días" in focused.markdown
    assert focused.metadata["markdown_selection"] == "bm25"
    assert focused.metadata["markdown_truncated"] is True


def test_extraction_prompt_is_used_as_the_query(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_extract_answer(prompt, chunks, schema, config, previous_error):
        seen.append(chunks)
        return {"plazo": "30"}, None, None

    monkeypatch.setattr("agentcrawl.graph.extract_answer", fake_extract_answer)
    monkeypatch.setattr(
        "agentcrawl.graph.fetch_source",
        lambda source, config: ("", {"fetcher": "file", "document_type": "markdown"}),
    )
    monkeypatch.setattr(
        "agentcrawl.graph.markdown_from_fetched_content", lambda html, metadata: _long_markdown()
    )
    config = CrawlConfig(chunk_size=500, max_chunks=2, auto_reattempt=False)

    result = CrawlGraph(config).run("doc.md", "¿Cuál es el plazo de devolución?")

    assert any("30 días" in chunk for chunk in seen[0])
    assert result.metadata["chunking"]["chunk_selection"] == "bm25"
