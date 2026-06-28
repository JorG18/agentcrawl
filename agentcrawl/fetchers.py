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


def _safe_urlopen(
    request,
    *,
    timeout: float,
    allow_private_network: bool,
    airgap: bool = False,
    allowlist_domains: tuple = (),
    audit_trail: Any | None = None,
    target_host: str | None = None,
):
    handlers: list[urllib.request.BaseHandler] = [
        _SafeRedirectHandler(allow_private_network=allow_private_network)
    ]
    if airgap or audit_trail is not None:
        from .airgap import _AirgapHandler, AuditTrail

        audit_for_handler = audit_trail if audit_trail is not None else AuditTrail()
        handlers.append(
            _AirgapHandler(
                target=request.full_url,
                allowlist=allowlist_domains,
                audit=audit_for_handler,
                target_host=target_host,
            )
        )
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)


def fetch_source(source: str, config: CrawlConfig) -> tuple[str, dict[str, Any]]:
    if not is_probably_url(source):
        return _fetch_local_file(source)
    validate_remote_url(source, allow_private_network=config.allow_private_network)
    if config.fetcher == "http":
        try:
            return _fetch_http(source, config)
        except FetchError as exc:
            if config.browser_fallback and _should_browser_fallback(str(exc), config):
                backend = config.browser_backend
                html = _fetch_browser(source, config)
                return html, {
                    "fetcher": backend,
                    "fallback_from": "http",
                    "final_url": source,
                }
            raise
    if config.fetcher in {"playwright", "camofox"}:
        backend = config.fetcher
        html = _fetch_browser(source, config, backend=backend)
        return html, {"fetcher": backend, "final_url": source}
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
    resolved = str(path.resolve())
    content, metadata = read_local_document(path)
    return content, {
        "fetcher": "file",
        "source_path": str(path),
        "final_url": resolved,
        **metadata,
    }


def _fetch_http(url: str, config: CrawlConfig) -> tuple[str, dict[str, Any]]:
    headers = {"user-agent": config.user_agent or "AgentCrawl/0.1"}
    request = urllib.request.Request(url, headers=headers)
    from urllib.parse import urlparse

    target_host = urlparse(url).hostname or ""

    # Build the audit trail ONCE (may be passed into the opener per call).
    # When the user opts into audit=True, every entry the airgap/AirgapHandler
    # records bleeds back into the trail's .records list; we then expose it
    # via fetch_metadata so AgentCrawl.scrape() can attach it to the document.
    from .airgap import AuditTrail

    audit_trail: AuditTrail | None = AuditTrail() if config.audit else None

    last_exc: Exception | None = None
    for attempt in range(max(1, config.http_retries + 1)):
        # Fresh per-attempt view so a partial failure doesn't poison the
        # next retry's audit log; only kept at the top if it actually exists.
        per_attempt_trail: AuditTrail | None = (
            AuditTrail() if audit_trail is not None else None
        )
        try:
            with _safe_urlopen(
                request,
                timeout=config.timeout_ms / 1000,
                allow_private_network=config.allow_private_network,
                airgap=config.airgap,
                allowlist_domains=config.allowlist_domains,
                audit_trail=per_attempt_trail,
                target_host=target_host,
            ) as response:
                final_url = response.geturl()
                validate_remote_url(
                    final_url,
                    allow_private_network=config.allow_private_network,
                )
                html_bytes = response.read()
                try:
                    len_bytes = len(html_bytes)
                except Exception:  # pragma: no cover - extremely defensive
                    len_bytes = 0
                fetch_metadata: dict[str, Any] = {
                    "fetcher": "http",
                    "final_url": final_url,
                }
                if per_attempt_trail is not None:
                    per_attempt_trail.record(
                        "GET",
                        url,
                        final_url=final_url,
                        status=getattr(response, "status", None) or 200,
                        bytes_count=len_bytes,
                        target_host=target_host,
                    )
                    fetch_metadata.update(per_attempt_trail.to_metadata())
                return html_bytes.decode("utf-8", errors="replace"), fetch_metadata
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if per_attempt_trail is not None:
                per_attempt_trail.record(
                    "GET",
                    url,
                    final_url=url,
                    status=exc.code,
                    bytes_count=0,
                    target_host=target_host,
                )
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
            if config.browser_init_script:
                page.add_init_script(config.browser_init_script)
            if config.browser_block_resources:
                blocked = set(config.browser_block_resources)

                def block_selected_resources(route):
                    request = route.request
                    if request.resource_type in blocked:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", block_selected_resources)
            page.goto(url, wait_until=config.wait_until, timeout=config.timeout_ms)
            if config.browser_wait_for_selector:
                page.wait_for_selector(config.browser_wait_for_selector, timeout=config.timeout_ms)
            if config.browser_wait_ms > 0:
                page.wait_for_timeout(config.browser_wait_ms)
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
