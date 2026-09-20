"""Regression tests for SEC-8: per-key scoping of the cache and aggregates.

Per-key isolation landed for jobs and failures first. The cache and the
aggregate endpoints were left global, so a regular key could read which domains
another key had scraped and wipe every key's cache with an unfiltered
``DELETE /v1/cache``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl.server import ScrapeRequest, _scrape_cache_key, app, server
from agentcrawl.storage import SQLiteStore

client = TestClient(app, raise_server_exceptions=False)
KEY_A = "key-a"
KEY_B = "key-b"
PRIVATE_DOMAIN = "key-a-internal.example.com"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def auth_two_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Authenticated server with two regular keys and an isolated database."""
    monkeypatch.setattr(server, "store", SQLiteStore(tmp_path / "scoping.db"))
    monkeypatch.setattr(server, "auth_enabled", True)
    monkeypatch.setattr(server, "api_keys", {KEY_A, KEY_B})
    monkeypatch.setattr(server, "owner_api_keys", set())
    monkeypatch.setattr(server, "dashboard_public", False)
    monkeypatch.setattr(server, "rate_limit_per_minute", 0)
    yield


def _seed_key_a() -> str:
    fingerprint = _fingerprint(KEY_A)
    server.store.set_cache(
        "cache-key-a",
        f"https://{PRIVATE_DOMAIN}/page",
        {"ok": True},
        600,
        owner_key=fingerprint,
    )
    server.store.create_job(
        "crawl", {"url": f"https://{PRIVATE_DOMAIN}/", "config": {}}, owner_key=fingerprint
    )
    server.store.record_usage(fingerprint, "/v1/scrape", 7)
    return fingerprint


def test_stats_scope_cache_and_jobs_to_the_calling_key(auth_two_keys) -> None:
    _seed_key_a()

    data_a = client.get("/v1/stats", headers=_auth(KEY_A)).json()["data"]
    assert data_a["cache_by_domain"] == {PRIVATE_DOMAIN: 1}
    assert data_a["usage_by_endpoint"]["/v1/scrape"] == 7
    assert data_a["cache_entries"] == 1
    assert sum(data_a["jobs"].values()) == 1

    data_b = client.get("/v1/stats", headers=_auth(KEY_B)).json()["data"]
    assert data_b["cache_by_domain"] == {}
    assert data_b["usage_by_endpoint"] == {}
    assert data_b["jobs"] == {}
    assert data_b["cache_entries"] == 0
    assert data_b["usage_total"] == 0


def test_dashboard_summary_is_scoped_too(auth_two_keys) -> None:
    _seed_key_a()

    summary_b = client.get("/api/dashboard/summary", headers=_auth(KEY_B)).json()["data"]
    assert summary_b["cache_by_domain"] == {}
    assert summary_b["totals"]["jobs"] == 0
    assert summary_b["totals"]["usage_units"] == 0

    summary_a = client.get("/api/dashboard/summary", headers=_auth(KEY_A)).json()["data"]
    assert summary_a["cache_by_domain"] == {PRIVATE_DOMAIN: 1}


def test_owner_key_still_sees_every_key(auth_two_keys, monkeypatch) -> None:
    fingerprint_a = _seed_key_a()
    monkeypatch.setattr(server, "owner_api_keys", {KEY_B})

    data = client.get("/v1/stats", headers=_auth(KEY_B)).json()["data"]

    assert data["cache_by_domain"] == {PRIVATE_DOMAIN: 1}
    assert data["jobs"] == {"queued": 1}
    assert data["cache_entries"] == 1
    # ...while its own usage stays its own.
    assert data["usage_total"] == 0
    assert server.store.cache_count(owner_key=fingerprint_a) == 1


def test_unfiltered_cache_delete_only_clears_the_callers_own_rows(auth_two_keys) -> None:
    fingerprint_a = _seed_key_a()
    fingerprint_b = _fingerprint(KEY_B)
    server.store.set_cache(
        "cache-key-b",
        "https://key-b-internal.example.com/page",
        {"ok": True},
        600,
        owner_key=fingerprint_b,
    )

    response = client.delete("/v1/cache", headers=_auth(KEY_B))

    assert response.status_code == 200
    assert response.json()["data"]["deleted"] == 1
    # Key A's entry is untouched; the operator can still clear everything.
    assert server.store.cache_count(owner_key=fingerprint_a) == 1
    assert server.store.cache_count(owner_key=fingerprint_b) == 0
    assert server.store.cache_count() == 1


def test_scrape_cache_key_is_scoped_to_the_owner() -> None:
    request = ScrapeRequest(url="https://example.com/")
    assert _scrape_cache_key(request, "a") != _scrape_cache_key(request, "b")
    assert _scrape_cache_key(request, "a") == _scrape_cache_key(request, "a")


def test_legacy_database_without_owner_key_migrates(tmp_path: Path) -> None:
    database = tmp_path / "legacy-cache.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            create table scrape_cache (
                cache_key text primary key,
                url text not null,
                response_json text not null,
                created_at real not null,
                expires_at real not null
            );
            """
        )

    store = SQLiteStore(database)
    store.set_cache("k", "https://example.com/", {"ok": True}, 600, owner_key="owner")

    assert store.cache_count(owner_key="owner") == 1
    assert store.cache_count(owner_key="someone-else") == 0
    # Legacy rows (owner_key = '') stay invisible to every key until they expire.
    assert store.cache_count(owner_key="") == 0
