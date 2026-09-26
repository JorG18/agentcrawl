"""Scrape every page of a sample with one tool and record what came back.

Each tool gets the URL and does its own fetching, as a user of that tool
would: AgentCrawl and Crawl4AI run locally (with a local browser), Firecrawl,
ScrapeGraphAI and Tavily are hosted APIs called with their own keys. API keys
are read from the environment only and never written anywhere; a tool whose
key is missing is recorded as skipped.

Usage::

    python -m benchmarks.web.run --tool agentcrawl --sample sample.json --out agentcrawl.jsonl.gz
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

PAGE_TIMEOUT_S = 60
Result = dict[str, Any]


def _result(page: dict[str, Any], tool: str, **fields: Any) -> Result:
    return {
        "id": page["id"],
        "url": page["url"],
        "tool": tool,
        "markdown": "",
        "error": None,
        **fields,
    }


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The body explains the refusal (credits, blocked site); headers are
        # never echoed, so the key cannot leak through an error message.
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from None


def _get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def run_agentcrawl(pages: list[dict[str, Any]], concurrency: int) -> list[Result]:
    from agentcrawl import AgentCrawl

    crawler = AgentCrawl({"timeout_ms": 30_000})

    def one(page: dict[str, Any]) -> Result:
        started = time.perf_counter()
        try:
            doc = crawler.scrape(page["url"])
        except Exception as exc:  # the library should not raise; record it if it does
            return _result(
                page,
                "agentcrawl",
                error=f"{type(exc).__name__}: {exc}"[:300],
                seconds=time.perf_counter() - started,
            )
        return _result(
            page,
            "agentcrawl",
            markdown=doc.markdown or "",
            error=(doc.errors[0][:300] if doc.errors else None),
            error_type=doc.metadata.get("error_type"),
            fetcher=doc.metadata.get("fetcher"),
            seconds=time.perf_counter() - started,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(one, pages))


def run_crawl4ai(pages: list[dict[str, Any]], concurrency: int) -> list[Result]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    try:
        from crawl4ai.content_filter_strategy import PruningContentFilterLXML as PruningFilter
    except ImportError:
        from crawl4ai.content_filter_strategy import PruningContentFilter as PruningFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningFilter()),
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT_S * 1000,
        verbose=False,
    )

    async def main() -> list[Result]:
        semaphore = asyncio.Semaphore(concurrency)
        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:

            async def one(page: dict[str, Any]) -> Result:
                async with semaphore:
                    started = time.perf_counter()
                    try:
                        result = await asyncio.wait_for(
                            crawler.arun(url=page["url"], config=config), PAGE_TIMEOUT_S + 15
                        )
                    except Exception as exc:
                        return _result(
                            page,
                            "crawl4ai",
                            error=f"{type(exc).__name__}: {exc}"[:300],
                            seconds=time.perf_counter() - started,
                        )
                    markdown = result.markdown
                    fit = getattr(markdown, "fit_markdown", "") if markdown is not None else ""
                    raw = getattr(markdown, "raw_markdown", "") if markdown is not None else ""
                    return _result(
                        page,
                        "crawl4ai",
                        markdown=fit or "",
                        raw_chars=len(raw or ""),
                        status_code=getattr(result, "status_code", None),
                        error=None if result.success else (result.error_message or "failed")[:300],
                        seconds=time.perf_counter() - started,
                    )

            return list(await asyncio.gather(*(one(page) for page in pages)))

    return asyncio.run(main())


def _run_threaded(
    pages: list[dict[str, Any]], tool: str, concurrency: int, call: Callable[[str], dict[str, Any]]
) -> list[Result]:
    def one(page: dict[str, Any]) -> Result:
        started = time.perf_counter()
        try:
            fields = call(page["url"])
        except Exception as exc:
            return _result(
                page,
                tool,
                error=f"{type(exc).__name__}: {exc}"[:300],
                seconds=time.perf_counter() - started,
            )
        return _result(page, tool, seconds=time.perf_counter() - started, **fields)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(one, pages))


def run_firecrawl(pages: list[dict[str, Any]], concurrency: int, key: str) -> list[Result]:
    headers = {"authorization": f"Bearer {key}"}

    def call(url: str) -> dict[str, Any]:
        body = _post_json(
            "https://api.firecrawl.dev/v2/scrape",
            {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "timeout": PAGE_TIMEOUT_S * 1000,
            },
            headers,
            PAGE_TIMEOUT_S + 30,
        )
        data = body.get("data") or {}
        metadata = data.get("metadata") or {}
        error = None if body.get("success") else str(body.get("error") or "failed")[:300]
        return {
            "markdown": data.get("markdown") or "",
            "error": error,
            "status_code": metadata.get("statusCode"),
        }

    return _run_threaded(pages, "firecrawl", concurrency, call)


def run_scrapegraph(pages: list[dict[str, Any]], concurrency: int, key: str) -> list[Result]:
    base = "https://api.scrapegraphai.com/v1/markdownify"
    headers = {"SGAI-APIKEY": key}

    def call(url: str) -> dict[str, Any]:
        body = _post_json(base, {"website_url": url}, headers, PAGE_TIMEOUT_S + 30)
        deadline = time.monotonic() + PAGE_TIMEOUT_S * 2
        while body.get("status") not in {"completed", "failed"} and body.get("request_id"):
            if time.monotonic() > deadline:
                return {"error": "timed out waiting for the result"}
            time.sleep(3)
            body = _get_json(f"{base}/{body['request_id']}", headers, 30)
        markdown = body.get("result") or body.get("content") or ""
        error = body.get("error") or (None if body.get("status") == "completed" else "failed")
        return {
            "markdown": markdown if isinstance(markdown, str) else "",
            "error": str(error)[:300] if error else None,
        }

    return _run_threaded(pages, "scrapegraph", concurrency, call)


def run_tavily(pages: list[dict[str, Any]], concurrency: int, key: str) -> list[Result]:
    headers = {"authorization": f"Bearer {key}"}
    batches = [pages[i : i + 20] for i in range(0, len(pages), 20)]

    def batch(chunk: list[dict[str, Any]]) -> list[Result]:
        started = time.perf_counter()
        try:
            body = _post_json(
                "https://api.tavily.com/extract",
                {
                    "urls": [page["url"] for page in chunk],
                    "format": "markdown",
                    "extract_depth": "basic",
                },
                headers,
                PAGE_TIMEOUT_S * 2,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
            return [_result(page, "tavily", error=error, seconds=None) for page in chunk]
        elapsed = (time.perf_counter() - started) / max(1, len(chunk))
        found = {item.get("url"): item for item in body.get("results", [])}
        failed = {item.get("url"): item for item in body.get("failed_results", [])}
        out = []
        for page in chunk:
            item = found.get(page["url"])
            if item is not None:
                out.append(
                    _result(page, "tavily", markdown=item.get("raw_content") or "", seconds=elapsed)
                )
            else:
                reason = (failed.get(page["url"]) or {}).get("error") or "not returned"
                out.append(_result(page, "tavily", error=str(reason)[:300], seconds=elapsed))
        return out

    with ThreadPoolExecutor(max_workers=max(1, concurrency // 4)) as pool:
        return [result for chunk in pool.map(batch, batches) for result in chunk]


TOOLS = {
    "agentcrawl": (None, run_agentcrawl),
    "crawl4ai": (None, run_crawl4ai),
    "firecrawl": ("FIRECRAWL_API_KEY", run_firecrawl),
    "scrapegraph": ("SCRAPEGRAPH_API_KEY", run_scrapegraph),
    "tavily": ("TAVILY_API_KEY", run_tavily),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("--sample", default="sample.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0, help="Only the first N pages.")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args(argv)

    pages = json.loads(Path(args.sample).read_text("utf-8"))["pages"]
    if args.limit:
        pages = pages[: args.limit]
    env_key, runner = TOOLS[args.tool]
    started = time.time()
    if env_key is None:
        results = runner(pages, args.concurrency)
    elif not os.environ.get(env_key):
        results = [_result(page, args.tool, skipped=f"{env_key} not set") for page in pages]
    else:
        results = runner(pages, args.concurrency, os.environ[env_key])
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    got = sum(1 for result in results if result.get("markdown"))
    print(
        f"{args.tool}: {got}/{len(results)} pages with output in {time.time() - started:.0f}s",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
