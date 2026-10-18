from __future__ import annotations

from agentcrawl.config import CrawlConfig
from agentcrawl.parsing import html_to_markdown


def _md(html: str) -> str:
    return html_to_markdown(html, CrawlConfig(), only_main_content=True)


def test_cookie_consent_text_is_dropped_from_markdown() -> None:
    html = (
        "<html><body>"
        "<main><article><h1>Authenticating With API Keys</h1>"
        "<p>Generate a key, store it in env vars, never commit it.</p>"
        "</article></main>"
        "<section><p>This site uses cookies to ensure you get the best experience.</p></section>"
        "<div><p>We use cookies for analytics and personalization. By continuing you "
        "accept the cookie policy and consent to cookies across this site.</p></div>"
        "</body></html>"
    )
    markdown = _md(html)
    assert "Authenticating With API Keys" in markdown
    assert "Generate a key" in markdown
    assert "This site uses cookies" not in markdown
    assert "consent to cookies" not in markdown


def test_paragraph_that_mentions_cookies_as_a_concept_is_kept() -> None:
    html = (
        "<html><body>"
        "<main><article><h1>Stateful sessions</h1>"
        "<p>API clients send a session cookie via the Cookie header on every request "
        "so the server can resume prior state in long-running crawls. This is distinct "
        "from consent banners; rotate the cookie every 24 hours for safety.</p>"
        "</article></main>"
        "</body></html>"
    )
    markdown = _md(html)
    assert "Stateful sessions" in markdown
    assert "session cookie" in markdown
    assert "Cookie header" in markdown


def test_long_cookie_heavy_block_is_not_dropped_as_boilerplate() -> None:
    html = (
        "<html><body>"
        "<main><article><h1>Cookie policy walkthrough</h1>"
        "<p>" + ("This site uses cookies. " * 60) + "</p>"
        "</article></main>"
        "</body></html>"
    )
    markdown = _md(html)
    assert "Cookie policy walkthrough" in markdown


def test_unclosed_list_items_do_not_nest() -> None:
    # HTML lets </li> be omitted; 400 such items crashed with RecursionError.
    items = "".join(f"<li>item number {i} with some words" for i in range(400))
    md = _md(f"<html><body><main><h1>List</h1><ul>{items}</ul></main></body></html>")
    assert "item number 0 " in md and "item number 399 " in md


def test_unclosed_table_cells_and_paragraphs_do_not_nest() -> None:
    from agentcrawl.parsing import _HTMLTreeParser

    parser = _HTMLTreeParser()
    parser.feed("<table><tr><td>A<td>1<tr><td>B<td>2</table><p>one<p>two<div>x</div>")
    table, p1, p2 = parser.root.children[:3]
    rows = [c for c in table.children if not isinstance(c, str)]
    assert [len(r.children) for r in rows] == [2, 2]
    assert (p1.tag, p1.children, p2.tag) == ("p", ["one"], "p")
    assert [c.tag for c in parser.root.children[3:]] == ["div"]


def test_very_deep_markup_falls_back_instead_of_crashing() -> None:
    md = _md(
        "<html><body>" + "<div>" * 1500 + "deep text here" + "</div>" * 1500 + "</body></html>"
    )
    assert "deep text here" in md


def test_links_resolve_against_final_url_and_base_href(monkeypatch) -> None:
    from agentcrawl import AgentCrawl

    pages = {
        "https://example.com/docs": (
            '<html><body><main><a href="intro">Intro</a></main></body></html>',
            "https://example.com/en/docs/",
        ),
        "https://example.com/v1/": (
            '<html><head><base href="/v2/"></head><body><a href="page">P</a></body></html>',
            "https://example.com/v1/",
        ),
    }
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source",
        lambda source, config: (
            pages[source][0],
            {"fetcher": "http", "final_url": pages[source][1]},
        ),
    )
    crawler = AgentCrawl({"browser_fallback": False})

    redirected = crawler.scrape("https://example.com/docs")
    based = crawler.scrape("https://example.com/v1/")

    assert redirected.links == ["https://example.com/en/docs/intro"]
    assert redirected.metadata["source_url"] == "https://example.com/docs"  # what was asked for
    assert based.links == ["https://example.com/v2/page"]
    assert "https://example.com/v2/page" in based.markdown  # Markdown agrees with links


def test_self_closing_hr_closes_an_open_paragraph() -> None:
    from agentcrawl.parsing import _HTMLTreeParser

    parser = _HTMLTreeParser()
    parser.feed("<p>one<hr/>two<p>three")
    assert [getattr(c, "tag", c) for c in parser.root.children] == ["p", "hr", "two", "p"]
