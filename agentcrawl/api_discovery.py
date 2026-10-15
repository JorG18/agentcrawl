"""Value-free discovery of the same-site JSON endpoints a page calls."""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlsplit

MAX_ENDPOINTS = 30
MAX_BODY_BYTES = 2_000_000
MAX_DEPTH = 6
ARRAY_SAMPLE = 20
MAX_KEYS = 100
TRUNCATED = "…"
_SECRET_SUFFIXES = (
    "token",
    "key",
    "apikey",
    "auth",
    "sig",
    "signature",
    "session",
    "sessionid",
    "sid",
    "secret",
    "password",
    "passwd",
    "pwd",
    "jwt",
    "code",
    "credential",
    "credentials",
)


def json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_DEPTH:
        return TRUNCATED
    if isinstance(value, dict):
        shape = {
            str(key): json_shape(item, depth + 1) for key, item in list(value.items())[:MAX_KEYS]
        }
        if len(value) > MAX_KEYS:
            shape[TRUNCATED] = TRUNCATED
        return shape
    if isinstance(value, list):
        merged: Any = None
        for item in value[:ARRAY_SAMPLE]:
            merged = _merge(merged, json_shape(item, depth + 1))
        return [] if merged is None else [merged]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _merge(left: Any, right: Any) -> Any:
    if left is None or left == "null":
        return right
    if isinstance(left, dict) and isinstance(right, dict):
        merged = dict(left)
        for key, value in right.items():
            merged[key] = _merge(merged.get(key), value)
        return merged
    return left


def _is_secret(name: str) -> bool:
    normalized = name.lower()
    for char in "_-.":
        normalized = normalized.replace(char, "")
    return normalized.endswith(_SECRET_SUFFIXES)


def redact_url(url: str) -> str:
    base, sep, query = url.partition("?")
    if not sep:
        return url
    query, hash_sep, fragment = query.partition("#")
    parts = []
    for part in query.split("&"):
        name, eq, _ = part.partition("=")
        parts.append(f"{name}=REDACTED" if eq and _is_secret(name) else part)
    return f"{base}?{'&'.join(parts)}{hash_sep}{fragment}"


def same_site(url: str, page_url: str) -> bool:
    # shortcut: no public-suffix list, so co.uk-style hosts compare by full host; add one if that bites.
    host = (urlsplit(url).hostname or "").lower()
    page_host = (urlsplit(page_url).hostname or "").lower()
    if not host or not page_host:
        return False
    base = page_host.removeprefix("www.")
    return host in (page_host, base) or host.endswith("." + base)


def _query_keys(query: str) -> tuple[str, ...]:
    return tuple(sorted({part.partition("=")[0] for part in query.split("&") if part}))


def _parse_json(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json_shape(json.loads(raw))
    except (TypeError, ValueError):
        return None


class ApiCollector:
    def __init__(self, page_url: str | Callable[[], str]) -> None:
        # A callable follows the tab, so a redirect to another domain is judged by where it landed.
        self.page_url = page_url if callable(page_url) else lambda: page_url
        self._found: dict[tuple, tuple[Any, dict]] = {}
        self.truncated = False

    def on_response(self, response: Any) -> None:
        try:
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            content_type = (response.headers or {}).get("content-type", "")
            if "json" not in content_type.lower():
                return
            if not same_site(response.url, self.page_url()):
                return
            parts = urlsplit(response.url)
            key = (
                request.method,
                parts.scheme,
                parts.hostname,
                parts.path,
                _query_keys(parts.query),
            )
            if key in self._found:
                self._found[key][1]["count"] += 1
                return
            if len(self._found) >= MAX_ENDPOINTS:
                self.truncated = True
                return
            try:
                post_data = request.post_data
            except Exception:
                post_data = None
            self._found[key] = (
                response,
                {
                    "method": request.method,
                    "url": redact_url(response.url),
                    "status": response.status,
                    "content_type": content_type,
                    "bytes": None,
                    "count": 1,
                    "shape": None,
                    "request_shape": _parse_json(post_data),
                },
            )
        except Exception:
            # A listener must never break the page render.
            return

    def finish(self, budget_ms: int) -> tuple[list[dict], bool]:
        deadline = time.monotonic() + max(budget_ms, 0) / 1000
        endpoints = []
        for response, endpoint in self._found.values():
            if time.monotonic() < deadline:
                try:
                    body = response.body()
                except Exception:
                    body = None
                if body is not None:
                    endpoint["bytes"] = len(body)
                    if len(body) > MAX_BODY_BYTES:
                        endpoint["too_large"] = True
                    else:
                        endpoint["shape"] = _parse_json(body)
            endpoints.append(endpoint)
        return endpoints, self.truncated
