"""Release security review (W3-01, 2026-10-21): credentials stay out of output.

- ``agentcrawl doctor`` printed ``AGENTCRAWL_BASE_URL`` with its
  ``user:password@`` part, and the remote-health error echoed the password
  (as did every ``--remote`` command): such a URL is now refused up front;
- ``/v1/scrape_many``, ``/v1/search`` and a failed crawl job returned or stored
  ``str(exc)`` without the credential redaction the other error paths use;
- a bearer token with non-ASCII characters crashed the key check (500).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.cli import main
from agentcrawl.remote_client import AgentCrawlClient
from agentcrawl.server import _run_crawl_job, app, server
from agentcrawl.storage import SQLiteStore

SECRET_URL = "http://user:SECRETPW@proxy.example:8080"


def _boom(*_args, **_kwargs):
    raise ValueError(f"proxy {SECRET_URL} refused the connection")


def test_doctor_refuses_base_url_credentials_without_printing_them(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://user:SECRETPW@remote.example")
    monkeypatch.delenv("AGENTCRAWL_API_KEY", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", _boom)  # never reached

    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    checks = json.loads(output)["checks"]

    assert "SECRETPW" not in output
    for check in ("remote_config", "remote_health"):
        assert checks[check]["ok"] is False
        assert "must not contain user:password@" in checks[check]["detail"]


@pytest.mark.parametrize(
    "base_url", ["http://user:SECRETPW@remote.example", "user:SECRETPW@remote.example:8000"]
)
def test_remote_client_refuses_base_url_credentials_without_echoing_them(base_url) -> None:
    # Without a scheme, urlsplit reads "user" as the scheme and finds no netloc.
    with pytest.raises(ValueError) as refused:
        AgentCrawlClient(base_url)

    assert "SECRETPW" not in str(refused.value)


def test_doctor_does_not_print_a_scheme_less_base_url_with_credentials(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "admin:SECRETPW@localhost:8000")

    assert main(["doctor"]) == 1
    assert "SECRETPW" not in capsys.readouterr().out


def test_cli_remote_with_credentials_exits_with_a_message(monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://user:SECRETPW@remote.example")

    with pytest.raises(SystemExit) as exited:
        main(["--remote", "scrape", "https://example.com"])

    assert "must not contain user:password@" in str(exited.value.code)
    assert "SECRETPW" not in str(exited.value.code)


def test_mcp_server_refuses_credentials_at_startup(monkeypatch) -> None:
    from agentcrawl import mcp_server

    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://user:SECRETPW@remote.example")
    monkeypatch.setattr(mcp_server.mcp, "run", lambda: pytest.fail("server started"))

    with pytest.raises(SystemExit) as exited:
        mcp_server.main()

    assert "SECRETPW" not in str(exited.value.code)


def test_doctor_reports_a_malformed_base_url(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://[::1")

    assert main(["doctor"]) == 1
    checks = json.loads(capsys.readouterr().out)["checks"]
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


def _failed_job(tmp_path: Path, monkeypatch, scrape) -> tuple[dict, list]:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr(AgentCrawl, "scrape", scrape)
    server.store = store

    _run_crawl_job(job_id, payload, None)

    job = store.get_job(job_id)
    assert job["status"] == "failed"
    return job, store.list_job_events(job_id)


def test_failed_job_error_event_and_log_are_redacted(tmp_path: Path, monkeypatch, caplog) -> None:
    job, events = _failed_job(tmp_path, monkeypatch, _boom)

    assert "SECRETPW" not in json.dumps([job, events], default=str)
    assert "***@proxy.example" in job["error"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"job {job['id']} failed" in logged and "SECRETPW" not in caplog.text


def test_failed_job_error_is_long_bounded_and_never_empty(tmp_path: Path, monkeypatch) -> None:
    long_message = "field missing; " * 40

    def raising(message: str):
        def scrape(*_args, **_kwargs):
            raise ValueError(message)

        return scrape

    def silent_error(*_args, **_kwargs):
        raise TimeoutError()

    long_job, _ = _failed_job(tmp_path / "long", monkeypatch, raising(long_message))
    huge_job, _ = _failed_job(tmp_path / "huge", monkeypatch, raising("x\x1b[31m" * 5_000))
    silent_job, _ = _failed_job(tmp_path / "silent", monkeypatch, silent_error)

    # Longer than the 300-char error-line cap, but bounded and without escapes.
    assert long_job["error"] == long_message.strip()
    assert len(huge_job["error"]) <= 4_000 and "\x1b" not in huge_job["error"]
    assert silent_job["error"] == "TimeoutError"
