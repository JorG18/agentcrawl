from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from html import unescape

from .config import CrawlConfig
from .models import SearchResult


def search_web(query: str, config: CrawlConfig) -> list[SearchResult]:
    if config.search_engine == "serper":
        return _search_serper(query, config)
    if config.search_engine == "none":
        return []
    return _search_duckduckgo(query, config)


def _search_serper(query: str, config: CrawlConfig) -> list[SearchResult]:
    api_key = config.serper_api_key or os.getenv("SERPER_API_KEY")
    if not api_key:
        raise ValueError("Serper search requires config['serper_api_key'] or SERPER_API_KEY.")
    request = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": config.search_limit}).encode("utf-8"),
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = []
    for item in payload.get("organic", [])[: config.search_limit]:
        if item.get("link"):
            results.append(
                SearchResult(
                    title=item.get("title", ""), url=item["link"], snippet=item.get("snippet", "")
                )
            )
    return results


def _search_duckduckgo(query: str, config: CrawlConfig) -> list[SearchResult]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url, headers={"user-agent": config.user_agent or "AgentCrawl/0.1"}
    )
    with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
        html = response.read().decode("utf-8", errors="replace")

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
