"""Firecrawl v2-compatible endpoints over AgentCrawl's own engine.

Code written for Firecrawl's v2 API (its SDKs, its MCP server, tutorials)
works against an AgentCrawl server by changing the base URL. Only the shape of
the requests and responses follows Firecrawl's public API reference; every
request goes through the ``/v1`` handlers, so authentication, SSRF checks,
cache, politeness, rate limits and usage are the same, and nothing is sent to
Firecrawl.

Options AgentCrawl cannot honour are refused with ``400`` and the reason,
never silently dropped. Two are accepted and deliberately not honoured:
``skipTlsVerification`` (certificates are always verified) and ``mobile:
false``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse

from . import server as v1

router = APIRouter(prefix="/v2")

_FORMATS = {
    "markdown": "markdown",
    "html": "html",
    "rawHtml": "html",
    "links": "links",
    "summary": "summary",  # needs AGENTCRAWL_LLM_MODEL on the server
}
# Accepted, with no effect on the result here.
_IGNORED = {
    "origin",
    "integration",
    "removeBase64Images",
    "blockAds",
    "fastMode",
    "storeInCache",
    "parsers",
    "skipTlsVerification",
    "zeroDataRetention",
}
_ENHANCED = "needs AgentCrawl Enhanced (managed proxies and browsers)"
# Crawl options the SDK always sends; only these values match what AgentCrawl
# does (it normalises URLs itself and always follows robots.txt).
_CRAWL_DEFAULTS = {
    "allowExternalLinks": False,
    "allowSubdomains": False,
    "crawlEntireDomain": False,
    "ignoreRobotsTxt": False,
    "ignoreQueryParameters": False,
    "regexOnFullURL": False,
    "deduplicateSimilarURLs": True,
}
_PAGE = 100  # documents per status response, then ``next``
_JOB_STATUS = {
    "queued": "scraping",
    "running": "scraping",
    "cancelling": "scraping",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _refuse(reason: str) -> HTTPException:
    return HTTPException(status_code=400, detail=reason)


def _formats(raw: Any) -> list[str]:
    formats: list[str] = []
    for item in raw or ["markdown"]:
        name = item.get("type") if isinstance(item, dict) else item
        if name == "screenshot":
            formats.append("screenshot")
        elif name in _FORMATS:
            formats.append(_FORMATS[name])
        else:
            raise _refuse(f"format {name!r} is not supported by AgentCrawl")
    return [*dict.fromkeys(formats), "metadata"]


def _actions(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    focused: str | None = None
    for action in raw:
        kind = action.get("type")
        if kind == "wait" and "selector" in action:
            steps.append({"type": "wait_for", "selector": action["selector"]})
        elif kind == "wait":
            steps.append({"type": "wait", "ms": int(action.get("milliseconds") or 1000)})
        elif kind == "click":
            focused = action.get("selector")
            steps.append({"type": "click", "selector": focused})
        elif kind == "write":
            if not focused:
                raise _refuse("a write action needs a click on the field before it")
            steps.append({"type": "type", "selector": focused, "text": action.get("text", "")})
        elif kind == "press":
            steps.append({"type": "press", "key": action.get("key", "")})
        elif kind == "scroll":
            if action.get("direction", "down") != "down":
                raise _refuse("only downward scroll actions are supported")
            steps.append({"type": "scroll"})
        else:
            raise _refuse(f"action {kind!r} is not supported by AgentCrawl")
    return steps


def _scrape_fields(body: dict[str, Any], *, skip: set[str] = frozenset()) -> dict[str, Any]:
    """Firecrawl scrape options -> keyword arguments of the v1 request models."""
    config: dict[str, Any] = {}
    fields: dict[str, Any] = {"formats": _formats(body.get("formats"))}
    for key, value in body.items():
        if key in skip or key in _IGNORED or key == "formats":
            continue
        if key == "onlyMainContent":
            fields["only_main_content"] = bool(value)
        elif key == "maxAge":
            fields["cache"] = bool(value)
        elif key == "timeout":
            config["page_budget_ms"] = int(value)
        elif key == "waitFor":
            if value:
                config.update(fetcher="playwright", browser_wait_ms=int(value))
        elif key == "actions":
            if value:
                config["fetcher"] = "playwright"
                config["browser_actions"] = _actions(value)
        elif key == "proxy":
            if value not in (None, "basic", "auto"):
                raise _refuse(f"proxy {value!r} {_ENHANCED}")
        elif key == "location":
            if value:
                raise _refuse(f"location {_ENHANCED}")
        elif key == "mobile":
            if value:
                raise _refuse("mobile emulation is not supported by AgentCrawl")
        else:
            raise _refuse(f"option {key!r} is not supported by AgentCrawl")
    if "screenshot" in fields["formats"]:
        config["fetcher"] = "playwright"
    if config:
        fields["config"] = config
    return fields


def _document(data: dict[str, Any]) -> dict[str, Any]:
    """An AgentCrawl document in Firecrawl's shape."""
    meta = data.get("metadata") or {}
    doc: dict[str, Any] = {}
    if "markdown" in data:
        doc["markdown"] = data["markdown"]
    if "html" in data:
        # AgentCrawl keeps the fetched page only; both names get it.
        doc["html"] = doc["rawHtml"] = data["html"]
    if "links" in data:
        doc["links"] = data["links"]
    if "summary" in data:
        doc["summary"] = data["summary"]
    if data.get("screenshot"):
        doc["screenshot"] = "data:image/png;base64," + data["screenshot"]
    errors = data.get("errors") or []
    status = meta.get("status_code") or meta.get("browser_status") or (None if errors else 200)
    doc["metadata"] = {
        key: value
        for key, value in {
            "title": meta.get("title"),
            "description": meta.get("description"),
            "language": meta.get("language") or meta.get("lang"),
            "ogTitle": meta.get("og:title"),
            "ogDescription": meta.get("og:description"),
            "ogImage": meta.get("og:image"),
            "sourceURL": meta.get("source_url") or data.get("url"),
            "url": meta.get("final_url") or data.get("url"),
            "statusCode": status,
            "contentType": meta.get("content_type"),
            "error": errors[0] if errors else None,
            "errorType": meta.get("error_type"),
        }.items()
        if value is not None
    }
    return doc


def _failure(result: dict[str, Any]) -> JSONResponse:
    data = result.get("data") or {}
    error_type = (data.get("metadata") or {}).get("error_type")
    status = {"timeout": 408, "client_challenge": 403, "blocked": 403}.get(error_type, 500)
    errors = data.get("errors") or [result.get("error") or "scrape failed"]
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": errors[0], "code": error_type},
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise _refuse("request body must be JSON") from exc
    if not isinstance(body, dict):
        raise _refuse("request body must be a JSON object")
    return body


@router.post("/scrape")
async def scrape(request: Request, api_key: str | None = Depends(v1.server.require_key)) -> Any:
    body = await _body(request)
    fields = _scrape_fields(body, skip={"url"})
    try:
        scrape_request = v1.ScrapeRequest(url=body.get("url") or "", **fields)
    except ValueError as exc:
        raise _refuse(str(exc)) from exc
    v1.server.validate_source(scrape_request.url)
    v1.server.validate_config_override(scrape_request.config)
    result = v1._scrape_one(scrape_request, api_key)
    if not result.get("success"):
        return _failure(result)
    return {"success": True, "data": _document(result["data"])}


@router.post("/map")
async def map_site(request: Request, api_key: str | None = Depends(v1.server.require_key)) -> Any:
    body = await _body(request)
    for key in body:
        if key not in {"url", "limit", "search", "sitemap", "includeSubdomains", *_IGNORED}:
            raise _refuse(f"option {key!r} is not supported by AgentCrawl")
    result = v1.map_site(
        v1.MapRequest(url=body.get("url") or "", max_urls=body.get("limit")), api_key
    )
    urls = (result.get("data") or {}).get("urls") or []
    search = str(body.get("search") or "").casefold()
    if search:
        urls = [url for url in urls if search in url.casefold()]
    return {"success": result["success"], "links": [{"url": url} for url in urls]}


@router.post("/search")
async def search(
    request: Request,
    api_key: str | None = Depends(v1.server.require_key),
    authorization: str | None = Header(default=None),
) -> Any:
    body = await _body(request)
    for key in body:
        if key not in {"query", "limit", "scrapeOptions", "sources", *_IGNORED}:
            raise _refuse(f"option {key!r} is not supported by AgentCrawl")
    options = body.get("scrapeOptions")
    fields = _scrape_fields(options) if options else {}
    fields.pop("config", None)
    result = v1.search(
        v1.SearchRequest(
            query=body.get("query") or "",
            limit=int(body.get("limit") or 5),
            scrape=bool(options),
            **fields,
        ),
        api_key,
        authorization,
    )
    web = []
    for item in result["data"]["results"]:
        entry = {"url": item["url"], "title": item["title"], "description": item["snippet"]}
        if item.get("data"):
            entry.update(_document(item["data"]))
        web.append(entry)
    return {"success": True, "data": {"web": web}}


@router.post("/crawl")
async def crawl(request: Request, api_key: str | None = Depends(v1.server.require_key)) -> Any:
    body = await _body(request)
    fields: dict[str, Any] = {}
    for key, value in body.items():
        if key in {"url", *_IGNORED} or value is None:
            continue
        if key == "limit":
            fields["max_pages"] = int(value)
        elif key == "maxDiscoveryDepth":
            fields["max_depth"] = int(value)
        elif key == "includePaths":
            fields["include"] = [f"*{path}*" for path in value]
        elif key == "excludePaths":
            fields["exclude"] = [f"*{path}*" for path in value]
        elif key == "prompt":
            fields["query"] = str(value)
        elif key == "scrapeOptions":
            options = _scrape_fields(value)
            fields["config"] = options.get("config", {})
        elif key in _CRAWL_DEFAULTS:
            if value != _CRAWL_DEFAULTS[key]:
                raise _refuse(f"{key}={value!r} is not supported by AgentCrawl")
        else:
            raise _refuse(f"option {key!r} is not supported by AgentCrawl")
    try:
        crawl_request = v1.CrawlRequest(url=body.get("url") or "", **fields)
    except ValueError as exc:
        raise _refuse(str(exc)) from exc
    started = v1.crawl(crawl_request, api_key, None, request.headers.get("authorization"))
    return {"success": True, "id": started["job_id"], "url": _job_url(request, "crawl", started)}


@router.post("/batch/scrape")
async def batch_scrape(
    request: Request, api_key: str | None = Depends(v1.server.require_key)
) -> Any:
    body = await _body(request)
    urls = [str(url) for url in body.get("urls") or []]
    if not urls or len(urls) > v1._MAX_BATCH_URLS:
        raise _refuse(f"urls must hold 1 to {v1._MAX_BATCH_URLS} URLs")
    fields = _scrape_fields(body, skip={"urls", "webhook", "ignoreInvalidURLs"})
    if body.get("webhook"):
        raise _refuse(f"webhooks {_ENHANCED}")
    valid, invalid = [], []
    for url in urls:
        try:
            v1.server.validate_source(url)
            valid.append(url)
        except HTTPException:
            invalid.append(url)
    if not valid:
        raise _refuse("none of the URLs can be scraped")
    config = fields.get("config", {})
    v1.server.validate_config_override(config)
    authorization = request.headers.get("authorization")
    if api_key is not None and not v1.server.is_owner_key(authorization):
        v1.server.check_rate_limit(api_key, units=len(valid) - 1)
    payload = {
        "url": valid[0],
        "urls": valid,
        "formats": fields["formats"],
        "only_main_content": fields.get("only_main_content"),
        "config": config,
    }
    job_id, created = v1.server.store.create_or_get_job("crawl", payload, owner_key=api_key or "")
    if created:
        v1.server.schedule_job(job_id, payload, api_key)
    started = {"job_id": job_id}
    return {
        "success": True,
        "id": job_id,
        "url": _job_url(request, "batch/scrape", started),
        "invalidURLs": invalid or None,
    }


def _job_url(request: Request, kind: str, started: dict[str, Any]) -> str:
    return str(request.base_url).rstrip("/") + f"/v2/{kind}/{started['job_id']}"


def _job_status(request: Request, job_id: str, skip: int, scope: Any) -> dict[str, Any]:
    job = v1.get_job(job_id, offset=skip, limit=_PAGE, scope=scope)["data"]
    result = job.get("result") or {}
    documents = result.get("documents")
    if documents is None:  # still running: pages saved so far
        documents = v1.server.store.get_job_documents(job_id, offset=skip, limit=_PAGE)
    progress = job.get("progress") or {}
    total = (result.get("pagination") or {}).get("total")
    completed = total if total is not None else int(progress.get("completed") or len(documents))
    has_more = bool((result.get("pagination") or {}).get("has_more"))
    next_url = None
    if has_more:
        next_url = str(request.url.include_query_params(skip=skip + len(documents)))
    return {
        "success": True,
        "status": _JOB_STATUS.get(job["status"], "scraping"),
        "completed": completed,
        "total": int(progress.get("total") or completed),
        "creditsUsed": completed,
        "expiresAt": None,
        "next": next_url,
        "data": [_document(doc) for doc in documents],
    }


@router.get("/crawl/{job_id}")
@router.get("/batch/scrape/{job_id}")
def job_status(
    request: Request,
    job_id: str,
    skip: int = Query(default=0, ge=0),
    scope: Any = Depends(v1.server.job_scope),
) -> Any:
    return _job_status(request, job_id, skip, scope)


@router.delete("/crawl/{job_id}")
@router.delete("/batch/scrape/{job_id}")
def cancel(job_id: str, scope: Any = Depends(v1.server.job_scope)) -> Any:
    v1.cancel_job(job_id, scope)
    return {"success": True, "status": "cancelled"}


def install(app: FastAPI) -> None:
    """Mount the routes; their errors use Firecrawl's ``{success, error}`` body."""
    app.include_router(router)

    @app.exception_handler(HTTPException)
    async def _errors(request: Request, exc: HTTPException) -> Any:
        if not request.url.path.startswith("/v2/"):
            return await http_exception_handler(request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": str(exc.detail)},
            headers=getattr(exc, "headers", None),
        )
