from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterator

from .config import CrawlConfig


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_ALWAYS_REMOVE_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}
_BOILERPLATE_TAGS = {"nav", "header", "footer", "aside", "form"}
_BOILERPLATE_HINTS = re.compile(
    r"\b(cookie|consent|banner|modal|newsletter|popup|advert|ads|promo|breadcrumb|"
    r"pagination|sidebar|social-share|share-buttons|related-posts)\b",
    re.IGNORECASE,
)
_CONTENT_HINTS = re.compile(
    r"\b(article|body|content|entry|main|post|story|documentation|docs|readme|"
    r"doc-content|page-content)\b",
    re.IGNORECASE,
)
_CONTENT_CONTAINER_TAGS = {"article", "main", "section", "div", "td"}
_MIN_CONTENT_CANDIDATE_CHARS = 120


@dataclass
class _HTMLNode:
    tag: str
    attrs: list[tuple[str, str | None]] = field(default_factory=list)
    children: list["_HTMLNode | str"] = field(default_factory=list)

    def attr(self, name: str) -> str:
        for key, value in self.attrs:
            if key.lower() == name and value is not None:
                return value
        return ""


class _HTMLTreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HTMLNode(tag.lower(), attrs)
        self._stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(_HTMLNode(tag.lower(), attrs))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def html_to_markdown(
    html: str,
    config: CrawlConfig,
    *,
    only_main_content: bool = True,
) -> str:
    html = extract_content_html(html, only_main_content=only_main_content)
    # Extract language tags from <pre><code class="language-python"> before conversion
    code_lang_map = _extract_code_language_tags(html)
    try:
        import html2text

        converter = html2text.HTML2Text()
        converter.ignore_links = not config.include_links
        converter.ignore_images = not config.include_images
        converter.body_width = 0
        converter.unicode_snob = True
        converter.pad_tables = True
        converter.mark_code = True
        markdown = converter.handle(html)
    except Exception:
        markdown = _fallback_text(html)
    return _clean_markdown(markdown, code_lang_map)[: config.max_input_chars]


def chunk_text(text: str, config: CrawlConfig) -> list[str]:
    if not text.strip():
        return []
    try:
        import semchunk

        chunker = semchunk.chunkerify(config.chunk_size)
        chunks = list(chunker(text))
    except Exception:
        chunks = [
            text[index : index + config.chunk_size]
            for index in range(0, len(text), config.chunk_size)
        ]
    return [chunk.strip() for chunk in chunks if chunk.strip()][: config.max_chunks]


def extract_content_html(html: str, *, only_main_content: bool = True) -> str:
    parser = _HTMLTreeParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return _legacy_strip_boilerplate(html)

    candidates = _content_candidates(parser.root)
    selected = (
        _select_content_node(candidates) if only_main_content and candidates else parser.root
    )
    return _serialize_node(selected, only_main_content=only_main_content)


def extraction_provenance(html: str, *, only_main_content: bool = True) -> dict[str, str]:
    parser = _HTMLTreeParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return {
            "extraction_strategy": "legacy_strip_boilerplate",
            "selected_content_hint": "legacy_regex",
        }
    candidates = _content_candidates(parser.root)
    if only_main_content and candidates:
        selected = _select_content_node(candidates)
        return {
            "extraction_strategy": "main_content",
            "selected_content_hint": _content_hint(selected),
        }
    return {
        "extraction_strategy": "full_document",
        "selected_content_hint": "document",
    }


def strip_boilerplate(html: str) -> str:
    return extract_content_html(html, only_main_content=False)


def _content_candidates(root: _HTMLNode) -> list[_HTMLNode]:
    semantic: list[_HTMLNode] = []
    hinted: list[_HTMLNode] = []
    scored: list[_HTMLNode] = []
    for node in _walk_nodes(root):
        if node.tag in {"main", "article"}:
            semantic.append(node)
        elif node.tag in {"div", "section"} and _CONTENT_HINTS.search(_node_identity(node)):
            hinted.append(node)
        elif node.tag in _CONTENT_CONTAINER_TAGS and _looks_like_content_candidate(node):
            scored.append(node)
    return semantic or hinted or scored


def _looks_like_content_candidate(node: _HTMLNode) -> bool:
    text = _node_text(node)
    if len(text) < _MIN_CONTENT_CANDIDATE_CHARS:
        return False
    descendants = list(_walk_nodes(node))
    paragraphs = sum(1 for child in descendants if child.tag in {"p", "li", "pre", "table"})
    if paragraphs < 2:
        return False
    links = sum(1 for child in descendants if child.tag == "a")
    link_density = links / max(1, paragraphs)
    return link_density <= 2.0


def _select_content_node(candidates: list[_HTMLNode]) -> _HTMLNode:
    return max(candidates, key=_content_score)


def _content_hint(node: _HTMLNode) -> str:
    identity = " ".join(_node_identity(node).split())
    if identity:
        return f"{node.tag}:{identity}"
    return node.tag


def _content_score(node: _HTMLNode) -> float:
    descendants = list(_walk_nodes(node))
    links = sum(1 for child in descendants if child.tag == "a")
    blocks = sum(1 for child in descendants if child.tag in {"p", "pre", "table", "li"})
    heading_bonus = 500 if any(child.tag == "h1" for child in descendants) else 0
    return len(_node_text(node)) + blocks * 80 + heading_bonus - links * 15


def _serialize_node(node: _HTMLNode, *, only_main_content: bool) -> str:
    if node.tag in _ALWAYS_REMOVE_TAGS or _is_hidden(node):
        return ""
    if node.tag != "document":
        if _BOILERPLATE_HINTS.search(_node_identity(node)):
            return ""
        if only_main_content and node.tag in _BOILERPLATE_TAGS:
            return ""

    children = "".join(
        html_module.escape(child, quote=False)
        if isinstance(child, str)
        else _serialize_node(child, only_main_content=only_main_content)
        for child in node.children
    )
    if node.tag == "document":
        return children

    attrs = "".join(
        f" {html_module.escape(key, quote=True)}"
        + (f'="{html_module.escape(value, quote=True)}"' if value is not None else "")
        for key, value in node.attrs
        if key.lower() not in {"style", "onclick", "onload"}
    )
    if node.tag in _VOID_TAGS:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{children}</{node.tag}>"


def _walk_nodes(node: _HTMLNode) -> Iterator[_HTMLNode]:
    for child in node.children:
        if isinstance(child, _HTMLNode):
            yield child
            yield from _walk_nodes(child)


def _node_text(node: _HTMLNode) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in _ALWAYS_REMOVE_TAGS:
            parts.append(_node_text(child))
    return " ".join(" ".join(parts).split())


def _node_identity(node: _HTMLNode) -> str:
    return " ".join((node.attr("id"), node.attr("class"), node.attr("role")))


def _is_hidden(node: _HTMLNode) -> bool:
    if node.attr("hidden") or node.attr("aria-hidden").lower() == "true":
        return True
    style = node.attr("style").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _legacy_strip_boilerplate(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|template|svg).*?>.*?</\1>", " ", html)
    return re.sub(r"(?is)<(nav|header|footer|aside|form).*?>.*?</\1>", " ", html)


def _fallback_text(html: str) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return html_module.unescape(" ".join(text.split()))


def _clean_markdown(markdown: str, code_lang_map: dict[int, str] | None = None) -> str:
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").splitlines()]
    cleaned: list[str] = []
    blank = False
    code_block_index = 0
    in_fenced_code = False

    for line in lines:
        if line.strip() == "[code]":
            lang = code_lang_map.get(code_block_index, "") if code_lang_map else ""
            cleaned.append(f"```{lang}")
            code_block_index += 1
            in_fenced_code = True
            blank = False
            continue
        if line.strip() == "[/code]":
            cleaned.append("```")
            in_fenced_code = False
            blank = False
            continue
        if line.strip() == "```":
            if in_fenced_code:
                cleaned.append("```")
                in_fenced_code = False
            else:
                lang = code_lang_map.get(code_block_index, "") if code_lang_map else ""
                cleaned.append(f"```{lang}")
                code_block_index += 1
                in_fenced_code = True
            blank = False
            continue
        if not line.strip():
            if cleaned and not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False

    return "\n".join(cleaned).strip()


def _extract_code_language_tags(html: str) -> dict[int, str]:
    """Extract language tags from code elements in document order.

    Returns a mapping from code block index to language string. Blocks without a
    language still advance the index so later tagged blocks stay aligned with
    html2text's generated fences.
    """
    pattern = re.compile(r"<code\b([^>]*)>", re.IGNORECASE)
    lang_map: dict[int, str] = {}
    for index, match in enumerate(pattern.finditer(html)):
        attrs = match.group(1)
        class_match = re.search(r"\sclass=[\"']([^\"']*)[\"']", attrs, re.IGNORECASE)
        if not class_match:
            continue
        language = _language_from_classes(class_match.group(1).split())
        if language:
            lang_map[index] = language
    return lang_map


def _language_from_classes(classes: list[str]) -> str:
    known_languages = {
        "bash",
        "c",
        "cpp",
        "csharp",
        "css",
        "go",
        "html",
        "java",
        "javascript",
        "js",
        "json",
        "markdown",
        "md",
        "php",
        "python",
        "ruby",
        "rust",
        "shell",
        "sql",
        "ts",
        "typescript",
        "xml",
        "yaml",
    }
    for cls in classes:
        if cls.startswith("language-"):
            return cls.removeprefix("language-")
        if cls.startswith("lang-"):
            return cls.removeprefix("lang-")
    for cls in classes:
        if cls in known_languages:
            return cls
    return ""
