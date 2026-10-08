import pytest

from agentcrawl.storage import SQLiteStore


def test_prepare_restart_recovery_leaves_a_live_lease_alone(tmp_path) -> None:
    """A running job whose schedule lease is still alive belongs to a live
    worker: another process starting up must not "recover" it (that paused
    the job until the lease expired and stamped a false interruption error)."""
    store = SQLiteStore(tmp_path / "live-lease.db")
    job = store.create_job("crawl", {"url": "https://example.com"})
    assert store.acquire_schedule_lease(job, "pid-alive", lease_seconds=300)
    assert store.claim_job(job)  # status = running, lease still held

    assert store.prepare_restart_recovery() == 0
    assert store.get_job(job)["status"] == "running"
    assert store.get_job(job)["error"] is None  # no false interruption stamp


def test_prepare_restart_recovery_requeues_an_expired_lease(tmp_path) -> None:
    """The case the function exists for: the worker died holding the job —
    its lease has expired, so the restart requeues it."""
    store = SQLiteStore(tmp_path / "expired-lease.db")
    job = store.create_job("crawl", {"url": "https://example.com"})
    store.acquire_schedule_lease(job, "pid-dead", lease_seconds=300)
    assert store.claim_job(job)
    # Simulate the crash: the lease aged out while the job stays 'running'.
    import sqlite3

    with sqlite3.connect(tmp_path / "expired-lease.db") as conn:
        conn.execute(
            "update jobs set schedule_lock_expires_at = ? where id = ?",
            (0.0, job),
        )

    assert store.prepare_restart_recovery() == 1
    assert store.get_job(job)["status"] == "queued"


def test_prepare_restart_recovery_counts_real_transitions_under_a_race(tmp_path) -> None:
    """Concurrent recover passes over the same crashed job each ran the
    UPDATE and counted the running rows they saw; the total reported more
    recoveries than jobs. The count is now the rows each pass actually
    transitioned."""
    import threading

    store = SQLiteStore(tmp_path / "race.db")
    job = store.create_job("crawl", {"url": "https://example.com"})
    assert store.claim_job(job)  # running, no lease (crash before claiming one)

    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(store.prepare_restart_recovery()))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 1  # exactly one pass transitioned the job
    assert store.get_job(job)["status"] == "queued"


def test_prepare_restart_recovery_only_sweeps_jobs_it_finalizes(tmp_path) -> None:
    """Recovering a crashed 'running' job must not delete documents that
    belong to unrelated cancelled jobs from earlier runs."""
    store = SQLiteStore(tmp_path / "recovery.db")

    crashed = store.create_job("crawl", {"url": "https://a.example"})
    store.claim_job(crashed)  # status = running
    store.save_job_checkpoint(
        crashed,
        {"version": 2, "root": "https://a.example", "queue": []},
        {"visited": 1},
        document={"url": "https://a.example/page", "markdown": "# Kept"},
    )

    old_cancelled = store.create_job("crawl", {"url": "https://b.example"})
    store.update_job(
        old_cancelled,
        "completed",
        result={"documents": [{"url": "https://b.example/page", "markdown": "# Old"}]},
    )
    store.update_job(old_cancelled, "cancelled")
    assert store.get_job_documents(old_cancelled) == []  # normal cancel clears docs

    # Give the old cancelled job documents again, as an operator-visible
    # artifact from a restore or earlier version.
    store.save_job_checkpoint(
        old_cancelled,
        {"version": 2, "root": "https://b.example", "queue": []},
        {"visited": 1},
        document={"url": "https://b.example/page", "markdown": "# Historical"},
    )
    assert store.get_job_documents(old_cancelled)

    assert store.prepare_restart_recovery() == 1

    # The recovered job keeps its documents for the resumed run.
    assert store.get_job(crashed)["status"] == "queued"
    assert store.get_job_documents(crashed)
    # The historical cancelled job's documents are untouched.
    assert store.get_job_documents(old_cancelled)


def test_cleanup_cache_checkpoints_wal(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    original_connect = SQLiteStore._connect

    class TrackingConnection:
        def __init__(self, connection_context):
            self.connection_context = connection_context

        def __enter__(self):
            self.conn = self.connection_context.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection_context.__exit__(*args)

        def execute(self, sql, parameters=()):
            calls.append(sql.lower())
            return self.conn.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    def tracking_connect(self):
        return TrackingConnection(original_connect(self))

    monkeypatch.setattr(SQLiteStore, "_connect", tracking_connect)
    store = SQLiteStore(tmp_path / "storage.db")

    store.cleanup_cache()

    assert any("wal_checkpoint" in sql for sql in calls)


def test_clear_cache_by_domain_deletes_only_matching_netloc(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "storage.db")
    store.set_cache("a", "https://target.example/one", {"ok": 1}, ttl_seconds=300)
    store.set_cache("b", "https://target.example:443/two", {"ok": 2}, ttl_seconds=300)
    store.set_cache("c", "https://other.example/three", {"ok": 3}, ttl_seconds=300)

    deleted = store.clear_cache(domain="target.example")

    assert deleted == 2
    assert store.get_cache("a") is None
    assert store.get_cache("b") is None
    assert store.get_cache("c") == {"ok": 3}


def _completed_crawl_with_failure(store: SQLiteStore, url: str, failed_at: float) -> str:
    job_id = store.create_job("crawl", {"url": "https://root.example/"})
    assert store.claim_job(job_id)
    store.update_job(
        job_id,
        "completed",
        {
            "documents": [],
            "metadata": {
                "terminal_failures": [
                    {
                        "url": url,
                        "attempts": 1,
                        "error_type": "timeout",
                        "message": "timeout",
                        "retryable": True,
                        "failed_at": failed_at,
                    }
                ]
            },
        },
    )
    return job_id


def test_list_crawl_failures_filters_domain_before_pagination(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "storage.db")
    _completed_crawl_with_failure(store, "https://other.example/newer", 200.0)
    target_job = _completed_crawl_with_failure(store, "https://target.example/older", 100.0)

    failures = store.list_crawl_failures(domain="target.example", limit=1)

    assert len(failures) == 1
    assert failures[0]["job_id"] == target_job
    assert failures[0]["url"] == "https://target.example/older"


def test_a_cancel_that_lands_during_the_last_page_is_not_overwritten(tmp_path) -> None:
    """The worker checks for a cancel only between pages: a cancel that
    arrives while the last page is scraping was accepted by the API
    (status 'cancelling') and then silently replaced by 'completed'. The
    final write must honour it, keeping the pages already scraped."""
    store = SQLiteStore(tmp_path / "late-cancel.db")
    job = store.create_job("crawl", {"url": "https://example.com"})
    assert store.claim_job(job)
    assert store.request_job_cancel(job)  # lands mid-page

    document = {"url": "https://example.com/", "markdown": "ok"}
    store.update_job(job, "completed", result={"documents": [document], "metadata": {}})

    assert store.get_job(job)["status"] == "cancelled"
    assert [doc["url"] for doc in store.get_job_documents(job)] == ["https://example.com/"]


def test_a_job_without_a_cancel_completes_as_before(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "no-cancel.db")
    job = store.create_job("crawl", {"url": "https://example.com"})
    assert store.claim_job(job)
    store.update_job(job, "completed", result={"documents": [], "metadata": {}})
    assert store.get_job(job)["status"] == "completed"


def test_store_closes_every_connection(tmp_path, monkeypatch) -> None:
    import sqlite3

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    store = SQLiteStore(tmp_path / "closing.db")
    job_id = store.create_job("crawl", {"url": "https://example.com"})
    assert store.get_job(job_id) is not None

    assert len(opened) >= 3
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("select 1")  # raises only on a closed connection


def test_store_rolls_back_a_failed_write(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "rollback.db")
    with pytest.raises(RuntimeError):
        with store._connect() as conn:
            conn.execute(
                "insert into jobs (id, type, status, request_json, created_at, updated_at)"
                " values ('x', 'crawl', 'queued', '{}', 0, 0)"
            )
            raise RuntimeError("boom")
    assert store.get_job("x") is None
