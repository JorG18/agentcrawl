"""The MCP exposes a small tool set by default; operators opt into the rest."""

from __future__ import annotations

import json

import pytest

from agentcrawl import mcp_server


@pytest.fixture()
def tools():
    saved = dict(mcp_server.mcp._tool_manager._tools)
    yield lambda: {tool.name: tool for tool in mcp_server.mcp._tool_manager.list_tools()}
    mcp_server.mcp._tool_manager._tools.clear()
    mcp_server.mcp._tool_manager._tools.update(saved)


def _schema_chars(listing) -> int:
    return sum(
        len(json.dumps({"name": t.name, "description": t.description, "input": t.parameters}))
        for t in listing.values()
    )


def test_core_profile_without_server_or_search(monkeypatch, tools) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTCRAWL_SEARCH_ENGINE", raising=False)
    full_chars = _schema_chars(tools())
    removed = mcp_server.apply_profile("core")
    names = set(tools())
    assert names == {"scrape_url", "scrape_many", "map_site", "crawl_site", "extract_structured"}
    assert "usage" in removed and "clear_cache" in removed
    # The point of the profile: well under half the context of the full set.
    assert _schema_chars(tools()) < full_chars / 2


def test_core_profile_keeps_search_and_jobs_when_configured(monkeypatch, tools) -> None:
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("AGENTCRAWL_SEARCH_ENGINE", "duckduckgo")
    mcp_server.apply_profile("core")
    assert {"search_web", "get_job"} <= set(tools())
    assert "cache_stats" not in tools()


def test_full_profile_keeps_everything(monkeypatch, tools) -> None:
    before = set(tools())
    monkeypatch.setenv("AGENTCRAWL_MCP_PROFILE", "full")
    assert mcp_server.apply_profile() == []
    assert set(tools()) == before


def test_unknown_profile_is_an_error(tools) -> None:
    with pytest.raises(ValueError, match="AGENTCRAWL_MCP_PROFILE"):
        mcp_server.apply_profile("everything")
