from __future__ import annotations

import pathlib
import re
import urllib.parse
from html.parser import HTMLParser


class HTMLFactsParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []
        self.title: str = ""
        self.metadata: dict[str, str] = {}
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs if value is not None}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(normalize_url(attrs_dict["href"], self.base_url))
        if tag == "link" and attrs_dict.get("href") and attrs_dict.get("rel"):
            rel = attrs_dict["rel"].lower()
            if "canonical" in rel:
                self.metadata["canonical"] = normalize_url(attrs_dict["href"], self.base_url)
        if tag == "meta":
            key = attrs_dict.get("name") or attrs_dict.get("property")
            value = attrs_dict.get("content")
            if key and value:
                normalized_key = key.lower()
                if normalized_key in {
                    "description",
                    "og:title",
                    "og:description",
                    "og:type",
                    "og:url",
                    "twitter:title",
                    "twitter:description",
                }:
                    self.metadata[normalized_key] = value.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def extract_html_facts(html: str, base_url: str) -> tuple[list[str], dict[str, str]]:
    parser = HTMLFactsParser(base_url)
    try:
        parser.feed(html)
    except Exception:
        pass
    links = sorted({url for url in parser.links if url})
    metadata = dict(parser.metadata)
    if parser.title:
        metadata["title"] = parser.title
    metadata.setdefault("source_url", base_url)
    return links, metadata


def normalize_url(url: str, base_url: str) -> str:
    cleaned = url.strip()
    if not cleaned.startswith(("http://", "https://")) and not base_url.startswith(
        ("http://", "https://")
    ):
        cleaned = cleaned.split("#", 1)[0].split("?", 1)[0]
        if not cleaned:
            return str(pathlib.Path(base_url).expanduser().resolve())
        base_path = pathlib.Path(base_url).expanduser()
        target = pathlib.Path(cleaned).expanduser()
        if not target.is_absolute():
            target = base_path.parent / target
        return str(target.resolve())

    absolute = urllib.parse.urljoin(base_url, cleaned)
    parsed = urllib.parse.urlsplit(absolute)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return absolute

    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if "" == hostname:
        return absolute
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    try:
        port = parsed.port
    except ValueError:
        return absolute
    default_port = 80 if scheme == "http" else 443
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"

    path = parsed.path or "/"
    query = _stable_query(parsed.query)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def same_domain(url: str, root_url: str) -> bool:
    return _origin(url) == _origin(root_url)


def url_allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(re.search(pattern, url) for pattern in include):
        return False
    return not any(re.search(pattern, url) for pattern in exclude)


def _stable_query(query: str) -> str:
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    ignored_prefixes = ("utm_",)
    ignored = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}
    kept = [
        (key, value)
        for key, value in pairs
        if key.lower() not in ignored
        and not any(key.lower().startswith(prefix) for prefix in ignored_prefixes)
    ]
    kept.sort(key=lambda item: (item[0], item[1]))
    return urllib.parse.urlencode(kept, doseq=True)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 80 if scheme == "http" else 443 if scheme == "https" else None
    return scheme, hostname, port
