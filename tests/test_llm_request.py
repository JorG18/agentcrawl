"""What reaches the LLM: temperature only when set, structured output when offered."""

from __future__ import annotations

import sys
import types

from agentcrawl.config import CrawlConfig
from agentcrawl.extraction import extract_answer
from agentcrawl.llm import get_llm

SCHEMA = {"type": "object", "properties": {"price": {"type": "number"}}, "required": ["price"]}


def _capture_init(monkeypatch) -> dict:
    seen: dict = {}

    def init_chat_model(model, **kwargs):
        seen.update(model=model, **kwargs)
        return object()

    module = types.SimpleNamespace(init_chat_model=init_chat_model)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", module)
    return seen


def test_temperature_is_sent_only_when_set(monkeypatch) -> None:
    """Claude Opus 5.5, Sonnet 5.5 and Fable reject a non-default temperature,
    and 0.0 used to be sent on every call."""
    seen = _capture_init(monkeypatch)
    get_llm(CrawlConfig(llm_model="anthropic:claude-opus-5-5"))
    assert "temperature" not in seen

    seen.clear()
    get_llm(CrawlConfig.from_dict({"llm_model": "x:y", "llm_temperature": 0.2}))
    assert seen["temperature"] == 0.2


class _StructuredModel:
    def __init__(self) -> None:
        self.schemas: list = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return types.SimpleNamespace(invoke=lambda prompt: {"price": 20})

    def invoke(self, prompt):  # would only be reached without structured output
        raise AssertionError("free-text path used")


def test_schema_extraction_uses_structured_output_when_the_model_has_it() -> None:
    model = _StructuredModel()
    answer, error, _ = extract_answer("the price", ["Lamp $20"], SCHEMA, CrawlConfig(llm=model))
    assert (answer, error) == ({"price": 20}, None)
    assert model.schemas[0]["title"] == "extraction"


def test_plain_callables_still_get_the_json_prompt() -> None:
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return '{"price": 20}'

    answer, error, _ = extract_answer("the price", ["Lamp $20"], SCHEMA, CrawlConfig(llm=llm))
    assert (answer, error) == ({"price": 20}, None)
    assert "Return valid JSON only" in prompts[0]
