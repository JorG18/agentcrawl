from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentcrawl.cli import main
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore


def test_dashboard_summary_endpoint_returns_operational_snapshot(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "dashboard.db")
    job_id = store.create_job("crawl", {"url": "https://example.com", "wait": False})
    store.update_job(
        job_id,
        "failed",
        {
            "documents": [],
            "errors": ["timeout"],
            "metadata": {
                "terminal_failures": [
                    {
                        "url": "https://example.com/a",
                        "attempts": 2,
                        "error_type": "timeout",
                        "message": "Request timed out",
                        "retryable": True,
                    }
                ]
            },
        },
    )
    store.record_usage(None, "/v1/crawl", units=3)
    store.set_cache("example", "https://example.com/a", {"success": True}, 120)
    server.store = store

    response = TestClient(app).get("/api/dashboard/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["jobs"] == {"failed": 1}
    assert data["crawl_failures"]["by_status"] == {"open": 1}
    assert data["crawl_failures"]["open_retryable"] == 1
    assert data["usage_by_endpoint"] == {"/v1/crawl": 3}
    assert data["cache_by_domain"] == {"example.com": 1}
    assert data["totals"] == {
        "jobs": 1,
        "open_failures": 1,
        "retryable_failures": 1,
        "cache_entries": 1,
        "usage_units": 3,
    }


def test_dashboard_html_endpoint_renders_static_page(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "dashboard.db")
    store.create_job("crawl", {"url": "https://example.com", "wait": False})
    server.store = store

    response = TestClient(app).get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AgentCrawl Dashboard" in response.text
    assert "Queued" in response.text
    assert "/api/dashboard/summary" in response.text


def test_dashboard_cli_writes_static_html_from_sqlite(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_API_KEY", raising=False)
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    db_path = tmp_path / "dashboard.db"
    output_path = tmp_path / "dashboard.html"
    SQLiteStore(db_path).create_job("crawl", {"url": "https://example.com", "wait": False})

    exit_code = main(["dashboard", "--db", str(db_path), "--output", str(output_path)])

    assert exit_code == 0
    assert "dashboard.html" in capsys.readouterr().out
    html = output_path.read_text(encoding="utf-8")
    assert "AgentCrawl Dashboard" in html
    assert "Queued" in html
