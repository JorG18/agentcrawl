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
