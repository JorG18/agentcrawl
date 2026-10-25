"""Browser retry on 200-OK challenge pages when the user opted in.

This module is the **local** fallback path for Community. It does not
bypass Cloudflare / anti-bot protections in the paid sense — it switches
to the user-locally configured browser fetcher (playwright or camofox)
for a single retry, only when the user explicitly enabled
`browser_fallback=true` and the original fetch was a plain HTTP request.

Heuristics:
- Returns ``None`` on any failure so callers fall back to the original
  ``client_challenge`` error document. Never raises.
- Skips retry for non-HTTP sources (file://, raw HTML strings, etc.).
- Skips retry when the browser backend itself is unavailable/misconfigured.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .fetchers import FetchError, fetch_source

if TYPE_CHECKING:
    from .config import CrawlConfig

logger = logging.getLogger(__name__)


def _is_remote_url(source: str) -> bool:
    s = source.lower()
    return s.startswith("http://") or s.startswith("https://")


def attempt_browser_retry(
    source: str,
    *,
    blocked_reason: str,
    original_config: "CrawlConfig",
    diagnostics: dict | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Try fetching ``source`` with the configured browser backend.

    Returns the page's ``(html, fetch_metadata)`` on success, for the caller's
    normal scrape path, or ``None`` on any failure (so the caller can fall
    back to the original challenge error).
    When the browser met a challenge too, its ``challenge_attempts`` go into
    ``diagnostics`` so the challenge error can still show them.
    """
    if not _is_remote_url(source):
        return None

    try:
        from dataclasses import replace

        browser_config = replace(original_config, fetcher=original_config.browser_backend)
    except Exception:
        return None

    try:
        html, fetch_metadata = fetch_source(source, browser_config)
    except FetchError as exc:
        if diagnostics is not None and getattr(exc, "challenge_attempts", None):
            diagnostics["challenge_attempts"] = exc.challenge_attempts
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_retry: unexpected fetch error: %s", exc)
        return None

    # Re-check the page in case the browser also saw a challenge.
    from .crawler import _blocked_page_reason

    if _blocked_page_reason(html):
        if diagnostics is not None and fetch_metadata.get("challenge_attempts"):
            diagnostics["challenge_attempts"] = fetch_metadata["challenge_attempts"]
        return None

    return html, fetch_metadata
