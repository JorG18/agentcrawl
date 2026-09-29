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


AWS_WAF = """<!DOCTYPE html><html lang="en"><head><title></title>
<script>window.awsWafCookieDomainList = [];</script>
<script src="https://abc.us-east-1.token.awswaf.com/abc/def/challenge.js"></script>
</head><body><div id="challenge-container"></div>
<script>AwsWafIntegration.getToken().then(() => window.location.reload(true));</script>
<noscript><h1>JavaScript is disabled</h1>
In order to continue, we need to verify that you're not a robot.
This requires JavaScript. Enable JavaScript and then reload the page.</noscript>
</body></html>"""


def test_waf_that_blanks_the_browser_is_a_challenge_not_an_empty_page(monkeypatch) -> None:
    # barchart.com: the AWS WAF shell renders to <body></body> in the browser.
    calls = _fake_browser(monkeypatch, html="<html><head></head><body></body></html>")
    monkeypatch.setattr(
        fetchers, "_fetch_http", lambda source, config: (AWS_WAF, {"fetcher": "http"})
    )
    document = AgentCrawl().scrape("https://waf.example.org/", formats=["markdown"])
    assert calls == ["https://waf.example.org/"]  # rendered once, no second browser run
    assert document["metadata"]["error_type"] == "client_challenge"
    assert "vendor: aws waf" in document["metadata"]["challenge_signals"]


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
        def run(self, launch_kwargs, job, wait_s):
            waits.append(wait_s)
            raise FetchError(f"waited {wait_s:.0f} s for a free browser")

    monkeypatch.setattr(fetchers, "_get_browser_pool", lambda: Busy())
    with pytest.raises(FetchError, match="waited 55 s"):
        fetchers._fetch_playwright("https://example.org/", CrawlConfig())
    assert waits == [55.0]  # 30 s load + 15 s interstitial + 10 s network idle


@browser
def test_failed_guarded_navigation_says_why(monkeypatch) -> None:
    # Chromium only reports net::ERR_FAILED when the guard's request fails.
    config = CrawlConfig(fetcher="playwright", timeout_ms=5_000, browser_strict_network=True)
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


# -- a server that forgets its intermediate certificate -----------------------


def _make_chain(tmp, aia_url: str) -> dict[str, str]:
    import subprocess

    def run(*args):
        subprocess.run(["openssl", *args], check=True, capture_output=True, cwd=tmp)

    (tmp / "ca.ext").write_text("basicConstraints=critical,CA:TRUE\nkeyUsage=keyCertSign,cRLSign\n")
    (tmp / "leaf.ext").write_text(
        f"subjectAltName=DNS:localhost\nauthorityInfoAccess=caIssuers;URI:{aia_url}\n"
    )
    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "root.key",
        "-out",
        "root.pem",
        "-days",
        "2",
        "-subj",
        "/CN=Test Root",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=keyCertSign,cRLSign",
    )
    for name, subject, issuer, ext in (
        ("mid", "/CN=Test Intermediate", "root", "ca.ext"),
        ("leaf", "/CN=localhost", "mid", "leaf.ext"),
    ):
        run(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
            "-subj",
            subject,
        )
        run(
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            f"{issuer}.pem",
            "-CAkey",
            f"{issuer}.key",
            "-CAcreateserial",
            "-out",
            f"{name}.pem",
            "-days",
            "2",
            "-extfile",
            ext,
        )
    run("x509", "-in", "mid.pem", "-outform", "DER", "-out", "mid.der")
    return {
        k: str(tmp / v)
        for k, v in {
            "root": "root.pem",
            "leaf": "leaf.pem",
            "key": "leaf.key",
            "mid": "mid.der",
        }.items()
    }


def test_missing_intermediate_is_fetched_and_still_verified(tmp_path, monkeypatch) -> None:
    import shutil
    import ssl

    from agentcrawl import security

    if not shutil.which("openssl"):
        pytest.skip("openssl CLI not installed")

    class Issuer(http.server.BaseHTTPRequestHandler):
        body = b""

        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("content-length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)

        def log_message(self, *_args):
            pass

    issuer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Issuer)
    chain = _make_chain(tmp_path, f"http://127.0.0.1:{issuer.server_address[1]}/mid.der")
    Issuer.body = open(chain["mid"], "rb").read()

    class Page(_Handler):
        routes = {"/": (200, ARTICLE)}

    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    served = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    served.load_cert_chain(chain["leaf"], chain["key"])  # leaf only, no intermediate
    site.socket = served.wrap_socket(site.socket, server_side=True)
    for server in (issuer, site):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    # Loopback stands in for a public host; the test root stands in for the
    # system trust store.
    monkeypatch.setattr(
        security,
        "resolve_public_addresses",
        lambda host, port: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
    )
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(
        ssl,
        "_create_default_https_context",
        lambda: ssl.create_default_context(cafile=chain["root"]),
    )
    monkeypatch.setattr(security, "_intermediates", {})
    url = f"https://localhost:{site.server_address[1]}/"
    try:
        html, metadata = fetchers.fetch_source(url, CrawlConfig(browser_fallback=False))
        assert "Paragraph 11 of the real article" in html

        # An intermediate that does not lead to a trusted root is not trusted.
        monkeypatch.setattr(security, "_intermediates", {})
        monkeypatch.setattr(ssl, "_create_default_https_context", ssl.create_default_context)
        with pytest.raises(FetchError, match="CERTIFICATE_VERIFY_FAILED"):
            fetchers.fetch_source(url, CrawlConfig(browser_fallback=False, http_retries=0))
    finally:
        issuer.shutdown()
        site.shutdown()


def test_bad_certificate_redirect_is_followed_with_verification(monkeypatch) -> None:
    """gamepass.com serves another name's certificate and only redirects; the
    redirect target is fetched (and verified) on its own, never the body."""
    from agentcrawl import security

    fetched = []

    def fake_http(url, config):
        fetched.append(url)
        if url == "https://bare.example/":
            raise FetchError("certificate verify failed: Hostname mismatch", error_type="tls_error")
        return "<html><body><main><h1>Real</h1><p>Page</p></main></body></html>", {
            "fetcher": "http",
            "final_url": url,
        }

    monkeypatch.setattr(fetchers, "_fetch_http", fake_http)
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)
    monkeypatch.setattr(
        security, "redirect_past_invalid_certificate", lambda url, timeout: "https://www.example/"
    )
    config = CrawlConfig(browser_fallback=False)
    _content, metadata = fetchers._fetch_source("https://bare.example/", config)
    assert fetched == ["https://bare.example/", "https://www.example/"]
    assert metadata["redirected_past_invalid_certificate"] == "https://bare.example/"

    fetched.clear()
    with pytest.raises(FetchError):  # airgap never leaves the named host this way
        fetchers._fetch_source("https://bare.example/", CrawlConfig(airgap=True))
    assert fetched == ["https://bare.example/"]


def test_one_budget_covers_every_step_of_a_page(monkeypatch) -> None:
    """HTTP timeout, browser navigation, interstitial and network idle each
    had their own limit and together took over a minute."""
    seen = []

    def slow_http(url, config):
        seen.append(fetchers._http_timeout_seconds(config))
        time.sleep(0.45)
        raise FetchError("HTTP fetch failed: The read operation timed out")

    def fake_browser(url, config, **kwargs):
        seen.append(fetchers._budget_ms(config.timeout_ms))
        fetchers._require_budget(config.timeout_ms, "the page loaded")
        return "<html></html>"

    monkeypatch.setattr(fetchers, "_fetch_http", slow_http)
    monkeypatch.setattr(fetchers, "_fetch_browser", fake_browser)
    monkeypatch.setattr(fetchers, "_browser_backend_available", lambda backend: True)
    monkeypatch.setattr(fetchers, "validate_remote_url", lambda url, **kwargs: None)

    config = CrawlConfig(page_budget_ms=400)
    with pytest.raises(FetchError) as info:
        fetchers.fetch_source("https://example.org/", config)
    assert seen[0] <= 0.4  # the HTTP timeout is capped by the budget
    assert seen[1] == 0  # nothing left for the browser: no second minute
    assert "timed out" in str(info.value.__cause__ or info.value)


def test_a_retry_of_the_same_page_shares_its_budget() -> None:
    """The browser retry after a challenge started a fresh 45 s budget, so one
    page took over two minutes."""
    with fetchers.page_deadline(CrawlConfig(page_budget_ms=1_000)):
        with fetchers.page_deadline(CrawlConfig(page_budget_ms=60_000)):
            assert fetchers._budget_ms(60_000) <= 1_000
    assert fetchers._budget_ms(60_000) == 60_000  # no page running: no cap
