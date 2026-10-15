from __future__ import annotations

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.errors import coverage_of


@pytest.mark.parametrize(
    ("metadata", "errors", "status", "reasons"),
    [
        ({"markdown_chars": 900}, [], "complete", []),
        ({"error_type": "client_challenge"}, ["x"], "blocked", ["client_challenge"]),
        ({"error_type": "blocked"}, ["x"], "blocked", ["blocked"]),
        ({"error_type": "not_found"}, ["x"], "failed", ["not_found"]),
        ({}, ["boom"], "failed", ["fetch_error"]),
        ({"markdown_chars": 50, "javascript_required": True}, [], "partial", ["needs_javascript"]),
        (
            {"markdown_chars": 50, "browser_render_error": "x"},
            [],
            "partial",
            ["browser_render_failed"],
        ),
        (
            {"markdown_chars": 50, "network_idle_timeout": True},
            [],
            "partial",
            ["network_still_busy"],
        ),
        ({"markdown_chars": 0}, [], "partial", ["empty_page"]),
        ({"markdown_chars": 50, "markdown_truncated": True}, [], "truncated", ["token_budget"]),
        ({"markdown_chars": 50, "api_truncated": True}, [], "truncated", ["api_endpoint_limit"]),
        # Precedence: the worst status wins, every reason is listed.
        (
            {"markdown_chars": 50, "markdown_truncated": True, "network_idle_timeout": True},
            [],
            "partial",
            ["network_still_busy", "token_budget"],
        ),
    ],
)
def test_coverage_of(metadata, errors, status, reasons):
    assert coverage_of(metadata, errors) == {"status": status, "reasons": reasons}


def test_scrape_reports_coverage(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1>Hi</h1><p>" + "word " * 2000 + "</p></body></html>")
    crawler = AgentCrawl({"allow_local_files": True})

    full = crawler.scrape(str(page), formats=["markdown"])
    cut = crawler.scrape(str(page), formats=["markdown"], max_tokens=50)
    document = crawler.scrape(str(page))
    missing = crawler.scrape(str(tmp_path / "nope.html"), formats=["markdown"])

    assert full["metadata"]["coverage"]["status"] == "complete"
    assert cut["metadata"]["coverage"] == {"status": "truncated", "reasons": ["token_budget"]}
    assert document.metadata["coverage"]["status"] == "complete"
    assert missing["metadata"]["coverage"]["status"] == "failed"
