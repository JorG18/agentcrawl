"""An unknown output format is an error, not a silently empty result."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore


def _page(tmp_path: Path) -> str:
    page = tmp_path / "page.html"
    page.write_text("<main><h1>Hi</h1></main>", encoding="utf-8")
    return str(page)


def test_engine_rejects_an_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="raw_html"):
        AgentCrawl({}).scrape(_page(tmp_path), formats=["markdown", "raw_html"])


def test_server_rejects_an_unknown_format(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "server.db")
    server.allow_local_files = True
    client = TestClient(app)
    page = _page(tmp_path)
    for path, body in (
        ("/v1/scrape", {"url": page}),
        ("/v1/scrape_many", {"urls": [page]}),
        ("/v1/search", {"query": "x"}),
    ):
        response = client.post(path, json={**body, "formats": ["markdown", "raw_html"]})
        assert response.status_code == 422, path
        assert "raw_html" in response.text
    ok = client.post("/v1/scrape", json={"url": page, "formats": ["markdown", "outline"]})
    assert ok.status_code == 200


def test_cli_rejects_an_unknown_format(tmp_path: Path, capsys) -> None:
    from agentcrawl.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["scrape", _page(tmp_path), "--format", "raw_html"])
    assert exit_info.value.code == 2
    assert "raw_html" in capsys.readouterr().err


def test_scrape_many_refuses_an_unknown_format_once() -> None:
    import pytest

    from agentcrawl import AgentCrawl

    with pytest.raises(ValueError, match="markdwon"):
        AgentCrawl().scrape_many(
            ["https://example.com/a", "https://example.com/b"], formats=["markdwon"]
        )
