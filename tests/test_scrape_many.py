"""Batch scraping over the library, API, MCP and CLI (new in 0.2.0).

Before this, ``scrape_many`` existed only on the LLM ``AgentCrawler``; an agent
on the API/MCP path had to make N sequential calls.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.cli import main as cli_main
from agentcrawl.mcp_server import scrape_many as mcp_scrape_many
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore


def _pages(tmp_path: Path, count: int) -> list[str]:
    paths = []
    for index in range(count):
        page = tmp_path / f"page{index}.html"
        page.write_text(f"<html><body><h1>Page {index}</h1></body></html>", encoding="utf-8")
        paths.append(str(page))
    return paths


def test_library_scrape_many_keeps_input_order_and_isolates_failures(tmp_path: Path) -> None:
    pages = _pages(tmp_path, 3)
    sources = [pages[0], str(tmp_path / "missing.html"), pages[2]]

    documents = AgentCrawl({"parallelism": 3}).scrape_many(sources)

    assert [doc.url for doc in documents] == sources
    assert "Page 0" in documents[0].markdown and "Page 2" in documents[2].markdown
    assert documents[1].ok is False and "not found" in documents[1].errors[0]


def test_library_scrape_many_is_polite_per_host(monkeypatch) -> None:
    active: dict[str, int] = {}
    peak: dict[str, int] = {}
    lock = threading.Lock()

    def fake_scrape(self, source, formats=None, only_main_content=None, **_kwargs):
        host = source.split("/")[2]
        with lock:
            active[host] = active.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), active[host])
        time.sleep(0.05)
        with lock:
            active[host] -= 1
        return {"url": source, "errors": []}

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    sources = [f"https://a.example.com/{i}" for i in range(6)] + ["https://b.example.com/"]

    AgentCrawl({"parallelism": 8}).scrape_many(sources, per_host_concurrency=2)

    assert peak["a.example.com"] == 2


def test_api_scrape_many_isolates_bad_urls_and_meters_per_url(tmp_path: Path, monkeypatch) -> None:
    server.store = SQLiteStore(tmp_path / "batch.db")
    server.auth_enabled = True
    server.api_keys = {"batch-key"}
    server.rate_limit_per_minute = 5
    server._rate_windows = {}
    monkeypatch.setattr(
        AgentCrawl,
        "scrape",
        lambda self, url, formats=None, only_main_content=None, **_kwargs: {
            "url": url,
            "markdown": "ok",
            "errors": [],
        },
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer batch-key"}

    response = client.post(
        "/v1/scrape_many",
        json={"urls": ["https://example.com/a", "http://127.0.0.1/admin", "https://example.org/"]},
        headers=headers,
    )
    too_many = client.post(
        "/v1/scrape_many",
        json={"urls": ["https://example.com/1", "https://example.com/2", "https://example.com/3"]},
        headers=headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert [item["url"] for item in body["data"]] == [
        "https://example.com/a",
        "http://127.0.0.1/admin",
        "https://example.org/",
    ]
    assert body["data"][1]["success"] is False and "127.0.0.1" in body["data"][1]["error"]
    assert body["summary"] == {"total": 3, "succeeded": 2, "failed": 1}
    assert too_many.status_code == 429  # 3 units used, 3 more exceed 5


def test_api_scrape_many_bounds_the_batch(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "batch-bounds.db")
    client = TestClient(app)
    urls = [f"https://example.com/{i}" for i in range(101)]
    assert client.post("/v1/scrape_many", json={"urls": urls}).status_code == 422
    assert client.post("/v1/scrape_many", json={"urls": []}).status_code == 422


def test_mcp_scrape_many_runs_locally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    # Local files are opt-in on the MCP (S1); these fixtures are local pages.
    monkeypatch.setenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "true")
    pages = _pages(tmp_path, 2)

    result = mcp_scrape_many(pages, formats=["markdown"])

    assert result["summary"] == {"total": 2, "succeeded": 2, "failed": 0}
    assert "Page 1" in result["data"][1]["data"]["markdown"]


def test_cli_scrape_many_reads_a_url_file(tmp_path: Path, capsys) -> None:
    pages = _pages(tmp_path, 2)
    url_file = tmp_path / "urls.txt"
    url_file.write_text(f"# batch\n{pages[0]}\n\n{pages[1]}  # second\n", encoding="utf-8")

    assert cli_main(["scrape-many", "--file", str(url_file), "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "Page 0" in output and "Page 1" in output
