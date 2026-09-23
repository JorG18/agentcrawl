"""Regression tests for the browser network guard (2026-09-23 audit).

Before the guard, the Playwright path only validated the final ``page.url``:
redirect hops, iframes, images, ``fetch()``/XHR and WebSockets reached private
addresses and non-allowlisted hosts freely, so ``airgap=True`` with a browser
fetch kept nothing off the network. Verified with real Chromium: an unguarded
page leaked 5 requests to ``localhost``; the guarded one leaked 0.

Playwright is not a Community test dependency, so these tests drive the guard
with small fakes that mirror the Route / Request / APIResponse surface it uses.
"""

from __future__ import annotations

import pytest

from agentcrawl.airgap import AuditTrail
from agentcrawl.browser_guard import MAX_REDIRECTS, BrowserNetworkGuard
from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError


class FakeResponse:
    def __init__(self, status: int, location: str | None = None, body: bytes = b"ok") -> None:
        self.status = status
        self.headers = {"location": location} if location else {}
        self._body = body

    def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, url: str, *, navigation: bool = False, method: str = "GET") -> None:
        self.url = url
        self.method = method
        self._navigation = navigation
        self.frame = "main"
        self.resource_type = "document" if navigation else "image"

    def is_navigation_request(self) -> bool:
        return self._navigation


class FakeRoute:
    """Serves ``responses[url]`` for every fetch, like a tiny web."""

    def __init__(self, request: FakeRequest, responses: dict[str, FakeResponse]) -> None:
        self.request = request
        self.responses = responses
        self.fetched: list[str] = []
        self.outcome: tuple | None = None

    def fetch(self, url: str | None = None, **_kwargs):
        target = url or self.request.url
        self.fetched.append(target)
        return self.responses[target]

    def abort(self, reason: str = "failed") -> None:
        self.outcome = ("abort", reason)

    def continue_(self) -> None:
        self.outcome = ("continue",)

    def fulfill(self, **kwargs) -> None:
        self.outcome = ("fulfill", kwargs)


class FakeWebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connected = False

    def connect_to_server(self) -> None:
        self.connected = True


def _guard(url: str = "https://example.com/", **config) -> BrowserNetworkGuard:
    guard = BrowserNetworkGuard(url, CrawlConfig.from_dict(config), AuditTrail())
    guard.main_frame = "main"
    return guard


def test_check_blocks_private_targets_by_default() -> None:
    guard = _guard()
    assert "127.0.0.1" in (guard.check("http://127.0.0.1:8000/admin") or "")
    assert guard.check("https://example.com/asset.png") is None
    assert guard.check("data:image/png;base64,AAAA") is None


def test_check_applies_the_airgap_allowlist() -> None:
    guard = _guard(airgap=True, allow_private_network=True)
    assert "airgap blocked" in (guard.check("https://tracker.example.org/p.gif") or "")
    assert guard.check("https://example.com/other") is None
    allowlisted = _guard(
        airgap=True, allow_private_network=True, allowlist_domains=["*.example.org"]
    )
    assert allowlisted.check("https://cdn.example.org/app.js") is None


def test_check_covers_websockets_and_rejects_other_schemes() -> None:
    guard = _guard(airgap=True, allow_private_network=True)
    assert "airgap blocked" in (guard.check("wss://tracker.example.org/socket") or "")
    assert "scheme not allowed" in (guard.check("file:///etc/passwd") or "")


def test_subresource_redirect_to_a_private_address_is_blocked_before_it_is_sent() -> None:
    guard = _guard()
    responses = {"https://example.com/img": FakeResponse(302, "http://127.0.0.1/secret.png")}
    route = FakeRoute(FakeRequest("https://example.com/img"), responses)

    guard.handle_route(route)

    assert route.outcome == ("abort", "blockedbyclient")
    assert route.fetched == ["https://example.com/img"]  # the private hop never fetched
    metadata = guard.audit_trail.to_metadata()
    assert metadata["audit_blocked_request_count"] == 1


def test_subresource_redirect_chain_is_walked_inside_the_guard() -> None:
    guard = _guard()
    responses = {
        "https://example.com/a.png": FakeResponse(301, "/b.png"),
        "https://example.com/b.png": FakeResponse(200, body=b"png"),
    }
    route = FakeRoute(FakeRequest("https://example.com/a.png"), responses)

    guard.handle_route(route)

    assert route.fetched == ["https://example.com/a.png", "https://example.com/b.png"]
    assert route.outcome[0] == "fulfill"
    assert route.outcome[1]["response"] is responses["https://example.com/b.png"]


def test_main_navigation_redirect_is_handed_back_to_the_fetcher() -> None:
    guard = _guard()
    responses = {"https://example.com/": FakeResponse(302, "https://www.example.com/")}
    route = FakeRoute(FakeRequest("https://example.com/", navigation=True), responses)

    guard.handle_route(route)

    assert guard.pending_navigation == "https://www.example.com/"
    assert route.outcome[0] == "fulfill"


def test_redirect_loops_are_bounded() -> None:
    guard = _guard()
    responses = {"https://example.com/loop": FakeResponse(302, "/loop")}
    route = FakeRoute(FakeRequest("https://example.com/loop"), responses)

    guard.handle_route(route)

    assert route.outcome == ("abort", "failed")
    assert len(route.fetched) == MAX_REDIRECTS + 1


def test_refused_websocket_never_connects() -> None:
    guard = _guard(airgap=True, allow_private_network=True)
    refused = FakeWebSocketRoute("wss://tracker.example.org/socket")
    allowed = FakeWebSocketRoute("wss://example.com/socket")

    guard.handle_websocket(refused)
    guard.handle_websocket(allowed)

    assert refused.connected is False
    assert allowed.connected is True


def test_camofox_refuses_to_run_under_airgap(monkeypatch) -> None:
    from agentcrawl import fetchers

    called = []
    monkeypatch.setattr(fetchers, "_fetch_camofox", lambda url, config: called.append(url))

    with pytest.raises(FetchError, match="airgap cannot be enforced on the camofox"):
        fetchers._fetch_browser("https://example.com/", CrawlConfig(airgap=True), backend="camofox")
    assert called == []


def test_browser_fetch_carries_the_audit_trail(monkeypatch) -> None:
    from agentcrawl import fetchers

    def fake_playwright(url, config, *, audit_trail=None):
        audit_trail.record("GET", url, final_url=url, status=200, bytes_count=10)
        return "<html><body>ok</body></html>"

    monkeypatch.setattr(fetchers, "_fetch_playwright", fake_playwright)

    _html, metadata = fetchers.fetch_source(
        "https://example.com/", CrawlConfig(fetcher="playwright", audit=True)
    )

    assert metadata["audit_request_count"] == 1
    assert metadata["audit_total_bytes"] == 10


def test_api_rejects_camofox_unless_the_operator_enabled_it(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from agentcrawl.server import app, server
    from agentcrawl.storage import SQLiteStore

    server.store = SQLiteStore(tmp_path / "camofox.db")
    original = dict(server.default_config)
    try:
        server.default_config["fetcher"] = "http"
        server.default_config["browser_backend"] = "playwright"
        client = TestClient(app)
        response = client.post(
            "/v1/scrape", json={"url": "https://example.com/", "config": {"fetcher": "camofox"}}
        )
        assert response.status_code == 400
        assert "camofox" in response.json()["detail"]
    finally:
        server.default_config = original
