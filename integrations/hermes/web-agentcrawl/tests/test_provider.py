from __future__ import annotations

import json

from provider import AgentCrawlWebProvider


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(
            {
                "success": True,
                "data": {
                    "url": "https://example.com",
                    "markdown": "# Example",
                    "metadata": {"title": "Example", "cache_hit": True},
                },
            }
        ).encode()


def test_extract_one_normalizes_agentcrawl(monkeypatch):
    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://agentcrawl.test")
    monkeypatch.setenv("AGENTCRAWL_API_KEY", "secret")
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _Response())

    result = AgentCrawlWebProvider()._extract_one("https://example.com", ["markdown", "metadata"])

    assert result["content"] == "# Example"
    assert result["title"] == "Example"
    assert result["metadata"]["provider"] == "agentcrawl"


def test_provider_is_extract_only():
    provider = AgentCrawlWebProvider()
    assert provider.supports_extract() is True
    assert provider.supports_search() is False
