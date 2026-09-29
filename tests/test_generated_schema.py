"""An LLM writes a CSS schema once; summaries never fail the page."""

from __future__ import annotations

import json

from agentcrawl import AgentCrawl
from agentcrawl import crawler as crawler_module

PAGE = (
    "<html><body><main><h1>Shop</h1>"
    "<div class='product'><h2>Lamp</h2><span class='price'>$20</span></div>"
    "<div class='product'><h2>Desk</h2><span class='price'>$150</span></div>"
    "</main><script>var noise = 1;</script></body></html>"
)
GOOD = {
    "baseSelector": "div.product",
    "fields": [
        {"name": "name", "selector": "h2", "type": "text"},
        {"name": "price", "selector": ".price", "type": "text", "transform": "number"},
    ],
}


def _page(monkeypatch) -> None:
    monkeypatch.setattr(
        crawler_module,
        "fetch_source",
        lambda source, config: (PAGE, {"fetcher": "http", "final_url": source}),
    )


def test_the_llm_corrects_a_schema_that_matched_nothing(monkeypatch) -> None:
    _page(monkeypatch)
    prompts: list[str] = []
    replies = iter(
        [
            json.dumps({"baseSelector": "li.item", "fields": [{"name": "n", "type": "text"}]}),
            "```json\n" + json.dumps(GOOD) + "\n```",
        ]
    )

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    result = AgentCrawl({"llm": llm}).generate_css_schema("https://example.com/", "products")

    assert result["schema"] == GOOD
    assert result["data"] == [{"name": "Lamp", "price": 20}, {"name": "Desk", "price": 150}]
    assert result["attempts"] == 2
    assert "var noise" not in prompts[0]  # scripts are not sent to the model
    assert "matched nothing" in prompts[1]


def test_summary_uses_the_llm_and_a_failure_keeps_the_page(monkeypatch) -> None:
    _page(monkeypatch)
    ok = AgentCrawl({"llm": lambda prompt: " Two products. "}).scrape(
        "https://example.com/", formats=["markdown", "summary"]
    )
    assert ok["summary"] == "Two products."

    no_llm = AgentCrawl().scrape(
        "https://example.com/", formats=["markdown", "summary", "metadata"]
    )
    assert "Lamp" in no_llm["markdown"]
    assert no_llm["summary"] is None
    assert "LLM" in no_llm["metadata"]["summary_error"]
