"""The /v2 routes answer what Firecrawl's SDK sends, in the shape it reads.

The request bodies are what firecrawl-py 4.45 sends with its defaults.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import pytest

from agentcrawl.server import _run_crawl_job, app, server
from agentcrawl.storage import SQLiteStore

SDK_SCRAPE_DEFAULTS = {
    "onlyMainContent": True,
    "skipTlsVerification": True,
    "removeBase64Images": True,
    "fastMode": False,
    "blockAds": True,
    "storeInCache": True,
    "maxAge": 14400000,
    "mobile": False,
    "origin": "python-sdk@4.45.0",
}


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>Guide</title></head><body><main><h1>Guide</h1>"
        "<p>Install it with pip and read the docs.</p><a href='https://example.com/a'>a</a>"
        "</main></body></html>",
        encoding="utf-8",
    )
    server.store = SQLiteStore(tmp_path / "server.db")
    server.allow_local_files = True
    return TestClient(app), str(page)


def test_scrape_answers_in_firecrawl_shape(tmp_path: Path) -> None:
    client, page = _client(tmp_path)
    body = client.post(
        "/v2/scrape", json={"url": page, "formats": ["markdown", "links"], **SDK_SCRAPE_DEFAULTS}
    ).json()
    assert body["success"] is True
    data = body["data"]
    assert "Install it with pip" in data["markdown"]
    assert data["links"] == ["https://example.com/a"]
    assert data["metadata"]["title"] == "Guide"
    assert data["metadata"]["sourceURL"] == page


def test_unsupported_options_are_refused_with_the_reason(tmp_path: Path) -> None:
    client, page = _client(tmp_path)
    for extra, reason in (
        ({"proxy": "stealth"}, "Enhanced"),
        ({"formats": ["json"]}, "json"),
        ({"location": {"country": "DE"}}, "Enhanced"),
        ({"includeTags": ["main"]}, "includeTags"),
    ):
        response = client.post("/v2/scrape", json={"url": page, **extra})
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert reason in response.json()["error"]


def test_failed_page_is_an_error_status(tmp_path: Path) -> None:
    client, _page = _client(tmp_path)
    response = client.post("/v2/scrape", json={"url": str(tmp_path / "missing.html")})
    assert response.status_code >= 400
    assert response.json()["success"] is False


@pytest.fixture
def run_jobs_inline(monkeypatch):
    """Run a scheduled job at once, as the worker thread would."""
    monkeypatch.setattr(
        server,
        "schedule_job",
        lambda job_id, payload, api_key, *a: _run_crawl_job(job_id, payload, api_key),
    )


def _wait(client: TestClient, url: str) -> dict:
    return client.get(url).json()


def test_batch_scrape_is_a_durable_job(tmp_path: Path, run_jobs_inline) -> None:
    client, page = _client(tmp_path)
    started = client.post(
        "/v2/batch/scrape",
        json={"urls": [page, page + "?again"], "formats": ["markdown"], **SDK_SCRAPE_DEFAULTS},
    ).json()
    assert started["success"] is True and started["url"].endswith(
        f"/v2/batch/scrape/{started['id']}"
    )
    status = _wait(client, f"/v2/batch/scrape/{started['id']}")
    assert status["status"] == "completed"
    assert status["completed"] == 2
    assert all("markdown" in doc for doc in status["data"])


def test_crawl_accepts_the_sdk_defaults(tmp_path: Path, run_jobs_inline) -> None:
    client, page = _client(tmp_path)
    started = client.post(
        "/v2/crawl",
        json={
            "url": page,
            "limit": 1,
            "ignoreQueryParameters": False,
            "deduplicateSimilarURLs": True,
            "crawlEntireDomain": False,
            "allowExternalLinks": False,
            "allowSubdomains": False,
            "ignoreRobotsTxt": False,
            "regexOnFullURL": False,
            "zeroDataRetention": False,
        },
    ).json()
    assert started["success"] is True
    status = _wait(client, f"/v2/crawl/{started['id']}")
    assert status["status"] == "completed"
    assert status["data"][0]["metadata"]["title"] == "Guide"
    refused = client.post("/v2/crawl", json={"url": page, "ignoreRobotsTxt": True})
    assert refused.status_code == 400
