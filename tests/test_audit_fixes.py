"""Tests covering the 4 bugs + 4 optimizations applied on 2026-06-28.

These tests live alongside the existing suite so they're easy to find when
the audit doc (archive/2026-06-28/CODE_AUDIT_2026-06-28.md) is reviewed.
Each test names the audit ID in the docstring for traceability.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import warnings
from collections import deque
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.crawler import _blocked_page_reason, _html_to_plain_text, _pop_ready_item
from agentcrawl.config import CrawlConfig
from agentcrawl.exceptions import FetchError
import agentcrawl.fetchers as fetchers_module
from agentcrawl.fetchers import _fetch_http
from agentcrawl.server import server
from agentcrawl.storage import SQLiteStore


# ---------------------------------------------------------------------------
# BUG #1 (Alta): multi-worker race in server.py
# ---------------------------------------------------------------------------


def test_acquire_schedule_lease_is_atomic_across_stores(tmp_path: Path) -> None:
    """Two SQLiteStore instances hitting the same DB should both observe
    the lease after the first acquires it; the second sees ``False`` and
    does not write a competing schedule_lock row."""
    database = tmp_path / "lease.db"
    store_a = SQLiteStore(database)
    store_b = SQLiteStore(database)

    job_id = store_a.create_job("crawl", {"url": "https://example.com"})
    assert store_a.acquire_schedule_lease(job_id, "instance-a", lease_seconds=60) is True
    assert store_b.acquire_schedule_lease(job_id, "instance-b", lease_seconds=60) is False
    # Releasing the first lease hands the slot back to a re-attempt.
    store_a.release_schedule_lease(job_id, "instance-a")
    assert store_b.acquire_schedule_lease(job_id, "instance-b", lease_seconds=60) is True


def test_acquire_schedule_lease_skips_cancelled_or_running(tmp_path: Path) -> None:
    database = tmp_path / "lease-status.db"
    store = SQLiteStore(database)

    job_id = store.create_job("crawl", {"url": "https://example.com"})
    assert store.claim_job(job_id)
    assert store.acquire_schedule_lease(job_id, "anybody") is False
    # Cancel-requested jobs must not re-acquire either.
    store.update_job(job_id, "queued")  # un-claim so we can simulate
    assert store.request_job_cancel(job_id)
    store.update_job(job_id, "queued")
    assert store.acquire_schedule_lease(job_id, "anybody") is False


def test_schedule_job_skips_when_other_instance_holds_lease(tmp_path: Path, monkeypatch) -> None:
    """In a multi-worker race the second ``schedule_job`` call from the same
    server instance must short-circuit when another instance already holds
    a live lease. We simulate the race by manually acquiring a lease and
    then calling ``server.schedule_job`` and asserting it returns ``False``
    without creating a Timer."""
    database = tmp_path / "schedule-race.db"
    server.store = SQLiteStore(database)
    server.instance_id = "instance-test"
    server.schedule_lease_seconds = 30.0
    server._workers_started = True
    monkeypatch.setattr(server, "_job_threads_lock", threading.Lock())

    job_id = server.store.create_job("crawl", {"url": "https://example.com"})
    assert server.store.acquire_schedule_lease(job_id, "competing-worker", lease_seconds=60)

    original_queue_put = server._job_queue.put
    put_calls: list[tuple] = []

    def fake_put(item):
        put_calls.append(item)
        return original_queue_put(item)

    monkeypatch.setattr(server._job_queue, "put", fake_put)
    scheduled = server.schedule_job(job_id, {"url": "https://example.com"}, None)
    assert scheduled is False
    assert put_calls == []


# ---------------------------------------------------------------------------
# BUG #2 (Media): audit trail preserved across retries
# ---------------------------------------------------------------------------


def test_fetch_http_attaches_audit_trail_on_terminal_failure(monkeypatch) -> None:
    """When all retries fail, the FetchError carries an ``audit_trail``
    attribute whose ``to_metadata`` exposes per-attempt records."""
    cfg = CrawlConfig(
        fetcher="http",
        audit=True,
        http_retries=2,
        http_retry_delay=0.0,
        timeout_ms=1000,
    )

    def fake_safe_urlopen(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(fetchers_module, "_safe_urlopen", fake_safe_urlopen)

    with pytest.raises(FetchError) as exc_info:
        _fetch_http("https://example.com/", cfg)

    err = exc_info.value
    assert hasattr(err, "audit_trail"), "FetchError should carry an audit_trail attribute"
    trail = err.audit_trail
    assert trail is not None
    metadata = trail.to_metadata()
    assert metadata["audit_request_count"] == 3  # initial + 2 retries
    assert {record["status"] for record in metadata["audit_records"]} == {503}


def test_scrape_surfaces_audit_trail_in_error_metadata(monkeypatch) -> None:
    """``AgentCrawl.scrape`` must merge the audit_trail metadata into the
    ScrapeDocument.metadata when the underlying fetch raised.

    We monkeypatch ``AgentCrawl._fetch_http`` directly to short-circuit the
    http→browser fallback path in the dispatcher (otherwise a fake urlopen
    failure would route us into the browser fetcher and miss the
    audit_trail surface contract)."""
    from agentcrawl.exceptions import FetchError

    cfg = CrawlConfig(
        fetcher="http",
        audit=True,
        http_retries=1,
        http_retry_delay=0.0,
        user_agent="audit-test",
    )

    from agentcrawl.airgap import AuditTrail

    trail = AuditTrail()
    trail.record(
        "GET", "https://example.com/", status=503, bytes_count=0, target_host="example.com"
    )
    trail.record(
        "GET", "https://example.com/", status=503, bytes_count=0, target_host="example.com"
    )

    def fake_fetch_http(*args, **kwargs):
        err = FetchError("HTTP fetch failed for https://example.com/: boom")
        err.audit_trail = trail
        raise err

    import agentcrawl.fetchers as fetchers_module

    monkeypatch.setattr(fetchers_module, "_fetch_http", fake_fetch_http)

    doc = AgentCrawl(cfg).scrape("https://example.com/")
    assert doc.errors
    assert doc.metadata["audit_request_count"] == 2
    assert doc.metadata["error_type"] == "fetch_error"


def test_no_audit_trail_attribute_when_audit_off() -> None:
    """Without audit=True, the FetchError must NOT carry an audit_trail
    attribute, so the existing error path stays untouched."""
    cfg = CrawlConfig(fetcher="http", audit=False, http_retries=0)
    import agentcrawl.fetchers as f

    original = f._safe_urlopen

    def fake_safe_urlopen(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 502, "boom", {}, None)

    f._safe_urlopen = fake_safe_urlopen
    try:
        with pytest.raises(FetchError) as exc_info:
            _fetch_http("https://example.com/", cfg)
        assert getattr(exc_info.value, "audit_trail", None) is None
    finally:
        f._safe_urlopen = original


# ---------------------------------------------------------------------------
# BUG #3 (Media/Baja): blocked-page detection strips HTML
# ---------------------------------------------------------------------------


def test_blocked_page_reason_strips_html_before_matching() -> None:
    """``<script>client challenge</script>`` alone must not trigger the
    heuristic; only user-visible text should."""
    script_only = "<html><script>client challenge</script></html>"
    assert _blocked_page_reason(script_only) == ""

    body_text = "<html><body>please disable any ad blockers</body></html>"
    assert _blocked_page_reason(body_text) == "disable any ad blockers"

    # The body+title variant from the existing test should still match.
    full = (
        "<html><head><title>Client Challenge</title></head>"
        "<body>required part of this site couldn\u2019t load</body></html>"
    )
    assert _blocked_page_reason(full) != ""


def test_html_to_plain_text_drops_scripts_and_tags() -> None:
    html = (
        "<script>function nope(){ return 'client challenge';}</script>"
        "<style>body{color:red}</style>"
        "<p>The user-facing challenge text.</p>"
    )
    plain = _html_to_plain_text(html)
    assert "client challenge" not in plain
    assert "color:red" not in plain
    assert "user-facing challenge text" in plain


# ---------------------------------------------------------------------------
# BUG #4 (Baja): CrawlConfig(dict) silent bypass
# ---------------------------------------------------------------------------


def test_crawl_config_warns_when_llm_field_receives_dict() -> None:
    """``CrawlConfig({'airgap': True})`` is the historical footgun: the
    dict lands in ``self.llm`` and ``airgap`` is never applied. The
    dataclass now warns via ``__post_init__``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = CrawlConfig({"airgap": True})
    assert cfg.airgap is False  # field default
    assert any(issubclass(w.category, UserWarning) for w in caught)
    assert any("from_dict" in str(w.message) for w in caught)


def test_crawl_config_no_warning_on_from_dict_path() -> None:
    """Going through ``from_dict`` is the recommended path; no warning
    should fire."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = CrawlConfig.from_dict({"airgap": True})
    assert cfg.airgap is True
    assert not any(issubclass(w.category, UserWarning) for w in caught)


# ---------------------------------------------------------------------------
# OPT #1 (Alto): tighten domain LIKE patterns
# ---------------------------------------------------------------------------


def test_domain_filter_excludes_suffix_collision(tmp_path: Path) -> None:
    """``example.com`` must NOT match ``example.com.evil.com`` because the
    three-pattern LIKE filter requires a path, a port, or an exact match."""
    store = SQLiteStore(tmp_path / "filter.db")
    job_a = store.create_job("crawl", {"url": "https://root.example/a"})
    job_b = store.create_job("crawl", {"url": "https://root.example/b"})
    job_c = store.create_job("crawl", {"url": "https://root.example/c"})

    for url, job_id in [
        ("https://example.com/page", job_a),
        ("https://example.com:443/page", job_b),
        ("https://example.com.evil.com/page", job_c),
    ]:
        with store._connect() as conn:
            conn.execute(
                """
                insert or replace into crawl_failures (
                    id, job_id, url, attempts, error_type, message, retryable,
                    status, failed_at, retried_at, resolved_at, created_at, updated_at
                ) values (?, ?, ?, 1, 'timeout', 'timeout', 1, 'open', ?, null, null, ?, ?)
                """,
                (
                    f"id-{url}",
                    job_id,
                    url,
                    time.time(),
                    time.time(),
                    time.time(),
                ),
            )

    failures = store.list_crawl_failures(domain="example.com", limit=10)
    urls = [str(failure["url"]) for failure in failures]
    assert "https://example.com/page" in urls
    assert "https://example.com:443/page" in urls
    assert "https://example.com.evil.com/page" not in urls


# ---------------------------------------------------------------------------
# OPT #2 (Medio): _pop_ready_item avoids rotation when all delayed
# ---------------------------------------------------------------------------


def test_pop_ready_item_returns_immediately_when_all_delayed() -> None:
    """When every item's ``ready_at`` is in the future, the function must
    return ``(None, earliest)`` without rotating the deque. We verify by
    checking that the deque order is preserved across the call."""
    queue = deque(
        [
            {"url": "https://a", "ready_at": 100.0, "depth": 0, "attempt": 0},
            {"url": "https://b", "ready_at": 200.0, "depth": 0, "attempt": 0},
            {"url": "https://c", "ready_at": 150.0, "depth": 0, "attempt": 0},
        ]
    )
    snapshot = list(queue)

    item, earliest = _pop_ready_item(queue, now=50.0)

    assert item is None
    assert earliest == 100.0
    # No rotation: the deque is back in its original order afterwards.
    assert list(queue) == snapshot


def test_pop_ready_item_pops_front_when_ready_immediately() -> None:
    """The post-fix contract returns ``earliest == min(ready_at across items)``
    even when a ready item was popped. The previously-returned ``None`` would
    have lost the soonest-deadline signal the caller still wants for logging."""
    queue = deque(
        [
            {"url": "https://a", "ready_at": 0.0, "depth": 0, "attempt": 0},
            {"url": "https://b", "ready_at": 50.0, "depth": 0, "attempt": 0},
        ]
    )

    item, earliest = _pop_ready_item(queue, now=100.0)

    assert item is not None
    assert item["url"] == "https://a"
    assert earliest == 0.0
    assert queue.popleft()["url"] == "https://b"


def test_pop_ready_item_finds_ready_item_when_front_is_delayed() -> None:
    """Regression for the audit's OPT #2 bug: a delayed front item must not
    be returned as ready when a later item is ready. Rotation must surface
    the first ready item rather than the delayed front."""
    queue = deque(
        [
            {"url": "https://a-delayed", "ready_at": 200.0, "depth": 0, "attempt": 0},
            {"url": "https://b-ready", "ready_at": 0.0, "depth": 0, "attempt": 0},
        ]
    )

    item, earliest = _pop_ready_item(queue, now=100.0)

    assert item is not None
    assert item["url"] == "https://b-ready"
    assert earliest == 0.0
    # Only the ready item was popped; the delayed one stays in the deque
    # at index 0 so the next loop iteration sees it again.
    remaining = list(queue)
    assert len(remaining) == 1
    assert remaining[0]["url"] == "https://a-delayed"


# ---------------------------------------------------------------------------
# OPT #3 (Medio): per-process migration cache
# ---------------------------------------------------------------------------


def test_sqlite_store_migration_runs_once_per_path(tmp_path: Path, monkeypatch) -> None:
    """Eager: the second ``SQLiteStore(path)`` on the same filesystem path
    within one process should skip the migration block. We prove it by counting
    calls to ``pragma table_info``. ``:memory:`` paths are excluded from the
    cache because in-memory DBs are scoped per-connection and would leak the
    empty schema across otherwise-unrelated connections."""
    database = tmp_path / "caching.db"
    calls: list[str] = []
    original_connect = SQLiteStore._connect

    class Tracking:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, params=()):
            calls.append(sql.lower())
            return self.conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(SQLiteStore, "_connect", lambda self: Tracking(original_connect(self)))

    store_a = SQLiteStore(database)
    after_first = sum(1 for c in calls if "pragma table_info" in c)
    assert after_first >= 1

    store_b = SQLiteStore(database)
    after_second = sum(1 for c in calls if "pragma table_info" in c)
    # The cache means store_b did NOT re-run pragma table_info.
    assert after_second == after_first
    # And both stores are usable.
    job_id_a = store_a.create_job("crawl", {"url": "https://example.com"})
    job_id_b = store_b.create_job("crawl", {"url": "https://other.example"})
    assert job_id_a != job_id_b


def test_sqlite_store_memory_path_unsupported_by_design() -> None:
    """``sqlite3.connect(":memory:")`` returns a *different* private DB for
    every connection. ``SQLiteStore`` opens a new connection per call
    (``_init`` runs in one, ``create_job`` opens another), so a schema
    created in ``_init`` is gone by the time a later method queries the
    table. Therefore ``SQLiteStore(":memory:")`` is **not supported** and
    the audit fix to OPT#3 keeps the per-process cache on disk-backed
    paths only. ``test_sqlite_store_filesystem_path_caches_migration``
    covers the supported path.
    """

    # We don't construct a ``SQLiteStore(":memory:")`` here on purpose --
    # any attempt would hit ``no such table: jobs``. This stub exists so
    # pytest still has a regression marker for the design decision.


# ---------------------------------------------------------------------------
# OPT #4 (Trivial): empty CSV export avoids mkdir
# ---------------------------------------------------------------------------
# Regression for OPT#4 lives in ``tests/test_csv_export.py::test_export_failures_csv_handles_empty_list``.
# We keep the audit-fixes file focused on the new assertions so the two
# suites don't drift.


# ---------------------------------------------------------------------------
# OPT #5 (Bloque C): _BROWSER_SEMAPHORE configurable via env var
# ---------------------------------------------------------------------------


def test_browser_semaphore_uses_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without AGENTCRAWL_BROWSER_CONCURRENCY the helper must default to 2."""
    monkeypatch.delenv("AGENTCRAWL_BROWSER_CONCURRENCY", raising=False)
    from agentcrawl import fetchers as fetchers_module
    from agentcrawl.fetchers import _get_browser_semaphore

    monkeypatch.setattr(fetchers_module, "_browser_sem", None)
    sem = _get_browser_semaphore()
    # Acquire two slots (the configured limit) before the third is rejected.
    a = sem.acquire(blocking=False)
    b = sem.acquire(blocking=False)
    c = sem.acquire(blocking=False)
    assert a is True
    assert b is True
    assert c is False  # third concurrent request is rejected
    sem.release()
    sem.release()


def test_browser_semaphore_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTCRAWL_BROWSER_CONCURRENCY=4 must yield a semaphore with value 4."""
    monkeypatch.setenv("AGENTCRAWL_BROWSER_CONCURRENCY", "4")
    from agentcrawl import fetchers as fetchers_module
    from agentcrawl.fetchers import _get_browser_semaphore

    monkeypatch.setattr(fetchers_module, "_browser_sem", None)
    sem = _get_browser_semaphore()
    # We can acquire up to 4 slots before the 5th is rejected.
    acquired = []
    for _ in range(5):
        if sem.acquire(blocking=False):
            acquired.append(True)
        else:
            acquired.append(False)
    assert acquired == [True, True, True, True, False]
    for _ in range(4):
        sem.release()


def test_browser_semaphore_floors_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTCRAWL_BROWSER_CONCURRENCY=0 must not break the semaphore."""
    monkeypatch.setenv("AGENTCRAWL_BROWSER_CONCURRENCY", "0")
    from agentcrawl import fetchers as fetchers_module
    from agentcrawl.fetchers import _get_browser_semaphore

    monkeypatch.setattr(fetchers_module, "_browser_sem", None)
    sem = _get_browser_semaphore()
    # Floor of 1 means we can still acquire one slot.
    assert sem.acquire(blocking=False) is True
    sem.release()
