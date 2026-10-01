"""The /v2 routes answer what Firecrawl's SDK sends, in the shape it reads.

The request bodies are what firecrawl-py 4.45 sends with its defaults.

Regression: ``actions`` translated from Firecrawl's shape must meet the same
bounds as the native ``browser_actions`` config (at most 25 steps, per-step
limits), so the /v2 border validates the translated list itself with
``validate_actions`` instead of relying on a later re-validation to catch it.
Batch URLs are deduplicated (first occurrence wins), matching Firecrawl.
A batch whose every URL fails is reported as a failed job (still with the
per-page errors), and an empty URL list fails the batch helper cleanly.
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


def test_oversized_actions_are_refused_at_the_v2_border(tmp_path: Path) -> None:
    """More actions than the native config allows are a 400 here, with the
    same message a native ``browser_actions`` list would get."""
    client, page = _client(tmp_path)
    response = client.post(
        "/v2/scrape", json={"url": page, "actions": [{"type": "click", "selector": "#a"}] * 30}
    )
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert "at most 25" in response.json()["error"]


def test_oversized_actions_are_refused_on_batch_scrape_too(tmp_path: Path) -> None:
    client, page = _client(tmp_path)
    response = client.post(
        "/v2/batch/scrape",
        json={"urls": [page], "actions": [{"type": "click", "selector": "#a"}] * 26},
    )
    assert response.status_code == 400
    assert "at most 25" in response.json()["error"]


def test_actions_within_the_bounds_still_work(tmp_path: Path) -> None:
    """A valid action list is accepted and translated (a Firecrawl ``wait``
    becomes the native ``{'type': 'wait', 'ms': ...}`` step)."""
    from agentcrawl.firecrawl_compat import _scrape_fields

    client, page = _client(tmp_path)
    response = client.post(
        "/v2/scrape", json={"url": page, "actions": [{"type": "wait", "milliseconds": 100}]}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert _scrape_fields({"actions": [{"type": "wait", "milliseconds": 100}]})["config"][
        "browser_actions"
    ] == [{"type": "wait", "ms": 100}]


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


def test_batch_scrape_deduplicates_repeated_urls(tmp_path: Path, run_jobs_inline) -> None:
    """Firecrawl deduplicates a batch; the compat is the contract. A repeated
    URL is scraped once and billed once (creditsUsed), not twice."""
    client, page = _client(tmp_path)
    started = client.post(
        "/v2/batch/scrape", json={"urls": [page, page], "formats": ["markdown"]}
    ).json()
    assert started["success"] is True
    status = _wait(client, f"/v2/batch/scrape/{started['id']}")
    assert status["status"] == "completed"
    assert status["completed"] == 1
    assert status["creditsUsed"] == 1
    assert len(status["data"]) == 1


def test_batch_scrape_keeps_distinct_urls(tmp_path: Path, run_jobs_inline) -> None:
    """Deduplication never drops a distinct URL: a batch of distinct pages
    still completes with every document present. (Documents come back in the
    store's order — created_at, url — which is not the sent order; that is
    pre-existing storage behavior, not something the dedupe changes.)"""
    client, page = _client(tmp_path)
    second = tmp_path / "second.html"
    second.write_text(
        "<html><head><title>Second</title></head><body><main><p>second page</p></main></body></html>",
        encoding="utf-8",
    )
    started = client.post(
        "/v2/batch/scrape", json={"urls": [str(second), page], "formats": ["markdown"]}
    ).json()
    assert started["success"] is True
    status = _wait(client, f"/v2/batch/scrape/{started['id']}")
    assert status["status"] == "completed"
    assert status["completed"] == 2
    assert {doc["metadata"]["sourceURL"] for doc in status["data"]} == {str(second), page}


def test_run_batch_without_urls_fails_cleanly() -> None:
    """Defense in depth: /v2/batch/scrape refuses an empty list and /v1
    validates its input, but a direct caller of _run_batch must get a clear
    ValueError here, not an IndexError on urls[0] inside the worker."""
    from agentcrawl.server import _run_batch

    with pytest.raises(ValueError, match="no URLs"):
        _run_batch(None, {"urls": []}, None, None, None, None)


def test_batch_where_every_url_fails_is_a_failed_job(tmp_path: Path, run_jobs_inline) -> None:
    """Firecrawl distinguishes failed from completed: a batch whose only URL
    came back with errors must not look like a successful run. The per-page
    errors stay inspectable in the status data."""
    client, _page = _client(tmp_path)
    started = client.post(
        "/v2/batch/scrape", json={"urls": [str(tmp_path / "missing.html")]}
    ).json()
    assert started["success"] is True
    status = _wait(client, f"/v2/batch/scrape/{started['id']}")
    assert status["status"] == "failed"
    assert len(status["data"]) == 1
    assert status["data"][0]["metadata"].get("errorType")


def test_batch_with_partial_failures_still_completes(tmp_path: Path, run_jobs_inline) -> None:
    """One good page and one failing page is a completed run whose failures
    are recorded per page — the partial-success contract does not change."""
    client, page = _client(tmp_path)
    started = client.post(
        "/v2/batch/scrape", json={"urls": [page, str(tmp_path / "missing.html")]}
    ).json()
    assert started["success"] is True
    status = _wait(client, f"/v2/batch/scrape/{started['id']}")
    assert status["status"] == "completed"
    assert status["completed"] == 2
    errors = [bool(doc["metadata"].get("error")) for doc in status["data"]]
    assert errors == [False, True] or sorted(errors) == [False, True]


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
