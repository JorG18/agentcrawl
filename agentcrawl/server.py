from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager, contextmanager
from collections import deque
import json
import os
import queue
import secrets
import threading
import time
import urllib.parse
from typing import Any

from .dashboard import dashboard_summary, render_dashboard_html
from .errors import classify_error
from .crawler import AgentCrawl
from .serializers import to_jsonable
from .security import validate_remote_url
from .utils import is_probably_url
from .storage import SQLiteStore
import uuid

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Query
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field, ConfigDict
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install agentcrawl[server] to run the API server.") from exc


class ScrapeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    formats: list[str] = Field(default_factory=lambda: ["markdown", "links", "metadata"])
    only_main_content: bool | None = None
    cache: bool = True
    cache_ttl_seconds: int | None = Field(default=None, ge=1, le=2_592_000)
    config: dict[str, Any] = Field(default_factory=dict)


class MapRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    max_urls: int | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CrawlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    max_pages: int | None = Field(default=None, ge=1, le=10_000)
    max_depth: int | None = Field(default=None, ge=0, le=100)
    include: list[str] | None = None
    exclude: list[str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False


class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str = Field(min_length=1, max_length=8192)
    prompt: str
    output_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    config: dict[str, Any] = Field(default_factory=dict)


class RetryFailuresRequest(BaseModel):
    failure_ids: list[str] | None = None
    urls: list[str] | None = None
    retry_all: bool = False


class AgentCrawlServer:
    def __init__(self) -> None:
        self.store = SQLiteStore(os.getenv("AGENTCRAWL_DB", "agentcrawl.db"))
        # Stable per-process identifier used as the schedule-lease owner. We
        # generate it once so that even if the server instantiates itself
        # multiple times the lease records belong to the same logical owner.
        self.instance_id = (
            os.getenv("AGENTCRAWL_INSTANCE_ID") or f"pid-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.schedule_lease_seconds = float(os.getenv("AGENTCRAWL_SCHEDULE_LEASE_SECONDS", "300"))
        self.job_workers = int(os.getenv("AGENTCRAWL_WORKERS", "4"))
        self._job_semaphore = threading.BoundedSemaphore(self.job_workers)
        self._job_threads_lock = threading.Lock()
        self._job_threads: dict[str, threading.Thread] = {}
        self._job_queue: queue.Queue[tuple[str, dict[str, Any], str | None]] = queue.Queue()
        self._queued_jobs: set[str] = set()
        self._workers_started = False
        self.rate_limit_per_minute = int(os.getenv("AGENTCRAWL_RATE_LIMIT_PER_MINUTE", "60"))
        self._rate_lock = threading.Lock()
        self._rate_windows: dict[str, deque[float]] = {}
        self.auth_enabled = os.getenv("AGENTCRAWL_AUTH_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.api_keys = {
            key.strip() for key in os.getenv("AGENTCRAWL_API_KEYS", "").split(",") if key.strip()
        }
        self.owner_api_keys = {
            key.strip()
            for key in os.getenv("AGENTCRAWL_OWNER_API_KEYS", "").split(",")
            if key.strip()
        }
        self.domain_min_delay = float(os.getenv("AGENTCRAWL_DOMAIN_MIN_DELAY", "0.0"))
        self.domain_max_concurrency = max(
            0, int(os.getenv("AGENTCRAWL_DOMAIN_MAX_CONCURRENCY", "2"))
        )
        self.crawl_job_page_quantum = max(
            0, int(os.getenv("AGENTCRAWL_CRAWL_JOB_PAGE_QUANTUM", "5"))
        )
        self.cache_enabled = os.getenv("AGENTCRAWL_CACHE_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.cache_ttl_seconds = int(os.getenv("AGENTCRAWL_CACHE_TTL_SECONDS", "86400"))
        self.allow_local_files = os.getenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.allow_private_network = os.getenv(
            "AGENTCRAWL_ALLOW_PRIVATE_NETWORK", "false"
        ).lower() in {"1", "true", "yes", "on"}
        self._domain_lock = threading.Lock()
        self._domain_last_seen: dict[str, float] = {}
        self._domain_semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._recover_lock = threading.Lock()
        self.default_config = {
            "fetcher": os.getenv("AGENTCRAWL_FETCHER", "http"),
            "browser_backend": os.getenv("AGENTCRAWL_BROWSER_BACKEND", "playwright"),
            "camofox_base_url": os.getenv("AGENTCRAWL_CAMOFOX_URL", "http://127.0.0.1:9377"),
            "camofox_access_key": os.getenv("AGENTCRAWL_CAMOFOX_ACCESS_KEY") or None,
            "camofox_user_id": os.getenv("AGENTCRAWL_CAMOFOX_USER_ID", "agentcrawl"),
            "headless": os.getenv("AGENTCRAWL_HEADLESS", "true").lower()
            in {"1", "true", "yes", "on"},
            "timeout_ms": int(os.getenv("AGENTCRAWL_TIMEOUT_MS", "30000")),
            "http_retries": int(os.getenv("AGENTCRAWL_HTTP_RETRIES", "2")),
            "http_retry_delay": float(os.getenv("AGENTCRAWL_HTTP_RETRY_DELAY", "1.0")),
            "browser_fallback": os.getenv("AGENTCRAWL_BROWSER_FALLBACK", "true").lower()
            in {"1", "true", "yes", "on"},
            "domain_min_delay": float(os.getenv("AGENTCRAWL_DOMAIN_MIN_DELAY", "0.0")),
            "user_agent": os.getenv(
                "AGENTCRAWL_USER_AGENT",
                "Mozilla/5.0 (compatible; AgentCrawl/0.1; +https://agentcrawl.local)",
            ),
            "allow_private_network": self.allow_private_network,
            "crawl_depth": int(os.getenv("AGENTCRAWL_CRAWL_DEPTH", "1")),
            "crawl_max_pages": int(os.getenv("AGENTCRAWL_CRAWL_MAX_PAGES", "25")),
            "crawl_url_retries": int(os.getenv("AGENTCRAWL_CRAWL_URL_RETRIES", "2")),
            "crawl_retry_delay": float(os.getenv("AGENTCRAWL_CRAWL_RETRY_DELAY", "2.0")),
            "crawl_retry_max_delay": float(os.getenv("AGENTCRAWL_CRAWL_RETRY_MAX_DELAY", "60.0")),
            "respect_robots_txt": os.getenv("AGENTCRAWL_RESPECT_ROBOTS_TXT", "true").lower()
            in {"1", "true", "yes", "on"},
        }

    def require_key(self, authorization: str | None = Header(default=None)) -> str | None:
        if not self.auth_enabled:
            return None
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer API key.")
        if not self.api_keys:
            raise HTTPException(
                status_code=503, detail="Authentication is enabled but no API keys are configured."
            )
        api_key = authorization.split(" ", 1)[1].strip()
        if not any(secrets.compare_digest(api_key, expected) for expected in self.api_keys):
            raise HTTPException(status_code=403, detail="Invalid API key.")
        key_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        if not any(secrets.compare_digest(api_key, owner) for owner in self.owner_api_keys):
            self.check_rate_limit(key_id)
        return key_id

    def check_rate_limit(self, key_id: str) -> None:
        if self.rate_limit_per_minute <= 0:
            return
        now = time.monotonic()
        cutoff = now - 60
        with self._rate_lock:
            requests = self._rate_windows.setdefault(key_id, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.rate_limit_per_minute:
                raise HTTPException(status_code=429, detail="API key rate limit exceeded.")
            requests.append(now)

    def merged_config(self, override: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "fetcher",
            "browser_backend",
            "headless",
            "timeout_ms",
            "http_retries",
            "http_retry_delay",
            "browser_fallback",
            "browser_fallback_statuses",
            "wait_until",
            "user_agent",
            "include_links",
            "include_images",
            "max_input_chars",
            "crawl_depth",
            "crawl_max_pages",
            "crawl_same_domain",
            "crawl_url_retries",
            "crawl_retry_delay",
            "crawl_retry_max_delay",
            "crawl_retry_error_types",
            "crawl_include",
            "crawl_exclude",
            "respect_robots_txt",
        }
        safe_override = {key: value for key, value in override.items() if key in allowed}
        return {
            **self.default_config,
            **safe_override,
            "allow_private_network": self.allow_private_network,
        }

    def validate_source(self, source: str) -> None:
        if not is_probably_url(source):
            if not self.allow_local_files:
                raise HTTPException(
                    status_code=400, detail="Local file sources are disabled on this server."
                )
            return
        try:
            validate_remote_url(source, allow_private_network=self.allow_private_network)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def throttle_url(self, url: str) -> None:
        if self.domain_min_delay <= 0:
            return
        parsed = urllib.parse.urlsplit(url)
        domain = parsed.netloc.lower()
        if not domain:
            return
        with self._domain_lock:
            now = time.monotonic()
            available_at = max(now, self._domain_last_seen.get(domain, now))
            self._domain_last_seen[domain] = available_at + self.domain_min_delay
        wait_for = available_at - now
        if wait_for > 0:
            time.sleep(wait_for)

    @contextmanager
    def domain_slot(self, url: str):
        domain = urllib.parse.urlsplit(url).netloc.lower()
        semaphore: threading.BoundedSemaphore | None = None
        if domain and self.domain_max_concurrency > 0:
            with self._domain_lock:
                semaphore = self._domain_semaphores.get(domain)
                if semaphore is None:
                    semaphore = threading.BoundedSemaphore(self.domain_max_concurrency)
                    self._domain_semaphores[domain] = semaphore
            semaphore.acquire()
        try:
            self.throttle_url(url)
            yield
        finally:
            if semaphore is not None:
                semaphore.release()

    def _ensure_job_workers(self) -> None:
        with self._job_threads_lock:
            if self._workers_started:
                return
            self._workers_started = True
            for index in range(self.job_workers):
                thread = threading.Thread(
                    target=self._job_worker,
                    daemon=True,
                    name=f"agentcrawl-worker-{index + 1}",
                )
                thread.start()

    def _job_worker(self) -> None:
        while True:
            job_id, payload, api_key = self._job_queue.get()
            try:
                _run_crawl_job(job_id, payload, api_key)
            finally:
                self._job_queue.task_done()

    def _enqueue_job(
        self,
        job_id: str,
        payload: dict[str, Any],
        api_key: str | None,
    ) -> None:
        with self._job_threads_lock:
            self._job_threads.pop(job_id, None)
        self._job_queue.put((job_id, payload, api_key))
        # Drop the in-process de-dup entry as soon as the job lands on
        # the queue. Without this, a transient ``claim_job`` failure (the
        # row flipped status while we were waking the timer) would leave
        # ``_queued_jobs`` holding the id until the next process restart,
        # blocking future ``schedule_job`` calls for the same id. The
        # cross-process lease remains the safety net for inter-worker
        # dedupe.
        self._queued_jobs.discard(job_id)

    def schedule_job(
        self,
        job_id: str,
        payload: dict[str, Any],
        api_key: str | None,
        available_at: float = 0.0,
    ) -> bool:
        self._ensure_job_workers()
        with self._job_threads_lock:
            if job_id in self._queued_jobs:
                return False
            current = self._job_threads.get(job_id)
            if current and current.is_alive():
                return False
            self._queued_jobs.add(job_id)
        # Cross-process dedupe: only one worker (out of N Uvicorn workers
        # racing through ``recover_jobs``) actually schedules the job.
        # The other workers see ``False`` here and skip the queue-and-timer
        # fan-out that used to create zombie BoundedSemaphore acquisitions.
        lease_seconds = max(
            self.schedule_lease_seconds,
            max(1.0, available_at - time.time()) + 30.0,
        )
        if not self.store.acquire_schedule_lease(
            job_id, self.instance_id, lease_seconds=lease_seconds
        ):
            with self._job_threads_lock:
                self._queued_jobs.discard(job_id)
            return False
        try:
            delay = max(0.0, available_at - time.time())
            with self._job_threads_lock:
                if delay <= 0:
                    self._job_queue.put((job_id, payload, api_key))
                    return True
                thread = threading.Timer(
                    delay,
                    self._enqueue_job,
                    args=(job_id, payload, api_key),
                )
                thread.name = f"agentcrawl-retry-{job_id[:8]}"
                thread.daemon = True
                self._job_threads[job_id] = thread
                thread.start()
                return True
        except Exception:
            # If anything blows up while wiring the timer we drop the lease
            # so another worker (or the next recover pass) can try again.
            self.store.release_schedule_lease(job_id, self.instance_id)
            with self._job_threads_lock:
                self._queued_jobs.discard(job_id)
            raise

    def recover_jobs(self) -> int:
        with self._recover_lock:
            recovered = 0
            for job in self.store.recoverable_jobs():
                if self.schedule_job(
                    job["id"],
                    job["request"],
                    job["owner_key"] or None,
                    job["available_at"],
                ):
                    recovered += 1
            return recovered


server = AgentCrawlServer()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    server.store.prepare_restart_recovery()
    server.recover_jobs()
    yield


app = FastAPI(title="AgentCrawl", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "agentcrawl", "auth_enabled": server.auth_enabled}


@app.get("/api/dashboard/summary")
def dashboard_summary_endpoint() -> dict[str, Any]:
    return {"success": True, "data": dashboard_summary(server.store)}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    summary = dashboard_summary(server.store)
    return HTMLResponse(render_dashboard_html(summary))


@app.post("/v1/scrape")
def scrape(
    request: ScrapeRequest, api_key: str | None = Depends(server.require_key)
) -> dict[str, Any]:
    server.validate_source(request.url)
    cache_key = _scrape_cache_key(request)
    use_cache = server.cache_enabled and request.cache
    if use_cache:
        cached = server.store.get_cache(cache_key)
        if cached is not None:
            cached.setdefault("data", {}).setdefault("metadata", {})["cache_hit"] = True
            server.store.record_usage(api_key, "/v1/scrape.cache_hit")
            return cached

    crawler = AgentCrawl(server.merged_config(request.config))
    with server.domain_slot(request.url):
        result = crawler.scrape(
            request.url, formats=request.formats, only_main_content=request.only_main_content
        )
    payload = to_jsonable(result)
    payload.setdefault("metadata", {})["cache_hit"] = False
    payload["metadata"]["cache_enabled"] = use_cache
    _attach_error_type(payload)
    response = {"success": not bool(payload.get("errors")), "data": payload}
    if use_cache and response["success"]:
        ttl_seconds = request.cache_ttl_seconds or server.cache_ttl_seconds
        server.store.set_cache(cache_key, request.url, response, ttl_seconds)
    server.store.record_usage(api_key, "/v1/scrape")
    return response


@app.post("/v1/map")
def map_site(
    request: MapRequest, api_key: str | None = Depends(server.require_key)
) -> dict[str, Any]:
    server.validate_source(request.url)
    crawler = AgentCrawl(server.merged_config(request.config))
    with server.domain_slot(request.url):
        result = crawler.map(
            request.url,
            max_urls=request.max_urls,
            include=request.include,
            exclude=request.exclude,
        )
    server.store.record_usage(api_key, "/v1/map")
    return {"success": result.ok, "data": to_jsonable(result)}


@app.post("/v1/crawl")
def crawl(
    request: CrawlRequest,
    api_key: str | None = Depends(server.require_key),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    server.validate_source(request.url)
    payload = request.model_dump()
    if request.wait:
        result = _run_crawl(payload)
        server.store.record_usage(
            api_key, "/v1/crawl", units=max(1, len(result.get("documents", [])))
        )
        return {"success": not bool(result.get("errors")), "data": result}

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 255:
            raise HTTPException(status_code=400, detail="Invalid Idempotency-Key.")
    try:
        job_id, created = server.store.create_or_get_job(
            "crawl",
            payload,
            owner_key=api_key or "",
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        server.schedule_job(job_id, payload, api_key)
    job = server.store.get_job(job_id)
    return {
        "success": True,
        "job_id": job_id,
        "status": job["status"] if job else "queued",
        "deduplicated": not created,
    }


@app.get("/v1/jobs/{job_id}")
def get_job(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    job = server.store.get_job(
        job_id,
        document_offset=offset,
        document_limit=limit,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"success": True, "data": job}


@app.get("/v1/jobs/{job_id}/events")
def get_job_events(
    job_id: str,
    event_type: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    if server.store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    events = server.store.list_job_events(
        job_id,
        event_type=event_type,
        offset=offset,
        limit=limit,
    )
    server.store.record_usage(api_key, "/v1/jobs.events")
    return {"success": True, "data": {"job_id": job_id, "events": events, "returned": len(events)}}


@app.delete("/v1/jobs/{job_id}")
def cancel_job(
    job_id: str,
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    job = server.store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not server.store.request_job_cancel(job_id):
        raise HTTPException(
            status_code=409,
            detail=f"Job cannot be cancelled from status {job['status']}.",
        )
    server.store.record_usage(api_key, "/v1/jobs.cancel")
    return {"success": True, "data": {"job_id": job_id, "status": "cancelling"}}


@app.get("/v1/failures")
def list_failures(
    job_id: str | None = None,
    status: str | None = Query(default="open"),
    retryable: bool | None = None,
    error_type: str | None = None,
    domain: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    failures = server.store.list_crawl_failures(
        job_id=job_id,
        status=status,
        retryable=retryable,
        error_type=error_type,
        domain=domain,
        offset=offset,
        limit=limit,
    )
    server.store.record_usage(api_key, "/v1/failures")
    return {"success": True, "data": {"failures": failures, "returned": len(failures)}}


@app.get("/v1/jobs/{job_id}/failures")
def list_job_failures(
    job_id: str,
    status: str | None = Query(default="open"),
    retryable: bool | None = None,
    error_type: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    if server.store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    failures = server.store.list_crawl_failures(
        job_id=job_id,
        status=status,
        retryable=retryable,
        error_type=error_type,
        offset=offset,
        limit=limit,
    )
    server.store.record_usage(api_key, "/v1/jobs.failures")
    return {
        "success": True,
        "data": {"job_id": job_id, "failures": failures, "returned": len(failures)},
    }


@app.post("/v1/jobs/{job_id}/failures/retry")
def retry_job_failures(
    job_id: str,
    request: RetryFailuresRequest,
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    try:
        failures = server.store.retry_crawl_failures(
            job_id,
            failure_ids=request.failure_ids,
            urls=request.urls,
            retry_all=request.retry_all,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if failures:
        job = server.store.get_job(job_id)
        if job is not None:
            server.schedule_job(job_id, job["request"], api_key)
    server.store.record_usage(api_key, "/v1/jobs.failures.retry")
    return {
        "success": True,
        "data": {"job_id": job_id, "retried": len(failures), "failures": failures},
    }


@app.post("/v1/extract")
def extract(
    request: ExtractRequest, api_key: str | None = Depends(server.require_key)
) -> dict[str, Any]:
    server.validate_source(request.url)
    crawler = AgentCrawl(server.merged_config(request.config))
    with server.domain_slot(request.url):
        result = crawler.extract(request.url, request.prompt, request.output_schema)
    server.store.record_usage(api_key, "/v1/extract")
    return {"success": bool(getattr(result, "ok", True)), "data": to_jsonable(result)}


@app.get("/v1/usage")
def usage(api_key: str | None = Depends(server.require_key)) -> dict[str, Any]:
    return {"success": True, "data": {"usage": server.store.usage_count(api_key)}}


@app.get("/v1/stats")
def stats(api_key: str | None = Depends(server.require_key)) -> dict[str, Any]:
    server.store.cleanup_cache()
    return {
        "success": True,
        "data": {
            "usage_total": server.store.usage_count(api_key),
            "usage_by_endpoint": server.store.usage_by_endpoint(),
            "jobs": server.store.job_counts(),
            "job_events": server.store.job_event_counts(),
            "crawl_queue": server.store.crawl_queue_metrics(),
            "crawl_failures": server.store.crawl_failure_metrics(),
            "cache_entries": server.store.cache_count(),
            "cache_by_domain": server.store.cache_by_domain(),
            "cache_enabled": server.cache_enabled,
            "cache_ttl_seconds": server.cache_ttl_seconds,
            "domain_min_delay": server.domain_min_delay,
            "domain_max_concurrency": server.domain_max_concurrency,
            "rate_limit_per_minute": server.rate_limit_per_minute,
            "job_workers": server.job_workers,
            "crawl_job_page_quantum": server.crawl_job_page_quantum,
        },
    }


@app.delete("/v1/cache")
def clear_cache(
    domain: str | None = None,
    url: str | None = None,
    api_key: str | None = Depends(server.require_key),
) -> dict[str, Any]:
    if domain and url:
        raise HTTPException(status_code=400, detail="Use either domain or url, not both.")
    deleted = server.store.clear_cache(domain=domain, url=url)
    server.store.record_usage(api_key, "/v1/cache.delete")
    return {
        "success": True,
        "data": {"deleted": deleted, "domain": domain, "url": url},
    }


def _run_crawl_job(job_id: str, payload: dict[str, Any], api_key: str | None) -> None:
    acquired = False
    reschedule_at: float | None = None
    try:
        while not acquired:
            acquired = server._job_semaphore.acquire(timeout=0.5)
            if not acquired and server.store.job_cancel_requested(job_id):
                server.store.update_job(job_id, "cancelled")
                return
        if server.store.job_cancel_requested(job_id):
            server.store.update_job(job_id, "cancelled")
            return
        if not server.store.claim_job(job_id):
            return
        result = _run_crawl(
            payload,
            progress_callback=lambda progress: server.store.update_job_progress(job_id, progress),
            should_cancel=lambda: server.store.job_cancel_requested(job_id),
            resume_state=_resume_state(job_id),
            checkpoint_callback=lambda checkpoint, progress, document: (
                server.store.save_job_checkpoint(
                    job_id,
                    checkpoint,
                    progress,
                    to_jsonable(document) if document is not None else None,
                )
            ),
            max_run_pages=server.crawl_job_page_quantum or None,
        )
        metadata = result.get("metadata", {})
        if metadata.get("retry_scheduled"):
            reschedule_at = float(metadata["next_retry_at"])
            server.store.requeue_job(
                job_id,
                reschedule_at,
                event_type="retry_scheduled",
                payload={"next_retry_at": reschedule_at},
            )
            return
        if metadata.get("fairness_yielded"):
            reschedule_at = time.time()
            server.store.requeue_job(
                job_id,
                reschedule_at,
                event_type="fairness_yielded",
                payload={"page_quantum": server.crawl_job_page_quantum},
            )
            return
        status = "cancelled" if metadata.get("cancelled") else "completed"
        server.store.update_job(job_id, status, result=result)
        server.store.record_usage(
            api_key,
            "/v1/crawl",
            units=max(1, len(result.get("documents", []))),
        )
    except Exception as exc:
        server.store.update_job(job_id, "failed", error=str(exc))
    finally:
        if acquired:
            server._job_semaphore.release()
        with server._job_threads_lock:
            server._job_threads.pop(job_id, None)
            server._queued_jobs.discard(job_id)
        if reschedule_at is not None:
            server.schedule_job(job_id, payload, api_key, reschedule_at)


def _resume_state(job_id: str) -> dict[str, Any] | None:
    checkpoint = server.store.get_job_checkpoint(job_id)
    if checkpoint is None:
        return None
    checkpoint["documents"] = server.store.get_job_documents(job_id)
    return checkpoint


def _run_crawl(
    payload: dict[str, Any],
    progress_callback: Any | None = None,
    should_cancel: Any | None = None,
    resume_state: dict[str, Any] | None = None,
    checkpoint_callback: Any | None = None,
    max_run_pages: int | None = None,
) -> dict[str, Any]:
    crawler = AgentCrawl(server.merged_config(payload.get("config", {})))
    result = crawler.crawl(
        payload["url"],
        max_pages=payload.get("max_pages"),
        max_depth=payload.get("max_depth"),
        include=payload.get("include"),
        exclude=payload.get("exclude"),
        progress_callback=progress_callback,
        should_cancel=should_cancel,
        resume_state=resume_state,
        checkpoint_callback=checkpoint_callback,
        before_fetch=server.domain_slot,
        max_run_pages=max_run_pages,
    )
    return to_jsonable(result)


def _scrape_cache_key(request: ScrapeRequest) -> str:
    payload = {
        "pipeline_version": 2,
        "url": request.url,
        "formats": sorted(request.formats),
        "only_main_content": request.only_main_content,
        "config": request.config,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _attach_error_type(payload: dict[str, Any]) -> None:
    errors = payload.get("errors") or []
    if not errors:
        return
    payload.setdefault("metadata", {})["error_type"] = classify_error(str(errors[0]))
