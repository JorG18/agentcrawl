"""Local stealth: proxy rotation, the Patchright retry, and next steps."""

from __future__ import annotations

import sys
import types

import pytest

from agentcrawl import AgentCrawl, fetchers
from agentcrawl.config import CrawlConfig
from agentcrawl.errors import error_metadata
from agentcrawl.exceptions import FetchError

from test_fetchers import FakeBrowser, FakeChromium, FakePage, FakePlaywright

CHALLENGE = "<html><head><title>Just a moment...</title></head><body>Checking</body></html>"
REAL = "<html><body><main><h1>Real page</h1><p>Content.</p></main></body></html>"


def _engine(monkeypatch, name: str, html: str) -> FakeChromium:
    page = FakePage()
    page.content = lambda: html
    chromium = FakeChromium(FakeBrowser(page))
    module = types.SimpleNamespace(sync_playwright=lambda: FakePlaywright(chromium))
    monkeypatch.setitem(sys.modules, f"{name}.sync_api", module)
    return chromium


@pytest.fixture
def engines(monkeypatch):
    playwright = _engine(monkeypatch, "playwright", CHALLENGE)
    patchright = _engine(monkeypatch, "patchright", REAL)
    real_find_spec = fetchers.importlib.util.find_spec
    monkeypatch.setattr(
        fetchers.importlib.util,
        "find_spec",
        lambda name, *a: object() if name == "patchright" else real_find_spec(name, *a),
    )
    monkeypatch.setattr(fetchers, "_wait_out_interstitial", lambda page, budget, **kwargs: 0)
    return playwright, patchright


def test_a_challenged_page_is_retried_once_with_patchright(engines) -> None:
    playwright, patchright = engines
    config = CrawlConfig(network_idle=False, proxy="http://p1:8080, http://p2:8080")

    html = fetchers._fetch_playwright("https://example.com/", config)

    assert html == REAL
    first, second = playwright.launch_kwargs, patchright.launch_kwargs
    assert first["args"] == ["--disable-blink-features=AutomationControlled"]
    assert second["args"] == []  # Patchright hides automation itself
    assert {first["proxy"]["server"], second["proxy"]["server"]} == {
        "http://p1:8080",
        "http://p2:8080",
    }  # the retry takes the next proxy


def test_patchright_engine_without_the_extra_says_how_to_install(monkeypatch) -> None:
    _engine(monkeypatch, "playwright", REAL)
    real_find_spec = fetchers.importlib.util.find_spec
    monkeypatch.setattr(
        fetchers.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "patchright" else real_find_spec(name, *a),
    )
    with pytest.raises(FetchError, match="stealth"):
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="patchright"))


def test_unknown_browser_engine_is_refused() -> None:
    with pytest.raises(ValueError, match="browser_engine"):
        CrawlConfig.from_dict({"browser_engine": "chrome"})


def test_errors_say_what_to_do_next() -> None:
    assert "proxy" in error_metadata(FetchError("x", error_type="client_challenge"))["next_step"]
    assert "map_site" in error_metadata(FetchError("HTTP Error 404: Not Found"))["next_step"]


def test_a_challenge_document_carries_the_next_step(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source",
        lambda source, config: (CHALLENGE, {"fetcher": "playwright", "final_url": source}),
    )
    doc = AgentCrawl({"browser_fallback": False}).scrape("https://example.com/")
    assert doc.metadata["error_type"] == "client_challenge"
    assert "Enhanced" in doc.metadata["next_step"]


@pytest.fixture
def cloak(monkeypatch):
    """A fake CloakBrowser install whose binary is present."""
    state = {"installed": True, "ensured": 0}

    def ensure_binary():
        # May validate a license key or download a binary: never in a scrape.
        state["ensured"] += 1
        return "/opt/cloak/chrome"

    def binary_info():
        if isinstance(state["installed"], Exception):
            raise state["installed"]
        return {"installed": state["installed"], "binary_path": "/opt/cloak/chrome"}

    modules = {
        "cloakbrowser": types.SimpleNamespace(),
        "cloakbrowser.download": types.SimpleNamespace(
            ensure_binary=ensure_binary, binary_info=binary_info
        ),
        "cloakbrowser.browser": types.SimpleNamespace(
            build_args=lambda stealth, extra, headless=True: ["--cloak-arg"]
        ),
        "cloakbrowser.config": types.SimpleNamespace(
            IGNORE_DEFAULT_ARGS=["--enable-automation", "--enable-unsafe-swiftshader"]
        ),
        "cloakbrowser.license": types.SimpleNamespace(
            license_error_message=lambda text: "seat limit reached" if "=76" in text else None
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    real_find_spec = fetchers.importlib.util.find_spec
    monkeypatch.setattr(
        fetchers.importlib.util,
        "find_spec",
        lambda name, *a: object() if name == "cloakbrowser" else real_find_spec(name, *a),
    )
    monkeypatch.delenv("CLOAKBROWSER_BINARY_PATH", raising=False)
    state["chromium"] = _engine(monkeypatch, "playwright", REAL)
    return state


def test_cloak_engine_launches_the_cloak_binary(cloak) -> None:
    config = CrawlConfig(browser_engine="cloak", network_idle=False, proxy="http://p1:8080")

    assert fetchers._fetch_playwright("https://example.com/", config) == REAL

    chromium = cloak["chromium"]
    launch = chromium.launch_kwargs
    assert launch["executable_path"] == "/opt/cloak/chrome"
    assert launch["args"] == ["--cloak-arg"]
    assert launch["ignore_default_args"] == ["--enable-automation", "--enable-unsafe-swiftshader"]
    assert "channel" not in launch
    assert cloak["ensured"] == 0  # no license check or download inside a page
    assert launch["proxy"]["server"] == "http://p1:8080"
    # The binary's own (Chrome on Windows) identity; ours would contradict it.
    assert chromium.browser.context_kwargs["user_agent"] is None


def test_cloak_keeps_a_custom_user_agent(cloak) -> None:
    config = CrawlConfig(browser_engine="cloak", network_idle=False, user_agent="MyBot/1")
    fetchers._fetch_playwright("https://example.com/", config)
    assert cloak["chromium"].browser.context_kwargs["user_agent"] == "MyBot/1"


def test_cloak_without_the_extra_is_a_config_error(cloak, monkeypatch) -> None:
    monkeypatch.setattr(fetchers.importlib.util, "find_spec", lambda name, *a: None)
    with pytest.raises(FetchError, match=r"agentcrawl-ai\[cloak\]") as raised:
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="cloak"))
    assert raised.value.error_type == "config_error"


def test_cloak_without_the_binary_is_a_config_error(cloak) -> None:
    cloak["installed"] = False
    with pytest.raises(FetchError, match="python -m cloakbrowser install") as raised:
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="cloak"))
    assert raised.value.error_type == "config_error"
    assert cloak["ensured"] == 0  # never downloaded inside a page's budget


def test_cloak_binary_path_override_skips_the_install_check(cloak, monkeypatch, tmp_path) -> None:
    cloak["installed"] = False
    binary = tmp_path / "chrome"
    binary.write_text("")
    monkeypatch.setenv("CLOAKBROWSER_BINARY_PATH", str(binary))
    config = CrawlConfig(browser_engine="cloak", network_idle=False)
    assert fetchers._fetch_playwright("https://example.com/", config) == REAL
    assert cloak["chromium"].launch_kwargs["executable_path"] == str(binary)


def test_cloak_binary_path_override_to_a_missing_file_is_a_config_error(
    cloak, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("CLOAKBROWSER_BINARY_PATH", str(tmp_path / "nope"))
    with pytest.raises(FetchError, match="CLOAKBROWSER_BINARY_PATH") as raised:
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="cloak"))
    assert raised.value.error_type == "config_error"


def test_a_refused_cloak_launch_names_the_engine_and_the_cure(cloak) -> None:
    def refuse(**_kwargs):
        raise RuntimeError("BrowserType.launch: process did exit: exitCode=76")

    cloak["chromium"].launch = refuse
    with pytest.raises(FetchError) as raised:
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="cloak"))
    message = str(raised.value)
    assert "CloakBrowser refused to launch: seat limit reached" in message
    assert "AGENTCRAWL_BROWSER_CONCURRENCY=1" in message


def test_cloak_on_an_unsupported_platform_is_a_config_error(cloak) -> None:
    cloak["installed"] = RuntimeError("no build for linux-riscv64")
    with pytest.raises(FetchError, match="linux-riscv64") as raised:
        fetchers._fetch_playwright("https://example.com/", CrawlConfig(browser_engine="cloak"))
    assert raised.value.error_type == "config_error"


def test_cloak_is_a_valid_engine() -> None:
    assert CrawlConfig.from_dict({"browser_engine": "cloak"}).browser_engine == "cloak"
