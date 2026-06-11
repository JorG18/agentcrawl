from agentcrawl.storage import SQLiteStore


def test_cleanup_cache_checkpoints_wal(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    original_connect = SQLiteStore._connect

    class TrackingConnection:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

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
