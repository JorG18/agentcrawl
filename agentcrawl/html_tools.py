from __future__ import annotations

from functools import lru_cache
import json
import pathlib
import re
import urllib.parse
from html.parser import HTMLParser

try:
    import regex as _regex  # matches with a timeout (a core dependency)
except ImportError:  # pragma: no cover - broken install
    _regex = None

# re holds the GIL while it backtracks: one (a+)+ pattern froze the server.
_MATCH_TIMEOUT_S = 0.05
_MAX_PATTERNS = 20
_PATTERN_ERRORS: tuple[type[Exception], ...] = (re.error, TimeoutError)
if _regex is not None:
    _PATTERN_ERRORS += (_regex.error,)


class HTMLFactsParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []
        self.title: str = ""
        self.metadata: dict[str, str] = {}
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []
        self._jsonld_documents: list[object] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs if value is not None}
        tag = tag.lower()
        if tag == "html" and attrs_dict.get("lang", "").strip():
            # The first <html> wins, as in browsers: later ones sit inside <svg> or <textarea> demos.
            self.metadata.setdefault("language", attrs_dict["lang"].strip())
        if tag == "title":
            self._in_title = True
        if tag == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []
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
                    "og:image",
                    "og:type",
                    "og:url",
                    "twitter:title",
                    "twitter:description",
                }:
                    self.metadata[normalized_key] = value.strip()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            payload = "".join(self._jsonld_parts).strip()
            if payload:
                try:
                    self._jsonld_documents.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_jsonld:
            self._jsonld_parts.append(data)


class _BaseHrefParser(HTMLParser):
    """Finds the first <base href> the way a browser does: comments and script
    text are not tags, and only the real ``href`` attribute counts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base":
            href = dict(attrs).get("href")
            if href:
                self.href = href
                raise _BaseFound
        elif tag == "body":
            raise _BaseFound  # <base> belongs in <head>; stop reading here


class _BaseFound(Exception):
    pass


def document_base_url(html: str, url: str) -> str:
    """The URL relative links resolve against: the first http(s) ``<base href>``, else ``url``."""
    if "<base" not in html[:200_000].lower():
        return url
    parser = _BaseHrefParser()
    try:
        parser.feed(html[:200_000])
    except _BaseFound:
        pass
    except Exception:  # noqa: BLE001 - a malformed head keeps the page URL
        return url
    if not parser.href:
        return url
    base = urllib.parse.urljoin(url, parser.href.strip())
    # Browsers ignore a javascript:/data:/mailto: base; so do we.
    return base if base.startswith(("http://", "https://")) else url


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
    metadata.update(_jsonld_metadata(parser._jsonld_documents))
    metadata.setdefault("source_url", base_url)
    return links, metadata


def _jsonld_metadata(documents: list[object]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    nodes = list(_iter_jsonld_nodes(documents))
    if not nodes:
        return metadata
    metadata["jsonld_count"] = str(len(nodes))
    schema_types = sorted({schema_type for node in nodes for schema_type in _schema_types(node)})
    if schema_types:
        metadata["schema_types"] = ",".join(schema_types)
    product = next((node for node in nodes if "Product" in _schema_types(node)), None)
    if product:
        if name := _json_scalar(product.get("name")):
            metadata["product_name"] = name
        if sku := _json_scalar(product.get("sku")):
            metadata["product_sku"] = sku
        brand = product.get("brand")
        if isinstance(brand, dict) and (brand_name := _json_scalar(brand.get("name"))):
            metadata["product_brand"] = brand_name
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            if price := _json_scalar(offers.get("price")):
                metadata["product_price"] = price
            if currency := _json_scalar(offers.get("priceCurrency")):
                metadata["product_currency"] = currency
            if availability := _json_scalar(offers.get("availability")):
                metadata["product_availability"] = availability.rsplit("/", 1)[-1]
        rating = product.get("aggregateRating")
        if isinstance(rating, dict):
            if rating_value := _json_scalar(rating.get("ratingValue")):
                metadata["product_rating_value"] = rating_value
            if review_count := _json_scalar(rating.get("reviewCount")):
                metadata["product_review_count"] = review_count
    return metadata


def _iter_jsonld_nodes(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _iter_jsonld_nodes(item)
        return
    if not isinstance(value, dict):
        return
    graph = value.get("@graph")
    if isinstance(graph, list):
        yield from _iter_jsonld_nodes(graph)
    yield value


def _schema_types(node: dict[str, object]) -> list[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _json_scalar(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float):
        return str(value)
    return ""


@lru_cache(maxsize=256)
def _normalize_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".").lower()
    if not normalized:
        return ""
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return normalized


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

    hostname = _normalize_hostname(parsed.hostname or "")
    if not hostname:
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
    # Both fail closed: a pattern that times out neither admits nor un-excludes.
    if include and not any(_pattern_matches(pattern, url) for pattern in include):
        return False
    return not any(_pattern_matches(pattern, url, on_timeout=True) for pattern in exclude)


def _pattern_matches(pattern: str, url: str, *, on_timeout: bool = False) -> bool:
    """``re.search`` that a caller's typo can never turn into an exception.

    ``include`` / ``exclude`` are user input (the CLI flags and the API's
    ``include`` / ``exclude`` bodies), and an uncompilable pattern used to
    raise ``re.error`` from inside the crawl loop — which surfaced as an HTTP
    500 on ``POST /v1/map``. The API rejects bad patterns up front; this
    guarantees the library cannot crash mid-crawl either: an unusable pattern
    simply matches nothing.
    """
    try:
        if _regex is not None:
            return _regex.search(pattern, url, timeout=_MATCH_TIMEOUT_S) is not None
        return re.search(pattern, url) is not None
    except TimeoutError:
        return on_timeout
    except _PATTERN_ERRORS:
        return False


def validate_url_patterns(patterns: list[str] | None) -> list[str] | None:
    """Raise ``ValueError`` for a pattern that cannot compile, naming it."""
    if patterns is None:
        return None
    if len(patterns) > _MAX_PATTERNS:
        # Each URL is matched against every pattern, each up to the timeout.
        raise ValueError(f"At most {_MAX_PATTERNS} patterns are accepted, got {len(patterns)}")
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise ValueError(f"Patterns must be strings, got {type(pattern).__name__}")
        try:
            # Validate with the engine that will match.
            (_regex or re).compile(pattern)
        except _PATTERN_ERRORS as exc:
            raise ValueError(f"Invalid regular expression {pattern!r}: {exc}") from exc
    return patterns


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
    hostname = _normalize_hostname(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 80 if scheme == "http" else 443 if scheme == "https" else None
    return scheme, hostname, port
