from __future__ import annotations

import json
import re
from typing import Any


def log(enabled: bool, message: str) -> None:
    if enabled:
        print(message)


def is_probably_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def is_empty_answer(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _pydantic():
    try:
        from pydantic import BaseModel, TypeAdapter
    except ImportError as exc:
        raise RuntimeError(
            "Pydantic is required for schema validation. Install agentcrawl[llm]."
        ) from exc
    return BaseModel, TypeAdapter


def _is_pydantic_model(schema: Any) -> bool:
    try:
        BaseModel, _ = _pydantic()
    except RuntimeError:
        return False
    return isinstance(schema, type) and issubclass(schema, BaseModel)


def schema_json(schema: Any) -> str:
    if schema is None:
        return "No schema was provided. Return a JSON object or clean markdown as requested."
    if _is_pydantic_model(schema):
        return json.dumps(schema.model_json_schema(), indent=2)
    try:
        _, TypeAdapter = _pydantic()
        return json.dumps(TypeAdapter(schema).json_schema(), indent=2)
    except Exception:
        return str(schema)


def validate_with_schema(value: Any, schema: Any) -> Any:
    if schema is None:
        return value
    BaseModel, TypeAdapter = _pydantic()
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        if isinstance(value, str):
            return schema.model_validate_json(value)
        return schema.model_validate(value)
    adapter = TypeAdapter(schema)
    if isinstance(value, str):
        return adapter.validate_json(value)
    return adapter.validate_python(value)


def to_plain_data(value: Any) -> Any:
    try:
        BaseModel, _ = _pydantic()
    except RuntimeError:
        BaseModel = ()
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items()}
    return value


def extract_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
    if not match:
        raise ValueError("LLM response did not contain JSON.")
    return json.loads(match.group(1))
