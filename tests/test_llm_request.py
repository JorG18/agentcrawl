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


class _ProviderStatusError(RuntimeError):
    """What the real SDKs raise: an exception carrying the HTTP status."""

    status_code = 429


class _FailingStructuredModel:
    def with_structured_output(self, schema):
        def invoke(prompt):
            raise _ProviderStatusError(
                "Error code: 429 - Rate limit reached.\nsecond line of provider noise"
            )

        return types.SimpleNamespace(invoke=invoke)

    def invoke(self, prompt):  # pragma: no cover - the failure happens first
        raise AssertionError("free-text path used")


def test_structured_output_provider_failure_is_classified_and_sanitized() -> None:
    """A structured-output provider failure reaches the caller as the
    project's vocabulary (classified type + sanitized detail + next_step),
    not as the raw provider message: one line, bounded, no control chars."""
    answer, error, _ = extract_answer(
        "the price", ["Lamp $20"], SCHEMA, CrawlConfig(llm=_FailingStructuredModel())
    )
    assert answer == ""
    assert error is not None
    assert "[rate_limited]" in error
    assert "next_step: Too many requests: wait before retrying" in error
    assert "\n" not in error
    assert len(error) <= 330  # classified prefix + 300-char sanitized detail


def test_structured_output_provider_failure_still_reattempts(monkeypatch) -> None:
    """The answer is empty, so CrawlGraph's auto_reattempt fires again (the
    model gets the classified failure as previous_error) instead of giving up."""
    from agentcrawl import graph as graph_module

    # Hermetic: CrawlGraph imports fetch_source directly, so patch it there.
    monkeypatch.setattr(
        graph_module,
        "fetch_source",
        lambda source, config: ("<html><body><main><p>Lamp $20</p></main></body></html>", {}),
    )
    from agentcrawl.graph import CrawlGraph

    calls: list[str] = []

    class _Model:
        def with_structured_output(self, schema):
            def invoke(prompt):
                calls.append(prompt)
                if len(calls) == 1:
                    raise _ProviderStatusError("Error code: 429 - Rate limit reached.")
                return {"price": 20}

            return types.SimpleNamespace(invoke=invoke)

    result = CrawlGraph(CrawlConfig(llm=_Model())).run("https://example.com/", "the price", SCHEMA)
    assert len(calls) == 2
    assert result.answer == {"price": 20}
    assert result.metadata["llm_calls"] == 2
