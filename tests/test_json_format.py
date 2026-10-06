"""formats=["json"]: the page as data from the user's configured LLM, never the caller's."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl, extraction, llm
from agentcrawl.mcp_server import _json_request
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "hours": {"type": "integer"}},
    "required": ["name", "hours"],
}


@pytest.fixture
def page(tmp_path: Path) -> str:
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><main><h1>Vela Salvia</h1><p>Burns for 45 hours.</p>"
        "<p>Notes: sage, linen.</p></main></body></html>",
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A configured model that answers with ``replies`` in turn, logging prompts."""
    prompts: list[str] = []
    replies = ['{"name": "Vela Salvia", "hours": 45}']

    def fake(prompt: str) -> str:
        prompts.append(prompt)
        return replies[min(len(prompts), len(replies)) - 1]

    monkeypatch.setattr(llm, "get_llm", lambda config: fake)
    monkeypatch.setattr(extraction, "get_llm", lambda config: fake)
    return prompts


def test_json_is_extracted_and_validated(page: str, model: list[str]) -> None:
    result = AgentCrawl({"allow_local_files": True}).scrape(
        page, formats=["json"], json_options={"schema": SCHEMA}
    )
    assert result["json"] == {"name": "Vela Salvia", "hours": 45}
    assert result["metadata"]["json_llm_calls"] == 1
    assert "Burns for 45 hours" in model[0]


def test_no_model_says_what_to_do(page: str) -> None:
    result = AgentCrawl({"allow_local_files": True}).scrape(
        page, formats=["json"], json_options={"schema": SCHEMA}
    )
    assert result["json"] is None
    assert "AGENTCRAWL_LLM_MODEL" in result["metadata"]["json_error"]


def test_json_needs_a_schema_or_a_prompt(page: str, model: list[str]) -> None:
    result = AgentCrawl({"allow_local_files": True}).scrape(page, formats=["json"])
    assert result["json"] is None and "schema" in result["metadata"]["json_error"]
    assert model == []


def test_a_batch_over_the_page_cap_is_refused_before_any_call(model: list[str]) -> None:
    crawler = AgentCrawl({"llm_max_pages": 1})
    with pytest.raises(ValueError, match="llm_max_pages=1"):
        crawler.scrape_many(["https://a.example", "https://b.example"], formats=["json"])
    assert model == []


def test_mcp_schema_asks_for_json_without_the_markdown() -> None:
    assert _json_request(None, None)[0] == ["markdown", "links", "metadata"]
    assert _json_request(None, SCHEMA) == (["metadata", "json"], {"schema": SCHEMA})


def _client(tmp_path: Path) -> TestClient:
    server.store = SQLiteStore(tmp_path / "server.db")
    server.allow_local_files = True
    return TestClient(app)


def test_firecrawl_v2_json_format(tmp_path: Path, page: str, model: list[str]) -> None:
    body = (
        _client(tmp_path)
        .post("/v2/scrape", json={"url": page, "formats": [{"type": "json", "schema": SCHEMA}]})
        .json()
    )
    assert body["success"] is True
    assert body["data"]["json"] == {"name": "Vela Salvia", "hours": 45}


def test_firecrawl_v2_json_is_refused_where_it_would_cost_per_crawled_page(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    crawl = client.post(
        "/v2/crawl",
        json={"url": "https://example.com", "scrapeOptions": {"formats": ["json"]}},
    )
    assert crawl.status_code == 400
    missing = client.post("/v2/scrape", json={"url": "https://example.com", "formats": ["json"]})
    assert missing.status_code == 400 and "schema or a prompt" in missing.text


def test_v1_does_not_cache_a_missing_json(tmp_path: Path, page: str) -> None:
    client = _client(tmp_path)
    request = {"url": page, "formats": ["json"], "json_options": {"schema": SCHEMA}}
    first = client.post("/v1/scrape", json=request).json()
    assert first["data"]["json"] is None
    second = client.post("/v1/scrape", json=request).json()
    assert second["data"]["metadata"]["cache_hit"] is False
