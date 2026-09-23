"""Deterministic CSS-schema extraction (new in 0.2.0).

``extract()`` was LLM-only; a repeated page structure paid model cost on every
page. A CSS schema is written once and then applied for free.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcrawl import AgentCrawl
from agentcrawl.cli import main as cli_main
from agentcrawl.css_extract import (
    compile_selector,
    extract_with_schema,
    is_css_schema,
    parse_html,
    select,
    validate_css_schema,
)
from agentcrawl.mcp_server import extract_structured
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore

SHOP = """
<html><body>
<nav><a href="/home">Home</a></nav>
<div class="grid">
  <div class="product featured" data-sku="A1">
    <h2>Lámpara <b>Olé</b></h2>
    <a class="link" href="/p/lampara">ver</a>
    <span class="price">$ 12.500,00</span>
    <ul><li class="tag">latón</li><li class="tag">led</li></ul>
    <p class="meta">SKU: LMP-01 · stock 3</p>
    <div class="seller"><span class="name">Northwind Studio</span></div>
    <div class="variant"><span class="size">S</span></div>
    <div class="variant"><span class="size">L</span></div>
  </div>
  <div class="product" data-sku="B2">
    <h2>Estante Roble</h2>
    <a class="link" href="https://cdn.example.com/p/estante">ver</a>
    <span class="price">USD 1,299.99</span>
    <script>var ignored = "<h2>nope</h2>";</script>
  </div>
</div>
</body></html>
"""

SCHEMA = {
    "name": "products",
    "baseSelector": "div.grid > div.product",
    "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "sku", "type": "attribute", "attribute": "data-sku"},
        {
            "name": "url",
            "selector": "a.link",
            "type": "attribute",
            "attribute": "href",
            "transform": "url",
        },
        {"name": "price", "selector": ".price", "type": "text", "transform": "number"},
        {"name": "tags", "selector": "li.tag", "type": "text", "multiple": True},
        {"name": "code", "selector": ".meta", "type": "regex", "pattern": r"SKU: ([\w-]+)"},
        {
            "name": "seller",
            "selector": ".seller",
            "type": "nested",
            "fields": [{"name": "name", "selector": ".name"}],
        },
        {
            "name": "variants",
            "selector": ".variant",
            "type": "list",
            "fields": [{"name": "size", "selector": ".size"}],
        },
        {"name": "stock", "selector": ".missing", "default": 0},
    ],
}


def test_schema_extracts_every_field_type() -> None:
    items = extract_with_schema(SHOP, SCHEMA, base_url="https://shop.example.com/c/velas")

    assert items == [
        {
            "title": "Lámpara Olé",
            "sku": "A1",
            "url": "https://shop.example.com/p/lampara",
            "price": 12500,
            "tags": ["latón", "led"],
            "code": "LMP-01",
            "seller": {"name": "Northwind Studio"},
            "variants": [{"size": "S"}, {"size": "L"}],
            "stock": 0,
        },
        {
            "title": "Estante Roble",
            "sku": "B2",
            "url": "https://cdn.example.com/p/estante",
            "price": 1299.99,
            "tags": [],
            "code": None,
            "seller": None,
            "variants": [],
            "stock": 0,
        },
    ]


def test_without_base_selector_one_object_is_returned() -> None:
    result = extract_with_schema(SHOP, {"fields": [{"name": "nav", "selector": "nav a"}]})
    assert result == {"nav": "Home"}


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("div.product.featured h2", ["h2"]),
        ("div.product:nth-of-type(2) h2", ["h2"]),
        ("[data-sku^=B] .price", ["span"]),
        ("a[href*='cdn']", ["a"]),
        ("li.tag + li", ["li"]),
        ("h2 ~ ul li:last-child", ["li"]),
        ("nav, .seller", ["nav", "div"]),
        ("#missing", []),
    ],
)
def test_selector_subset(selector: str, expected: list[str]) -> None:
    root = parse_html(SHOP)
    assert [node.tag for node in select(root, selector)] == expected


@pytest.mark.parametrize(
    "selector", ["div::before", "a:hover", "> a", "a >", "div, ,p", "a:nth-child()"]
)
def test_unsupported_selectors_fail_loudly(selector: str) -> None:
    with pytest.raises(ValueError):
        compile_selector(selector)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ([], "must be an object"),
        ({"fields": []}, "non-empty list"),
        ({"fields": [{"name": "a", "type": "attribute"}]}, "attribute is required"),
        ({"fields": [{"name": "a", "type": "regex", "pattern": "("}]}, "not a valid regex"),
        ({"fields": [{"name": "a"}, {"name": "a"}]}, "duplicate field"),
        ({"fields": [{"name": "a", "type": "magic"}]}, "type must be one of"),
        ({"fields": [{"name": "a", "transform": "eval"}]}, "transform must be one of"),
    ],
)
def test_invalid_schemas_are_rejected_before_fetching(schema, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_css_schema(schema)


def test_is_css_schema_tells_css_from_json_schema() -> None:
    assert is_css_schema(SCHEMA)
    assert not is_css_schema({"type": "object", "properties": {"title": {"type": "string"}}})


def test_engine_extract_css_reports_zero_llm_calls(tmp_path: Path) -> None:
    page = tmp_path / "shop.html"
    page.write_text(SHOP, encoding="utf-8")

    result = AgentCrawl().extract_css(str(page), SCHEMA)

    assert result["errors"] == []
    assert [item["title"] for item in result["data"]] == ["Lámpara Olé", "Estante Roble"]
    assert result["metadata"]["extraction_strategy"] == "css_schema"
    assert result["metadata"]["item_count"] == 2
    assert result["metadata"]["llm_calls"] == 0
    assert result["metadata"]["estimated_tokens"] < result["metadata"]["raw_html_tokens_estimate"]


def test_api_rejects_a_bad_schema_with_422(tmp_path: Path) -> None:
    server.store = SQLiteStore(tmp_path / "css.db")
    client = TestClient(app)
    response = client.post(
        "/v1/extract_css",
        json={
            "url": "https://example.com/",
            "schema": {"fields": [{"name": "a", "selector": "a:hover"}]},
        },
    )
    assert response.status_code == 422


def test_mcp_and_cli_extract_locally(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    page = tmp_path / "shop.html"
    page.write_text(SHOP, encoding="utf-8")
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(SCHEMA), encoding="utf-8")

    mcp_result = extract_structured(str(page), SCHEMA)
    assert mcp_result["success"] is True and len(mcp_result["data"]["data"]) == 2
    assert extract_structured(str(page), {"fields": []})["success"] is False

    assert cli_main(["extract-css", str(page), "--schema", str(schema_file)]) == 0
    assert "Estante Roble" in capsys.readouterr().out
