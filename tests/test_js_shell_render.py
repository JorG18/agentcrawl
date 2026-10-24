"""Script-rendered shells are re-read in the local browser (when allowed)."""

from __future__ import annotations

from pathlib import Path

from agentcrawl import fetchers
from agentcrawl.challenge import needs_javascript
from agentcrawl.config import CrawlConfig

QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"
SHELL = (QUALITY / "spa_shell.html").read_text(encoding="utf-8")
RENDERED = "<html><body><main><h1>Dashboard</h1><p>Rendered content.</p></main></body></html>"


def test_shell_needs_javascript_and_real_pages_do_not() -> None:
    assert needs_javascript(SHELL)
    for name in ("documentation.html", "article.html", "blog.html", "table.html"):
        assert not needs_javascript((QUALITY / name).read_text(encoding="utf-8")), name
    # No script at all: a short static page is just short.
    assert not needs_javascript("<html><body><p>Hello</p></body></html>")


def _patch(
    monkeypatch, *, available: bool, browser_html: str | None, http_html: str = SHELL
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(
        fetchers,
        "_fetch_http",
        lambda source, config: (http_html, {"fetcher": "http", "final_url": source}),
    )
    monkeypatch.setattr(fetchers, "_browser_backend_available", lambda backend: available)

    def fake_browser(source, config, **kwargs):
        calls.append(source)
        if browser_html is None:
            raise fetchers.FetchError("browser crashed", error_type="browser_error")
        return browser_html

    monkeypatch.setattr(fetchers, "_fetch_browser", fake_browser)
    return calls


def test_shell_is_rendered_in_the_browser(monkeypatch) -> None:
    calls = _patch(monkeypatch, available=True, browser_html=RENDERED)
    html, metadata = fetchers.fetch_source("https://app.example.org/", CrawlConfig())
    assert calls == ["https://app.example.org/"]
    assert html == RENDERED
    assert metadata["fallback_reason"] == "javascript_required"
    assert metadata["fetcher"] == "playwright"


def test_no_browser_installed_says_javascript_is_required(monkeypatch) -> None:
    calls = _patch(monkeypatch, available=False, browser_html=RENDERED)
    html, metadata = fetchers.fetch_source("https://app.example.org/", CrawlConfig())
    assert calls == []
    assert html == SHELL
    assert metadata["javascript_required"] is True


def test_browser_failure_keeps_the_http_result(monkeypatch) -> None:
    _patch(monkeypatch, available=True, browser_html=None)
    html, metadata = fetchers.fetch_source("https://app.example.org/", CrawlConfig())
    assert html == SHELL
    assert "browser crashed" in metadata["browser_render_error"]


def test_fallback_off_never_starts_a_browser(monkeypatch) -> None:
    calls = _patch(monkeypatch, available=True, browser_html=RENDERED)
    html, _ = fetchers.fetch_source("https://app.example.org/", CrawlConfig(browser_fallback=False))
    assert calls == []
    assert html == SHELL


ARTICLE_RENDERED = (
    "<html><body><main><h1>Leadership</h1>"
    + "".join(f"<p>Paragraph {i} of the article the browser rendered.</p>" for i in range(30))
    + "</main></body></html>"
)


def test_utility_classes_do_not_count_as_text(monkeypatch) -> None:
    # stanford.edu: Tailwind classes such as "[&_.card>*:first-child]:h-full"
    # carry ">", so the text counter read the class names as page text and a
    # thin page was never rendered.
    cls = " ".join(f"[&_.card>*:nth-child({i})]:h-full" for i in range(40))
    http_html = (
        f'<html><body><script src="/app.js"></script><main><div class="{cls}">'
        f'<h1>Leadership</h1><p>One short lead sentence.</p><img class="{cls}" src="a.jpg">'
        "</div></main></body></html>"
    )
    calls = _patch(monkeypatch, available=True, browser_html=ARTICLE_RENDERED, http_html=http_html)
    html, metadata = fetchers.fetch_source("https://www.example.edu/", CrawlConfig())
    assert calls == ["https://www.example.edu/"]
    assert metadata["fallback_reason"] == "thin_extraction"


def test_empty_page_without_scripts_but_with_noscript_is_rendered(monkeypatch) -> None:
    # disqus.com: plain HTTP gets a script-less page whose only text is a
    # <noscript> "browser unsupported" notice; the browser gets the real page.
    http_html = (
        '<html><body><noscript id="unsupportedBrowser">Sorry, your browser is unsupported.'
        '</noscript><iframe src="https://p.example.net/px" style="display:none"></iframe>'
        "</body></html>"
    )
    calls = _patch(monkeypatch, available=True, browser_html=ARTICLE_RENDERED, http_html=http_html)
    html, metadata = fetchers.fetch_source("https://disqus.example.com/", CrawlConfig())
    assert calls == ["https://disqus.example.com/"]
    assert metadata["fallback_reason"] == "thin_extraction"


def test_short_static_page_without_scripts_is_not_rendered(monkeypatch) -> None:
    calls = _patch(
        monkeypatch,
        available=True,
        browser_html=ARTICLE_RENDERED,
        http_html="<html><body><p>Hello</p></body></html>",
    )
    fetchers.fetch_source("https://static.example.org/", CrawlConfig())
    assert calls == []


def test_short_static_page_with_a_noscript_fallback_is_not_rendered(monkeypatch) -> None:
    # Static themes carry <noscript> image/CSS fallbacks; only an (almost) empty
    # script-less page is worth a browser.
    http_html = (
        "<html><head><noscript><link rel='stylesheet' href='/no-js.css'></noscript></head>"
        "<body><main><h1>About us</h1><p>We are a small bakery in the old town, baking bread"
        " every morning since 1987 with flour from the valley.</p></main></body></html>"
    )
    calls = _patch(monkeypatch, available=True, browser_html=ARTICLE_RENDERED, http_html=http_html)
    fetchers.fetch_source("https://bakery.example.org/", CrawlConfig())
    assert calls == []
