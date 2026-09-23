"""CLI/MCP configuration parity, CLI exit codes, and the last per-key aggregates (2026-09 audit)."""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl.cli import _local_config, main as cli_main
from agentcrawl.config import config_from_env
from agentcrawl.dashboard import dashboard_summary
from agentcrawl.mcp_server import _crawler
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore


@pytest.fixture
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"<html><body><h1>Local dev server</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- 0.2: the CLI reads the same env as MCP ---------------------------------


def test_cli_local_mode_honours_the_private_network_env(local_page, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AGENTCRAWL_ALLOW_PRIVATE_NETWORK", "true")
    assert cli_main(["scrape", local_page, "--format", "markdown"]) == 0
    assert "Local dev server" in capsys.readouterr().out


def test_cli_flag_reaches_a_local_server_and_the_refusal_explains_how(
    local_page, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("AGENTCRAWL_ALLOW_PRIVATE_NETWORK", raising=False)
    assert cli_main(["scrape", local_page]) == 1
    assert "--allow-private-network" in capsys.readouterr().out
    assert cli_main(["scrape", local_page, "--allow-private-network"]) == 0
    assert "Local dev server" in capsys.readouterr().out


def test_flags_beat_env_and_mcp_matches_the_cli(monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_RESPECT_ROBOTS_TXT", "true")
    monkeypatch.setenv("AGENTCRAWL_AUDIT", "true")
    monkeypatch.setenv("AGENTCRAWL_TIMEOUT_MS", "4000")
    import argparse

    args = argparse.Namespace(
        fetcher="http",
        allow_private_network=None,
        airgap=True,
        allowlist="docs.example.com, *.example.org",
        audit=None,
        timeout_ms=None,
        respect_robots_txt=False,
        browser_fallback=None,
    )
    config = _local_config(args)

    assert config["respect_robots_txt"] is False  # flag beat env
    assert config["airgap"] is True
    assert config["allowlist_domains"] == ["docs.example.com", "*.example.org"]
    assert config["audit"] is True and config["timeout_ms"] == 4000  # env kept
    mcp = _crawler().config
    env = config_from_env()
    assert (mcp.audit, mcp.timeout_ms, mcp.respect_robots_txt) == (
        env["audit"],
        env["timeout_ms"],
        env["respect_robots_txt"],
    )


# -- 0.3: exit codes ---------------------------------------------------------


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    page = tmp_path / "ok.html"
    page.write_text("<html><body><h1>ok</h1></body></html>", encoding="utf-8")

    assert cli_main(["scrape", str(page)]) == 0
    assert cli_main(["scrape", str(tmp_path / "missing.html")]) == 1
    assert cli_main(["scrape-many", str(page), str(tmp_path / "missing.html")]) == 1
    with pytest.raises(SystemExit) as usage:
        cli_main(["scrape-many"])
    assert usage.value.code == 2
    output = capsys.readouterr().out
    assert '"errors"' in output  # the JSON is still printed on failure


# -- 0.4: SEC-8 residue ------------------------------------------------------


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def test_job_events_and_queue_metrics_are_scoped_per_key(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "events.db")
    server.auth_enabled = True
    server.api_keys = {"key-a", "key-b"}
    for key, url in (("key-a", "https://a.example.com/"), ("key-b", "https://b.example.com/")):
        job_id, _ = server.store.create_or_get_job(
            "crawl", {"url": url}, owner_key=_fingerprint(key)
        )
        server.store.record_job_event(job_id, "retry_scheduled", {})
    server.store.record_job_event(
        server.store.create_or_get_job(
            "crawl", {"url": "https://a.example.com/2"}, owner_key=_fingerprint("key-a")
        )[0],
        "retry_scheduled",
        {},
    )
    client = TestClient(app)

    stats_b = client.get("/v1/stats", headers={"Authorization": "Bearer key-b"}).json()["data"]
    everyone = server.store.job_event_counts()

    assert stats_b["job_events"]["retry_scheduled"] == 1
    assert everyone["retry_scheduled"] == 3
    summary_b = dashboard_summary(server.store, owner_key=_fingerprint("key-b"))
    assert summary_b["job_events"]["retry_scheduled"] == 1
    assert summary_b["crawl_queue"]["ready"] == 1  # key-b's one queued job, not all 3
    assert dashboard_summary(server.store)["crawl_queue"]["ready"] == 3
