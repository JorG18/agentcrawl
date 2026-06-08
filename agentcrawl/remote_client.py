from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class AgentCrawlClient:
    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", api_key: str | None = None, timeout: int = 60
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def scrape(
        self,
        url: str,
        formats: list[str] | None = None,
        config: dict[str, Any] | None = None,
        *,
        only_main_content: bool | None = None,
        cache: bool = True,
        cache_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/scrape",
            {
                "url": url,
                "formats": formats or ["markdown", "links", "metadata"],
                "only_main_content": only_main_content,
                "cache": cache,
                "cache_ttl_seconds": cache_ttl_seconds,
                "config": config or {},
            },
        )

    def map(
        self, url: str, max_urls: int | None = None, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._post("/v1/map", {"url": url, "max_urls": max_urls, "config": config or {}})

    def crawl(
        self,
        url: str,
        max_pages: int | None = None,
        max_depth: int | None = None,
        wait: bool = False,
        config: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._post(
            "/v1/crawl",
            {
                "url": url,
                "max_pages": max_pages,
                "max_depth": max_depth,
                "wait": wait,
                "config": config or {},
            },
            headers=headers,
        )

    def job(
        self,
        job_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode({"offset": offset, "limit": limit})
        return self._get(f"/v1/jobs/{job_id}?{query}")

    def job_events(
        self,
        job_id: str,
        *,
        event_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                key: value
                for key, value in {
                    "event_type": event_type,
                    "offset": offset,
                    "limit": limit,
                }.items()
                if value is not None
            }
        )
        return self._get(f"/v1/jobs/{job_id}/events?{query}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._delete(f"/v1/jobs/{job_id}")

    def failures(
        self,
        *,
        job_id: str | None = None,
        status: str | None = "open",
        retryable: bool | None = None,
        error_type: str | None = None,
        domain: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                key: value
                for key, value in {
                    "job_id": job_id,
                    "status": status,
                    "retryable": retryable,
                    "error_type": error_type,
                    "domain": domain,
                    "offset": offset,
                    "limit": limit,
                }.items()
                if value is not None
            }
        )
        return self._get("/v1/failures" + (f"?{query}" if query else ""))

    def job_failures(
        self,
        job_id: str,
        *,
        status: str | None = "open",
        retryable: bool | None = None,
        error_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                key: value
                for key, value in {
                    "status": status,
                    "retryable": retryable,
                    "error_type": error_type,
                    "offset": offset,
                    "limit": limit,
                }.items()
                if value is not None
            }
        )
        return self._get(f"/v1/jobs/{job_id}/failures" + (f"?{query}" if query else ""))

    def retry_failures(
        self,
        job_id: str,
        *,
        failure_ids: list[str] | None = None,
        urls: list[str] | None = None,
        retry_all: bool = False,
    ) -> dict[str, Any]:
        return self._post(
            f"/v1/jobs/{job_id}/failures/retry",
            {"failure_ids": failure_ids, "urls": urls, "retry_all": retry_all},
        )

    def usage(self) -> dict[str, Any]:
        return self._get("/v1/usage")

    def stats(self) -> dict[str, Any]:
        return self._get("/v1/stats")

    def clear_cache(self, domain: str | None = None, url: str | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {key: value for key, value in {"domain": domain, "url": url}.items() if value}
        )
        path = "/v1/cache" + (f"?{query}" if query else "")
        return self._delete(path)

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(self.base_url + path, headers=self._headers())
        return self._open(request)

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                **self._headers(),
                "content-type": "application/json",
                **(headers or {}),
            },
            method="POST",
        )
        return self._open(request)

    def _delete(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            headers=self._headers(),
            method="DELETE",
        )
        return self._open(request)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"authorization": f"Bearer {self.api_key}"}

    def _open(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"AgentCrawl API error {exc.code}: {body}") from exc
