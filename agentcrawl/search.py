from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

from .config import CrawlConfig
from .fetchers import _read_bounded, _safe_urlopen
from .models import SearchResult


def search_web(
    query: str,
    config: CrawlConfig,
    audit_trail: Any | None = None,
) -> list[SearchResult]:
    """Run a web search through the same guard rails as page fetches.

    Search used to call ``urllib.request.urlopen`` directly. That skipped the
    airgap (so a search request went out under ``airgap=True``), skipped the
    audit trail (so it never showed up in ``audit_records``, contradicting
    "records every HTTP request") and skipped the bounded read.

    ``audit_trail``: pass an :class:`agentcrawl.airgap.AuditTrail` to capture
    the request; ``AgentCrawler.search_then_scrape`` does that when
    ``audit=True`` and returns the trail under ``audit``.
    """
    if config.search_engine == "serper":
        return _search_serper(query, config, audit_trail)
    if config.search_engine == "none":
        return []
    return _search_duckduckgo(query, config, audit_trail)


def _open_search_request(
    request: urllib.request.Request,
    config: CrawlConfig,
    audit_trail: Any | None,
):
    """Open a search request with airgap/audit and a bounded body.

    The search engine is deliberately **not** treated as the trust target: a
    caller who turned the airgap on did not ask for this host, so it has to be
    in ``allowlist_domains`` or the request is refused with a message that says
    how to fix it.
    """
    from .airgap import AirgapViolation
    from .security import validate_remote_url

    validate_remote_url(
        request.full_url,
        allow_private_network=config.allow_private_network,
    )
    try:
        return _safe_urlopen(
            request,
            timeout=config.timeout_ms / 1000,
            allow_private_network=config.allow_private_network,
            airgap=config.airgap,
            allowlist_domains=config.allowlist_domains,
            audit_trail=audit_trail,
            # "" means "no implicitly trusted host"; see _AirgapHandler.
            target_host="",
        )
    except AirgapViolation as exc:
        host = urllib.parse.urlsplit(request.full_url).hostname or request.full_url
        raise AirgapViolation(
            f"airgap blocked the search request to {host}. "
            f"Add {host} to allowlist_domains if search is intended."
        ) from exc


def _fetch_search_body(
    request: urllib.request.Request,
    config: CrawlConfig,
    audit_trail: Any | None,
) -> bytes:
    """Open, read (bounded) and record a search request.

    The record is written here rather than in the opener because only this
    layer knows the status and the real byte count. ``target_host=""`` keeps
    the request out of ``audit_third_party_request_count``: a search is an
    operation the caller explicitly asked for, not an off-target request made
    while scraping a page.
    """
    with _open_search_request(request, config, audit_trail) as response:
        body = _read_bounded(response, config.max_response_bytes, url=request.full_url)
        status = getattr(response, "status", None) or 200
        get_url = getattr(response, "geturl", None)
        final_url = get_url() if callable(get_url) else request.full_url
    if audit_trail is not None:
        audit_trail.record(
            request.get_method(),
            request.full_url,
            final_url=final_url,
            status=status,
            bytes_count=len(body),
            target_host="",
        )
    return body


def _search_serper(
    query: str,
    config: CrawlConfig,
    audit_trail: Any | None = None,
) -> list[SearchResult]:
    api_key = config.serper_api_key or os.getenv("SERPER_API_KEY")
    if not api_key:
        raise ValueError("Serper search requires config['serper_api_key'] or SERPER_API_KEY.")
    request = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": config.search_limit}).encode("utf-8"),
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(_fetch_search_body(request, config, audit_trail).decode("utf-8"))
    results = []
    for item in payload.get("organic", [])[: config.search_limit]:
        if item.get("link"):
            results.append(
                SearchResult(
                    title=item.get("title", ""), url=item["link"], snippet=item.get("snippet", "")
                )
            )
    return results


def _search_duckduckgo(
    query: str,
    config: CrawlConfig,
    audit_trail: Any | None = None,
) -> list[SearchResult]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url, headers={"user-agent": config.user_agent or "AgentCrawl/0.1"}
    )
    html = _fetch_search_body(request, config, audit_trail).decode("utf-8", errors="replace")

    results: list[SearchResult] = []
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="(?P<href>.*?)".*?>(?P<title>.*?)</a>.*?'
        r'<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        href = unescape(match.group("href"))
        parsed = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg", [href])[0]
        title = _strip_tags(unescape(match.group("title")))
        snippet = _strip_tags(unescape(match.group("snippet")))
        results.append(SearchResult(title=title, url=target, snippet=snippet))
        if len(results) >= config.search_limit:
            break
    return results


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()
