"""Bounded browser actions and screenshots (local Playwright path)."""

from __future__ import annotations

import http.server
import threading

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.browser_actions import MAX_ACTIONS, run_actions, validate_actions
from agentcrawl.config import CrawlConfig
from agentcrawl.crawler import _format_document
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import fetch_source
from agentcrawl.models import ScrapeDocument


def test_actions_are_validated_with_the_bad_step_named() -> None:
    steps = [
        {"type": "click", "selector": "#more"},
        {"type": "scroll", "times": 3},
        {"type": "wait", "ms": 500},
        {"type": "press", "key": "Enter"},
    ]
    assert CrawlConfig.from_dict({"browser_actions": steps}).browser_actions == tuple(steps)

    bad = [
        ({"type": "hover", "selector": "a"}, "type must be one of"),
        ({"type": "click"}, "missing selector"),
        ({"type": "click", "selector": "a", "js": "x"}, "does not accept js"),
        ({"type": "scroll", "times": 500}, "times must be an integer"),
        ({"type": "wait", "ms": 999_999}, "ms must be an integer"),
        ({"type": "type", "selector": "#q", "text": ""}, "text must be a non-empty"),
    ]
    for step, message in bad:
        with pytest.raises(ValueError, match=message):
            validate_actions([step])
    with pytest.raises(ValueError, match=f"at most {MAX_ACTIONS}"):
        validate_actions([{"type": "wait", "ms": 1}] * (MAX_ACTIONS + 1))
    with pytest.raises(ValueError, match="list of steps"):
        CrawlConfig.from_dict({"browser_actions": {"type": "click"}})


class FakePage:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_on = fail_on
        page = self

        class Mouse:
            def wheel(self, x, y):
                page.calls.append(("wheel", y))

        class Keyboard:
            def press(self, key):
                page.calls.append(("key", key))

        self.mouse, self.keyboard = Mouse(), Keyboard()

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == self.fail_on:
                raise TimeoutError("element not found")
            self.calls.append((name, *args))

        return call


def test_run_actions_logs_each_step_and_names_the_failure() -> None:
    page = FakePage()
    actions = validate_actions(
        [
            {"type": "click", "selector": "#more"},
            {"type": "type", "selector": "#q", "text": "hi"},
            {"type": "scroll", "times": 2},
        ]
    )

    log = run_actions(page, actions, timeout_ms=1000)

    assert [entry["type"] for entry in log] == ["click", "type", "scroll"]
    assert ("click", "#more") in page.calls and ("fill", "#q", "hi") in page.calls
    assert page.calls.count(("wheel", 10_000)) == 2
    with pytest.raises(RuntimeError, match=r"browser action 0 \(click\) failed"):
        run_actions(FakePage(fail_on="click"), actions, timeout_ms=1000)


def test_screenshot_or_actions_switch_the_fetch_to_the_browser() -> None:
    crawler = AgentCrawl()
    assert crawler._fetch_config("https://example.com", ["markdown"]).fetcher == "http"
    shot = crawler._fetch_config("https://example.com", ["markdown", "screenshot"])
    assert shot.fetcher == "playwright" and shot.screenshot is True
    acting = AgentCrawl({"browser_actions": [{"type": "wait", "ms": 1}]})
    assert acting._fetch_config("https://example.com", ["markdown"]).fetcher == "playwright"


def test_camofox_refuses_actions_and_screenshots() -> None:
    config = CrawlConfig.from_dict({"fetcher": "camofox", "screenshot": True})
    with pytest.raises(FetchError, match="local Playwright backend"):
        fetch_source("https://example.com/", config)


def test_screenshot_is_its_own_output_field() -> None:
    document = ScrapeDocument(
        url="https://example.com/",
        markdown="# Hi",
        text="Hi",
        metadata={"screenshot_png_base64": "iVBOR", "title": "Hi"},
    )

    payload = _format_document(document, ["markdown", "screenshot", "metadata"])

    assert payload["screenshot"] == "iVBOR"
    assert "screenshot_png_base64" not in payload["metadata"]
    assert "screenshot" not in _format_document(document, ["markdown"])


def test_live_playwright_click_and_screenshot() -> None:
    pytest.importorskip("playwright.sync_api")
    page_html = (
        b"<html><body><main><h1>Catalog</h1><p>Intro text for the catalog page.</p>"
        b"<button id=more onclick=\"document.getElementById('x').innerHTML="
        b"'<p>Hidden details revealed after click.</p>'\">More</button>"
        b"<div id=x></div></main></body></html>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page_html)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        crawler = AgentCrawl(
            {
                "allow_private_network": True,
                "browser_actions": [
                    {"type": "click", "selector": "#more"},
                    {"type": "wait_for", "selector": "#x p"},
                ],
            }
        )
        result = crawler.scrape(
            f"http://127.0.0.1:{server.server_address[1]}/",
            formats=["markdown", "screenshot", "metadata"],
        )
    finally:
        server.shutdown()
        server.server_close()
    errors = result.get("errors") or []
    if errors and ("Executable doesn't exist" in errors[0] or "install" in errors[0]):
        pytest.skip("no Chromium for this Playwright version")

    assert not errors
    assert "Hidden details revealed after click." in result["markdown"]
    assert result["screenshot"].startswith("iVBOR")  # base64 PNG
    assert [step["type"] for step in result["metadata"]["browser_actions_log"]] == [
        "click",
        "wait_for",
    ]
