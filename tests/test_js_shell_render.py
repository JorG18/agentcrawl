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


def _patch(monkeypatch, *, available: bool, browser_html: str | None) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(
        fetchers,
        "_fetch_http",
        lambda source, config: (SHELL, {"fetcher": "http", "final_url": source}),
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
