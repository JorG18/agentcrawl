"""Release security review (W3-01, 2026-10-21): credentials stay out of output.

- ``agentcrawl doctor`` printed ``AGENTCRAWL_BASE_URL`` with its
  ``user:password@`` part, and the remote-health error echoed the password;
- ``/v1/scrape_many``, ``/v1/search`` and a failed crawl job returned or stored
  ``str(exc)`` without the credential redaction the other error paths use;
- a bearer token with non-ASCII characters crashed the key check (500).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.cli import main
from agentcrawl.server import _run_crawl_job, app, server
from agentcrawl.storage import SQLiteStore

SECRET_URL = "http://user:SECRETPW@proxy.example:8080"


def _boom(*_args, **_kwargs):
    raise ValueError(f"proxy {SECRET_URL} refused the connection")


def test_doctor_hides_base_url_credentials(capsys, monkeypatch) -> None:
    # urllib refuses the userinfo URL before any network ("nonnumeric port").
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://user:SECRETPW@remote.example")
    monkeypatch.delenv("AGENTCRAWL_API_KEY", raising=False)

    main(["doctor"])
    output = capsys.readouterr().out
    checks = json.loads(output)["checks"]

    assert "SECRETPW" not in output
    assert checks["remote_config"]["detail"]["base_url"] == "http://***@remote.example"
    assert checks["remote_health"]["ok"] is False


def _api(tmp_path: Path) -> TestClient:
    server.store = SQLiteStore(tmp_path / "security.db")
    server.auth_enabled = True
    server.api_keys = {"good-key"}
    server.rate_limit_per_minute = 100
    server._rate_windows = {}
    return TestClient(app)


def test_non_ascii_bearer_is_refused_not_a_crash(tmp_path: Path) -> None:
    client = _api(tmp_path)

    # Sent as raw bytes: the HTTP client itself refuses a non-ASCII str header.
    response = client.get("/v1/usage", headers={"authorization": "Bearer clé".encode()})

    assert response.status_code == 403


def test_scrape_many_error_is_redacted(tmp_path: Path, monkeypatch) -> None:
    client = _api(tmp_path)
    monkeypatch.setattr(AgentCrawl, "scrape", _boom)

    response = client.post(
        "/v1/scrape_many",
        json={"urls": ["https://example.com/a"]},
        headers={"authorization": "Bearer good-key"},
    )

    assert response.status_code == 200
    error = response.json()["data"][0]["error"]
    assert "SECRETPW" not in error and "***@proxy.example" in error


def test_search_failure_is_redacted(tmp_path: Path, monkeypatch) -> None:
    client = _api(tmp_path)
    monkeypatch.setattr(server, "search_engine", "duckduckgo")
    monkeypatch.setattr("agentcrawl.server.search_web", _boom)

    response = client.post(
        "/v1/search", json={"query": "q"}, headers={"authorization": "Bearer good-key"}
    )

    assert response.status_code == 502
    assert "SECRETPW" not in response.text and "***@proxy.example" in response.text


def test_failed_job_error_and_event_are_redacted(tmp_path: Path, monkeypatch) -> None:
    store = SQLiteStore(tmp_path / "job.db")
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
    monkeypatch.setattr(AgentCrawl, "scrape", _boom)
    server.store = store

    _run_crawl_job(job_id, payload, None)

    job = store.get_job(job_id)
    stored = json.dumps([job, store.list_job_events(job_id)], default=str)
    assert job["status"] == "failed"
    assert "SECRETPW" not in stored and "***@proxy.example" in job["error"]
