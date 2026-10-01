from __future__ import annotations

import json
from typing import Any

from .config import CrawlConfig
from .errors import classify_exception, next_step, sanitize_error_message
from .llm import get_llm, invoke_llm
from .utils import extract_json, schema_json, to_plain_data, validate_with_schema


def extract_answer(
    prompt: str,
    chunks: list[str],
    schema: Any,
    config: CrawlConfig,
    previous_error: str | None = None,
) -> tuple[Any, str | None, str | None]:
    if config.output_format == "markdown" and schema is None:
        system_prompt = _markdown_prompt(prompt, chunks, previous_error)
    else:
        system_prompt = _json_prompt(prompt, chunks, schema, config.reasoning, previous_error)

    llm = get_llm(config)
    if schema is not None and not config.reasoning and hasattr(llm, "with_structured_output"):
        # The provider returns parsed JSON; "Return valid JSON only" and
        # extract_json stay for plain callables, which have no such feature.
        try:
            json_schema = json.loads(schema_json(schema))
            # Some LangChain providers require a title on a dict schema.
            json_schema.setdefault("title", "extraction")
            structured = llm.with_structured_output(json_schema)
            parsed = structured.invoke(system_prompt)
            return to_plain_data(validate_with_schema(parsed, schema)), None, None
        except Exception as exc:
            # The provider's own message can carry endpoint URLs, prompt or
            # response fragments; the caller gets the classified type, the
            # sanitized detail and the project's next_step instead.
            error_type = classify_exception(exc)
            detail = sanitize_error_message(str(exc)) or error_type
            return "", f"[{error_type}] {detail} (next_step: {next_step(error_type)})", None
    text = invoke_llm(llm, system_prompt)

    reasoning = None
    if config.output_format == "markdown" and schema is None:
        return text.strip(), None, reasoning

    try:
        parsed = extract_json(text)
        if config.reasoning and isinstance(parsed, dict) and "_reasoning" in parsed:
            reasoning = str(parsed.pop("_reasoning"))
        validated = validate_with_schema(parsed, schema)
        return to_plain_data(validated), None, reasoning
    except Exception as exc:
        return text, str(exc), reasoning


def _json_prompt(
    prompt: str,
    chunks: list[str],
    schema: Any,
    reasoning: bool,
    previous_error: str | None,
) -> str:
    reasoning_instruction = (
        'Include a concise "_reasoning" field explaining evidence selection, then the requested fields.'
        if reasoning
        else "Do not include reasoning. Return only the requested JSON."
    )
    retry = (
        f"\nPrevious attempt failed: {previous_error}\nFix the output.\n" if previous_error else ""
    )
    return f"""You extract structured data from website text.
Task: {prompt}
{retry}
Schema:
{schema_json(schema)}

Rules:
- Use only evidence in the page text.
- Return valid JSON only.
- If a value is missing, use null or an empty list as appropriate.
- {reasoning_instruction}

Page text:
{_join_chunks(chunks)}
"""


def _markdown_prompt(prompt: str, chunks: list[str], previous_error: str | None) -> str:
    retry = f"\nPrevious attempt was empty: {previous_error}\n" if previous_error else ""
    return f"""You convert website text into clean markdown.
Task: {prompt}
{retry}
Rules:
- Use only page content.
- Remove boilerplate, navigation, cookie banners, and unrelated links.
- Keep headings, bullets, tables, and important facts.

Page text:
{_join_chunks(chunks)}
"""


def _join_chunks(chunks: list[str]) -> str:
    return "\n\n".join(f"[chunk {index + 1}]\n{chunk}" for index, chunk in enumerate(chunks))
