from agentcrawl.storage import SQLiteStore


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
