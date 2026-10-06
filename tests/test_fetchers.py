from __future__ import annotations

import codecs
import json
import sys
import types
import urllib.error

import pytest

from agentcrawl import fetchers

from agentcrawl import AgentCrawl
from agentcrawl.airgap import AirgapViolation
from agentcrawl.config import DEFAULT_USER_AGENT, CrawlConfig
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import (
    _browser_backend_available,
    _decode_http_body,
    _fetch_camofox,
    _fetch_http,
    _fetch_playwright,
    _read_bounded,
    _response_charset,
    _retry_delay,
    fetch_source,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _rate_headers():
    import email.message

    headers = email.message.Message()
    headers["Retry-After"] = "30"
    return headers


class _FakeHttpResp:
    def __init__(self, body=b"<html>ok</html>"):
        self._body = body
        self._read = False
        import email.message

        self.headers = email.message.Message()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "https://example.com/a"

    def read1(self, n=-1):
        if self._read:
            return b""
        self._read = True
        return self._body

    def read(self, n=-1):
        return self.read1(n)

    def close(self):
        pass


def test_decode_http_body_honors_declared_charset() -> None:
    body = "caf\u00e9 \u00f1and\u00fa".encode("iso-8859-1")
    assert _decode_http_body(body, "iso-8859-1") == "caf\u00e9 \u00f1and\u00fa"


def test_decode_http_body_rejects_lying_utf8_declaration() -> None:
    body = "caf\u00e9".encode("iso-8859-1")  # 0xE9 is invalid UTF-8
    assert _decode_http_body(body, "utf-8") == "caf\ufffd"  # replace, no crash


def test_decode_http_body_bom_overrides_charset_declaration() -> None:
    body = codecs.BOM_UTF16_LE + "caf\u00e9".encode("utf-16-le")
    # Declared charset lies (or is absent); the BOM wins.
    assert _decode_http_body(body, "iso-8859-1") == "caf\u00e9"


def test_decode_http_body_falls_back_to_latin1_for_undecodable_bytes() -> None:
    body = b"caf\xe9\xff\xfe"
    assert _decode_http_body(body, None) == "caf\u00e9\u00ff\u00fe"


def test_response_charset_reads_content_type_header() -> None:
    import email.message

    headers = email.message.Message()
    headers["Content-Type"] = "text/html; charset=windows-1252"
    assert _response_charset(headers) == "windows-1252"


def test_response_charset_survives_plain_dict_headers() -> None:
    # Some callers/tests pass a plain dict; get_content_charset is absent.
    assert _response_charset({"content-type": "text/html"}) is None


def test_retry_delay_clamps_hostile_retry_after_values() -> None:
    config = CrawlConfig(http_retry_delay=1.0)
    assert _retry_delay(config, 0, "999999") == 30.0
    assert _retry_delay(config, 0, "-5") == 0.0
    assert _retry_delay(config, 0, "3.5") == 3.5
    assert _retry_delay(config, 0, "not-a-number") == config.http_retry_delay


def test_long_retry_after_is_cut_by_the_page_budget(monkeypatch) -> None:
    """A server's Retry-After is clamped to 30 s, but a page budget of under
    a second must not sit out the whole wait: the retry loop gives up early
    and the page budget, not Retry-After, bounds the worker."""
    import time as _time_module

    sleeps: list[float] = []

    def fake_urlopen(request, timeout, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 429, "slow down", _rate_headers(), None)

    monkeypatch.setattr(fetchers, "_safe_urlopen", fake_urlopen)
    monkeypatch.setattr(fetchers.time, "sleep", lambda s: sleeps.append(s))
    config = CrawlConfig(
        http_retries=3, http_retry_delay=0.0, page_budget_ms=500, browser_fallback=False
    )
    started = _time_module.monotonic()
    with fetchers.page_deadline(config):
        with pytest.raises(FetchError) as excinfo:
            _fetch_http("https://example.com/a", config)
    elapsed = _time_module.monotonic() - started
    assert sleeps == []  # the budget can't cover the wait: no sleep at all
    assert elapsed < 1.0  # cut early, not the full 30 s wait
    assert excinfo.value.status_code == 429


def test_long_retry_after_waits_fully_without_a_deadline(monkeypatch) -> None:
    """No page deadline running (plain library use): the clamped Retry-After
    wait happens as before, then the retry succeeds."""
    attempts = {"n": 0}

    def fake_urlopen(request, timeout, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", _rate_headers(), None)
        return _FakeHttpResp()

    monkeypatch.setattr(fetchers, "_safe_urlopen", fake_urlopen)
    monkeypatch.setattr(fetchers.time, "sleep", lambda s: sleeps_record.append(s))
    sleeps_record: list[float] = []
    config = CrawlConfig(http_retries=2, http_retry_delay=0.0)
    html, metadata = _fetch_http("https://example.com/a", config)
    assert attempts["n"] == 2
    assert sleeps_record == [30.0]  # clamped Retry-After, fully waited
    assert metadata["status_code"] == 200


def test_read_bounded_cuts_a_whole_body_reader_at_the_limit() -> None:
    """A reader whose read(n) ignores n (test doubles, third-party adapters)
    used to have its whole oversized body materialized before the limit was
    checked; now the body past the limit is never held in memory."""

    class _WholeBodyOnly:
        """Accepts ``n`` and ignores it, returning the whole body forever."""

        def read(self, n=-1):
            return b"x" * 1000

    with pytest.raises(FetchError, match="exceeds the 10 byte limit"):
        _read_bounded(_WholeBodyOnly(), 10)

    class _NoAmt:
        """No ``amt`` parameter: one call, whole body, at exactly the limit."""

        def read(self):
            return b"y" * 10

    assert _read_bounded(_NoAmt(), 10) == b"y" * 10  # at the limit: fine
    with pytest.raises(FetchError, match="exceeds the 9 byte limit"):
        _read_bounded(_NoAmt(), 9)  # one byte over: cut before holding it


def test_read_bounded_drip_still_hits_its_read_deadline() -> None:
    """The slow-drip read deadline (classified as timeout) is untouched by
    the streaming change."""
    import time as _time_module

    class _Drip:
        def read1(self, n=-1):
            _time_module.sleep(0.05)
            return b"chunk"

    with pytest.raises(FetchError, match="took longer than"):
        _read_bounded(_Drip(), 1_000_000, deadline_seconds=0.2)


def test_interstitial_polls_never_overshoot_the_budget() -> None:
    """With an almost-spent budget the wait loop polls in the remaining-
    time slices instead of fixed 500 ms steps that overshoot the deadline."""

    class _Page:
        def __init__(self):
            self.waits: list[int] = []
            self.title_calls = 0

        def title(self):
            self.title_calls += 1
            return "Just a moment..."  # never clears

        def wait_for_timeout(self, ms):
            self.waits.append(ms)
            import time

            time.sleep(ms / 1000)

    page = _Page()
    waited = fetchers._wait_out_interstitial(page, 150)
    assert sum(page.waits) <= 150  # no poll past the deadline
    assert 100 <= waited <= 250  # about the budget, not the fixed 500 ms


def test_interstitial_clears_within_the_first_poll() -> None:
    """A page that clears immediately still gets exactly one short wait."""

    class _Page:
        def __init__(self):
            self.waits: list[int] = []

        def title(self):
            return "Just a moment..." if len(self.waits) == 0 else "The real page"

        def wait_for_timeout(self, ms):
            self.waits.append(ms)

        def wait_for_load_state(self, state, timeout):
            pass

    page = _Page()
    waited = fetchers._wait_out_interstitial(page, 15_000)
    assert page.waits == [500]  # full poll when the budget allows it
    assert waited >= 1


def test_camofox_fetch_creates_evaluates_and_closes_tab(monkeypatch) -> None:
    requests = []
    responses = iter(
        [
            {"tabId": "tab-1", "url": "https://example.com"},
            {"ok": True, "result": "<html><h1>Stealth page</h1></html>"},
            {"ok": True},
        ]
    )

    def fake_urlopen(request, timeout, **_kwargs):
        requests.append(request)
        return _Response(next(responses))

    monkeypatch.setattr(fetchers, "_safe_urlopen", fake_urlopen)
    config = CrawlConfig(
        fetcher="camofox",
        camofox_base_url="http://camofox.test:9377",
        camofox_access_key="secret",
        camofox_user_id="test-user",
    )

    html, metadata = fetch_source("https://example.com", config)

    assert "Stealth page" in html
    assert metadata == {
        "fetcher": "camofox",
        "final_url": "https://example.com",
        "dns_pinned": False,
    }
    assert [request.method for request in requests] == ["POST", "POST", "DELETE"]
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert requests[1].full_url.endswith("/tabs/tab-1/evaluate")
    assert requests[2].full_url.endswith("/tabs/tab-1?userId=test-user")


def test_camofox_closes_tab_after_evaluate_failure(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout, **_kwargs):
        requests.append(request)
        if len(requests) == 1:
            return _Response({"tabId": "tab-2"})
        if len(requests) == 2:
            raise urllib.error.HTTPError(request.full_url, 500, "failed", {}, None)
        return _Response({"ok": True})

    monkeypatch.setattr(fetchers, "_safe_urlopen", fake_urlopen)

    with pytest.raises(FetchError, match="Camofox HTTP 500"):
        _fetch_camofox("https://example.com", CrawlConfig(fetcher="camofox"))

    assert requests[-1].method == "DELETE"


def test_camofox_hung_service_is_cut_by_the_page_budget(monkeypatch) -> None:
    """A hung camofox service used to hold the worker until timeout_ms on its
    own; now the page budget cuts it first and the error is a timeout."""
    import time as _time_module

    def hanging_urlopen(request, timeout, **_kwargs):
        seen.append(timeout)
        _time_module.sleep(timeout + 0.1)  # outlast whatever timeout was granted
        raise TimeoutError("timed out")

    seen: list[float] = []
    monkeypatch.setattr(fetchers, "_safe_urlopen", hanging_urlopen)
    config = CrawlConfig(
        fetcher="camofox", camofox_base_url="http://camofox.test:9377", page_budget_ms=400
    )
    started = _time_module.monotonic()
    with fetchers.page_deadline(config):
        with pytest.raises(FetchError, match="timed out|Camofox") as excinfo:
            _fetch_camofox("https://example.com", config)
    elapsed = _time_module.monotonic() - started
    assert "timeout" in str(excinfo.value) or "Camofox" in str(excinfo.value)
    assert elapsed < 2.0  # cut by the budget, not hung for timeout_ms (default 30 s)
    assert seen[0] <= 0.4  # the socket timeout is the budget remainder, not timeout_ms


def test_dns_pinned_metadata_is_true_on_each_fetch_path(monkeypatch, tmp_path) -> None:
    """The metadata says the truth per path: pinned only for the plain-HTTP
    fetch of a URL; browser paths resolve DNS themselves; camofox is operator
    infrastructure reached the way a proxy is."""
    monkeypatch.setattr(
        fetchers,
        "_fetch_http",
        lambda url, config: ("<html>ok</html>", {"fetcher": "http", "final_url": url}),
    )
    monkeypatch.setattr(fetchers, "_fetch_camofox", lambda url, config: "<html>camofox</html>")
    _html, metadata = fetch_source("https://example.com", CrawlConfig(fetcher="http"))
    assert metadata["dns_pinned"] is True
    _html, metadata = fetch_source(
        "https://example.com", CrawlConfig(fetcher="http", allow_private_network=True)
    )
    assert metadata["dns_pinned"] is False
    page = tmp_path / "doc.md"
    page.write_text("# doc", encoding="utf-8")
    _html, metadata = fetch_source(str(page), CrawlConfig(fetcher="http"))
    assert "dns_pinned" not in metadata  # a local file touched no DNS: absent, not False
    _html, metadata = fetch_source("https://example.com", CrawlConfig(fetcher="camofox"))
    assert metadata["dns_pinned"] is False
    assert metadata["fetcher"] == "camofox"


def test_camofox_normal_flow_still_works(monkeypatch) -> None:
    """The safe-urlopen swap keeps the happy path (and the DELETE cleanup)
    exactly as before."""
    requests = []
    responses = iter(
        [
            {"tabId": "tab-9"},
            {"ok": True, "result": "<html><h1>Camofox page</h1></html>"},
            {"ok": True},
        ]
    )

    def fake_urlopen(request, timeout, **_kwargs):
        requests.append(request)
        return _Response(next(responses))

    monkeypatch.setattr(fetchers, "_safe_urlopen", fake_urlopen)
    config = CrawlConfig(fetcher="camofox", camofox_base_url="http://camofox.test:9377")
    html, metadata = fetch_source("https://example.com", config)
    assert "Camofox page" in html
    assert metadata["fetcher"] == "camofox"
    assert [request.method for request in requests] == ["POST", "POST", "DELETE"]


def test_http_block_falls_back_to_camofox(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_http",
        lambda url, config: (_ for _ in ()).throw(FetchError("HTTP 403 for example.com")),
    )
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_camofox",
        lambda url, config: "<html><h1>Rendered</h1></html>",
    )
    config = CrawlConfig(
        fetcher="http",
        browser_backend="camofox",
        browser_fallback=True,
        browser_fallback_statuses=(403,),
    )

    html, metadata = fetch_source("https://example.com", config)

    assert "Rendered" in html
    assert metadata == {
        "fetcher": "camofox",
        "fallback_from": "http",
        "final_url": "https://example.com",
        "dns_pinned": False,
    }


class FakePage:
    def __init__(self):
        self.url = "https://example.com/"
        self.goto_calls = []
        self.load_states = []
        self.add_init_script_calls = []
        self.route_calls = []
        self.wait_for_selectors = []
        self.wait_for_timeouts = []

    def goto(self, url, wait_until, timeout):
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})

    def wait_for_load_state(self, state, timeout):
        self.load_states.append({"state": state, "timeout": timeout})

    def add_init_script(self, script):
        self.add_init_script_calls.append(script)

    def route(self, pattern, handler):
        self.route_calls.append({"pattern": pattern, "handler": handler})

    def wait_for_selector(self, selector, timeout):
        self.wait_for_selectors.append({"selector": selector, "timeout": timeout})

    def wait_for_timeout(self, timeout):
        self.wait_for_timeouts.append(timeout)

    def content(self):
        return "<html><body>ok</body></html>"


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = 0
        self.route_calls = []
        self.websocket_routes = []

    def route(self, pattern, handler):
        self.route_calls.append({"pattern": pattern, "handler": handler})

    def route_web_socket(self, pattern, handler):
        self.websocket_routes.append({"pattern": pattern, "handler": handler})

    def new_page(self):
        return self.page

    def close(self):
        self.closed += 1


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.context_kwargs = None
        self.context = None
        self.closed = 0

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        self.context = FakeContext(self.page)
        return self.context

    def close(self):
        self.closed += 1


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = None

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def start(self):
        return self

    def stop(self):
        pass


def install_fake_playwright(monkeypatch):
    page = FakePage()
    browser = FakeBrowser(page)
    chromium = FakeChromium(browser)
    fake_module = types.SimpleNamespace(sync_playwright=lambda: FakePlaywright(chromium))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    return page, browser, chromium


def test_playwright_uses_default_user_agent_when_config_none(monkeypatch) -> None:
    page, browser, _chromium = install_fake_playwright(monkeypatch)

    html = _fetch_playwright(
        "https://example.com/", CrawlConfig(user_agent=None, network_idle=False)
    )

    assert "ok" in html
    # The default config guards the browser's network (SSRF), and service
    # workers are blocked because they can issue requests the route never sees.
    assert browser.context_kwargs == {
        "user_agent": DEFAULT_USER_AGENT,
        "locale": "en-US",
        "service_workers": "block",
    }
    assert page.load_states == []


def test_playwright_presents_the_real_chrome_version(monkeypatch) -> None:
    """The bot user agent got the browser fallback the same Cloudflare
    challenge / CDN 403 the HTTP fetch got; a caller's own UA still wins."""
    _page, browser, chromium = install_fake_playwright(monkeypatch)
    browser.version = "147.0.7727.15"

    _fetch_playwright("https://example.com/", CrawlConfig(network_idle=False))

    agent = browser.context_kwargs["user_agent"]
    assert "Chrome/147.0.0.0 Safari/537.36" in agent
    assert "Headless" not in agent and "AgentCrawl" not in agent
    assert "--disable-blink-features=AutomationControlled" in chromium.launch_kwargs["args"]
    # Not chromium-headless-shell, whose client hints say "HeadlessChrome".
    assert chromium.launch_kwargs["channel"] == "chromium"

    _fetch_playwright("https://example.com/", CrawlConfig(user_agent="Mine/1", network_idle=False))
    assert browser.context_kwargs["user_agent"] == "Mine/1"


def test_playwright_honors_network_idle_option(monkeypatch) -> None:
    page, _browser, _chromium = install_fake_playwright(monkeypatch)

    _fetch_playwright("https://example.com/", CrawlConfig(network_idle=True, timeout_ms=1234))

    assert page.goto_calls == [
        {"url": "https://example.com/", "wait_until": "domcontentloaded", "timeout": 1234}
    ]
    assert page.load_states == [{"state": "networkidle", "timeout": 1234}]


def test_playwright_applies_browser_workflow_options(monkeypatch) -> None:
    page, _browser, _chromium = install_fake_playwright(monkeypatch)
    config = CrawlConfig(
        browser_wait_for_selector="main.app-content",
        browser_wait_ms=250,
        browser_block_resources=("image", "font"),
        browser_init_script="window.__agentcrawl = true;",
        network_idle=False,
    )

    _fetch_playwright("https://example.com/", config)

    assert page.add_init_script_calls == ["window.__agentcrawl = true;"]
    # One context-level route carries both resource blocking and the guard.
    context = _browser.context
    assert [call["pattern"] for call in context.route_calls] == ["**/*"]
    assert page.wait_for_selectors == [{"selector": "main.app-content", "timeout": 30000}]
    assert page.wait_for_timeouts == [250]


def test_playwright_closes_the_context_it_opened(monkeypatch) -> None:
    """The context was never closed and the browser was closed twice (the
    second close happened after the Playwright session had already stopped,
    so it failed silently and leaked browser processes on error paths)."""
    _page, browser, _chromium = install_fake_playwright(monkeypatch)

    _fetch_playwright("https://example.com/", CrawlConfig(network_idle=False))

    assert browser.context is not None
    assert browser.context.closed == 1
    assert browser.closed == 0  # kept open for the next page
    fetchers.shutdown_browser_pool()
    assert browser.closed == 1


def test_playwright_closes_resources_when_the_page_fails(monkeypatch) -> None:
    page, browser, _chromium = install_fake_playwright(monkeypatch)

    def failing_goto(url, wait_until, timeout):
        raise RuntimeError("navigation interrupted")

    monkeypatch.setattr(page, "goto", failing_goto)

    with pytest.raises(FetchError):
        _fetch_playwright("https://example.com/", CrawlConfig(network_idle=False))

    assert browser.context.closed == 1
    fetchers.shutdown_browser_pool()
    assert browser.closed == 1


def test_browser_backend_availability_tracks_the_optional_dependency(monkeypatch) -> None:
    from importlib.util import find_spec as real_find_spec

    def fake_find_spec(name):
        if name == "playwright":
            return None
        return real_find_spec(name)

    monkeypatch.setattr("agentcrawl.fetchers.importlib.util.find_spec", fake_find_spec)

    assert _browser_backend_available("playwright") is False
    # Camofox is a local service reached over HTTP: availability is decided
    # per request, not by an installed package.
    assert _browser_backend_available("camofox") is True
    assert _browser_backend_available("unknown-backend") is False


def test_default_config_keeps_the_honest_error_without_the_browser_extra(monkeypatch) -> None:
    """Regression (2026-09 audit): with the default ``browser_fallback=True``
    and no ``[browser]`` extra installed, every 403 reported
    "Playwright is not installed" as ``browser_error`` instead of the honest
    ``blocked`` — and ``browser_error`` is a retryable class, so crawls spent
    their retry budget on pages that were never going to work."""
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_http",
        lambda url, config: (_ for _ in ()).throw(
            FetchError(f"HTTP fetch failed for {url}: HTTP Error 403: Forbidden")
        ),
    )
    monkeypatch.setattr("agentcrawl.fetchers._browser_backend_available", lambda backend: False)

    document = AgentCrawl({"fetcher": "http"}).scrape("https://example.com/blocked")

    assert document.ok is False
    assert document.metadata["error_type"] == "blocked"
    assert "Playwright is not installed" not in document.errors[0]


def test_browser_fallback_failure_keeps_the_original_http_error(monkeypatch) -> None:
    """When the fallback itself fails, the reported error must stay the real
    HTTP one; the fallback reason is attached for diagnostics."""
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_http",
        lambda url, config: (_ for _ in ()).throw(
            FetchError(f"HTTP fetch failed for {url}: HTTP Error 403: Forbidden")
        ),
    )
    monkeypatch.setattr("agentcrawl.fetchers._browser_backend_available", lambda backend: True)
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_browser",
        lambda url, config, backend=None: (_ for _ in ()).throw(
            FetchError("Playwright fetch failed: browser crashed")
        ),
    )

    document = AgentCrawl({"fetcher": "http"}).scrape("https://example.com/blocked")

    assert document.metadata["error_type"] == "blocked"
    assert "HTTP Error 403" in document.errors[0]
    assert "browser crashed" in document.metadata["browser_fallback_error"]


def test_policy_denials_are_not_retried(monkeypatch) -> None:
    """Regression (2026-09 audit): ``validate_remote_url`` and the airgap
    handler refuse a request for reasons that cannot change between attempts.
    Retrying them only burned the backoff budget and then reported the denial
    wrapped as a generic transport error."""
    attempts: list[str] = []
    sleeps: list[float] = []

    def denying_open(request, **kwargs):
        attempts.append(request.full_url)
        raise AirgapViolation("airgap blocked request to https://tracker.example/")

    monkeypatch.setattr("agentcrawl.fetchers._safe_urlopen", denying_open)
    monkeypatch.setattr("agentcrawl.fetchers.time.sleep", sleeps.append)

    with pytest.raises(FetchError) as excinfo:
        _fetch_http("https://example.com/", CrawlConfig(http_retries=2, http_retry_delay=1.0))

    assert attempts == ["https://example.com/"]
    assert sleeps == []
    assert "airgap blocked request" in str(excinfo.value)


class _Ctx:
    """Wraps a fake response so ``with _safe_urlopen(...) as response`` works."""

    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *_args):
        return False


class _StreamingResponse:
    """Mimics ``http.client.HTTPResponse``: ``read(amt)`` advances the body."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + amt]
        self._offset += len(chunk)
        return chunk


def test_read_bounded_rejects_a_body_over_the_limit() -> None:
    """Regression (2026-09 audit, second pass): ``_fetch_http`` called
    ``response.read()`` with no ceiling. A server can omit Content-Length and
    stream forever, and extraction only keeps ``max_input_chars``, so an
    oversized or hostile page exhausted memory before the surplus was
    discarded (41 MB read / 79 MB peak for a 64 KB budget, measured)."""
    response = _StreamingResponse(b"a" * 4096)

    with pytest.raises(FetchError) as excinfo:
        _read_bounded(response, 1024, url="https://example.com/big")

    assert "exceeds the 1024 byte limit" in str(excinfo.value)
    assert "max_response_bytes" in str(excinfo.value)


def test_read_bounded_returns_the_body_in_full_when_within_the_limit() -> None:
    payload = b"<html><body>ok</body></html>"

    assert _read_bounded(_StreamingResponse(payload), 1024) == payload


def test_read_bounded_stops_after_one_call_for_readers_without_amt() -> None:
    """A reader that ignores ``amt`` (test doubles, some adapters) returns the
    whole body per call, so the helper must not loop forever — and must still
    enforce the cap."""
    calls: list[int] = []

    class WholeBodyOnly:
        def read(self):
            calls.append(1)
            return b"x" * 10

    assert _read_bounded(WholeBodyOnly(), 1000) == b"x" * 10
    assert len(calls) == 1

    with pytest.raises(FetchError):
        _read_bounded(WholeBodyOnly(), 5)


def test_fetch_http_refuses_an_oversized_body(monkeypatch) -> None:
    response = _StreamingResponse(b"z" * 5000)
    response.geturl = lambda: "https://example.com/big"  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "agentcrawl.fetchers._safe_urlopen", lambda request, **kwargs: _Ctx(response)
    )

    with pytest.raises(FetchError) as excinfo:
        _fetch_http(
            "https://example.com/big",
            CrawlConfig(http_retries=0, max_response_bytes=1000),
        )

    assert "exceeds the 1000 byte limit" in str(excinfo.value)


def test_transient_failures_are_still_retried(monkeypatch) -> None:
    """The retry loop must keep retrying genuine transport faults; only
    deterministic denials bypass it."""
    attempts: list[str] = []
    sleeps: list[float] = []

    def flaky_open(request, **kwargs):
        attempts.append(request.full_url)
        raise urllib.error.URLError("connection reset by peer")

    monkeypatch.setattr("agentcrawl.fetchers._safe_urlopen", flaky_open)
    monkeypatch.setattr("agentcrawl.fetchers.time.sleep", sleeps.append)

    with pytest.raises(FetchError):
        _fetch_http("https://example.com/", CrawlConfig(http_retries=2, http_retry_delay=0.5))

    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]


def test_certificate_failure_falls_back_to_the_verifying_browser(monkeypatch) -> None:
    """Sites with an incomplete certificate chain fail in Python but load in a
    browser, which fetches the missing intermediate (web-sample benchmark)."""
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_http",
        lambda url, config: (_ for _ in ()).throw(
            FetchError(
                f"HTTP fetch failed for {url}: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>",
                error_type="tls_error",
            )
        ),
    )
    monkeypatch.setattr("agentcrawl.fetchers._browser_backend_available", lambda backend: True)
    article = "".join(f"<p>Paragraph {i} of a real page.</p>" for i in range(5))
    monkeypatch.setattr(
        "agentcrawl.fetchers._fetch_browser",
        lambda url, config, backend=None: f"<html><body><main>{article}</main></body></html>",
    )

    document = AgentCrawl({"fetcher": "http"}).scrape("https://example.com/")

    assert document.ok
    assert "Paragraph 4" in document.markdown
    assert document.metadata["fallback_from"] == "http"


def test_browser_is_launched_once_for_many_pages(monkeypatch) -> None:
    """Chromium was launched (and closed) for every page."""
    _page, browser, chromium = install_fake_playwright(monkeypatch)
    launches = []
    real_launch = chromium.launch
    monkeypatch.setattr(chromium, "launch", lambda **kw: launches.append(kw) or real_launch(**kw))
    monkeypatch.setenv("AGENTCRAWL_BROWSER_CONCURRENCY", "1")

    for _ in range(3):
        _fetch_playwright("https://example.com/", CrawlConfig(network_idle=False))

    assert len(launches) == 1
    # A different launch setting (a proxy) gets a browser of its own.
    _fetch_playwright("https://example.com/", CrawlConfig(network_idle=False, proxy="http://p:1"))
    assert len(launches) == 2 and browser.closed == 1


class _TurnstilePage:
    """A "Just a moment" page whose Turnstile clears once its checkbox is ticked."""

    def __init__(self, box: dict | None):
        self.clicks: list[tuple[float, float]] = []
        box_ = box

        class _Element:
            def bounding_box(self):
                return box_

        class _Frame:
            url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile"

            def frame_element(self):
                return _Element()

        page = self

        class _Mouse:
            def click(self, x, y, delay):
                page.clicks.append((x, y))

        self.frames = [_Frame()]
        self.mouse = _Mouse()

    def title(self):
        return "The real page" if self.clicks else "Just a moment..."

    def wait_for_timeout(self, ms):
        import time

        time.sleep(ms / 1000)

    def wait_for_load_state(self, state, timeout):
        pass


def test_turnstile_checkbox_is_ticked_when_the_page_does_not_clear(monkeypatch) -> None:
    monkeypatch.setattr(fetchers, "_FIRST_CLICK_AFTER_S", 0.0)
    page = _TurnstilePage({"x": 100, "y": 200, "width": 300, "height": 65})
    capture: dict = {}
    fetchers._wait_out_interstitial(page, 5_000, click=True, capture=capture)
    assert page.clicks == [(128, 232.5)]  # the checkbox, left side of the widget
    assert capture["challenge_clicks"] == 1


def test_turnstile_is_not_clicked_when_disabled_or_invisible(monkeypatch) -> None:
    monkeypatch.setattr(fetchers, "_FIRST_CLICK_AFTER_S", 0.0)
    page = _TurnstilePage({"x": 0, "y": 0, "width": 300, "height": 65})
    fetchers._wait_out_interstitial(page, 700, click=False)
    assert page.clicks == []
    hidden = _TurnstilePage({"x": 0, "y": 0, "width": 0, "height": 0})
    fetchers._wait_out_interstitial(hidden, 700, click=True)
    assert hidden.clicks == []
