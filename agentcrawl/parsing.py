from __future__ import annotations

import hashlib
import html as html_module
import re
import textwrap
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterator

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
    r"pagination|sidebar|toc|sticky|rail|social-share|share-buttons|related-posts)\b",
    re.IGNORECASE,
)
# Text-level cookie/consent notice patterns. Used to drop leaf nodes whose
# body text is dominated by cookie-consent wording even when the surrounding
# container has no `cookie`/`consent` class or id.
_COOKIE_CONSENT_TEXT_RE = re.compile(
    r"\b(cookies?\s+consent|we\s+use\s+cookies|"
    r"this\s+(site|website)\s+uses\s+cookies|"
    r"cookie\s+(policy|settings|preferences|notice)|"
    r"accept\s+(all\s+)?cookies|manage\s+cookies?|"
    r"by\s+continuing\s+you\s+accept|consent\s+to\s+cookies)\b",
    re.IGNORECASE,
)
_CONTENT_HINTS = re.compile(
    r"\b(article|body|content|entry|main|post|story|documentation|docs|readme|"
    r"doc-content|page-content)\b",
    re.IGNORECASE,
)
# Whole class/id tokens only: "index-column_main" or "page--index" are layout
# names, and matching them inside a token emptied real pages.
_INDEX_TOKENS = {"index", "toc", "genindex"}
# Generated back-of-document indexes (RFCs put theirs in "appendix-D") are
# recognised by their heading, not by the appendix id: the other appendices
# (acknowledgements, changes, authors) are content.
_INDEX_HEADING_RE = re.compile(r"^(?:[A-Z]\.\s*|Appendix [A-Z]\.?\s*)?Index$", re.IGNORECASE)
_CONTENT_CONTAINER_TAGS = {"article", "main", "section", "div", "td", "body"}
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
    base_url: str | None = None,
) -> str:
    """Convert HTML to Markdown **without** applying any output budget.

    ``base_url`` (the page's final http(s) URL) turns relative links and image
    sources into absolute ones, so an agent can follow them as written.

    The parser deliberately does not truncate: only the caller can record a
    truncation in the document metadata, and a silent cap here made the
    primary product output lose content with nothing to show for it. Callers
    apply :func:`apply_output_budget` and report what it dropped.
    """
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
        # Padding aligned table columns with spaces for human eyes; on real
        # pages (GitHub file lists) that was a third of the output characters.
        converter.pad_tables = False
        if base_url and base_url.startswith(("http://", "https://")):
            converter.baseurl = base_url
        converter.mark_code = True
        markdown = converter.handle(html)
    except Exception:
        markdown = _fallback_text(html)
    return _clean_markdown(markdown, code_lang_map)


def apply_output_budget(text: str, limit: int) -> tuple[str, int]:
    """Cut ``text`` to ``limit`` characters and report how much was dropped.

    Returns ``(text, chars_omitted)``. Truncation is a decision the caller has
    to own: only it knows where to record the fact, and losing content without
    saying so is what this helper exists to prevent.
    """
    if limit <= 0 or len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def budget_markdown(
    markdown: str, limit: int, query: str | None = None
) -> tuple[str, int, dict[str, Any]]:
    """Fit ``markdown`` in ``limit`` chars; with a query, keep the relevant blocks.

    Returns ``(text, chars_omitted, info)``; ``info["markdown_selection"]`` is
    ``"bm25"`` when relevance picked the kept blocks and ``"head"`` otherwise.
    """
    if limit <= 0 or len(markdown) <= limit:
        return markdown, 0, {}
    if query and query.strip():
        from .relevance import select_within_budget

        selected, info = select_within_budget(markdown, query, limit)
        if info.get("ranked"):
            return (
                selected,
                max(0, len(markdown) - len(selected)),
                {
                    "markdown_selection": "bm25",
                    "markdown_blocks_total": info["blocks_total"],
                    "markdown_blocks_kept": info["blocks_kept"],
                },
            )
    text, omitted = apply_output_budget(markdown, limit)
    return text, omitted, {"markdown_selection": "head"}


def chunk_text_stats(
    text: str, config: CrawlConfig, query: str | None = None
) -> tuple[list[str], dict[str, Any]]:
    """Split ``text`` into chunks and report what the ``max_chunks`` cap dropped.

    The old behaviour returned the first ``max_chunks`` chunks and threw the
    rest away silently, so a long page answered questions about its first
    half only — while the extraction prompt still claimed to use "the page
    text". The caller now gets the counts and can surface them.
    """
    if not text.strip():
        return [], {
            "chunks_total": 0,
            "chunks_used": 0,
            "chunks_omitted": 0,
            "chunk_chars_omitted": 0,
        }
    try:
        import semchunk

        chunker = semchunk.chunkerify(config.chunk_size)
        chunks = list(chunker(text))
    except Exception:
        chunks = [
            text[index : index + config.chunk_size]
            for index in range(0, len(text), config.chunk_size)
        ]
    cleaned = [chunk.strip() for chunk in chunks if chunk.strip()]
    selection = "head"
    if query and config.relevance_chunking and len(cleaned) > config.max_chunks:
        from .relevance import select_top_passages

        keep, ranked = select_top_passages(cleaned, query, config.max_chunks)
        if ranked:
            selection = "bm25"
        kept = set(keep)
        used = [chunk for index, chunk in enumerate(cleaned) if index in kept]
        omitted = [chunk for index, chunk in enumerate(cleaned) if index not in kept]
    else:
        used = cleaned[: config.max_chunks]
        omitted = cleaned[config.max_chunks :]
    stats: dict[str, Any] = {
        "chunk_selection": selection,
        "chunks_total": len(cleaned),
        "chunks_used": len(used),
        "chunks_omitted": len(omitted),
        "chunk_chars_omitted": sum(len(chunk) for chunk in omitted),
    }
    return used, stats


def chunk_text(text: str, config: CrawlConfig) -> list[str]:
    """Chunks for extraction. See :func:`chunk_text_stats` for the omission counts."""
    return chunk_text_stats(text, config)[0]


# When the selected main content keeps less than this share of the page's
# readable text, the selection is treated as wrong (a slider, a modal, a
# container hidden until scripts run) and a wider extraction is used instead.
_MIN_SELECTED_SHARE = 0.25
_MIN_PAGE_CHARS_FOR_FALLBACK = 500


def extract_content_html(html: str, *, only_main_content: bool = True) -> str:
    parser = _HTMLTreeParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return _legacy_strip_boilerplate(html)

    candidates = _content_candidates(parser.root)
    selected = _select_content_node(candidates) if only_main_content and candidates else parser.root
    serialized = _serialize_node(selected, only_main_content=only_main_content)
    if not only_main_content:
        return serialized
    # Never return (nearly) nothing for a page that has text: fall back to the
    # whole page without navigation and boilerplate, then to the whole page
    # with class-based hiding ignored (scripts reveal those containers).
    page_chars = len(_node_text(parser.root))
    if page_chars < _MIN_PAGE_CHARS_FOR_FALLBACK:
        return serialized
    if not _is_clear_article(selected):
        whole = _serialize_node(parser.root, only_main_content=True)
        if _html_text_chars(serialized) < _HOMEPAGE_SHARE * _html_text_chars(whole):
            serialized = whole
    for node, honor_hidden_classes in ((parser.root, True), (parser.root, False)):
        if _html_text_chars(serialized) >= _MIN_SELECTED_SHARE * page_chars:
            break
        wider = _serialize_node(
            node, only_main_content=True, honor_hidden_classes=honor_hidden_classes
        )
        if _html_text_chars(wider) > _html_text_chars(serialized):
            serialized = wider
    return serialized


# Homepage mode: when the selection is not one clear article and holds less
# than this share of the page (minus menus, footers and boilerplate), a
# homepage's sections were missed and the whole page minus boilerplate is used.
_HOMEPAGE_SHARE = 0.5


def _is_clear_article(node: _HTMLNode) -> bool:
    descendants = list(_walk_nodes(node))
    articles = sum(1 for child in descendants if child.tag == "article")
    h1 = sum(1 for child in descendants if child.tag == "h1") + (node.tag == "h1")
    paragraphs = sum(1 for child in descendants if child.tag == "p")
    return (node.tag == "article" or h1 == 1) and articles <= 1 and paragraphs >= 3


def _html_text_chars(html: str) -> int:
    return len("".join(re.sub(r"(?s)<[^>]+>", " ", html_module.unescape(html)).split()))


def extraction_provenance(html: str, *, only_main_content: bool = True) -> dict[str, Any]:
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
        selected_text = _node_text(selected)
        return {
            "extraction_strategy": "main_content",
            "selected_content_hint": _content_hint(selected),
            "selected_content_tag": selected.tag,
            "selected_content_id": selected.attr("id"),
            "selected_content_classes": selected.attr("class"),
            "candidate_count": len(candidates),
            "selected_content_score": round(_content_score(selected), 2),
            "selected_text_chars": len(selected_text),
            "content_sha256": hashlib.sha256(selected_text.encode("utf-8")).hexdigest(),
        }
    return {
        "extraction_strategy": "full_document",
        "selected_content_hint": "document",
        "selected_content_tag": "document",
        "candidate_count": len(candidates),
    }


def strip_boilerplate(html: str) -> str:
    return extract_content_html(html, only_main_content=False)


def markdown_structure_metrics(markdown: str) -> dict[str, int]:
    lines = markdown.splitlines()
    return {
        "heading_count": sum(1 for line in lines if re.match(r"^#{1,6}\s+", line)),
        "fenced_code_block_count": sum(1 for line in lines if line.startswith("```")),
        "table_row_count": sum(1 for line in lines if line.strip().startswith("|")),
        "link_markdown_count": len(re.findall(r"\[[^\]]+\]\([^)]+\)", markdown)),
    }


def _content_candidates(root: _HTMLNode) -> list[_HTMLNode]:
    scored: list[_HTMLNode] = []
    for node in _walk_nodes(root):
        # A node the serializer drops (a modal, a cookie banner) can never be
        # the answer: selecting it returned an empty page.
        if _is_boilerplate(node) or _is_hidden(node):
            continue
        if node.tag in _CONTENT_CONTAINER_TAGS and _looks_like_content_candidate(node):
            scored.append(node)
    return scored


def _looks_like_content_candidate(node: _HTMLNode) -> bool:
    text = _node_text(node)
    if len(text) < _MIN_CONTENT_CANDIDATE_CHARS:
        return False
    descendants = list(_walk_nodes(node))
    paragraphs = sum(1 for child in descendants if child.tag in {"p", "li", "pre", "table"})
    if paragraphs < 2:
        return False
    if _is_main_landmark(node):
        return True  # declared by the page; link-heavy articles (Wikipedia) count
    links = sum(1 for child in descendants if child.tag == "a")
    link_density = links / max(1, paragraphs)
    return link_density <= 2.0


def _select_content_node(candidates: list[_HTMLNode]) -> _HTMLNode:
    best = max(candidates, key=_content_score)
    # The page's own "main" landmark beats a bigger container around it: the
    # extra text in <body> is its header, sidebar and footer. Only a landmark
    # inside the winner, and only when it holds most of the winner's text, so a
    # tiny mislabeled <main> never wins and a precise <article> is kept.
    inside_best = {id(node) for node in _walk_nodes(best)}
    landmarks = [
        node
        for node in candidates
        if _is_main_landmark(node) and id(node) in inside_best and not _is_boilerplate(node)
    ]
    if landmarks and not _is_main_landmark(best):
        landmark = max(landmarks, key=_content_score)
        if len(_node_text(landmark)) >= 0.5 * len(_node_text(best)):
            return landmark
    return best


def _is_main_landmark(node: _HTMLNode) -> bool:
    return node.tag == "main" or node.attr("role").lower() == "main"


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
    semantic_bonus = 350 if node.tag in {"main", "article", "body"} else 0
    hint_bonus = 250 if _CONTENT_HINTS.search(_node_identity(node)) else 0
    boilerplate_penalty = 700 if _is_boilerplate(node) else 0
    index_penalty = 10000 if _is_index_node(node) else 0
    child_boilerplate_penalty = sum(120 for child in descendants if _is_boilerplate(child))
    return (
        len(_node_text(node))
        + blocks * 80
        + heading_bonus
        + semantic_bonus
        + hint_bonus
        - links * 15
        - boilerplate_penalty
        - index_penalty
        - child_boilerplate_penalty
    )


def _serialize_node(
    node: _HTMLNode, *, only_main_content: bool, honor_hidden_classes: bool = True
) -> str:
    if node.tag in _ALWAYS_REMOVE_TAGS or _is_hidden(node, classes=honor_hidden_classes):
        return ""
    if node.tag != "document":
        if _is_boilerplate(node):
            return ""
        if only_main_content and _is_index_node(node):
            return ""
        if (
            only_main_content
            and node.tag in _BOILERPLATE_TAGS
            and not (node.tag == "form" and _is_page_form(node))
        ):
            return ""
        if _is_cookie_consent_text(node):
            return ""

    children = "".join(
        html_module.escape(child, quote=False)
        if isinstance(child, str)
        else _serialize_node(
            child,
            only_main_content=only_main_content,
            honor_hidden_classes=honor_hidden_classes,
        )
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


def _is_index_node(node: _HTMLNode) -> bool:
    if _INDEX_TOKENS & set(_node_identity(node).lower().split()):
        return True
    if node.tag not in {"section", "div"}:
        return False
    heading = _first_heading(node, depth=2)
    return heading is not None and bool(_INDEX_HEADING_RE.match(_node_text(heading)))


def _first_heading(node: _HTMLNode, *, depth: int) -> _HTMLNode | None:
    for child in node.children:
        if not isinstance(child, _HTMLNode):
            continue
        if child.tag in {"h1", "h2", "h3"}:
            return child
        if depth > 1 and child.tag not in {"section", "article"}:
            found = _first_heading(child, depth=depth - 1)
            if found is not None:
                return found
    return None


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


def _is_cookie_consent_text(node: _HTMLNode) -> bool:
    """Return True for non-empty, leaf-ish nodes whose text reads as a
    cookie / consent notice. Containers with substantial non-cookie
    children are left alone so legitimate content that merely mentions
    cookies is preserved."""
    if node.tag not in {"p", "div", "section", "aside", "small", "span", "li"}:
        return False
    text = _node_text(node)
    if not text:
        return False
    if len(text) > 600:  # long blocks are content, not boilerplate
        return False
    if not _COOKIE_CONSENT_TEXT_RE.search(text):
        return False
    # Require at least one cookie/consent phrase to be near the start so we
    # don't drop paragraphs that contain the word "cookie" in unrelated
    # technical content (e.g. docs about cookies-as-a-feature).
    head = text[:160]
    return bool(_COOKIE_CONSENT_TEXT_RE.search(head))


def _is_boilerplate(node: _HTMLNode) -> bool:
    """A cookie banner, sidebar, promo... named so by its id/class/role.

    ``<html>``, ``<body>`` and the page's main landmark are never boilerplate,
    whatever their classes say: WordPress puts page-state words on ``<body>``
    ("sticky-header", "has-sidebar"), and matching them dropped whole pages.
    """
    if node.tag in {"html", "body"} or _is_main_landmark(node):
        return False
    # Utility-CSS tokens ('[grid-template-areas:"main_sidebar"]', "md:rail")
    # describe layout, not what the element is.
    identity = " ".join(
        token
        for token in _node_identity(node).split()
        if not any(mark in token for mark in "[]:\"'")
    )
    words = {word.lower() for word in _BOILERPLATE_HINTS.findall(identity)}
    if not words:
        return False
    return not words <= _SOFT_HINTS or _is_link_heavy(node)


# Layout words that also name real content ("hero banner", "promo" cards, a
# "sticky" intro, an FAQ "modal"): boilerplate only when the node is mostly links.
_SOFT_HINTS = {"banner", "modal", "promo", "sidebar", "sticky", "rail"}


def _is_link_heavy(node: _HTMLNode) -> bool:
    text = len(_node_text(node))
    links = sum(len(_node_text(child)) for child in _walk_nodes(node) if child.tag == "a")
    return text < 40 or links >= 0.5 * text


def _is_page_form(node: _HTMLNode) -> bool:
    """A ``<form>`` wrapping the page itself (ASP.NET WebForms), not a widget.

    Search boxes and sign-up forms are boilerplate; a form that holds the
    page's headings or main landmark is the page.
    """
    return any(
        child.tag in {"h1", "h2", "main", "article"} or _is_main_landmark(child)
        for child in _walk_nodes(node)
    )


def _node_identity(node: _HTMLNode) -> str:
    return " ".join((node.attr("id"), node.attr("class"), node.attr("role")))


def _is_hidden(node: _HTMLNode, *, classes: bool = True) -> bool:
    # Inactive tab panels (docs "macOS / Linux / Windows" tabs) are hidden only
    # until a click; their content is real, so keep it.
    if node.attr("role").lower() == "tabpanel":
        return False
    identity = _node_identity(node).lower()
    hidden_tokens = {"hidden", "sr-only", "visually-hidden", "screen-reader-only"}
    visually_hidden = any(token in identity.replace("_", "-").split() for token in hidden_tokens)
    # A table header kept only for screen readers (GitHub Docs) still names the
    # columns; without it the Markdown table has no header row at all.
    if node.tag in {"thead", "caption"} and visually_hidden and not node.attr("hidden"):
        return False
    if node.attr("hidden"):
        return True
    if node.attr("aria-hidden").lower() == "true":
        # Collapsed accordion and FAQ panels are aria-hidden until clicked;
        # their text is content. Hidden menus are mostly links.
        return _is_link_heavy(node)
    if visually_hidden and classes:
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
    code: list[str] | None = None  # lines of the fenced block being read

    def open_fence(native_lang: str = "") -> list[str]:
        nonlocal code_block_index, blank
        lang = code_lang_map.get(code_block_index, "") if code_lang_map else ""
        cleaned.append(f"```{native_lang or lang}")
        code_block_index += 1
        blank = False
        return []

    def close_fence(block: list[str]) -> None:
        nonlocal blank
        # html2text indents ``<pre>`` content by four spaces and keeps the
        # blank lines around it; inside a fence both are just wrong code.
        cleaned.extend(textwrap.dedent("\n".join(block)).strip("\n").split("\n") if block else [])
        cleaned.append("```")
        blank = False

    for line in lines:
        marker = line.strip()
        if code is None and marker in {"[code]", "```"}:
            code = open_fence()
            continue
        if code is None and _NATIVE_FENCE_RE.match(marker):
            # A fence that already names its language keeps it and still
            # takes its slot in the <pre> order.
            code = open_fence(marker[3:])
            continue
        if code is not None and marker in {"[/code]", "```"}:
            close_fence(code)
            code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if not marker:
            if cleaned and not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(_EMPHASIS_GAP_RE.sub(r"\1\2", line))
        blank = False
    if code is not None:
        close_fence(code)

    compact = _compact_tables(_strip_link_titles("\n".join(cleaned)))
    return _EXTRA_BLANK_LINES_RE.sub("\n\n", compact).strip()


# html2text leaves a space between closing emphasis and punctuation
# ("**web crawler** , sometimes"): a visible defect, and a phrase search
# for "web crawler, sometimes" fails on it.
_NATIVE_FENCE_RE = re.compile(r"^```[A-Za-z0-9_+#.-]+$")
_EMPHASIS_GAP_RE = re.compile(r"(\S(?:\*\*|__|\*|_)) ([,.;:!?)\]])")


# ``[text](url "title")``: link titles are tooltips ("This path skips through
# empty directories"), pure token cost for an agent.
_EXTRA_BLANK_LINES_RE = re.compile(r"\n{3,}")
# Accessibility skip links ("Skip to main content", "Jump to content") are navigation, not content.
_SKIP_LINK_RE = re.compile(
    r"^\s*\[?(?:skip|jump) to (?:main )?(?:content|navigation)\]?(?:\([^)]*\))?\s*$", re.I
)
# ``[](url)``: a link whose only content was an image (badges, logos) once
# images are dropped. Nothing for an agent to read; the bare URL is noise.
_EMPTY_LINK_RE = re.compile(r"(?<![!\]])\[\]\((?:[^()\s]|\([^()\s]*\))*\)")
_LINK_TITLE_RE = re.compile(r'(\]\((?:[^()\s]|\([^()\s]*\))+) "[^"\n]*"\)')
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _strip_link_titles(markdown: str) -> str:
    out: list[str] = []
    in_code = False
    for line in markdown.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        if in_code:
            out.append(line)
            continue
        if _SKIP_LINK_RE.match(line):
            continue
        line = _LINK_TITLE_RE.sub(r"\1)", line)
        stripped = _EMPTY_LINK_RE.sub("", line)
        if stripped != line and not stripped.strip():
            continue  # the line held nothing but empty links
        out.append(stripped.rstrip() if stripped != line else line)
    return "\n".join(out)


def _compact_tables(markdown: str) -> str:
    """Trim cell whitespace and drop a column that repeats its left neighbour.

    Sites often render one column twice (a desktop and a mobile variant, one of
    them hidden with CSS); the reader sees one, the Markdown got both.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and "|" in lines[index]
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            out.extend(_compact_table(lines[index:end]))
            index = end
            continue
        out.append(lines[index])
        index += 1
    return "\n".join(out)


def _split_row(line: str, outer_pipes: bool) -> list[str]:
    # html2text writes tables without outer pipes, so there a leading "|" is an
    # empty first cell (GitHub's screen-reader-only header), not decoration.
    text = line.strip()
    if outer_pipes:
        if text.startswith("|"):
            text = text[1:]
        if text.endswith("|") and not text.endswith("\\|"):
            text = text[:-1]
    return [cell.strip() for cell in _CELL_SPLIT_RE.split(text)]


def _compact_table(rows: list[str]) -> list[str]:
    outer_pipes = rows[1].strip().startswith("|")
    cells = [_split_row(row, outer_pipes) for row in rows]
    width = len(cells[1])
    if any(len(row) != width for row in cells):
        return [row.rstrip() for row in rows]
    header, body = cells[0], cells[2:]
    keep: list[int] = []
    for column in range(width):
        if body and all(not row[column] for row in body):
            continue  # no data at all (often filled in later by JavaScript)
        previous = keep[-1] if keep else None
        if (
            previous is not None
            and body
            and all(row[column] == row[previous] for row in body)
            # Two real columns can hold equal values (Min/Max both 1), so only
            # a twin with the same or a missing header counts as a duplicate.
            and (header[column] == header[previous] or not header[column] or not header[previous])
        ):
            if not header[previous]:
                header[previous] = header[column]
            continue  # the same data twice
        keep.append(column)
    if not keep:
        return [row.rstrip() for row in rows]
    rendered = ["| " + " | ".join(header[column] for column in keep) + " |"]
    rendered.append("|" + "|".join("---" for _ in keep) + "|")
    rendered.extend("| " + " | ".join(row[column] for column in keep) + " |" for row in body)
    return rendered


_PRE_RE = re.compile(r"<pre\b([^>]*)>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r"\sclass=[\"']([^\"']*)[\"']", re.IGNORECASE)
_CODE_OPEN_RE = re.compile(r"\s*<code\b([^>]*)>", re.IGNORECASE)
# Wrapper classes that name the language one level up: Sphinx
# (``highlight-python3``), Pygments/Rouge (``language-ruby highlighter-rouge``).
_WRAPPER_LANG_RE = re.compile(r"class=[\"'][^\"']*\bhighlight-([a-z0-9+#-]+)", re.IGNORECASE)


def _extract_code_language_tags(html: str) -> dict[int, str]:
    """Language of each ``<pre>`` block, by its position among ``<pre>`` blocks.

    html2text turns every ``<pre>`` (and only ``<pre>``) into a fenced block, so
    the index has to count ``<pre>`` elements. It used to count every
    ``<code>``, so one inline ``code`` span before the first block shifted
    every language onto the wrong fence (or off the page). The language comes
    from the ``<pre>`` class (MDN: ``brush: js``), its ``<code>`` child
    (``language-rust``), or a wrapper just before it (Sphinx:
    ``highlight-python3``).
    """
    lang_map: dict[int, str] = {}
    for index, match in enumerate(_PRE_RE.finditer(html)):
        classes: list[str] = []
        pre_class = _CLASS_ATTR_RE.search(match.group(1))
        if pre_class:
            classes += pre_class.group(1).replace(":", " ").split()
        code = _CODE_OPEN_RE.match(html, match.end())
        if code:
            code_class = _CLASS_ATTR_RE.search(code.group(1))
            if code_class:
                classes = code_class.group(1).split() + classes
        language = _language_from_classes(classes)
        if not language:
            window = html[max(0, match.start() - 300) : match.start()]
            wrappers = _WRAPPER_LANG_RE.findall(window)
            if wrappers:
                language = _language_from_classes([f"language-{wrappers[-1]}"])
        if language:
            lang_map[index] = language
    return lang_map


_LANGUAGE_ALIASES = {
    "python3": "python",
    "py": "python",
    "pycon": "python",
    "sh": "bash",
    "console": "console",
    "shell-session": "console",
    "javascript": "javascript",
}
_NOT_A_LANGUAGE = {"default", "none", "plain", "plaintext", "notranslate", "nohighlight"}


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
        for prefix in ("language-", "lang-"):
            if cls.startswith(prefix):
                language = cls.removeprefix(prefix).lower()
                if language in _NOT_A_LANGUAGE:
                    return ""
                return _LANGUAGE_ALIASES.get(language, language)
    for cls in classes:
        if cls.lower() in known_languages:
            return _LANGUAGE_ALIASES.get(cls.lower(), cls.lower())
    return ""
