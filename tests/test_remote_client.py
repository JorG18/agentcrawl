from __future__ import annotations

import json

from agentcrawl.remote_client import AgentCrawlClient


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"success": True}).encode("utf-8")


def test_client_sends_idempotency_key_and_job_pagination(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = AgentCrawlClient("http://agentcrawl.test", "secret")

    client.crawl(
        "https://example.com",
        max_pages=10,
        idempotency_key="crawl-once",
    )
    client.job("job-1", offset=100, limit=25)
    client.job_events("job-1", event_type="completed", limit=10)
    client.job_failures("job-1", retryable=True, limit=25)
    client.retry_failures("job-1", urls=["https://example.com/retry"])

    assert requests[0].headers["Idempotency-key"] == "crawl-once"
    assert requests[1].full_url.endswith("/v1/jobs/job-1?offset=100&limit=25")
    assert requests[2].full_url.endswith(
        "/v1/jobs/job-1/events?event_type=completed&offset=0&limit=10"
    )
    assert requests[3].full_url.endswith(
        "/v1/jobs/job-1/failures?status=open&retryable=True&offset=0&limit=25"
    )
    assert requests[4].full_url.endswith("/v1/jobs/job-1/failures/retry")
