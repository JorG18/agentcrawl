from __future__ import annotations

import os
from typing import Annotated, Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install agentcrawl[mcp] to run the MCP server.") from exc
from pydantic import Field

from .config import config_from_env
from .crawler import AgentCrawl
from .remote_client import AgentCrawlClient
from .serializers import to_jsonable

mcp = FastMCP("agentcrawl")


def _client() -> AgentCrawlClient | None:
    base_url = os.getenv("AGENTCRAWL_BASE_URL")
    if not base_url:
        return None
    return AgentCrawlClient(
        base_url=base_url,
        api_key=os.getenv("AGENTCRAWL_API_KEY"),
        timeout=int(os.getenv("AGENTCRAWL_MCP_TIMEOUT", "120")),
    )


def _crawler() -> AgentCrawl:
    """Local-mode engine, configured from the documented ``AGENTCRAWL_*`` vars.

    MCP is the agent-facing entrance, so the privacy switches (airgap, audit,
    private network) must be reachable here. The mapping lives in
    ``config.config_from_env`` and is shared with the CLI's local mode.
    """
    return AgentCrawl(config_from_env())


@mcp.tool()
def scrape_url(
    url: Annotated[str, Field(description="Public HTTP(S) page URL to extract.")],
    formats: Annotated[
        list[str] | None,
        Field(
            description="Any of: markdown, text, links, metadata, html, chunks (citable pieces), screenshot (PNG, needs a browser)."
        ),
    ] = None,
    use_cache: Annotated[
        bool,
        Field(
            description="Server mode: use the cache. Keep true unless fresh content is required."
        ),
    ] = True,
    cache_ttl_seconds: Annotated[
        int | None,
        Field(description="Server mode: cache lifetime in seconds."),
    ] = None,
    only_main_content: Annotated[
        bool | None,
        Field(description="True: main content only. False: whole page."),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="What you are looking for; long pages keep the most relevant passages."),
    ] = None,
    browser_actions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "Browser steps before reading (max 25): {type: click|type|press|scroll|"
                "scroll_to_end|virtual_scroll|wait|wait_for, selector?, text?, key?, "
                "times?, max_scrolls?, ms?}. Only for content that needs interaction."
            )
        ),
    ] = None,
    session: Annotated[
        str | None,
        Field(description="Local mode: saved login name (agentcrawl login) for private pages."),
    ] = None,
) -> dict[str, Any]:
    """Read one web page as clean Markdown. Use it whenever you have a URL.

    Retries transient failures and renders JavaScript pages in a local browser
    when one is installed. A failure says why in metadata.error_type
    (client_challenge, blocked, not_found, timeout, network_error...).
    """
    overrides: dict[str, Any] = {}
    if browser_actions:
        from .browser_actions import validate_actions

        try:
            overrides["browser_actions"] = list(validate_actions(browser_actions))
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
    if session:
        from .sessions import validate_session_name

        try:
            overrides["browser_session"] = validate_session_name(session)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
    client = _client()
    if client is not None:
        return client.scrape(
            url,
            formats=formats or ["markdown", "links", "metadata"],
            config=overrides or None,
            cache=use_cache,
            cache_ttl_seconds=cache_ttl_seconds,
            only_main_content=only_main_content,
            query=query,
        )
    crawler = AgentCrawl({**config_from_env(), **overrides}) if overrides else _crawler()
    return to_jsonable(
        crawler.scrape(
            url,
            formats=formats or ["markdown", "links", "metadata"],
            only_main_content=only_main_content,
            query=query,
        )
    )


@mcp.tool()
def scrape_many(
    urls: Annotated[
        list[str],
        Field(description="Public HTTP(S) page URLs to extract, 1 to 100.", min_length=1),
    ],
    formats: Annotated[
        list[str] | None,
        Field(description="Any of: markdown, text, links, metadata, html, chunks."),
    ] = None,
    only_main_content: Annotated[
        bool | None,
        Field(description="True: main content only. False: whole page."),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="What you are looking for; long pages keep the most relevant passages."),
    ] = None,
) -> dict[str, Any]:
    """Read several known URLs in one call, in order; each page fails on its own."""
    if len(urls) > 100:
        return {"success": False, "error": "scrape_many accepts at most 100 URLs per call."}
    client = _client()
    if client is not None:
        return client.scrape_many(
            urls,
            formats=formats or ["markdown", "links", "metadata"],
            only_main_content=only_main_content,
            query=query,
        )
    documents = _crawler().scrape_many(
        urls,
        formats=formats or ["markdown", "links", "metadata"],
        only_main_content=only_main_content,
        query=query,
    )
    items = [
        {"url": url, "success": not document.get("errors"), "data": document}
        for url, document in zip(urls, (to_jsonable(doc) for doc in documents))
    ]
    return {
        "success": all(item["success"] for item in items),
        "data": items,
        "summary": {
            "total": len(items),
            "succeeded": sum(1 for item in items if item["success"]),
            "failed": sum(1 for item in items if not item["success"]),
        },
    }


@mcp.tool()
def search_web(
    query: Annotated[str, Field(description="What to search the web for.", min_length=1)],
    limit: Annotated[
        int,
        Field(description="Number of results, 1 to 20.", ge=1, le=20),
    ] = 5,
    scrape: Annotated[
        bool,
        Field(description="Also read each result page. False: titles, URLs and snippets only."),
    ] = True,
    only_main_content: Annotated[
        bool | None,
        Field(description="True: main content only. False: whole page."),
    ] = None,
) -> dict[str, Any]:
    """Search the web and read the top results (for questions without a URL).

    Each result has title, URL, snippet and, with scrape=true, the passages that
    best match the query, ready to cite.
    """
    client = _client()
    if client is not None:
        return client.search(query, limit=limit, scrape=scrape, only_main_content=only_main_content)
    try:
        payload = _crawler().search(
            query, limit=limit, scrape=scrape, only_main_content=only_main_content
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": all(item.get("success", True) for item in payload["results"]),
        "data": payload,
    }


@mcp.tool()
def check_changes(
    url: Annotated[str, Field(description="Public HTTP(S) page URL to re-read.")],
    previous_markdown: Annotated[
        str | None,
        Field(description="Markdown from your earlier read of this page, to get a diff."),
    ] = None,
    previous_sha256: Annotated[
        str | None,
        Field(description="metadata.markdown_sha256 from the earlier read, if you kept only that."),
    ] = None,
    etag: Annotated[
        str | None, Field(description="metadata.etag from the earlier read (enables a 304).")
    ] = None,
    last_modified: Annotated[
        str | None, Field(description="metadata.last_modified from the earlier read.")
    ] = None,
) -> dict[str, Any]:
    """Check whether a page changed since you last read it, and what changed.

    Returns changed true/false, a unified diff when previous_markdown is given,
    and the current document when it changed. Cheaper than re-reading and
    comparing yourself; with etag/last_modified an unchanged page may not even
    send its body. Runs on the local engine.
    """
    metadata = {
        key: value
        for key, value in {
            "markdown_sha256": previous_sha256,
            "etag": etag,
            "last_modified": last_modified,
        }.items()
        if value
    }
    previous = {"markdown": previous_markdown or "", "metadata": metadata}
    has_previous = bool(previous_markdown or metadata)
    return _crawler().diff(url, previous if has_previous else None)


@mcp.tool()
def extract_structured(
    url: Annotated[str, Field(description="Public HTTP(S) page URL to extract from.")],
    schema: Annotated[
        dict[str, Any],
        Field(
            description=(
                "CSS extraction schema: {baseSelector?, fields: [{name, selector?, "
                "type: text|attribute|html|regex|nested|list, attribute?, pattern?, "
                "multiple?, transform?: strip|lower|upper|number|url, fields?}]}."
            )
        ),
    ],
) -> dict[str, Any]:
    """Extract JSON from a page with CSS selectors, no LLM.

    Best for repeated structures (listings, products, tables): write the schema
    once, reuse it. baseSelector returns a list, otherwise one object.
    """
    from .css_extract import validate_css_schema

    try:
        validate_css_schema(schema)
    except ValueError as exc:
        return {"success": False, "error": f"invalid schema: {exc}"}
    client = _client()
    if client is not None:
        return client.extract_css(url, schema)
    result = _crawler().extract_css(url, schema)
    return {"success": not result["errors"], "data": result}


@mcp.tool()
def map_site(
    url: Annotated[str, Field(description="Public HTTP(S) site or page URL.")],
    max_urls: Annotated[
        int | None,
        Field(description="Maximum number of same-site URLs to return."),
    ] = None,
) -> dict[str, Any]:
    """List a site's URLs (sitemap, llms.txt, links) without reading every page."""
    client = _client()
    if client is not None:
        return client.map(url, max_urls=max_urls)
    return to_jsonable(_crawler().map(url, max_urls=max_urls))


@mcp.tool()
def crawl_site(
    url: Annotated[str, Field(description="Public HTTP(S) starting URL.")],
    max_pages: Annotated[
        int | None,
        Field(description="Maximum pages to scrape. Set a bounded value."),
    ] = None,
    max_depth: Annotated[
        int | None,
        Field(description="Maximum link depth from the starting URL."),
    ] = None,
    wait: Annotated[
        bool,
        Field(description="Server mode: wait for small crawls; otherwise poll get_job."),
    ] = False,
    idempotency_key: Annotated[
        str | None,
        Field(description="Server mode: key that prevents duplicate crawl jobs."),
    ] = None,
    query: Annotated[
        str | None,
        Field(
            description="What you are looking for; follows relevant links first and stops when pages stop matching."
        ),
    ] = None,
) -> dict[str, Any]:
    """Read several pages of one site by following links (bounded pages and depth).

    Locally it returns the documents. In server mode it may return a job_id to
    poll with get_job.
    """
    client = _client()
    if client is not None:
        return client.crawl(
            url,
            max_pages=max_pages,
            max_depth=max_depth,
            wait=wait,
            idempotency_key=idempotency_key,
            **({"query": query} if query else {}),
        )
    return to_jsonable(_crawler().crawl(url, max_pages=max_pages, max_depth=max_depth, query=query))


@mcp.tool()
def get_job(
    job_id: Annotated[str, Field(description="Job ID returned by crawl_site.")],
    offset: Annotated[
        int,
        Field(description="Zero-based document offset for completed crawl results."),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="Documents to return, from 1 to 500."),
    ] = 100,
) -> dict[str, Any]:
    """Check one crawl job and retrieve progress or final documents.

    Poll the same job_id. A queued job may be waiting for a persisted retry;
    keep polling it instead of starting another crawl. Stop on completed, failed,
    or cancelled. Use offset and limit to read large result sets page by page.
    """
    client = _client()
    if client is None:
        return {"error": "Job polling requires remote API mode via AGENTCRAWL_BASE_URL."}
    return client.job(job_id, offset=offset, limit=limit)


@mcp.tool()
def job_events(
    job_id: Annotated[str, Field(description="Crawl job ID to inspect.")],
    event_type: Annotated[
        str | None,
        Field(description="Optional event filter such as retry_scheduled or completed."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum events to return, from 1 to 500.")] = 100,
) -> dict[str, Any]:
    """Inspect one crawl job's event history for queueing, retries, yields, and completion."""
    client = _client()
    if client is None:
        return {"error": "Job events require remote API mode via AGENTCRAWL_BASE_URL."}
    return client.job_events(job_id, event_type=event_type, limit=limit)


@mcp.tool()
def cancel_job(
    job_id: Annotated[str, Field(description="Running or queued crawl job ID.")],
) -> dict[str, Any]:
    """Cancel one queued or running crawl job when it is no longer needed."""
    client = _client()
    if client is None:
        return {"error": "Job cancellation requires remote API mode via AGENTCRAWL_BASE_URL."}
    return client.cancel_job(job_id)


@mcp.tool()
def inspect_failures(
    job_id: Annotated[
        str | None,
        Field(description="Optional crawl job ID. Omit to inspect open failures across jobs."),
    ] = None,
    retryable_only: Annotated[
        bool,
        Field(description="When true, return only failures that can be retried."),
    ] = False,
    error_type: Annotated[
        str | None,
        Field(description="Optional error class filter such as timeout or rate_limited."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum failures to return, from 1 to 500.")] = 100,
) -> dict[str, Any]:
    """Inspect terminal crawl URL failures for debugging and selective retries."""
    client = _client()
    if client is None:
        return {"error": "Failure inspection requires remote API mode via AGENTCRAWL_BASE_URL."}
    if job_id:
        return client.job_failures(
            job_id,
            retryable=True if retryable_only else None,
            error_type=error_type,
            limit=limit,
        )
    return client.failures(
        retryable=True if retryable_only else None,
        error_type=error_type,
        limit=limit,
    )


@mcp.tool()
def retry_failures(
    job_id: Annotated[
        str, Field(description="Crawl job ID whose retryable URL failures to requeue.")
    ],
    failure_ids: Annotated[
        list[str] | None,
        Field(description="Specific failure IDs to retry. Omit when using urls or retry_all."),
    ] = None,
    urls: Annotated[
        list[str] | None,
        Field(
            description="Specific failed URLs to retry. Omit when using failure_ids or retry_all."
        ),
    ] = None,
    retry_all: Annotated[
        bool,
        Field(description="Retry all open retryable failures for the job."),
    ] = False,
) -> dict[str, Any]:
    """Requeue one, selected, or all retryable crawl URL failures without duplicating documents."""
    client = _client()
    if client is None:
        return {"error": "Failure retry requires remote API mode via AGENTCRAWL_BASE_URL."}
    return client.retry_failures(
        job_id,
        failure_ids=failure_ids,
        urls=urls,
        retry_all=retry_all,
    )


@mcp.tool()
def usage() -> dict[str, Any]:
    """Return AgentCrawl usage counters. This is an operator tool, not scraping."""
    client = _client()
    if client is None:
        return {"mode": "local", "usage_tracking": False}
    return client.usage()


@mcp.tool()
def cache_stats() -> dict[str, Any]:
    """Return AgentCrawl cache, job, and service statistics for diagnostics."""
    client = _client()
    if client is None:
        return {"mode": "local", "cache": False, "jobs": False}
    return client.stats()


@mcp.tool()
def clear_cache(
    domain: Annotated[
        str | None,
        Field(description="Optional domain whose cached pages should be deleted."),
    ] = None,
    url: Annotated[
        str | None,
        Field(description="Optional exact URL whose cached result should be deleted."),
    ] = None,
) -> dict[str, Any]:
    """Delete cached scrape results for maintenance or forced freshness.

    Provide one domain or exact URL. With neither filter this clears all cache,
    so do that only when the user explicitly requests it.
    """
    client = _client()
    if client is None:
        return {"mode": "local", "cleared": 0, "cache": False}
    return client.clear_cache(domain=domain, url=url)


# What an agent needs to read the web. Every tool schema is sent to the model
# on each turn, so the operator tools (usage, cache, job history, retries,
# change checks) cost context in every conversation where nobody uses them.
CORE_TOOLS = frozenset(
    {
        "scrape_url",
        "scrape_many",
        "search_web",
        "map_site",
        "crawl_site",
        "get_job",
        "extract_structured",
    }
)
PROFILES = ("core", "full")


def apply_profile(profile: str | None = None) -> list[str]:
    """Keep only the tools of ``profile``; return the names removed.

    ``core`` (default) keeps :data:`CORE_TOOLS`, minus ``get_job`` without a
    server and ``search_web`` without a search engine; ``full`` keeps everything.
    Set ``AGENTCRAWL_MCP_PROFILE=full`` to expose the operator tools.
    """
    selected = (profile or os.getenv("AGENTCRAWL_MCP_PROFILE") or "core").strip().lower()
    if selected not in PROFILES:
        raise ValueError(
            f"Unknown AGENTCRAWL_MCP_PROFILE {selected!r}; use one of: {', '.join(PROFILES)}"
        )
    if selected == "full":
        return []
    keep = set(CORE_TOOLS)
    if not os.getenv("AGENTCRAWL_BASE_URL"):
        keep.discard("get_job")  # the local engine runs crawls inline, no jobs
    search_engine = (os.getenv("AGENTCRAWL_SEARCH_ENGINE") or "none").strip().lower()
    if search_engine == "none" and not os.getenv("AGENTCRAWL_BASE_URL"):
        keep.discard("search_web")  # would only ever answer "search is off"
    removed = []
    for tool in list(mcp._tool_manager.list_tools()):
        if tool.name not in keep:
            mcp.remove_tool(tool.name)
            removed.append(tool.name)
    return removed


def main() -> None:
    apply_profile()
    mcp.run()


if __name__ == "__main__":
    main()
