from __future__ import annotations

import json
import urllib.error

import pytest

from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import _fetch_camofox, fetch_source


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_camofox_fetch_creates_evaluates_and_closes_tab(monkeypatch) -> None:
    requests = []
    responses = iter(
        [
            {"tabId": "tab-1", "url": "https://example.com"},
            {"ok": True, "result": "<html><h1>Stealth page</h1></html>"},
            {"ok": True},
        ]
    )

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response(next(responses))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = CrawlConfig(
        fetcher="camofox",
        camofox_base_url="http://camofox.test:9377",
        camofox_access_key="secret",
        camofox_user_id="test-user",
    )

    html, metadata = fetch_source("https://example.com", config)

    assert "Stealth page" in html
    assert metadata == {"fetcher": "camofox"}
    assert [request.method for request in requests] == ["POST", "POST", "DELETE"]
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert requests[1].full_url.endswith("/tabs/tab-1/evaluate")
    assert requests[2].full_url.endswith("/tabs/tab-1?userId=test-user")


def test_camofox_closes_tab_after_evaluate_failure(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if len(requests) == 1:
            return _Response({"tabId": "tab-2"})
        if len(requests) == 2:
            raise urllib.error.HTTPError(request.full_url, 500, "failed", {}, None)
        return _Response({"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(FetchError, match="Camofox HTTP 500"):
        _fetch_camofox("https://example.com", CrawlConfig(fetcher="camofox"))

    assert requests[-1].method == "DELETE"


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
    assert metadata == {"fetcher": "camofox", "fallback_from": "http"}
