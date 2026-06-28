from __future__ import annotations

from pathlib import Path
import queue
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl, ScrapeDocument
import agentcrawl.server as server_module
from agentcrawl.server import _run_crawl_job, app, server
from agentcrawl.storage import SQLiteStore


def test_scrape_cache_controls_and_stats(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<main><h1>Cached page</h1></main>", encoding="utf-8")
    server.store = SQLiteStore(tmp_path / "server.db")
    server.allow_local_files = True
    client = TestClient(app)

    uncached = client.post(
        "/v1/scrape",
        json={"url": str(page), "formats": ["markdown"], "cache": False},
    ).json()
    assert uncached["data"]["metadata"]["cache_enabled"] is False
    assert server.store.cache_count() == 0

    first = client.post(
        "/v1/scrape",
        json={"url": str(page), "formats": ["markdown"], "cache_ttl_seconds": 120},
    ).json()
    second = client.post(
        "/v1/scrape",
        json={"url": str(page), "formats": ["markdown"], "cache_ttl_seconds": 120},
    ).json()
    assert first["data"]["metadata"]["cache_hit"] is False
    assert second["data"]["metadata"]["cache_hit"] is True

    stats = client.get("/v1/stats").json()
    assert stats["data"]["cache_entries"] == 1
    assert stats["data"]["cache_by_domain"] == {"local": 1}
    assert stats["data"]["crawl_queue"] == {
        "ready": 0,
        "delayed": 0,
        "running": 0,
        "cancelling": 0,
    }
    assert stats["data"]["crawl_failures"] == {
        "by_status": {},
        "open_retryable": 0,
        "open_by_error_type": {},
    }


def test_clear_cache_by_domain_and_url(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "cache.db")
    store.set_cache("one", "https://example.com/a", {"success": True}, 120)
    store.set_cache("two", "https://example.com/b", {"success": True}, 120)
    store.set_cache("three", "https://other.example/c", {"success": True}, 120)
    server.store = store
    client = TestClient(app)

    by_url = client.delete("/v1/cache", params={"url": "https://other.example/c"}).json()
    assert by_url["data"]["deleted"] == 1

    by_domain = client.delete("/v1/cache", params={"domain": "example.com"}).json()
    assert by_domain["data"]["deleted"] == 2
    assert store.cache_count() == 0


def test_clear_cache_rejects_domain_and_url_together(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "cache.db")
    client = TestClient(app)

    response = client.delete(
        "/v1/cache",
        params={"domain": "example.com", "url": "https://example.com"},
    )

    assert response.status_code == 400


def test_cancel_queued_job_and_expose_progress(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "jobs.db")
    job_id = server.store.create_job("crawl", {"url": "https://example.com"})
    client = TestClient(app)

    response = client.delete(f"/v1/jobs/{job_id}")
    job = client.get(f"/v1/jobs/{job_id}").json()["data"]

    assert response.status_code == 200
    assert job["status"] == "cancelling"
    assert job["cancel_requested"] is True
    assert job["progress"] == {"visited": 0, "pending": 1, "failed": 0, "discovered": 1}


def test_completed_job_cannot_be_cancelled(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "jobs.db")
    job_id = server.store.create_job("crawl", {"url": "https://example.com"})
    server.store.update_job(job_id, "completed", result={"documents": []})
    client = TestClient(app)

    response = client.delete(f"/v1/jobs/{job_id}")

    assert response.status_code == 409


def test_public_server_rejects_local_files_and_private_targets(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "security.db")
    server.allow_local_files = False
    server.allow_private_network = False
    client = TestClient(app)

    local_file = client.post("/v1/scrape", json={"url": "/etc/passwd"})
    localhost = client.post("/v1/scrape", json={"url": "http://127.0.0.1:8000/health"})
    credentials = client.post("/v1/scrape", json={"url": "https://user:pass@example.com"})
    override = client.post(
        "/v1/scrape",
        json={
            "url": "http://127.0.0.1:8000/health",
            "config": {"allow_private_network": True},
        },
    )

    assert local_file.status_code == 400
    assert localhost.status_code == 400
    assert credentials.status_code == 400
    assert override.status_code == 400


def test_authentication_fails_closed_and_returns_key_fingerprint(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "auth.db")
    server.auth_enabled = True
    client = TestClient(app)

    unconfigured = client.get("/v1/usage", headers={"authorization": "Bearer any-key"})
    assert unconfigured.status_code == 503

    server.api_keys = {"secret-key"}
    invalid = client.get("/v1/usage", headers={"authorization": "Bearer wrong-key"})
    valid = client.get("/v1/usage", headers={"authorization": "Bearer secret-key"})

    assert invalid.status_code == 403
    assert valid.status_code == 200


def test_per_key_rate_limit(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "rate.db")
    server.auth_enabled = True
    server.api_keys = {"rate-key"}
    server.rate_limit_per_minute = 2
    server._rate_windows = {}
    client = TestClient(app)
    headers = {"authorization": "Bearer rate-key"}

    assert client.get("/v1/usage", headers=headers).status_code == 200
    assert client.get("/v1/usage", headers=headers).status_code == 200
    assert client.get("/v1/usage", headers=headers).status_code == 429


def test_rate_limit_uses_sliding_window_not_fixed_reset(monkeypatch) -> None:
    current_time = 0.0

    def fake_monotonic() -> float:
        return current_time

    monkeypatch.setattr(server_module.time, "monotonic", fake_monotonic)
    server.rate_limit_per_minute = 3
    server._rate_windows = {}

    current_time = 0.0
    server.check_rate_limit("api-key")
    current_time = 58.9
    server.check_rate_limit("api-key")
    current_time = 59.9
    server.check_rate_limit("api-key")
    current_time = 60.1
    server.check_rate_limit("api-key")
    current_time = 60.2

    with pytest.raises(server_module.HTTPException) as exc_info:
        server.check_rate_limit("api-key")

    assert exc_info.value.status_code == 429


def test_running_job_is_requeued_with_checkpoint_after_restart(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "restart.db"
    store = SQLiteStore(database)
    payload = {
        "url": "https://example.com/",
        "max_pages": 3,
        "max_depth": 1,
        "include": None,
        "exclude": None,
        "config": {"respect_robots_txt": False},
        "wait": False,
    }
    job_id = store.create_job("crawl", payload)
    assert store.claim_job(job_id)
    store.save_job_checkpoint(
        job_id,
        {
            "version": 1,
            "root": "https://example.com/",
            "queue": [["https://example.com/pending", 1]],
            "queued": ["https://example.com/", "https://example.com/pending"],
            "visited": ["https://example.com/"],
            "discovered": ["https://example.com/", "https://example.com/pending"],
            "errors": [],
            "failed_urls": [],
        },
        {"visited": 1, "pending": 1, "failed": 0, "discovered": 2},
        {
            "url": "https://example.com/",
            "markdown": "# Root",
            "text": "Root",
            "html": "",
            "links": ["https://example.com/pending"],
            "metadata": {},
            "errors": [],
        },
    )

    restarted_store = SQLiteStore(database)
    assert restarted_store.get_job(job_id)["status"] == "running"
    assert restarted_store.prepare_restart_recovery() == 1
    assert restarted_store.get_job(job_id)["status"] == "queued"
    calls: list[str] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        calls.append(source)
        return ScrapeDocument(url=source, markdown="# Pending", text="Pending")

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    server.store = restarted_store
    _run_crawl_job(job_id, payload, None)

    job = restarted_store.get_job(job_id)
    assert calls == ["https://example.com/pending"]
    assert job["status"] == "completed"
    assert job["progress"]["visited"] == 2
    assert len(job["result"]["documents"]) == 2


def test_existing_database_migrates_durable_job_columns(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            create table jobs (
                id text primary key,
                type text not null,
                status text not null,
                request_json text not null,
                result_json text,
                error text,
                progress_json text,
                cancel_requested integer not null default 0,
                created_at real not null,
                updated_at real not null
            );
            create table usage_events (
                id text primary key, api_key text, endpoint text not null,
                units integer not null, created_at real not null
            );
            create table scrape_cache (
                cache_key text primary key, url text not null,
                response_json text not null, created_at real not null,
                expires_at real not null
            );
            """
        )

    store = SQLiteStore(database)
    job_id, created = store.create_or_get_job(
        "crawl",
        {"url": "https://example.com"},
        owner_key="owner",
        idempotency_key="migration-test",
    )

    assert created is True
    assert store.get_job(job_id)["status"] == "queued"
    with sqlite3.connect(database) as conn:
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_crawl_idempotency_key_deduplicates_and_rejects_changed_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    page = tmp_path / "page.html"
    other = tmp_path / "other.html"
    page.write_text("<h1>Page</h1>", encoding="utf-8")
    other.write_text("<h1>Other</h1>", encoding="utf-8")
    server.store = SQLiteStore(tmp_path / "idempotency.db")
    server.allow_local_files = True
    monkeypatch.setattr(server, "schedule_job", lambda *args: True)
    client = TestClient(app)
    headers = {"Idempotency-Key": "crawl-once"}

    first = client.post("/v1/crawl", json={"url": str(page)}, headers=headers)
    repeated = client.post("/v1/crawl", json={"url": str(page)}, headers=headers)
    conflict = client.post("/v1/crawl", json={"url": str(other)}, headers=headers)

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["job_id"] == first.json()["job_id"]
    assert repeated.json()["deduplicated"] is True
    assert conflict.status_code == 409


def test_retrying_job_is_requeued_and_releases_worker(tmp_path: Path, monkeypatch) -> None:
    store = SQLiteStore(tmp_path / "retry.db")
    payload = {
        "url": "https://example.com/unstable",
        "max_pages": 1,
        "max_depth": 0,
        "include": None,
        "exclude": None,
        "config": {
            "respect_robots_txt": False,
            "crawl_url_retries": 2,
            "crawl_retry_delay": 30.0,
        },
        "wait": False,
    }
    job_id = store.create_job("crawl", payload)
    scheduled: list[tuple[str, float]] = []

    def failing_scrape(self, source, formats=None, only_main_content=None):
        return ScrapeDocument(url=source, markdown="", text="", errors=["timeout"])

    monkeypatch.setattr(AgentCrawl, "scrape", failing_scrape)
    monkeypatch.setattr(
        server,
        "schedule_job",
        lambda job_id, payload, api_key, available_at=0.0: (
            scheduled.append((job_id, available_at)) or True
        ),
    )
    server.store = store

    _run_crawl_job(job_id, payload, None)

    job = store.get_job(job_id)
    events = store.list_job_events(job_id)
    assert job["status"] == "queued"
    assert job["available_at"] > 0
    assert job["progress"]["retries"] == 1
    assert [event["event_type"] for event in events] == [
        "created",
        "claimed",
        "retry_scheduled",
    ]
    assert scheduled == [(job_id, job["available_at"])]
    assert server._job_semaphore.acquire(blocking=False)
    server._job_semaphore.release()


def test_job_events_endpoint_and_stats(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "events.db")
    job_id = store.create_job("crawl", {"url": "https://example.com"})
    assert store.claim_job(job_id)
    store.update_job(job_id, "completed", result={"documents": [], "metadata": {}})
    server.store = store
    client = TestClient(app)

    events = client.get(f"/v1/jobs/{job_id}/events").json()["data"]["events"]
    stats = client.get("/v1/stats").json()["data"]

    assert [event["event_type"] for event in events] == ["created", "claimed", "completed"]
    assert stats["job_events"] == {"claimed": 1, "completed": 1, "created": 1}
    assert stats["crawl_queue"] == {
        "ready": 0,
        "delayed": 0,
        "running": 0,
        "cancelling": 0,
    }
    assert stats["crawl_failures"] == {
        "by_status": {},
        "open_retryable": 0,
        "open_by_error_type": {},
    }


def test_terminal_failures_can_be_inspected_and_retried_without_duplicate_documents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "failures.db")
    payload = {
        "url": "https://example.com/",
        "max_pages": 1,
        "max_depth": 0,
        "include": None,
        "exclude": None,
        "config": {"respect_robots_txt": False},
        "wait": False,
    }
    job_id = store.create_job("crawl", payload)
    store.update_job(
        job_id,
        "completed",
        result={
            "source": "https://example.com/",
            "documents": [
                {
                    "url": "https://example.com/",
                    "markdown": "# Root",
                    "text": "Root",
                    "html": "",
                    "links": [],
                    "metadata": {},
                    "errors": [],
                }
            ],
            "visited_urls": ["https://example.com/"],
            "discovered_urls": ["https://example.com/", "https://example.com/retry"],
            "errors": ["https://example.com/retry: timeout"],
            "metadata": {
                "terminal_failures": [
                    {
                        "url": "https://example.com/retry",
                        "attempts": 3,
                        "error_type": "timeout",
                        "message": "timeout",
                        "retryable": True,
                        "failed_at": time.time(),
                    }
                ]
            },
        },
    )
    server.store = store
    monkeypatch.setattr(server, "schedule_job", lambda *args, **kwargs: True)
    client = TestClient(app)

    failures = client.get(f"/v1/jobs/{job_id}/failures").json()["data"]["failures"]
    stats_before_retry = client.get("/v1/stats").json()["data"]
    retry = client.post(
        f"/v1/jobs/{job_id}/failures/retry",
        json={"failure_ids": [failures[0]["id"]]},
    ).json()["data"]
    queued = store.get_job(job_id)

    assert failures[0]["url"] == "https://example.com/retry"
    assert failures[0]["retryable"] is True
    assert stats_before_retry["crawl_failures"] == {
        "by_status": {"open": 1},
        "open_retryable": 1,
        "open_by_error_type": {"timeout": 1},
    }
    assert retry["retried"] == 1
    assert queued["status"] == "queued"
    assert queued["progress"]["pending"] == 1

    def recovered_scrape(self, source, formats=None, only_main_content=None):
        return ScrapeDocument(url=source, markdown="# Recovered", text="Recovered")

    monkeypatch.setattr(AgentCrawl, "scrape", recovered_scrape)
    _run_crawl_job(job_id, queued["request"], None)

    completed = store.get_job(job_id)
    resolved = store.list_crawl_failures(job_id=job_id, status="resolved")
    assert completed["status"] == "completed"
    assert completed["result"]["pagination"]["total"] == 2
    assert {document["url"] for document in completed["result"]["documents"]} == {
        "https://example.com/",
        "https://example.com/retry",
    }
    assert resolved[0]["url"] == "https://example.com/retry"


def test_completed_job_documents_are_paginated(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "pagination.db")
    job_id = store.create_job("crawl", {"url": "https://example.com"})
    documents = [
        {
            "url": f"https://example.com/{index}",
            "markdown": f"# Page {index}",
            "text": f"Page {index}",
            "html": "",
            "links": [],
            "metadata": {},
            "errors": [],
        }
        for index in range(5)
    ]
    store.update_job(
        job_id,
        "completed",
        result={
            "source": "https://example.com",
            "documents": documents,
            "visited_urls": [document["url"] for document in documents],
            "discovered_urls": [document["url"] for document in documents],
            "errors": [],
            "metadata": {},
        },
    )
    server.store = store
    client = TestClient(app)

    first = client.get(f"/v1/jobs/{job_id}", params={"offset": 0, "limit": 2}).json()["data"]
    second = client.get(f"/v1/jobs/{job_id}", params={"offset": 2, "limit": 2}).json()["data"]

    assert len(first["result"]["documents"]) == 2
    assert first["result"]["pagination"] == {
        "offset": 0,
        "limit": 2,
        "returned": 2,
        "total": 5,
        "has_more": True,
    }
    assert [document["url"] for document in second["result"]["documents"]] == [
        "https://example.com/2",
        "https://example.com/3",
    ]
    assert second["result"]["pagination"]["has_more"] is True


def test_legacy_embedded_job_documents_remain_paginated(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "legacy-results.db")
    job_id = store.create_job("crawl", {"url": "https://example.com"})
    legacy_documents = [
        {"url": f"https://example.com/{index}", "markdown": str(index), "text": str(index)}
        for index in range(3)
    ]
    with store._connect() as conn:
        conn.execute(
            "update jobs set status = 'completed', result_json = ? where id = ?",
            (
                __import__("json").dumps(
                    {"documents": legacy_documents, "metadata": {}, "errors": []}
                ),
                job_id,
            ),
        )

    job = store.get_job(job_id, document_offset=1, document_limit=1)

    assert [document["url"] for document in job["result"]["documents"]] == ["https://example.com/1"]
    assert job["result"]["pagination"]["total"] == 3
    assert job["result"]["pagination"]["has_more"] is True


def test_owner_api_key_bypasses_rate_limit(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "owner-rate.db")
    server.auth_enabled = True
    server.api_keys = {"owner-key"}
    server.owner_api_keys = {"owner-key"}
    server.rate_limit_per_minute = 1
    server._rate_windows = {}
    client = TestClient(app)
    headers = {"authorization": "Bearer owner-key"}

    assert client.get("/v1/usage", headers=headers).status_code == 200
    assert client.get("/v1/usage", headers=headers).status_code == 200


def test_job_scheduler_preserves_fifo_order(monkeypatch, tmp_path) -> None:
    """The queue worker must drain jobs in FIFO order. After the 2026-06-28
    audit added cross-process scheduling leases, ``schedule_job`` requires a
    real ``queued`` row in the jobs table to acquire a lease. We short-circuit
    the lease here (the FIFO ordering is what we're testing) and supply two
    matching DB rows.

    We use a filesystem-backed SQLiteStore under ``tmp_path`` rather than
    ``":memory:"`` because ``sqlite3.connect(":memory:")`` returns a *fresh
    private* in-memory database per connection, so the schema created in
    ``_init`` would be gone by the time ``create_job`` opens its second
    connection.
    """
    original_queue = server._job_queue
    original_started = server._workers_started
    original_queued = server._queued_jobs
    original_threads = server._job_threads
    original_store = server.store
    try:
        server._job_queue = queue.Queue()
        server._workers_started = True
        server._queued_jobs = set()
        server._job_threads = {}
        store = SQLiteStore(tmp_path / "fifo.db")
        server.store = store
        monkeypatch.setattr(
            SQLiteStore, "acquire_schedule_lease", lambda self, *a, **kw: True
        )
        store.create_job("crawl", {"url": "https://one.example"})
        store.create_job("crawl", {"url": "https://two.example"})

        assert server.schedule_job("first", {"url": "https://one.example"}, None)
        assert server.schedule_job("second", {"url": "https://two.example"}, None)

        assert server._job_queue.get_nowait()[0] == "first"
        assert server._job_queue.get_nowait()[0] == "second"
    finally:
        server._job_queue = original_queue
        server._workers_started = original_started
        server._queued_jobs = original_queued
        server._job_threads = original_threads
        server.store = original_store


def test_domain_slot_limits_same_domain_concurrency() -> None:
    original_limit = server.domain_max_concurrency
    original_delay = server.domain_min_delay
    original_semaphores = server._domain_semaphores
    original_seen = server._domain_last_seen
    server.domain_max_concurrency = 1
    server.domain_min_delay = 0
    server._domain_semaphores = {}
    server._domain_last_seen = {}
    active = 0
    maximum = 0
    lock = threading.Lock()

    def enter_slot() -> None:
        nonlocal active, maximum
        with server.domain_slot("https://example.com/page"):
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1

    try:
        threads = [threading.Thread(target=enter_slot) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert maximum == 1
    finally:
        server.domain_max_concurrency = original_limit
        server.domain_min_delay = original_delay
        server._domain_semaphores = original_semaphores
        server._domain_last_seen = original_seen
