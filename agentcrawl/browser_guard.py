"""Network guard for the local browser fetcher.

The HTTP fetcher validates every hop (SSRF guard + airgap) because urllib lets
us install handlers. A browser is different: the page decides what to load —
redirect hops, iframes, images, scripts, ``fetch()``/XHR, WebSockets — and
before this guard only the *final* ``page.url`` was checked, after every one of
those requests had already left the machine. ``airgap=True`` with a browser
fallback therefore did not keep anything off the network.

Two Playwright facts shape the design (both verified against Playwright 1.61 +
Chromium, 2026-09-23):

1. A route handler only sees the *first* URL of a redirect chain; the browser
   follows 3xx hops internally without calling the handler again, even when the
   handler fulfilled the 3xx itself.
2. ``route.fetch(max_redirects=0)`` lets the handler perform the request and
   inspect the 3xx before the browser sees it.

So every request is performed by the guard with ``max_redirects=0``:

- sub-resources walk their redirect chain inside the guard, validating each
  hop, and the browser receives the final response;
- the main-frame navigation does *not* walk the chain in place (the page would
  keep the wrong URL/origin); the guard records the validated next hop in
  ``pending_navigation`` and the fetcher issues a fresh ``goto`` for it, which
  is routed again.

Known limits, documented rather than hidden: DNS is resolved separately by the
browser (no IP pinning on this path), and Camofox cannot be intercepted from
here at all.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .exceptions import FetchError
from .security import validate_remote_url

MAX_REDIRECTS = 10

# Schemes that never reach the network.
_LOCAL_SCHEMES = frozenset({"data", "blob", "about"})


class BrowserNetworkGuard:
    def __init__(self, target_url: str, config: Any, audit_trail: Any | None = None) -> None:
        self.target_host = (urllib.parse.urlsplit(target_url).hostname or "").lower()
        self.allow_private_network = bool(config.allow_private_network)
        self.airgap = bool(config.airgap)
        # Same semantics as ``_AirgapHandler``: an empty allowlist means "the
        # target host only".
        self.allowlist = [
            str(item).lower().strip() for item in (config.allowlist_domains or ()) if item
        ] or [self.target_host]
        self.audit_trail = audit_trail
        self.blocked: list[dict[str, str]] = []
        self.pending_navigation: str | None = None
        self.main_frame: Any | None = None
        self._validated_hosts: dict[tuple[str, str, int | None], str | None] = {}

    @property
    def enforcing(self) -> bool:
        return self.airgap or not self.allow_private_network

    @property
    def active(self) -> bool:
        return self.enforcing or self.audit_trail is not None

    # -- decisions ---------------------------------------------------------

    def check(self, url: str) -> str | None:
        """Return why ``url`` must not be requested, or ``None`` when allowed."""
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return f"unparseable URL: {url[:200]}"
        scheme = parts.scheme.lower()
        if scheme in _LOCAL_SCHEMES:
            return None
        if scheme in {"ws", "wss"}:
            validation_url = ("http" if scheme == "ws" else "https") + url[len(scheme) :]
        elif scheme in {"http", "https"}:
            validation_url = url
        else:
            return f"scheme not allowed in the guarded browser: {scheme}"
        host = (parts.hostname or "").lower()
        if self.airgap and not self._airgap_allows(host):
            return (
                f"airgap blocked request to {url} "
                f"(target={self.target_host}, allowlist={self.allowlist})"
            )
        if not self.allow_private_network:
            try:
                port = parts.port
            except ValueError:
                return "Invalid URL port."
            key = (scheme, host, port)
            if key not in self._validated_hosts:
                try:
                    validate_remote_url(validation_url, allow_private_network=False)
                    self._validated_hosts[key] = None
                except FetchError as exc:
                    self._validated_hosts[key] = str(exc)
            return self._validated_hosts[key]
        return None

    def _airgap_allows(self, host: str) -> bool:
        from .airgap import _match

        if not host or host == self.target_host:
            return True
        return any(_match(host, entry) for entry in self.allowlist)

    # -- bookkeeping -------------------------------------------------------

    def _block(self, method: str, url: str, reason: str) -> None:
        self.blocked.append({"url": url, "reason": reason})
        if self.audit_trail is not None:
            self.audit_trail.record(
                method,
                url,
                final_url=url,
                status=0,
                target_host=self.target_host,
                blocked=True,
            )

    def _record(self, method: str, url: str, status: int | None, bytes_count: int) -> None:
        if self.audit_trail is not None:
            self.audit_trail.record(
                method,
                url,
                final_url=url,
                status=status,
                bytes_count=bytes_count,
                target_host=self.target_host,
            )

    def _is_main_navigation(self, request: Any) -> bool:
        try:
            if not request.is_navigation_request():
                return False
            return self.main_frame is None or request.frame == self.main_frame
        except Exception:
            # Service-worker requests have no frame; they are never the main
            # navigation.
            return False

    # -- Playwright handlers -------------------------------------------------

    def handle_route(self, route: Any) -> None:
        request = route.request
        method = request.method
        url = request.url
        reason = self.check(url)
        if reason:
            self._block(method, url, reason)
            route.abort("blockedbyclient")
            return
        if urllib.parse.urlsplit(url).scheme.lower() in _LOCAL_SCHEMES:
            route.continue_()
            return

        main_navigation = self._is_main_navigation(request)
        fetch_kwargs: dict[str, Any] = {"max_redirects": 0}
        try:
            response = route.fetch(**fetch_kwargs)
        except Exception:
            self._record(method, url, None, 0)
            route.abort("failed")
            return

        hops = 0
        while 300 <= int(response.status) < 400 and _header(response, "location"):
            self._record(method, url, int(response.status), 0)
            next_url = urllib.parse.urljoin(url, _header(response, "location"))
            reason = self.check(next_url)
            if reason:
                self._block(method, next_url, reason)
                route.abort("blockedbyclient")
                return
            if main_navigation:
                # Hand the hop back to the fetcher: a fresh ``goto`` keeps the
                # page URL/origin right and is routed through this guard again.
                self.pending_navigation = next_url
                route.fulfill(status=200, content_type="text/html", body="")
                return
            hops += 1
            if hops > MAX_REDIRECTS:
                self._block(method, next_url, f"too many redirects (>{MAX_REDIRECTS})")
                route.abort("failed")
                return
            # 307/308 keep method and body; every other redirect becomes a GET.
            if int(response.status) not in {307, 308}:
                method = "GET"
                fetch_kwargs = {"max_redirects": 0, "method": "GET", "post_data": None}
            url = next_url
            try:
                response = route.fetch(url=url, **fetch_kwargs)
            except Exception:
                self._record(method, url, None, 0)
                route.abort("failed")
                return

        body_size = 0
        if self.audit_trail is not None:
            try:
                body_size = len(response.body())
            except Exception:
                body_size = 0
        self._record(method, url, int(response.status), body_size)
        route.fulfill(response=response)

    def handle_websocket(self, ws: Any) -> None:
        url = ws.url
        reason = self.check(url)
        if reason:
            # Not calling ``connect_to_server()`` is the refusal: Playwright
            # keeps the socket mocked and never opens the real connection.
            # (``ws.close()`` from a sync handler deadlocks the driver —
            # observed with Playwright 1.61.)
            self._block("GET", url, reason)
            return
        self._record("GET", url, 101, 0)
        ws.connect_to_server()


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() == name:
            return str(value)
    return ""
