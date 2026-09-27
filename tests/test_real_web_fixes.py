"""Fixes from the 400-page real-web benchmark (benchmarks/web).

- a self-clearing interstitial ("Just a moment...") is waited out in the local
  browser, up to ``browser_challenge_wait_ms``; nothing is solved or evaded;
- a network that never goes idle no longer throws away a loaded page;
- a browser fallback answered with an error page (a CDN's 403) keeps the
  honest HTTP error instead of returning the error page as content;
- a page whose HTML extracts to almost nothing is rendered in the browser;
- a slow HTTP server is handed to the browser instead of retried at length.

The real-browser tests run against a local server and are skipped when
Chromium is not installed.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

from agentcrawl import AgentCrawl, fetchers
from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError
from agentcrawl.parsing import html_to_markdown

ARTICLE = (
    "<!doctype html><html><head><title>Real page</title></head><body>"
    "<nav><a href='/'>Home</a></nav><main><h1>Harbour news</h1>"
    + "".join(
        f"<p>Paragraph {i} of the real article about the harbour and its ferries.</p>"
        for i in range(12)
    )
    + "</main></body></html>"
)

MOMENT = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>Checking your browser before accessing the site.</h1>
<script>setTimeout(function () { location.replace('/real'); }, 1200);</script>
</body></html>"""

STUCK = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<h1>Checking your browser before accessing the site.</h1>
<script>/* never clears */</script></body></html>"""

BUSY = ARTICLE.replace(
    "</body>", "<script>setInterval(function(){fetch('/ping?'+Date.now())},150)</script></body>"
)

DENIED = """<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>
You don't have permission to access "http://www.example.com/" on this server.<p>
Reference #18.5c2d1402.1790000000.1a2b3c4d</p></body></html>"""

# The HTML has a menu and a footer but the article only arrives by script.
THIN = (
    """<!doctype html><html><head><title>News</title></head><body>
<nav>"""
    + " ".join(f'<a href="/s{i}">Section number {i}</a>' for i in range(30))
    + """</nav>
<div id="app"></div>
<footer><p>Contact</p><p>Privacy</p></footer>
<script>
document.getElementById('app').innerHTML = '<main><h1>Front page</h1>' +
  Array.from({length: 12}, (_, i) =>
    '<p>Story ' + i + ' rendered by the page script with enough words to count.</p>'
  ).join('') + '</main>';
</script></body></html>"""
)


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {
        "/real": (200, ARTICLE),
        "/moment": (403, MOMENT),
        "/stuck": (403, STUCK),
        "/busy": (200, BUSY),
        "/denied": (403, DENIED),
        "/thin": (200, THIN),
        "/ping": (204, ""),
    }

    def do_GET(self):  # noqa: N802 - http.server API
        status, body = self.routes.get(self.path.split("?")[0], (404, "not found"))
        data = body.encode("utf-8")
        self.send_response(status)
        if data:
            self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _chromium_launches() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


browser = pytest.mark.skipif(not _chromium_launches(), reason="Chromium is not installed")


def _crawler(**extra) -> AgentCrawl:
    return AgentCrawl(
        {
            "allow_private_network": True,
            "respect_robots_txt": False,
            "timeout_ms": 15_000,
            "http_retries": 0,
            **extra,
        }
    )


@browser
def test_self_clearing_interstitial_is_waited_out(site) -> None:
    doc = _crawler().scrape(f"{site}/moment")
    assert doc.ok, doc.errors
    assert "Paragraph 11 of the real article" in doc.markdown
    assert doc.metadata["fallback_from"] == "http"
    assert doc.metadata["challenge_waited_ms"] >= 1000
    assert doc.metadata["browser_status"] == 200


@browser
def test_interstitial_that_never_clears_stays_a_challenge(site) -> None:
    started = time.monotonic()
    doc = _crawler(browser_challenge_wait_ms=2_000).scrape(f"{site}/stuck")
    assert not doc.ok
    assert doc.metadata["error_type"] == "client_challenge"
    # One browser run with one bounded wait, not a second browser retry.
    assert time.monotonic() - started < 12


@browser
def test_busy_network_keeps_the_rendered_page(site) -> None:
    doc = _crawler(fetcher="playwright", network_idle_ms=1_500).scrape(f"{site}/busy")
    assert doc.ok, doc.errors
    assert "Paragraph 11 of the real article" in doc.markdown
    assert doc.metadata["network_idle_timeout"] is True


@browser
def test_cdn_error_page_in_the_browser_is_not_content(site) -> None:
    doc = _crawler().scrape(f"{site}/denied")
    assert not doc.ok
    assert doc.metadata["error_type"] == "blocked"
    assert doc.metadata["browser_fallback_error"] == "browser got HTTP 403"
    assert doc.markdown == ""


@browser
def test_thin_extraction_is_rendered(site) -> None:
    doc = _crawler().scrape(f"{site}/thin")
    assert doc.ok, doc.errors
    assert "Story 11 rendered by the page script" in doc.markdown
    assert doc.metadata["fallback_reason"] == "thin_extraction"


# -- without a browser ------------------------------------------------------


def _fake_browser(monkeypatch, html: str = ARTICLE) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(fetchers, "_browser_backend_available", lambda backend: True)

    def fake(source, config, **kwargs):
        calls.append(source)
        return html

    monkeypatch.setattr(fetchers, "_fetch_browser", fake)
    return calls


def test_timeout_goes_to_the_browser_without_http_retries(monkeypatch) -> None:
    calls = _fake_browser(monkeypatch)
    attempts: list[float] = []

    def slow(request, *, timeout, **kwargs):
        attempts.append(timeout)
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(fetchers, "_safe_urlopen", slow)
    html, metadata = fetchers.fetch_source(
        "https://slow.example.org/", CrawlConfig(http_retries=2, http_retry_delay=0)
    )
    assert html == ARTICLE
    assert metadata["fallback_from"] == "http"
    assert calls == ["https://slow.example.org/"]
    assert attempts == [15.0]  # http_timeout_ms, one attempt


def test_full_timeout_and_retries_without_a_browser(monkeypatch) -> None:
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(fetchers, "_browser_backend_available", lambda backend: False)
    attempts: list[float] = []

    def slow(request, *, timeout, **kwargs):
        attempts.append(timeout)
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(fetchers, "_safe_urlopen", slow)
    with pytest.raises(FetchError):
        fetchers.fetch_source(
            "https://slow.example.org/", CrawlConfig(http_retries=2, http_retry_delay=0)
        )
    assert attempts == [30.0, 30.0, 30.0]


def test_thin_page_keeps_http_when_rendering_adds_nothing(monkeypatch) -> None:
    calls = _fake_browser(monkeypatch, html=THIN)
    monkeypatch.setattr(fetchers, "_fetch_http", lambda source, config: (THIN, {"fetcher": "http"}))
    html, metadata = fetchers.fetch_source("https://news.example.org/", CrawlConfig())
    assert calls == ["https://news.example.org/"]
    assert html == THIN
    assert metadata["fetcher"] == "http"
    assert metadata["browser_render_no_gain"] is True


def test_long_page_is_not_rendered(monkeypatch) -> None:
    calls = _fake_browser(monkeypatch)
    page = ARTICLE.replace("</body>", "<script>1</script></body>")
    monkeypatch.setattr(fetchers, "_fetch_http", lambda source, config: (page, {"fetcher": "http"}))
    html, metadata = fetchers.fetch_source("https://news.example.org/", CrawlConfig())
    assert calls == []
    assert html == page


# -- extraction -------------------------------------------------------------

_PARAGRAPHS = "".join(
    f"<p>Chapter {i} of the series was released today with new translated pages.</p>"
    for i in range(10)
)


def test_body_classes_never_make_the_page_boilerplate() -> None:
    # WordPress puts page-state words on <body>; "sticky" and "sidebar" used
    # to drop the whole page.
    html = (
        '<html><body class="home page-template sticky-header has-sidebar">'
        f'<div class="site-content"><h1>Latest releases</h1>{_PARAGRAPHS}</div>'
        '<div class="c-sidebar"><p>Popular this week</p></div></body></html>'
    )
    markdown = html_to_markdown(html, CrawlConfig())
    assert "Chapter 9 of the series" in markdown
    assert "Popular this week" not in markdown


def test_utility_css_layout_tokens_are_not_boilerplate() -> None:
    html = (
        "<html><body><main>"
        "<div class='grid [grid-template-areas:\"main_sidebar\"] md:rail'>"
        f"<h1>Today</h1>{_PARAGRAPHS}</div></main></body></html>"
    )
    assert "Chapter 9 of the series" in html_to_markdown(html, CrawlConfig())


def test_a_form_wrapping_the_page_is_the_page() -> None:
    # ASP.NET WebForms wrap the whole page in <form id="aspnetForm">.
    html = (
        '<html><body><form id="aspnetForm"><div class="container">'
        f"<h1>Network tools</h1>{_PARAGRAPHS}</div>"
        '<form class="search"><input name="q"><p>Search the site</p></form>'
        "</form></body></html>"
    )
    markdown = html_to_markdown(html, CrawlConfig())
    assert "Chapter 9 of the series" in markdown
    assert "Search the site" not in markdown


def test_queued_browser_fetch_waits_for_a_whole_browser_run(monkeypatch) -> None:
    pytest.importorskip("playwright")
    waits: list[float] = []

    class Busy:
        def acquire(self, timeout):
            waits.append(timeout)
            return False

    monkeypatch.setattr(fetchers, "_get_browser_semaphore", lambda: Busy())
    with pytest.raises(FetchError, match="waited 55 s"):
        fetchers._fetch_playwright("https://example.org/", CrawlConfig())
    assert waits == [55.0]  # 30 s load + 15 s interstitial + 10 s network idle


@browser
def test_failed_guarded_navigation_says_why(monkeypatch) -> None:
    # Chromium only reports net::ERR_FAILED when the guard's request fails.
    config = CrawlConfig(fetcher="playwright", timeout_ms=5_000)
    from agentcrawl import browser_guard

    for module in (fetchers, browser_guard):
        monkeypatch.setattr(module, "validate_remote_url", lambda url, **kwargs: None)
    with pytest.raises(FetchError, match="ECONNREFUSED"):
        fetchers._fetch_playwright("http://127.0.0.1:9/", config)


def test_collapsed_faq_panels_are_kept_hidden_menus_are_not() -> None:
    html = (
        f"<html><body><main><h1>Plans</h1>{_PARAGRAPHS}"
        '<div class="accordion-tray" aria-hidden="true"><p>Your storage plan renews '
        "every month and can be cancelled at any time from the settings.</p></div>"
        '<div class="menu" aria-hidden="true"><a href="/a">Account settings</a>'
        '<a href="/b">Billing history</a></div></main></body></html>'
    )
    markdown = html_to_markdown(html, CrawlConfig())
    assert "can be cancelled at any time" in markdown
    assert "Billing history" not in markdown


def test_link_light_banner_is_content_link_heavy_sidebar_is_not() -> None:
    html = (
        f"<html><body><main><h1>Home</h1>{_PARAGRAPHS}"
        '<section class="hero-banner"><p>Fast, private file sharing for teams that '
        "work across time zones.</p></section>"
        '<div class="sidebar">'
        + "".join(f'<a href="/t{i}">Trending topic number {i}</a>' for i in range(8))
        + "</div></main></body></html>"
    )
    markdown = html_to_markdown(html, CrawlConfig())
    assert "file sharing for teams" in markdown
    assert "Trending topic" not in markdown


def test_homepage_sections_are_not_cut_to_one_block() -> None:
    blocks = "".join(
        f"<section class='band'><h2>Service {i}</h2><p>Service {i} helps small shops "
        "sell online with payments, shipping and stock in one place.</p></section>"
        for i in range(10)
    )
    # A link-heavy menu makes the first block outscore <body>.
    menu = "".join(f"<a href='/{i}'>{i}</a>" for i in range(150))
    html = (
        f"<html><body><nav>{menu}</nav>"
        f"<div class='content'><h2>Welcome</h2>{_PARAGRAPHS}</div>{blocks}"
        "<footer><p>Copyright</p></footer></body></html>"
    )
    markdown = html_to_markdown(html, CrawlConfig())
    assert "Chapter 9 of the series" in markdown
    assert "Service 9 helps small shops" in markdown
    assert "Copyright" not in markdown
