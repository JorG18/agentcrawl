from __future__ import annotations

import codecs
import importlib.util
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .config import CrawlConfig
from .documents import CSV_CONTENT_TYPES, csv_to_markdown, read_local_document
from .exceptions import FetchError
from .security import pinned_handlers, validate_remote_url
from .utils import is_probably_url

_browser_sem: threading.BoundedSemaphore | None = None


def _get_browser_semaphore() -> threading.BoundedSemaphore:
    """Return a process-wide semaphore limiting concurrent browser fetches.

    The limit defaults to 2 and can be overridden via the
    ``AGENTCRAWL_BROWSER_CONCURRENCY`` environment variable so that
    parallel-test runners or memory-constrained hosts can tune it
    without changing code.
    """
    global _browser_sem
    if _browser_sem is None:
        limit = max(1, int(os.getenv("AGENTCRAWL_BROWSER_CONCURRENCY", "2")))
        _browser_sem = threading.BoundedSemaphore(limit)
    return _browser_sem


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
    if not allow_private_network:
        handlers.extend(pinned_handlers())
    if airgap or audit_trail is not None:
        from .airgap import _AirgapHandler, AuditTrail

        audit_for_handler = audit_trail if audit_trail is not None else AuditTrail()
        handlers.append(
            _AirgapHandler(
                target=request.full_url,
                allowlist=allowlist_domains,
                audit=audit_for_handler,
                target_host=target_host,
                # Observation must not become enforcement: `enforce` follows
                # `airgap` alone, never the presence of an audit trail.
                enforce=airgap,
            )
        )
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)


def _browser_backend_available(backend: str) -> bool:
    """Whether ``backend`` could plausibly run on this machine right now.

    ``playwright`` needs its optional dependency installed; ``camofox`` is a
    local service reached over HTTP, so availability is decided when the
    request is made. Callers use this to avoid replacing a real HTTP failure
    with an "install the extra" message.
    """
    if backend == "camofox":
        return True
    if backend == "playwright":
        return importlib.util.find_spec("playwright") is not None
    return False


def fetch_source(source: str, config: CrawlConfig) -> tuple[str, dict[str, Any]]:
    if not is_probably_url(source):
        return _fetch_local_file(source)
    validate_remote_url(source, allow_private_network=config.allow_private_network)
    if config.fetcher == "http":
        try:
            return _fetch_http(source, config)
        except FetchError as exc:
            if not (config.browser_fallback and _should_browser_fallback(str(exc), config)):
                raise
            backend = config.browser_backend
            # The browser is a *fallback*, so a fallback that cannot run must
            # never replace the real error. Without this check a default
            # install (no ``[browser]`` extra) reported "Playwright is not
            # installed" for every 403 instead of the honest ``blocked``
            # status — and ``browser_error`` is a retryable class, so crawls
            # then burned their whole retry budget on pages that were never
            # going to work.
            if not _browser_backend_available(backend):
                raise
            audit_kwargs, browser_trail = _browser_audit_kwargs(config)
            try:
                html = _fetch_browser(source, config, **audit_kwargs)
            except FetchError as browser_exc:
                # Keep the original HTTP failure (the honest one) and attach
                # the fallback reason so the caller can still see why the
                # browser attempt did not rescue the page.
                exc.browser_fallback_error = str(browser_exc)
                raise exc from browser_exc
            metadata: dict[str, Any] = {
                "fetcher": backend,
                "fallback_from": "http",
                "final_url": source,
            }
            if browser_trail is not None:
                metadata.update(browser_trail.to_metadata())
            return html, metadata
    if config.fetcher in {"playwright", "camofox"}:
        backend = config.fetcher
        audit_kwargs, browser_trail = _browser_audit_kwargs(config)
        html = _fetch_browser(source, config, backend=backend, **audit_kwargs)
        metadata = {"fetcher": backend, "final_url": source}
        if browser_trail is not None:
            metadata.update(browser_trail.to_metadata())
        return html, metadata
    raise FetchError(f"Unknown fetcher: {config.fetcher}")


def _fetch_browser(
    url: str,
    config: CrawlConfig,
    backend: str | None = None,
    audit_trail: Any | None = None,
) -> str:
    selected = backend or config.browser_backend
    if selected == "playwright":
        if audit_trail is not None:
            return _fetch_playwright(url, config, audit_trail=audit_trail)
        return _fetch_playwright(url, config)
    if selected == "camofox":
        if config.airgap:
            # Camofox is a separate service driving its own browser: nothing
            # here can see, let alone refuse, the requests its page makes.
            # Running it under airgap would silently void the guarantee.
            raise FetchError(
                "airgap cannot be enforced on the camofox backend; use "
                "browser_backend='playwright' or disable airgap."
            )
        return _fetch_camofox(url, config)
    raise FetchError(f"Unknown browser backend: {selected}")


def _browser_audit_kwargs(config: CrawlConfig) -> tuple[dict[str, Any], Any | None]:
    if not config.audit:
        return {}, None
    from .airgap import AuditTrail

    trail = AuditTrail()
    return {"audit_trail": trail}, trail


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
        try:
            with _safe_urlopen(
                request,
                timeout=config.timeout_ms / 1000,
                allow_private_network=config.allow_private_network,
                airgap=config.airgap,
                allowlist_domains=config.allowlist_domains,
                audit_trail=audit_trail,
                target_host=target_host,
            ) as response:
                final_url = response.geturl()
                validate_remote_url(
                    final_url,
                    allow_private_network=config.allow_private_network,
                )
                html_bytes = _read_bounded(
                    response,
                    config.max_response_bytes,
                    url=url,
                    deadline_seconds=read_deadline_seconds(config),
                )
                try:
                    len_bytes = len(html_bytes)
                except Exception:  # pragma: no cover - extremely defensive
                    len_bytes = 0
                fetch_metadata: dict[str, Any] = {
                    "fetcher": "http",
                    "final_url": final_url,
                }
                charset = _response_charset(response.headers)
                content_type = _response_content_type(response.headers)
                if audit_trail is not None:
                    audit_trail.record(
                        "GET",
                        url,
                        final_url=final_url,
                        status=getattr(response, "status", None) or 200,
                        bytes_count=len_bytes,
                        target_host=target_host,
                    )
                    fetch_metadata.update(audit_trail.to_metadata())
                body = _decode_http_body(html_bytes, charset)
                if content_type in CSV_CONTENT_TYPES:
                    # Data URLs serve CSV; parsed as HTML they became one
                    # unreadable paragraph. Render the table instead.
                    body, csv_metadata = csv_to_markdown(body)
                    fetch_metadata.update(csv_metadata)
                return body, fetch_metadata
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if audit_trail is not None:
                audit_trail.record(
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
            # A failed attempt is still a request that was made, so the trail
            # has to show it. The airgap handler already recorded its own
            # refusal, so skip that case to avoid a duplicate entry.
            from .airgap import AirgapViolation

            if audit_trail is not None and not isinstance(exc, AirgapViolation):
                audit_trail.record(
                    "GET",
                    url,
                    final_url=url,
                    status=None,
                    bytes_count=0,
                    target_host=target_host,
                )
            if _is_policy_denial(exc) or attempt >= config.http_retries:
                break
            time.sleep(_retry_delay(config, attempt, None))
    err = FetchError(f"HTTP fetch failed for {url}: {last_exc}")
    # When all retries are exhausted, ``audit_trail`` accumulated one
    # record per attempt. Without this attachment the trail would be
    # silently dropped on the way out and any caller that wanted to
    # surface the failed-request history would have nothing to render.
    # ``AgentCrawl.scrape`` picks ``audit_trail`` up via a ``getattr``
    # check in its FetchError handler and merges ``to_metadata`` into
    # the document.
    if audit_trail is not None:
        err.audit_trail = audit_trail
    raise err from last_exc


def _is_policy_denial(exc: BaseException) -> bool:
    """Whether ``exc`` is a deterministic refusal rather than a transient fault.

    ``validate_remote_url`` (SSRF guard) and ``_AirgapHandler`` reject a
    request for policy reasons that cannot change between attempts. Retrying
    them only spends the retry budget and the backoff before reporting the
    failure wrapped as a generic transport error, which buries the real
    reason. Everything else (timeouts, DNS, TLS, connection resets) stays
    retryable.
    """
    from .airgap import AirgapViolation

    return isinstance(exc, (AirgapViolation, FetchError))


_READ_CHUNK_BYTES = 65536

# Total wall-clock budget for reading one body, as a multiple of
# ``timeout_ms``. ``timeout_ms`` is a per-socket-operation timeout, so a server
# that drips one byte just under it keeps a worker busy indefinitely.
READ_DEADLINE_FACTOR = 3


def read_deadline_seconds(config: CrawlConfig) -> float:
    return max(1.0, config.timeout_ms / 1000 * READ_DEADLINE_FACTOR)


def _read_bounded(
    response: Any,
    limit: int,
    *,
    url: str = "",
    deadline_seconds: float | None = None,
) -> bytes:
    """Read a response body, refusing anything past ``limit`` bytes.

    Reads in chunks so an oversized body is rejected *while* it arrives rather
    than after it has been materialized in memory. The limit still holds for
    readers that only implement ``read()`` (test doubles, third-party
    adapters): those lose the streaming protection but not the cap.

    ``deadline_seconds`` bounds the *total* read time. ``read1`` is preferred
    because it returns after a single socket read; ``read(amt)`` would block
    until ``amt`` bytes arrived and a slow drip would never reach the check.
    """
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + deadline_seconds if deadline_seconds else None
    read1 = getattr(response, "read1", None)
    while True:
        try:
            if callable(read1):
                chunk = read1(_READ_CHUNK_BYTES)
            else:
                chunk = response.read(_READ_CHUNK_BYTES)
        except TypeError:
            # Reader without an ``amt`` parameter (test doubles, third-party
            # adapters): one call returns the whole body, so stop after it.
            chunk = response.read()
            last = True
        else:
            last = False
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FetchError(
                f"Response body exceeds the {limit} byte limit"
                + (f" for {url}" if url else "")
                + ". Raise max_response_bytes to accept larger pages."
            )
        chunks.append(chunk)
        if last:
            break
        if deadline is not None and time.monotonic() > deadline:
            raise FetchError(
                f"Response body took longer than {deadline_seconds:.0f}s to arrive"
                + (f" for {url}" if url else "")
                + f" (read deadline is {READ_DEADLINE_FACTOR}x timeout_ms)."
            )
    return b"".join(chunks)


def _response_content_type(headers: Any) -> str:
    get_type = getattr(headers, "get_content_type", None)
    if not callable(get_type):
        return ""
    try:
        return str(get_type()).lower()
    except Exception:  # pragma: no cover - malformed header values
        return ""


def _response_charset(headers: Any) -> str | None:
    """Read the charset declared by the response (Content-Type header).

    Defensive on purpose: tests and third-party callers can hand us a plain
    dict instead of ``http.client.HTTPMessage``, and plain dicts do not
    carry ``get_content_charset``.
    """
    get_charset = getattr(headers, "get_content_charset", None)
    if not callable(get_charset):
        return None
    try:
        return get_charset()
    except Exception:  # pragma: no cover - malformed header values
        return None


def _decode_http_body(data: bytes, charset: str | None) -> str:
    """Decode an HTTP body honoring the declared charset, with safe fallbacks.

    Order of preference:
    1. The charset declared in Content-Type (e.g. ``text/html; charset=iso-8859-1``).
    2. A BOM sniff (UTF-8 / UTF-16 LE/BE), which overrides a bogus declaration.
    3. UTF-8 (web default).
    4. latin-1, which never fails and preserves the byte -> char mapping.

    The previous behavior (always ``utf-8`` with ``errors="replace"``)
    silently mojibake'd latin-1 / windows-1252 pages, and the corrupted
    text was then cached and stored as extracted content.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16", errors="replace")
    if charset:
        try:
            return data.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError, TypeError, ValueError):
            pass  # unknown or lying charset declaration
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _retry_delay(config: CrawlConfig, attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            # Clamp from both sides: a hostile/misconfigured ``Retry-After``
            # must not stall the crawl (huge values) or crash ``time.sleep``
            # (negative values raise ValueError).
            return max(0.0, min(float(retry_after), 30.0))
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


def _fetch_playwright(url: str, config: CrawlConfig, *, audit_trail: Any | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright is not installed. Install agentcrawl[browser] or use fetcher='http'."
        ) from exc

    from .browser_guard import MAX_REDIRECTS, BrowserNetworkGuard

    guard = BrowserNetworkGuard(url, config, audit_trail)
    acquired = _get_browser_semaphore().acquire(timeout=max(1, config.timeout_ms / 1000))
    if not acquired:
        raise FetchError(f"Playwright fetch failed for {url}: browser concurrency limit reached")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=config.headless, proxy={"server": config.proxy} if config.proxy else None
            )
            # Cleanup has to happen *inside* the Playwright session: once the
            # ``with`` block exits the driver is already stopped, so a close
            # in the outer ``finally`` failed silently and leaked browser
            # processes on error paths.
            context = None
            try:
                context_kwargs: dict[str, Any] = {
                    "user_agent": config.user_agent or "AgentCrawl/0.1"
                }
                if guard.active:
                    # Service workers can issue requests the route never sees.
                    context_kwargs["service_workers"] = "block"
                context = browser.new_context(**context_kwargs)
                blocked_types = set(config.browser_block_resources or ())

                def route_request(route):
                    if route.request.resource_type in blocked_types:
                        route.abort()
                    elif guard.active:
                        guard.handle_route(route)
                    else:
                        route.continue_()

                if guard.active or blocked_types:
                    # Context-level so popups and iframes are covered too.
                    context.route("**/*", route_request)
                if guard.active and hasattr(context, "route_web_socket"):
                    context.route_web_socket("**/*", guard.handle_websocket)
                page = context.new_page()
                guard.main_frame = getattr(page, "main_frame", None)
                if config.browser_init_script:
                    page.add_init_script(config.browser_init_script)
                target = url
                for _hop in range(MAX_REDIRECTS + 1):
                    guard.pending_navigation = None
                    page.goto(target, wait_until=config.wait_until, timeout=config.timeout_ms)
                    if guard.pending_navigation is None:
                        break
                    target = guard.pending_navigation
                else:
                    raise FetchError(f"too many redirects (>{MAX_REDIRECTS})")
                if guard.enforcing and guard.blocked and not page.url.startswith("http"):
                    # The navigation itself was refused: report the guard's
                    # reason instead of Chromium's net::ERR_BLOCKED_BY_CLIENT.
                    raise FetchError(guard.blocked[0]["reason"])
                if config.browser_wait_for_selector:
                    page.wait_for_selector(
                        config.browser_wait_for_selector, timeout=config.timeout_ms
                    )
                if config.browser_wait_ms > 0:
                    page.wait_for_timeout(config.browser_wait_ms)
                if config.network_idle:
                    page.wait_for_load_state("networkidle", timeout=config.timeout_ms)
                validate_remote_url(page.url, allow_private_network=config.allow_private_network)
                return page.content()
            finally:
                for resource in (context, browser):
                    try:
                        resource.close()
                    except Exception:
                        pass
    except FetchError as exc:
        raise FetchError(f"Playwright fetch failed for {url}: {exc}") from exc
    except Exception as exc:
        reason = _main_navigation_block_reason(guard, url)
        raise FetchError(f"Playwright fetch failed for {url}: {reason or exc}") from exc
    finally:
        _get_browser_semaphore().release()


def _main_navigation_block_reason(guard: Any, url: str) -> str | None:
    """The guard's reason when the navigation died because the guard refused it."""
    for item in guard.blocked:
        return item["reason"]
    return None
