from __future__ import annotations

import json
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .config import CrawlConfig
from .documents import read_local_document
from .exceptions import FetchError
from .security import validate_remote_url
from .utils import is_probably_url

_BROWSER_SEMAPHORE = threading.BoundedSemaphore(2)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_private_network: bool = False):
        self.allow_private_network = allow_private_network
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_remote_url(newurl, allow_private_network=self.allow_private_network)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_urlopen(request, *, timeout: float, allow_private_network: bool):
    opener = urllib.request.build_opener(
        _SafeRedirectHandler(allow_private_network=allow_private_network)
    )
    return opener.open(request, timeout=timeout)


def fetch_source(source: str, config: CrawlConfig) -> tuple[str, dict[str, Any]]:
    if not is_probably_url(source):
        return _fetch_local_file(source)
    validate_remote_url(source, allow_private_network=config.allow_private_network)
    if config.fetcher == "http":
        try:
            return _fetch_http(source, config), {"fetcher": "http"}
        except FetchError as exc:
            if config.browser_fallback and _should_browser_fallback(str(exc), config):
                backend = config.browser_backend
                return _fetch_browser(source, config), {"fetcher": backend, "fallback_from": "http"}
            raise
    if config.fetcher in {"playwright", "camofox"}:
        backend = config.fetcher
        return _fetch_browser(source, config, backend=backend), {"fetcher": backend}
    raise FetchError(f"Unknown fetcher: {config.fetcher}")


def _fetch_browser(url: str, config: CrawlConfig, backend: str | None = None) -> str:
    selected = backend or config.browser_backend
    if selected == "playwright":
        return _fetch_playwright(url, config)
    if selected == "camofox":
        return _fetch_camofox(url, config)
    raise FetchError(f"Unknown browser backend: {selected}")


def _should_browser_fallback(message: str, config: CrawlConfig) -> bool:
    normalized = message.casefold()
    return any(
        f"http {status}" in normalized or f"http error {status}" in normalized
        for status in config.browser_fallback_statuses
    )


def _fetch_local_file(source: str) -> tuple[str, dict[str, Any]]:
    path = pathlib.Path(source).expanduser()
    if not path.exists():
        raise FetchError(f"Local file not found: {source}")
    content, metadata = read_local_document(path)
    return content, {"fetcher": "file", "source_path": str(path), **metadata}


def _fetch_http(url: str, config: CrawlConfig) -> str:
    headers = {"user-agent": config.user_agent or "AgentCrawl/0.1"}
    request = urllib.request.Request(url, headers=headers)
    last_exc: Exception | None = None
    for attempt in range(max(1, config.http_retries + 1)):
        try:
            with _safe_urlopen(
                request,
                timeout=config.timeout_ms / 1000,
                allow_private_network=config.allow_private_network,
            ) as response:
                validate_remote_url(
                    response.geturl(),
                    allow_private_network=config.allow_private_network,
                )
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= config.http_retries:
                break
            retry_after = exc.headers.get("Retry-After")
            delay = _retry_delay(config, attempt, retry_after)
            time.sleep(delay)
        except Exception as exc:
            last_exc = exc
            if attempt >= config.http_retries:
                break
            time.sleep(_retry_delay(config, attempt, None))
    raise FetchError(f"HTTP fetch failed for {url}: {last_exc}") from last_exc


def _retry_delay(config: CrawlConfig, attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(config.http_retry_delay * (2**attempt), 10.0)


def _fetch_camofox(url: str, config: CrawlConfig) -> str:
    base_url = config.camofox_base_url.rstrip("/")
    user_id = config.camofox_user_id
    session_key = f"agentcrawl-{uuid.uuid4().hex}"
    tab_id: str | None = None
    try:
        created = _camofox_request(
            base_url + "/tabs",
            config,
            method="POST",
            payload={"userId": user_id, "sessionKey": session_key, "url": url},
        )
        tab_id = str(created.get("tabId") or "")
        if not tab_id:
            raise FetchError("Camofox did not return a tabId.")
        evaluated = _camofox_request(
            base_url + f"/tabs/{urllib.parse.quote(tab_id, safe='')}/evaluate",
            config,
            method="POST",
            payload={"userId": user_id, "expression": "document.documentElement.outerHTML"},
        )
        html = evaluated.get("result")
        if not isinstance(html, str) or not html.strip():
            raise FetchError("Camofox returned an empty document.")
        return html
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"Camofox fetch failed for {url}: {exc}") from exc
    finally:
        if tab_id:
            query = urllib.parse.urlencode({"userId": user_id})
            try:
                _camofox_request(
                    base_url + f"/tabs/{urllib.parse.quote(tab_id, safe='')}?{query}",
                    config,
                    method="DELETE",
                )
            except Exception:
                pass


def _camofox_request(
    url: str,
    config: CrawlConfig,
    *,
    method: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    headers = {"content-type": "application/json"}
    if config.camofox_access_key:
        headers["authorization"] = f"Bearer {config.camofox_access_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FetchError(f"Camofox HTTP {exc.code}: {detail[:500]}") from exc
    except Exception as exc:
        raise FetchError(f"Camofox request failed: {exc}") from exc
    if not isinstance(body, dict):
        raise FetchError("Camofox returned an invalid response.")
    if body.get("error"):
        raise FetchError(f"Camofox error: {body['error']}")
    return body


def _fetch_playwright(url: str, config: CrawlConfig) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright is not installed. Install agentcrawl[browser] or use fetcher='http'."
        ) from exc

    acquired = _BROWSER_SEMAPHORE.acquire(timeout=max(1, config.timeout_ms / 1000))
    if not acquired:
        raise FetchError(f"Playwright fetch failed for {url}: browser concurrency limit reached")
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=config.headless, proxy={"server": config.proxy} if config.proxy else None
            )
            context = browser.new_context(user_agent=config.user_agent or "AgentCrawl/0.1")
            page = context.new_page()
            page.goto(url, wait_until=config.wait_until, timeout=config.timeout_ms)
            if config.network_idle:
                page.wait_for_load_state("networkidle", timeout=config.timeout_ms)
            validate_remote_url(page.url, allow_private_network=config.allow_private_network)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        raise FetchError(f"Playwright fetch failed for {url}: {exc}") from exc
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        _BROWSER_SEMAPHORE.release()
