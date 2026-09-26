"""DOCX, XLSX and PPTX to Markdown with the standard library only.

Office files are zip archives of XML parts, so reading their text needs no
extra dependency: ``zipfile`` plus ``xml.etree``. The output keeps what an
agent needs (headings, paragraphs, lists, tables, one table per sheet, one
section per slide) and drops layout. Archives are size-checked before any part
is inflated, and parts that declare a DOCTYPE are refused, so a zip bomb or an
entity-expansion payload fails with a clear error instead of eating memory.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

from .exceptions import FetchError

OFFICE_SUFFIXES = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}
OFFICE_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_MAX_PART_BYTES = 50 * 1024 * 1024
_MAX_SHEET_ROWS = 5_000
_MAX_CELL_CHARS = 500

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def office_to_markdown(data: bytes, kind: str) -> tuple[str, dict[str, Any]]:
    """Convert an Office file's bytes to Markdown plus metadata."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise FetchError(f"Not a valid {kind.upper()} file: {exc}") from exc
    with archive:
        total = sum(info.file_size for info in archive.infolist())
        if total > _MAX_UNCOMPRESSED_BYTES:
            raise FetchError(
                f"{kind.upper()} expands to more than "
                f"{_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB; refusing to read it"
            )
        reader = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[kind]
        markdown, metadata = reader(archive)
    return markdown.strip(), {"content_format": "markdown", "document_type": kind, **metadata}


def _xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_PART_BYTES:
        raise FetchError(f"Office part {name} is larger than the per-part limit")
    raw = archive.read(info)
    if b"<!DOCTYPE" in raw[:4096].upper():
        raise FetchError(f"Office part {name} declares a DOCTYPE; refusing to parse it")
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise FetchError(f"Office part {name} is not valid XML: {exc}") from exc


def _cell(value: str) -> str:
    value = " ".join(value.split()).replace("|", "\\|")
    return value if len(value) <= _MAX_CELL_CHARS else value[: _MAX_CELL_CHARS - 1] + "…"


def _table(rows: list[list[str]]) -> str:
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = [_cell(value) or f"column_{i + 1}" for i, value in enumerate(rows[0])]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows[1:]]
    return "\n".join(lines)


# --- DOCX -----------------------------------------------------------------


def _docx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = _xml(archive, "word/document.xml")
    if root is None:
        raise FetchError("DOCX has no word/document.xml")
    body = root.find(f"{_W}body")
    blocks: list[str] = []
    tables = 0
    for element in list(body) if body is not None else []:
        if element.tag == f"{_W}p":
            text = _docx_paragraph(element)
            if text:
                blocks.append(text)
        elif element.tag == f"{_W}tbl":
            rows = [
                [
                    " ".join(filter(None, (_docx_text(p) for p in cell.iter(f"{_W}p"))))
                    for cell in row.findall(f"{_W}tc")
                ]
                for row in element.findall(f"{_W}tr")
            ]
            table = _table(rows)
            if table:
                blocks.append(table)
                tables += 1
    return "\n\n".join(blocks), {"table_count": tables}


def _docx_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append(" ")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _docx_paragraph(paragraph: ElementTree.Element) -> str:
    text = _docx_text(paragraph)
    if not text:
        return ""
    props = paragraph.find(f"{_W}pPr")
    style = ""
    if props is not None:
        style_node = props.find(f"{_W}pStyle")
        if style_node is not None:
            style = (style_node.get(f"{_W}val") or "").lower()
        if props.find(f"{_W}numPr") is not None:
            return f"- {text}"
    if style == "title":
        return f"# {text}"
    match = re.fullmatch(r"heading ?(\d)", style)
    if match:
        return f"{'#' * min(int(match.group(1)), 6)} {text}"
    if "list" in style:
        return f"- {text}"
    return text


# --- XLSX -----------------------------------------------------------------


def _xlsx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    shared: list[str] = []
    strings = _xml(archive, "xl/sharedStrings.xml")
    if strings is not None:
        shared = ["".join(t.text or "" for t in si.iter(f"{_S}t")) for si in strings]
    workbook = _xml(archive, "xl/workbook.xml")
    if workbook is None:
        raise FetchError("XLSX has no xl/workbook.xml")
    targets = _relationships(archive, "xl/_rels/workbook.xml.rels", base="xl/")
    sections: list[str] = []
    sheet_meta: list[dict[str, Any]] = []
    for sheet in workbook.iter(f"{_S}sheet"):
        name = sheet.get("name") or "Sheet"
        part = targets.get(sheet.get(f"{_R}id") or "")
        root = _xml(archive, part) if part else None
        rows: list[list[str]] = []
        total_rows = 0
        for row in root.iter(f"{_S}row") if root is not None else []:
            total_rows += 1
            if len(rows) > _MAX_SHEET_ROWS:
                continue
            values: dict[int, str] = {}
            for cell in row.findall(f"{_S}c"):
                values[_column_index(cell.get("r") or "")] = _xlsx_value(cell, shared)
            if values:
                width = max(values) + 1
                rows.append([values.get(i, "") for i in range(width)])
        table = _table(rows)
        sheet_meta.append(
            {
                "name": name,
                "row_count": max(total_rows - 1, 0),
                "rows_omitted": max(total_rows - 1 - _MAX_SHEET_ROWS, 0),
            }
        )
        sections.append(f"## {name}\n\n{table}" if table else f"## {name}\n\n(empty sheet)")
    return "\n\n".join(sections), {"sheet_count": len(sheet_meta), "sheets": sheet_meta}


def _xlsx_value(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_S}t"))
    value = cell.findtext(f"{_S}v") or ""
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _column_index(ref: str) -> int:
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - 64)
    return max(index - 1, 0)


# --- PPTX -----------------------------------------------------------------


def _pptx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    presentation = _xml(archive, "ppt/presentation.xml")
    targets = _relationships(archive, "ppt/_rels/presentation.xml.rels", base="ppt/")
    order: list[str] = []
    if presentation is not None:
        ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        order = [
            targets[rid]
            for slide in presentation.iter(f"{ns}sldId")
            if (rid := slide.get(f"{_R}id") or "") in targets
        ]
    if not order:
        order = sorted(
            (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[-1]),
        )
    sections: list[str] = []
    for number, part in enumerate(order, start=1):
        root = _xml(archive, part)
        if root is None:
            continue
        lines = [
            text
            for paragraph in root.iter(f"{_A}p")
            if (text := "".join(t.text or "" for t in paragraph.iter(f"{_A}t")).strip())
        ]
        title = lines[0] if lines else ""
        body = "\n".join(f"- {line}" for line in lines[1:])
        heading = f"## Slide {number}: {title}" if title else f"## Slide {number}"
        sections.append(f"{heading}\n\n{body}".strip())
    return "\n\n".join(sections), {"slide_count": len(order)}


def _relationships(archive: zipfile.ZipFile, name: str, *, base: str) -> dict[str, str]:
    root = _xml(archive, name)
    targets: dict[str, str] = {}
    for rel in root.iter(f"{_PKG_REL}Relationship") if root is not None else []:
        target = rel.get("Target") or ""
        targets[rel.get("Id") or ""] = (
            target.lstrip("/") if target.startswith("/") else base + target
        )
    return targets
