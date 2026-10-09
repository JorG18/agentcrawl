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
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 5:
                # Free plans rate-limit; a 429 is our pacing, not the tool
                # failing the page.
                wait = exc.headers.get("retry-after", "")
                time.sleep(min(60.0, float(wait)) if wait.isdigit() else 10.0 * (attempt + 1))
                continue
            # The body explains the refusal (credits, blocked site); headers are
            # never echoed, so the key cannot leak through an error message.
            body = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {body}") from None
    raise RuntimeError("HTTP 429: still rate-limited after retries")


def _get_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


# Set by --keep-html: store the HTML AgentCrawl read for every page, so the
# extraction can be tuned offline against the other tools' output.
KEEP_HTML = False
HTML_CAP = 3_000_000


def run_agentcrawl(
    pages: list[dict[str, Any]],
    concurrency: int,
    tool: str = "agentcrawl",
    extra: dict[str, Any] | None = None,
) -> list[Result]:
    from agentcrawl import AgentCrawl

    # The page budget is the wall clock the report allows every local tool.
    crawler = AgentCrawl(
        {"timeout_ms": 30_000, "page_budget_ms": PAGE_TIMEOUT_S * 1000, **(extra or {})}
    )

    def one(page: dict[str, Any]) -> Result:
        started = time.perf_counter()
        try:
            doc = crawler.scrape(page["url"], formats=["markdown", "html"])
        except Exception as exc:  # the library should not raise; record it if it does
            return _result(
                page,
                tool,
                error=f"{type(exc).__name__}: {exc}"[:300],
                seconds=time.perf_counter() - started,
            )
        markdown = doc.get("markdown") or ""
        metadata = doc.get("metadata") or {}
        errors = doc.get("errors") or []
        diagnostics = {
            key: metadata.get(key)
            for key in (
                "raw_html_bytes",
                "javascript_required",
                "browser_render_error",
                "browser_render_no_gain",
                "browser_fallback_error",
                "fallback_reason",
                "network_idle_timeout",
                "challenge_waited_ms",
                "challenge_clicks",
                "challenge_attempts",
                "stealth_retry",
                "final_url",
            )
            if metadata.get(key) is not None
        }
        if KEEP_HTML or (len(markdown) < 300 and not errors):
            # Keep the page AgentCrawl saw, so an empty result can be
            # reproduced offline instead of guessed at.
            diagnostics["html"] = (doc.get("html") or "")[:HTML_CAP]
        return _result(
            page,
            tool,
            markdown=markdown,
            error=(errors[0][:300] if errors else None),
            error_type=metadata.get("error_type"),
            fetcher=metadata.get("fetcher"),
            seconds=time.perf_counter() - started,
            **diagnostics,
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
    # v2 API (the v1 markdownify endpoint rejects keys from the current
    # dashboard). "reader" mode is its main-content extraction, like
    # Firecrawl's onlyMainContent and Crawl4AI's fit_markdown.
    headers = {"SGAI-APIKEY": key}

    def call(url: str) -> dict[str, Any]:
        body = _post_json(
            "https://v2-api.scrapegraphai.com/api/scrape",
            {"url": url, "formats": [{"type": "markdown", "mode": "reader"}]},
            headers,
            PAGE_TIMEOUT_S * 2,
        )
        entry = (body.get("results") or {}).get("markdown") or {}
        data = entry.get("data") if isinstance(entry, dict) else entry
        if isinstance(data, list):
            data = "\n\n".join(str(part) for part in data)
        errors = body.get("errors") or {}
        warnings = (body.get("metadata") or {}).get("warnings") or []
        error = None
        if not data:
            error = str(errors.get("markdown") or errors or warnings or "no markdown")[:300]
        return {"markdown": data if isinstance(data, str) else "", "error": error}

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


def run_scrapling(pages: list[dict[str, Any]], concurrency: int) -> list[Result]:
    # Its strongest local mode, as an agent would pick it for a hard site:
    # Patchright with the Turnstile click, then the markdown its MCP returns.
    from scrapling.core.shell import Convertor
    from scrapling.fetchers import StealthyFetcher

    def call(url: str) -> dict[str, Any]:
        page = StealthyFetcher.fetch(
            url, headless=True, solve_cloudflare=True, timeout=PAGE_TIMEOUT_S * 1000
        )
        markdown = "".join(Convertor._extract_content(page, "markdown", main_content_only=True))
        return {"markdown": markdown, "status_code": page.status}

    return _run_threaded(pages, "scrapling", concurrency, call)


def run_agentcrawl_cloak(pages: list[dict[str, Any]], concurrency: int) -> list[Result]:
    """AgentCrawl with CloakBrowser's patched Chromium as the browser.

    Spike only: the launcher is swapped here, not in the engine. The context
    keeps the binary's own user agent, which matches its (Windows) fingerprint;
    AgentCrawl's Linux one would contradict it.
    """
    from cloakbrowser.browser import build_args
    from cloakbrowser.config import IGNORE_DEFAULT_ARGS
    from cloakbrowser.download import ensure_binary

    from agentcrawl import fetchers

    binary = ensure_binary()

    def launch(playwright: Any, launch_kwargs: dict[str, Any]) -> Any:
        launch_kwargs = {k: v for k, v in launch_kwargs.items() if k != "engine"}
        headless = launch_kwargs.pop("headless", True)
        return playwright.chromium.launch(
            executable_path=binary,
            headless=headless,
            args=build_args(True, launch_kwargs.pop("args", None), headless=headless),
            ignore_default_args=IGNORE_DEFAULT_ARGS,
            **launch_kwargs,
        )

    fetchers._launch_chromium = launch
    fetchers._browser_user_agent = lambda config, browser: None
    return run_agentcrawl(pages, concurrency, "agentcrawl-cloak")


def run_agentcrawl_chrometls(pages: list[dict[str, Any]], concurrency: int) -> list[Result]:
    """AgentCrawl whose HTTP tier speaks Chrome's TLS and headers (curl_cffi).

    Spike only: ``_safe_urlopen`` is swapped for curl_cffi with
    ``impersonate="chrome"`` behind a urllib-shaped adapter, so the rest of the
    HTTP path (bounded read, decoding, browser fallback) is unchanged. The
    target is still checked by the SSRF guard; redirect hops are not (the
    final URL is, by ``_fetch_http``).
    """
    import email.message
    import io
    import urllib.error

    from curl_cffi import requests as curl_requests

    from agentcrawl import fetchers
    from agentcrawl.security import validate_remote_url

    class Response(io.BytesIO):
        def __init__(self, reply: Any) -> None:
            super().__init__(reply.content)
            self.status = reply.status_code
            self._url = str(reply.url)
            self.headers = email.message.Message()
            for name, value in reply.headers.items():
                # curl_cffi already inflated the body.
                if name.lower() not in {"content-encoding", "content-length"}:
                    self.headers[name] = value

        def geturl(self) -> str:
            return self._url

    def urlopen(request: Any, timeout: float, **kwargs: Any) -> Response:
        validate_remote_url(
            request.full_url, allow_private_network=kwargs.get("allow_private_network", False)
        )
        conditional = {k: v for k, v in request.header_items() if k.lower().startswith("if-")}
        reply = curl_requests.get(
            request.full_url,
            impersonate="chrome",
            headers=conditional,
            timeout=timeout,
            allow_redirects=True,
            max_redirects=10,
        )
        response = Response(reply)
        if reply.status_code >= 400:
            raise urllib.error.HTTPError(
                response.geturl(), reply.status_code, reply.reason or "", response.headers, response
            )
        return response

    fetchers._safe_urlopen = urlopen
    return run_agentcrawl(pages, concurrency, "agentcrawl-chrometls")


TOOLS = {
    "agentcrawl": (None, run_agentcrawl),
    # Same engine, Camofox (hardened Firefox) as the browser fallback; needs a
    # Camofox server (AGENTCRAWL_CAMOFOX_URL, default http://127.0.0.1:9377).
    # Same engine, the browser launched with a window (run it under xvfb-run).
    "agentcrawl-headful": (
        None,
        lambda pages, concurrency: run_agentcrawl(
            pages, concurrency, "agentcrawl-headful", {"headless": False}
        ),
    ),
    "agentcrawl-cloak": (None, run_agentcrawl_cloak),
    "agentcrawl-chrometls": (None, run_agentcrawl_chrometls),
    "agentcrawl-camofox": (
        None,
        lambda pages, concurrency: run_agentcrawl(
            pages,
            concurrency,
            "agentcrawl-camofox",
            {
                "browser_backend": "camofox",
                "camofox_base_url": os.environ.get(
                    "AGENTCRAWL_CAMOFOX_URL", "http://127.0.0.1:9377"
                ),
            },
        ),
    ),
    "crawl4ai": (None, run_crawl4ai),
    "scrapling": (None, run_scrapling),
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
    parser.add_argument(
        "--keep-html", action="store_true", help="AgentCrawl: store the HTML of every page."
    )
    args = parser.parse_args(argv)
    global KEEP_HTML
    KEEP_HTML = args.keep_html

    pages = json.loads(Path(args.sample).read_text("utf-8"))["pages"]
    left_out: list[dict[str, Any]] = []
    if args.limit:
        pages, left_out = pages[: args.limit], pages[args.limit :]
    env_key, runner = TOOLS[args.tool]
    started = time.time()
    if env_key is None:
        results = runner(pages, args.concurrency)
    elif not os.environ.get(env_key):
        results = [_result(page, args.tool, skipped=f"{env_key} not set") for page in pages]
    else:
        results = runner(pages, args.concurrency, os.environ[env_key])
    # Pages beyond --limit are recorded as not run, not as failures.
    results += [_result(page, args.tool, skipped="beyond --limit") for page in left_out]
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
