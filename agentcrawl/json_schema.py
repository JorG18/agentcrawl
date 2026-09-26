"""Validate model output against a JSON Schema dict, with no extra dependency.

LLM extraction accepts a Pydantic model or type from Python callers, but the
HTTP API, MCP and CLI can only send a JSON Schema object. This module checks
that output against the schema so a wrong shape is caught and the model is
asked again, instead of being returned as if it were valid.

It covers the subset structured-output schemas use: ``type`` (including a list
of types and ``null``), ``properties``, ``required``, ``additionalProperties``,
``items``, ``enum``, ``const``, ``minItems``/``maxItems``,
``minLength``/``maxLength``, ``minimum``/``maximum``, ``anyOf``/``oneOf``,
local ``$ref`` into ``$defs``/``definitions``. Unknown keywords are ignored,
as JSON Schema itself specifies.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}
_MAX_DEPTH = 64


class JSONSchemaError(ValueError):
    """Output does not match the schema; the message names the path."""


def is_json_schema(schema: Any) -> bool:
    """True for a JSON Schema object (as opposed to a Pydantic model or type)."""
    return isinstance(schema, dict) and bool(
        {"type", "properties", "items", "anyOf", "oneOf", "enum", "$ref"} & set(schema)
    )


def validate_json_schema(value: Any, schema: dict[str, Any]) -> Any:
    """Return ``value`` unchanged if it matches ``schema``; raise otherwise."""
    _check(value, schema, "$", schema, 0)
    return value


def _check(value: Any, schema: Any, path: str, root: dict[str, Any], depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise JSONSchemaError(f"{path}: schema nests deeper than {_MAX_DEPTH} levels")
    if schema is True or not isinstance(schema, dict):
        return
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), path, root, depth + 1)
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in allowed):
            raise JSONSchemaError(f"{path}: expected {' or '.join(allowed)}, got {_name(value)}")
    if "enum" in schema and value not in schema["enum"]:
        raise JSONSchemaError(f"{path}: {value!r} is not one of {schema['enum']!r}")
    if "const" in schema and value != schema["const"]:
        raise JSONSchemaError(f"{path}: expected {schema['const']!r}")
    for key in ("anyOf", "oneOf"):
        if key in schema:
            matches = sum(_matches(value, option, path, root, depth) for option in schema[key])
            if matches == 0 or (key == "oneOf" and matches > 1):
                raise JSONSchemaError(f"{path}: does not match {key}")
    if isinstance(value, dict):
        _check_object(value, schema, path, root, depth)
    elif isinstance(value, list):
        _check_bounds(len(value), schema, "minItems", "maxItems", path, "items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{index}]", root, depth + 1)
    elif isinstance(value, str):
        _check_bounds(len(value), schema, "minLength", "maxLength", path, "characters")
    elif _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise JSONSchemaError(f"{path}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise JSONSchemaError(f"{path}: {value} is above the maximum {schema['maximum']}")


def _check_object(
    value: dict[str, Any], schema: dict[str, Any], path: str, root: dict[str, Any], depth: int
) -> None:
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in value:
            raise JSONSchemaError(f"{path}: missing required field {key!r}")
    extra = schema.get("additionalProperties", True)
    for key, item in value.items():
        if key in properties:
            _check(item, properties[key], f"{path}.{key}", root, depth + 1)
        elif extra is False:
            raise JSONSchemaError(f"{path}: unexpected field {key!r}")
        elif isinstance(extra, dict):
            _check(item, extra, f"{path}.{key}", root, depth + 1)


def _check_bounds(size: int, schema: dict[str, Any], low: str, high: str, path: str, unit: str):
    if low in schema and size < schema[low]:
        raise JSONSchemaError(f"{path}: needs at least {schema[low]} {unit}, got {size}")
    if high in schema and size > schema[high]:
        raise JSONSchemaError(f"{path}: allows at most {schema[high]} {unit}, got {size}")


def _matches(value: Any, schema: Any, path: str, root: dict[str, Any], depth: int) -> bool:
    try:
        _check(value, schema, path, root, depth + 1)
    except JSONSchemaError:
        return False
    return True


def _resolve(ref: Any, root: dict[str, Any]) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise JSONSchemaError(f"only local $ref values are supported, got {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise JSONSchemaError(f"$ref {ref!r} does not resolve")
        node = node[part]
    return node


def _is_type(value: Any, name: str) -> bool:
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    if name == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _TYPES.get(name, (object,)))


def _name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__
