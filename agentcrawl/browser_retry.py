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
from .html_tools import extract_html_facts
from .models import ScrapeDocument
from .parsing import html_to_markdown, markdown_structure_metrics, extraction_provenance

if TYPE_CHECKING:
    from .config import CrawlConfig

logger = logging.getLogger(__name__)


def _is_remote_url(source: str) -> bool:
    s = source.lower()
    return s.startswith("http://") or s.startswith("https://")


def attempt_browser_retry(
    source: str,
    *,
    original_metadata: dict[str, Any],
    blocked_reason: str,
    original_config: "CrawlConfig",
    only_main_content: bool | None,
    requested: list[str],
) -> ScrapeDocument | None:
    """Try fetching ``source`` with the configured browser backend.

    Returns a fully populated ScrapeDocument on success, or ``None`` on any
    failure (so the caller can fall back to the original challenge error).
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
    except FetchError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("browser_retry: unexpected fetch error: %s", exc)
        return None

    # Re-check the page in case the browser also saw a challenge.
    from .crawler import _blocked_page_reason

    if _blocked_page_reason(html):
        return None

    try:
        links, metadata = extract_html_facts(html, source)
        main_content = True if only_main_content is None else only_main_content
        markdown = html_to_markdown(html, browser_config, only_main_content=main_content)
        provenance = extraction_provenance(html, only_main_content=main_content)
        from .crawler import _markdown_to_text

        text = _markdown_to_text(markdown)
        structure = markdown_structure_metrics(markdown)
        source_url = str(metadata.get("source_url") or source)
        return ScrapeDocument(
            url=source,
            markdown=markdown,
            text=text,
            html=html if "html" in requested else "",
            links=links,
            metadata={
                **metadata,
                **fetch_metadata,
                **provenance,
                **structure,
                "source_url": source_url,
                "final_url": str(fetch_metadata.get("final_url") or source_url),
                "only_main_content": main_content,
                "content_format": "markdown",
                "markdown_chars": len(markdown),
                "text_chars": len(text),
                "link_count": len(links),
                "browser_retry": True,
                "browser_retry_reason": blocked_reason,
            },
            errors=[],
        )
    except Exception:  # noqa: BLE001
        return None
