from __future__ import annotations

from pathlib import Path

from agentcrawl.mcp_server import (
    cache_stats,
    crawl_site,
    get_job,
    inspect_failures,
    job_events,
    retry_failures,
    scrape_url,
)


def test_mcp_uses_local_engine_without_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    page = tmp_path / "index.html"
    page.write_text("<main><h1>Local MCP works</h1></main>", encoding="utf-8")

    payload = scrape_url(str(page), formats=["markdown"])

    assert "Local MCP works" in payload["markdown"]
    assert cache_stats()["mode"] == "local"


def test_mcp_forwards_idempotency_and_pagination(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def crawl(self, url, **kwargs):
            calls.append(("crawl", url, kwargs))
            return {"job_id": "job-1"}

        def job(self, job_id, **kwargs):
            calls.append(("job", job_id, kwargs))
            return {"status": "completed"}

        def job_events(self, job_id, **kwargs):
            calls.append(("job_events", job_id, kwargs))
            return {"events": []}

        def job_failures(self, job_id, **kwargs):
            calls.append(("job_failures", job_id, kwargs))
            return {"failures": []}

        def retry_failures(self, job_id, **kwargs):
            calls.append(("retry_failures", job_id, kwargs))
            return {"retried": 1}

    monkeypatch.setattr("agentcrawl.mcp_server._client", lambda: FakeClient())

    crawl_site(
        "https://example.com",
        max_pages=10,
        idempotency_key="stable-key",
    )
    get_job("job-1", offset=50, limit=25)
    job_events("job-1", event_type="completed", limit=10)
    inspect_failures("job-1", retryable_only=True, limit=10)
    retry_failures("job-1", urls=["https://example.com/retry"])

    assert calls == [
        (
            "crawl",
            "https://example.com",
            {
                "max_pages": 10,
                "max_depth": None,
                "wait": False,
                "idempotency_key": "stable-key",
            },
        ),
        ("job", "job-1", {"offset": 50, "limit": 25}),
        (
            "job_events",
            "job-1",
            {"event_type": "completed", "limit": 10},
        ),
        (
            "job_failures",
            "job-1",
            {"retryable": True, "error_type": None, "limit": 10},
        ),
        (
            "retry_failures",
            "job-1",
            {
                "failure_ids": None,
                "urls": ["https://example.com/retry"],
                "retry_all": False,
            },
        ),
    ]
