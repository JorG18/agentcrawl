"""Web search over the library, API, MCP and CLI (search, then read the results).

Search used to exist only on the LLM ``AgentCrawler``; an agent on the MCP/API
path had no way to go from a question to pages without another tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.cli import main as cli_main
from agentcrawl.mcp_server import search_web as mcp_search
from agentcrawl.models import SearchResult
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore

HITS = [
    SearchResult(title="A", url="https://example.com/a", snippet="first"),
    SearchResult(title="B", url="https://example.org/b", snippet="second"),
    SearchResult(title="C", url="https://example.com/c", snippet="third"),
]


def _fake_scrape(self, source, formats=None, only_main_content=None, query=None, **_kwargs):
    return {"url": source, "markdown": f"page {source} for {query}", "errors": []}


@pytest.fixture
def fake_search(monkeypatch):
    calls: list[tuple[str, int]] = []

    def search(query, config, audit_trail=None):
        calls.append((query, config.search_limit))
        return list(HITS)[: config.search_limit]

    monkeypatch.setattr("agentcrawl.search.search_web", search)
    monkeypatch.setattr("agentcrawl.server.search_web", search)
    monkeypatch.setattr(AgentCrawl, "scrape", _fake_scrape)
    return calls


def test_library_search_scrapes_results_with_the_query(fake_search) -> None:
    payload = AgentCrawl({"search_engine": "duckduckgo"}).search("rate limits", limit=2)

    assert fake_search == [("rate limits", 2)]
    assert [item["url"] for item in payload["results"]] == [hit.url for hit in HITS[:2]]
    first = payload["results"][0]
    assert first["success"] is True and first["snippet"] == "first"
    # The query is the relevance query of every scrape (BM25 passage selection).
    assert first["data"]["markdown"].endswith("for rate limits")


def test_library_search_without_scrape_returns_hits_only(fake_search) -> None:
    payload = AgentCrawl({"search_engine": "duckduckgo"}).search("q", scrape=False)

    assert all("data" not in item for item in payload["results"])
    assert len(payload["results"]) == 3


def test_library_search_is_opt_in(fake_search) -> None:
    with pytest.raises(ValueError, match="AGENTCRAWL_SEARCH_ENGINE"):
        AgentCrawl().search("q")
    with pytest.raises(ValueError, match="between 1 and 20"):
        AgentCrawl({"search_engine": "duckduckgo"}).search("q", limit=50)
    assert fake_search == []


def test_library_search_under_airgap_skips_non_allowlisted_hosts(fake_search) -> None:
    crawler = AgentCrawl(
        {
            "search_engine": "duckduckgo",
            "airgap": True,
            "allowlist_domains": ["example.com"],
        }
    )

    payload = crawler.search("q")

    assert [item["url"] for item in payload["results"]] == [
        "https://example.com/a",
        "https://example.com/c",
    ]
    assert payload["airgap_skipped"] == ["https://example.org/b"]


def test_mcp_search_reads_the_engine_from_env(fake_search, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTCRAWL_SEARCH_ENGINE", raising=False)

    disabled = mcp_search("q")
    monkeypatch.setenv("AGENTCRAWL_SEARCH_ENGINE", "duckduckgo")
    enabled = mcp_search("q", limit=1)

    assert disabled["success"] is False and "AGENTCRAWL_SEARCH_ENGINE" in disabled["error"]
    assert enabled["success"] is True
    assert enabled["data"]["results"][0]["url"] == HITS[0].url


def test_api_search_is_operator_controlled_and_metered(
    fake_search, tmp_path: Path, monkeypatch
) -> None:
    server.store = SQLiteStore(tmp_path / "search.db")
    server.auth_enabled = True
    server.api_keys = {"search-key"}
    server.rate_limit_per_minute = 10
    server._rate_windows = {}
    client = TestClient(app)
    headers = {"Authorization": "Bearer search-key"}

    monkeypatch.setattr(server, "search_engine", "none")
    disabled = client.post("/v1/search", json={"query": "q"}, headers=headers)
    monkeypatch.setattr(server, "search_engine", "duckduckgo")
    override = client.post(
        "/v1/search", json={"query": "q", "config": {"search_engine": "serper"}}, headers=headers
    )
    response = client.post("/v1/search", json={"query": "q", "limit": 2}, headers=headers)

    assert disabled.status_code == 400
    assert override.status_code == 400  # the engine is not a request setting
    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    assert [item["url"] for item in body["data"]["results"]] == [hit.url for hit in HITS[:2]]
    assert body["data"]["results"][1]["data"]["markdown"].endswith("for q")


def test_api_search_fails_private_result_urls_individually(fake_search, tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "search-ssrf.db")
    server.auth_enabled = False
    server.search_engine = "duckduckgo"
    fake = [
        SearchResult(title="ok", url="https://example.com/a"),
        SearchResult(title="bad", url="http://127.0.0.1/admin"),
    ]
    import agentcrawl.server as server_module

    original = server_module.search_web
    server_module.search_web = lambda query, config, audit_trail=None: fake
    try:
        body = TestClient(app).post("/v1/search", json={"query": "q"}).json()
    finally:
        server_module.search_web = original
        server.search_engine = "none"
        server.auth_enabled = True

    assert body["success"] is False
    assert body["data"]["results"][0]["success"] is True
    assert body["data"]["results"][1]["success"] is False
    assert "127.0.0.1" in body["data"]["results"][1]["error"]


def test_cli_search_exits_non_zero_when_disabled(fake_search, monkeypatch, capsys) -> None:
    monkeypatch.delenv("AGENTCRAWL_SEARCH_ENGINE", raising=False)
    assert cli_main(["search", "q"]) == 1
    assert "AGENTCRAWL_SEARCH_ENGINE" in capsys.readouterr().out

    monkeypatch.setenv("AGENTCRAWL_SEARCH_ENGINE", "duckduckgo")
    assert cli_main(["search", "q", "--limit", "1", "--no-scrape"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["results"] == [
        {"title": "A", "url": "https://example.com/a", "snippet": "first"}
    ]
