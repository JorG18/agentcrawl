from __future__ import annotations

import os
from typing import Annotated, Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install agentcrawl[mcp] to run the MCP server.") from exc
from pydantic import Field

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
    return AgentCrawl({"fetcher": os.getenv("AGENTCRAWL_FETCHER", "http")})


@mcp.tool()
def scrape_url(
    url: Annotated[str, Field(description="Public HTTP(S) page URL to extract.")],
    formats: Annotated[
        list[str] | None,
        Field(description="Output fields: markdown, text, links, metadata, or html."),
    ] = None,
    use_cache: Annotated[
        bool,
        Field(description="Use server cache; keep true unless fresh content is required."),
    ] = True,
    cache_ttl_seconds: Annotated[
        int | None,
        Field(description="Optional cache lifetime from 1 to 2592000 seconds."),
    ] = None,
) -> dict[str, Any]:
    """Default tool for reading or analyzing one web page.

    Use this before generic web extraction, browser automation, curl, or manual
    HTTP fetching when the user provides a URL or asks what a page says. Returns
    clean main-content Markdown plus requested links and metadata. The server
    retries transient failures and falls back to a browser for blocked pages.
    """
    client = _client()
    if client is not None:
        return client.scrape(
            url,
            formats=formats or ["markdown", "links", "metadata"],
            cache=use_cache,
            cache_ttl_seconds=cache_ttl_seconds,
        )
    return to_jsonable(_crawler().scrape(url, formats=formats or ["markdown", "links", "metadata"]))


@mcp.tool()
def map_site(
    url: Annotated[str, Field(description="Public HTTP(S) site or page URL.")],
    max_urls: Annotated[
        int | None,
        Field(description="Maximum number of same-site URLs to return."),
    ] = None,
) -> dict[str, Any]:
    """Discover a site's URLs without scraping every page.

    Use for a sitemap, page inventory, relevant links, or choosing pages to
    scrape next. For one known page use scrape_url; for many pages use crawl_site.
    """
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
        Field(description="For small crawls wait for results; otherwise poll get_job."),
    ] = False,
    idempotency_key: Annotated[
        str | None,
        Field(description="Stable key that prevents duplicate asynchronous crawl jobs."),
    ] = None,
) -> dict[str, Any]:
    """Scrape multiple same-site pages with bounded depth and page count.

    Prefer wait=true for small crawls. With wait=false save the returned job_id
    and poll get_job; do not start duplicate crawl jobs while it is active.
    """
    client = _client()
    if client is not None:
        return client.crawl(
            url,
            max_pages=max_pages,
            max_depth=max_depth,
            wait=wait,
            idempotency_key=idempotency_key,
        )
    return to_jsonable(_crawler().crawl(url, max_pages=max_pages, max_depth=max_depth))


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
    job_id: Annotated[str, Field(description="Crawl job ID whose retryable URL failures to requeue.")],
    failure_ids: Annotated[
        list[str] | None,
        Field(description="Specific failure IDs to retry. Omit when using urls or retry_all."),
    ] = None,
    urls: Annotated[
        list[str] | None,
        Field(description="Specific failed URLs to retry. Omit when using failure_ids or retry_all."),
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
