"""Deterministic structured extraction with CSS selectors — no LLM.

A schema is written once (by hand, or by an LLM once) and then applied to any
number of pages for free: no model calls, no tokens, the same answer every run.

Schema shape::

    {
      "name": "products",                  # optional, informational
      "baseSelector": "div.product",       # optional; omit for one object per page
      "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "url", "selector": "a", "type": "attribute", "attribute": "href",
         "transform": "url"},
        {"name": "price", "selector": ".price", "type": "text", "transform": "number"},
        {"name": "tags", "selector": "li.tag", "type": "text", "multiple": true},
        {"name": "sku", "selector": ".meta", "type": "regex", "pattern": "SKU: (\\\\w+)"},
        {"name": "seller", "selector": ".seller", "type": "nested", "fields": [...]},
        {"name": "variants", "selector": ".variant", "type": "list", "fields": [...]}
      ]
    }

Field types: ``text`` · ``attribute`` · ``html`` · ``regex`` · ``nested`` · ``list``.
Transforms: ``strip`` (default for text) · ``lower`` · ``upper`` · ``number`` · ``url``.
A field without ``selector`` reads the current element itself.

Supported selector subset (stdlib only, no new dependency): type, ``*``, ``#id``,
``.class``, ``[attr]``, ``[attr=v]``, ``[attr~=v]``, ``[attr^=v]``, ``[attr$=v]``,
``[attr*=v]``, ``:first-child``, ``:last-child``, ``:nth-child(n)``,
``:nth-of-type(n)``, combinators (descendant) ``>`` ``+`` ``~`` and ``,`` groups.
Anything else is rejected with a clear error instead of silently matching nothing.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SKIP_TEXT = frozenset({"script", "style", "template", "noscript"})
_MAX_DEPTH = 8  # nested/list recursion in a schema
_FIELD_TYPES = frozenset({"text", "attribute", "html", "regex", "nested", "list"})
_TRANSFORMS = frozenset({"strip", "lower", "upper", "number", "url"})


# --------------------------------------------------------------------------
# Minimal DOM
# --------------------------------------------------------------------------


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)  # Node | str
    parent: Node | None = None

    @property
    def element_children(self) -> list[Node]:
        return [child for child in self.children if isinstance(child, Node)]

    def iter_descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter_descendants()

    def text(self) -> str:
        parts: list[str] = []
        self._collect_text(parts)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def _collect_text(self, parts: list[str]) -> None:
        if self.tag in _SKIP_TEXT:
            return
        for child in self.children:
            if isinstance(child, Node):
                if child.tag in {"br", "p", "div", "li", "tr"}:
                    parts.append(" ")
                child._collect_text(parts)
            else:
                parts.append(child)

    def inner_html(self) -> str:
        return "".join(_serialize(child) for child in self.children)


def _serialize(item: Any) -> str:
    if not isinstance(item, Node):
        return html_module.escape(item, quote=False)
    attrs = "".join(
        f' {name}="{html_module.escape(value, quote=True)}"' for name, value in item.attrs.items()
    )
    if item.tag in _VOID:
        return f"<{item.tag}{attrs}>"
    return f"<{item.tag}{attrs}>{item.inner_html()}</{item.tag}>"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Tolerant close: pop to the matching open element; ignore stray ends.
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------
# Selectors
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comb>[>+~])
  | (?P<comma>,)
  | (?P<tag>\*|[a-zA-Z][a-zA-Z0-9-]*)
  | \#(?P<id>[\w-]+)
  | \.(?P<cls>[\w-]+)
  | \[\s*(?P<attr>[\w:-]+)\s*(?:(?P<op>[~^$*]?=)\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\]\s]+))\s*)?\]
  | :(?P<pseudo>first-child|last-child|nth-child|nth-of-type)(?:\(\s*(?P<arg>\d+)\s*\))?
    """,
    re.VERBOSE,
)


@dataclass
class _Compound:
    tag: str | None = None
    ids: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    attrs: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    pseudos: list[tuple[str, int | None]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.tag or self.ids or self.classes or self.attrs or self.pseudos)

    def matches(self, node: Node) -> bool:
        if self.tag and self.tag != "*" and node.tag != self.tag:
            return False
        if self.ids and node.attrs.get("id") not in self.ids:
            return False
        classes = node.attrs.get("class", "").split()
        if any(cls not in classes for cls in self.classes):
            return False
        for name, op, value in self.attrs:
            if name not in node.attrs:
                return False
            actual = node.attrs[name]
            if op is None:
                continue
            if op == "=" and actual != value:
                return False
            if op == "~=" and value not in actual.split():
                return False
            if op == "^=" and not actual.startswith(value or ""):
                return False
            if op == "$=" and not actual.endswith(value or ""):
                return False
            if op == "*=" and (value or "") not in actual:
                return False
        for pseudo, arg in self.pseudos:
            siblings = node.parent.element_children if node.parent else [node]
            if pseudo == "first-child" and siblings[0] is not node:
                return False
            if pseudo == "last-child" and siblings[-1] is not node:
                return False
            if pseudo == "nth-child" and (
                arg is None or arg > len(siblings) or siblings[arg - 1] is not node
            ):
                return False
            if pseudo == "nth-of-type":
                same = [sibling for sibling in siblings if sibling.tag == node.tag]
                if arg is None or arg > len(same) or same[arg - 1] is not node:
                    return False
        return True


# A complex selector is a list of (combinator, compound); the first combinator
# is always " " (descendant of the scope).
_Complex = list[tuple[str, _Compound]]


def compile_selector(selector: str) -> list[_Complex]:
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selector must be a non-empty string")
    groups: list[_Complex] = []
    current: _Complex = []
    compound = _Compound()
    combinator = " "
    position = 0
    text = selector.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise ValueError(f"unsupported selector syntax at {text[position:]!r} in {selector!r}")
        position = match.end()
        kind = match.lastgroup
        if kind in {"ws", "comb", "comma"}:
            if not compound.empty():
                current.append((combinator, compound))
                compound = _Compound()
                combinator = " "
            if kind == "comb":
                if not current:
                    raise ValueError(f"selector cannot start with a combinator: {selector!r}")
                combinator = match.group("comb")
            elif kind == "comma":
                if not current:
                    raise ValueError(f"empty selector group in {selector!r}")
                groups.append(current)
                current = []
            continue
        if kind == "tag":
            compound.tag = match.group("tag").lower()
        elif kind == "id":
            compound.ids.append(match.group("id"))
        elif kind == "cls":
            compound.classes.append(match.group("cls"))
        elif match.group("attr"):
            value = match.group("dq")
            if value is None:
                value = match.group("sq")
            if value is None:
                value = match.group("bare")
            compound.attrs.append((match.group("attr").lower(), match.group("op"), value))
        elif match.group("pseudo"):
            pseudo = match.group("pseudo")
            arg = match.group("arg")
            if pseudo.startswith("nth") and arg is None:
                raise ValueError(f":{pseudo} needs a positive integer argument in {selector!r}")
            compound.pseudos.append((pseudo, int(arg) if arg else None))
    if not compound.empty():
        current.append((combinator, compound))
    elif combinator != " ":
        raise ValueError(f"selector cannot end with a combinator: {selector!r}")
    if not current:
        raise ValueError(f"empty selector group in {selector!r}")
    groups.append(current)
    return groups


def _matches_complex(node: Node, complex_selector: _Complex, scope: Node) -> bool:
    """Right-to-left match of ``complex_selector`` for ``node`` inside ``scope``."""

    def match_at(candidate: Node, index: int) -> bool:
        combinator, compound = complex_selector[index]
        if not compound.matches(candidate):
            return False
        if index == 0:
            return _is_inside(candidate, scope)
        previous_index = index - 1
        if combinator == " ":
            ancestor = candidate.parent
            while ancestor is not None and ancestor is not scope:
                if match_at(ancestor, previous_index):
                    return True
                ancestor = ancestor.parent
            return False
        if combinator == ">":
            parent = candidate.parent
            return parent is not None and parent is not scope and match_at(parent, previous_index)
        siblings = candidate.parent.element_children if candidate.parent else []
        position = next(i for i, sibling in enumerate(siblings) if sibling is candidate)
        if combinator == "+":
            return position > 0 and match_at(siblings[position - 1], previous_index)
        return any(match_at(sibling, previous_index) for sibling in siblings[:position])

    return match_at(node, len(complex_selector) - 1)


def _is_inside(node: Node, scope: Node) -> bool:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor is scope:
            return True
        ancestor = ancestor.parent
    return False


def select(scope: Node, selector: str | list[_Complex]) -> list[Node]:
    compiled = compile_selector(selector) if isinstance(selector, str) else selector
    return [
        node
        for node in scope.iter_descendants()
        if any(_matches_complex(node, group, scope) for group in compiled)
    ]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def validate_css_schema(schema: Any, *, _depth: int = 0) -> None:
    """Raise ``ValueError`` naming the first problem; never touches a page."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if _depth > _MAX_DEPTH:
        raise ValueError(f"schema nesting deeper than {_MAX_DEPTH} levels")
    base = schema.get("baseSelector")
    if base is not None:
        compile_selector(base)
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("schema.fields must be a non-empty list")
    names: set[str] = set()
    for index, spec in enumerate(fields):
        where = f"fields[{index}]"
        if not isinstance(spec, dict):
            raise ValueError(f"{where} must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{where}.name must be a non-empty string")
        if name in names:
            raise ValueError(f"duplicate field name: {name}")
        names.add(name)
        kind = spec.get("type", "text")
        if kind not in _FIELD_TYPES:
            raise ValueError(f"{where}.type must be one of {sorted(_FIELD_TYPES)}")
        if spec.get("selector") is not None:
            compile_selector(spec["selector"])
        if kind == "attribute" and not isinstance(spec.get("attribute"), str):
            raise ValueError(f"{where}.attribute is required for type 'attribute'")
        if kind == "regex":
            try:
                re.compile(spec.get("pattern", ""))
            except (re.error, TypeError) as exc:
                raise ValueError(f"{where}.pattern is not a valid regex: {exc}") from exc
            if not spec.get("pattern"):
                raise ValueError(f"{where}.pattern is required for type 'regex'")
        if kind in {"nested", "list"}:
            validate_css_schema({"fields": spec.get("fields")}, _depth=_depth + 1)
        transform = spec.get("transform")
        if transform is not None and transform not in _TRANSFORMS:
            raise ValueError(f"{where}.transform must be one of {sorted(_TRANSFORMS)}")


def is_css_schema(schema: Any) -> bool:
    """Heuristic used by callers that accept either a CSS or a JSON Schema."""
    if not isinstance(schema, dict):
        return False
    if "baseSelector" in schema:
        return True
    fields = schema.get("fields")
    return isinstance(fields, list) and any(
        isinstance(spec, dict) and "selector" in spec for spec in fields
    )


def extract_with_schema(html: str, schema: dict[str, Any], *, base_url: str = "") -> Any:
    """Apply ``schema`` to ``html``: a list of objects with ``baseSelector``, else one object."""
    validate_css_schema(schema)
    root = parse_html(html)
    base = schema.get("baseSelector")
    if base:
        return [_extract_fields(item, schema["fields"], base_url) for item in select(root, base)]
    return _extract_fields(root, schema["fields"], base_url)


def _extract_fields(scope: Node, fields: list[dict[str, Any]], base_url: str) -> dict[str, Any]:
    return {spec["name"]: _extract_field(scope, spec, base_url) for spec in fields}


def _extract_field(scope: Node, spec: dict[str, Any], base_url: str) -> Any:
    kind = spec.get("type", "text")
    selector = spec.get("selector")
    nodes = select(scope, selector) if selector else [scope]
    default = spec.get("default")
    if kind == "list":
        return [_extract_fields(node, spec["fields"], base_url) for node in nodes]
    if kind == "nested":
        return _extract_fields(nodes[0], spec["fields"], base_url) if nodes else default
    values = [value for value in (_value(node, spec, base_url) for node in nodes)]
    values = [value for value in values if value not in (None, "")]
    if spec.get("multiple"):
        return values
    return values[0] if values else default


def _value(node: Node, spec: dict[str, Any], base_url: str) -> Any:
    kind = spec.get("type", "text")
    if kind == "attribute":
        raw: Any = node.attrs.get(spec["attribute"])
    elif kind == "html":
        raw = node.inner_html()
    elif kind == "regex":
        match = re.search(spec["pattern"], node.text())
        if not match:
            return None
        raw = match.group(1) if match.groups() else match.group(0)
    else:
        raw = node.text()
    if raw is None:
        return None
    return _transform(str(raw), spec.get("transform"), base_url)


_NUMBER = re.compile(r"-?\d[\d.,\s]*")


def _transform(value: str, transform: str | None, base_url: str) -> Any:
    value = value.strip()
    if transform == "lower":
        return value.lower()
    if transform == "upper":
        return value.upper()
    if transform == "url":
        return urllib.parse.urljoin(base_url, value) if base_url else value
    if transform == "number":
        return _parse_number(value)
    return value


def _parse_number(value: str) -> float | int | None:
    match = _NUMBER.search(value)
    if not match:
        return None
    digits = re.sub(r"\s", "", match.group(0)).rstrip(".,")
    # "1.234,56" (comma decimal) vs "1,234.56" (dot decimal): the last
    # separator present is the decimal one.
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        head, _, tail = digits.rpartition(",")
        digits = f"{head.replace(',', '')}.{tail}" if len(tail) != 3 else digits.replace(",", "")
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")
    try:
        number = float(digits)
    except ValueError:
        return None
    return int(number) if number.is_integer() and "." not in digits else number
