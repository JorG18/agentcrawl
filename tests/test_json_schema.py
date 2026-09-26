"""JSON Schema dicts (what the API, MCP and CLI send) validate LLM output."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcrawl import AgentCrawler
from agentcrawl.json_schema import JSONSchemaError, is_json_schema, validate_json_schema

PRODUCT = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "price": {"type": "number", "minimum": 0},
        "currency": {"enum": ["EUR", "USD"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "variant": {"$ref": "#/$defs/variant"},
        "note": {"type": ["string", "null"]},
    },
    "required": ["name", "price"],
    "additionalProperties": False,
    "$defs": {"variant": {"type": "object", "properties": {"size": {"type": "integer"}}}},
}


def test_valid_output_passes_through_unchanged() -> None:
    value = {
        "name": "Widget",
        "price": 10,
        "currency": "EUR",
        "tags": ["a"],
        "variant": {"size": 2},
        "note": None,
    }
    assert validate_json_schema(value, PRODUCT) is value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"price": 1}, "missing required field 'name'"),
        ({"name": "W", "price": "10"}, r"\$\.price: expected number, got string"),
        ({"name": "W", "price": -1}, "below the minimum"),
        ({"name": "W", "price": 1, "currency": "GBP"}, "is not one of"),
        ({"name": "W", "price": 1, "tags": ["a", 2]}, r"\$\.tags\[1\]: expected string"),
        ({"name": "W", "price": 1, "tags": ["a"] * 4}, "at most 3 items"),
        ({"name": "W", "price": 1, "variant": {"size": 1.5}}, r"\$\.variant\.size"),
        ({"name": "W", "price": 1, "color": "red"}, "unexpected field 'color'"),
        ({"name": "", "price": 1}, "at least 1 characters"),
        ({"name": "W", "price": True}, "expected number, got boolean"),
    ],
)
def test_wrong_output_is_rejected_with_its_path(value, message) -> None:
    with pytest.raises(JSONSchemaError, match=message):
        validate_json_schema(value, PRODUCT)


def test_any_of_and_one_of() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    validate_json_schema(3, schema)
    with pytest.raises(JSONSchemaError, match="anyOf"):
        validate_json_schema([], schema)
    with pytest.raises(JSONSchemaError, match="oneOf"):
        validate_json_schema(3, {"oneOf": [{"type": "integer"}, {"type": "number"}]})


def test_only_schema_dicts_take_this_path() -> None:
    assert is_json_schema(PRODUCT)
    assert not is_json_schema({"title": "just a dict"})
    assert not is_json_schema(dict)


def _page(tmp_path: Path) -> str:
    page = tmp_path / "page.html"
    page.write_text("<h1>Widget</h1><p>Price: 10 EUR</p>", encoding="utf-8")
    return str(page)


def test_extract_with_a_json_schema_dict(tmp_path: Path) -> None:
    crawler = AgentCrawler(
        {"llm": lambda _p: '{"name": "Widget", "price": 10}', "auto_reattempt": False}
    )

    result = crawler.extract(_page(tmp_path), "name and price", PRODUCT)

    assert result.errors == []
    assert result.answer == {"name": "Widget", "price": 10}


def test_extract_retries_when_the_model_breaks_the_schema(tmp_path: Path) -> None:
    replies = iter(['{"name": "Widget", "price": "ten"}', '{"name": "Widget", "price": 10}'])
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    result = AgentCrawler({"llm": llm}).extract(_page(tmp_path), "name and price", PRODUCT)

    assert result.answer == {"name": "Widget", "price": 10}
    assert len(prompts) == 2
    assert "$.price: expected number" in prompts[1]
