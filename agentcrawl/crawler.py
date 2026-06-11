from __future__ import annotations

import re
import urllib.parse
import urllib.request
import urllib.robotparser
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict
from typing import Any, Callable

from .config import CrawlConfig
from .documents import markdown_from_fetched_content
from .errors import classify_error
from .exceptions import FetchError
from .fetchers import fetch_source
from .html_tools import extract_html_facts, normalize_url, same_domain, url_allowed
from .models import CrawlRun, MapResult, ScrapeDocument
from .parsing import html_to_markdown, extraction_provenance, markdown_structure_metrics
from .security import validate_remote_url


_HEADING_MARKER_RE = re.compile(r"^#{1,6}\s+")
_LIST_MARKER_RE = re.compile(r"^[-*+]\s+")
_BLOCKQUOTE_MARKER_RE = re.compile(r"^>\s*")
_ORDERED_LIST_MARKER_RE = re.compile(r"^\d+[.)]\s+")


class AgentCrawl:
    """Local-first AgentCrawl engine.

    This path does not require an LLM. It turns live/local sources into clean
    context that agents can consume, and uses local resources by default.
    """

    def __init__(self, config: dict[str, Any] | CrawlConfig | None = None):
        self.config = CrawlConfig.from_dict(config)

    def scrape(
        self,
        source: str,
        formats: list[str] | None = None,
        only_main_content: bool | None = None,
    ) -> ScrapeDocument | dict[str, Any]:
        requested = formats or ["markdown"]
        try:
            html, fetch_metadata = fetch_source(source, self.config)
            links, metadata = extract_html_facts(html, source)
            main_content = True if only_main_content is None else only_main_content
            markdown = markdown_from_fetched_content(html, fetch_metadata)
            if markdown is None:
                markdown = html_to_markdown(
                    html,
                    self.config,
                    only_main_content=main_content,
                )
                provenance = extraction_provenance(html, only_main_content=main_content)
            else:
                provenance = {
                    "extraction_strategy": "document_passthrough",
                    "selected_content_hint": str(fetch_metadata.get("document_type") or "document"),
                }
            text = _markdown_to_text(markdown)
            structure_metrics = markdown_structure_metrics(markdown)
            source_url = str(metadata.get("source_url") or source)
            document = ScrapeDocument(
                url=source,
                markdown=markdown,
                text=text,
                html=html if "html" in requested else "",
                links=links,
                metadata={
                    **metadata,
                    **fetch_metadata,
                    **provenance,
                    **structure_metrics,
                    "source_url": source_url,
                    "final_url": str(fetch_metadata.get("final_url") or source_url),
                    "only_main_content": main_content,
                    "content_format": "markdown",
                    "markdown_chars": len(markdown),
                    "text_chars": len(text),
                    "link_count": len(links),
                },
            )
            if formats is None:
                return document
            return _format_document(document, requested)
        except FetchError as exc:
            message = str(exc)
            document = ScrapeDocument(
                url=source,
                markdown="",
                text="",
                metadata={"error_type": classify_error(message) or "fetch_error"},
                errors=[message],
            )
            if formats is None:
                return document
            return _format_document(document, requested)

    def map(
        self,
        source: str,
        max_urls: int | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> MapResult:
        limit = max_urls or self.config.crawl_max_pages
        include_patterns = include if include is not None else self.config.crawl_include
        exclude_patterns = exclude if exclude is not None else self.config.crawl_exclude
        discovered: set[str] = set()
        errors: list[str] = []

        for sitemap_url in _candidate_sitemaps(source, self.config):
            try:
                discovered.update(_read_sitemap(sitemap_url, self.config))
            except Exception as exc:
                message = str(exc)
                if "HTTP Error 404" not in message:
                    errors.append(f"{sitemap_url}: {exc}")

        if len(discovered) < limit:
            doc = self.scrape(source)
            if isinstance(doc, ScrapeDocument):
                discovered.update(doc.links)
                errors.extend(doc.errors)

        normalized_root = normalize_url(source, source)
        canonical_discovered = {normalize_url(url, normalized_root) for url in discovered if url}
        urls = [
            url
            for url in sorted(canonical_discovered)
            if same_domain(url, normalized_root)
            and url_allowed(url, include_patterns, exclude_patterns)
        ][:limit]
        return MapResult(
            source=source,
            urls=urls,
            errors=errors,
            metadata={"max_urls": limit, "same_domain": True},
        )

    def crawl(
        self,
        source: str,
        max_pages: int | None = None,
        max_depth: int | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        resume_state: dict[str, Any] | None = None,
        checkpoint_callback: Callable[[dict[str, Any], dict[str, Any], ScrapeDocument | None], None]
        | None = None,
        before_fetch: Callable[[str], Any] | None = None,
        max_run_pages: int | None = None,
    ) -> CrawlRun:
        page_limit = max_pages or self.config.crawl_max_pages
        depth_limit = max_depth if max_depth is not None else self.config.crawl_depth
        include_patterns = include if include is not None else self.config.crawl_include
        exclude_patterns = exclude if exclude is not None else self.config.crawl_exclude

        root = normalize_url(source, source)
        if resume_state:
            if resume_state.get("root") != root:
                raise ValueError("Crawl checkpoint does not match the requested root URL.")
            queue = deque(_decode_queue_item(item) for item in resume_state.get("queue", []))
            queued = {str(url) for url in resume_state.get("queued", [])}
            visited = {str(url) for url in resume_state.get("visited", [])}
            discovered = {str(url) for url in resume_state.get("discovered", [])}
            documents = [
                ScrapeDocument(**document) for document in resume_state.get("documents", [])
            ]
            errors = [str(error) for error in resume_state.get("errors", [])]
            failed_urls = [str(url) for url in resume_state.get("failed_urls", [])]
            terminal_failures = [
                dict(failure) for failure in resume_state.get("terminal_failures", [])
            ]
            retry_attempts = {
                str(url): int(attempt)
                for url, attempt in resume_state.get("retry_attempts", {}).items()
            }
        else:
            queue = deque([_queue_item(root, 0)])
            queued = {root}
            visited = set()
            discovered = {root}
            documents = []
            errors = []
            failed_urls = []
            terminal_failures = []
            retry_attempts = {}
        cancelled = False
        retry_scheduled = False
        fairness_yielded = False
        next_retry_at: float | None = None
        run_pages = 0
        robots = _load_robots(root, self.config) if self.config.respect_robots_txt else None
        defer_retries = checkpoint_callback is not None

        def report(
            *,
            checkpoint: bool = False,
            document: ScrapeDocument | None = None,
        ) -> None:
            progress: dict[str, Any] = {
                "visited": len(visited),
                "pending": len(queue),
                "failed": len(failed_urls),
                "discovered": len(discovered),
                "cancelled": cancelled,
            }
            if retry_attempts:
                progress["retries"] = sum(retry_attempts.values())
            if next_retry_at is not None:
                progress["next_retry_at"] = next_retry_at
            if progress_callback:
                progress_callback(progress)
            if checkpoint and checkpoint_callback:
                checkpoint_callback(
                    {
                        "version": 2,
                        "root": root,
                        "queue": list(queue),
                        "queued": sorted(queued),
                        "visited": sorted(visited),
                        "discovered": sorted(discovered),
                        "errors": errors,
                        "failed_urls": failed_urls,
                        "terminal_failures": terminal_failures,
                        "retry_attempts": retry_attempts,
                    },
                    progress,
                    document,
                )

        report(checkpoint=True)
        while queue and len(visited) < page_limit:
            if should_cancel and should_cancel():
                cancelled = True
                report(checkpoint=True)
                break

            item, earliest = _pop_ready_item(queue, time.time())
            if item is None:
                next_retry_at = earliest
                if defer_retries:
                    retry_scheduled = True
                    report(checkpoint=True)
                    break
                time.sleep(max(0.0, (earliest or time.time()) - time.time()))
                continue

            next_retry_at = None
            url = item["url"]
            depth = item["depth"]
            attempt = item["attempt"]
            if url in visited:
                continue
            if not url_allowed(url, include_patterns, exclude_patterns):
                report(checkpoint=True)
                continue
            if robots is not None and not robots.can_fetch("*", url):
                visited.add(url)
                failed_urls.append(url)
                message = "blocked by robots.txt"
                errors.append(f"{url}: {message}")
                terminal_failures.append(
                    _terminal_failure(
                        url,
                        attempt=attempt,
                        error_type="blocked",
                        message=message,
                        retryable=False,
                    )
                )
                report(checkpoint=True)
                continue

            if before_fetch:
                with before_fetch(url):
                    doc = self.scrape(url)
            else:
                doc = self.scrape(url)
            if not isinstance(doc, ScrapeDocument):
                continue
            if doc.errors:
                error_type = classify_error(doc.errors[0]) or "fetch_error"
                can_retry = (
                    attempt < self.config.crawl_url_retries
                    and error_type in self.config.crawl_retry_error_types
                )
                if can_retry:
                    delay = min(
                        self.config.crawl_retry_delay * (2**attempt),
                        self.config.crawl_retry_max_delay,
                    )
                    ready_at = time.time() + max(0.0, delay)
                    queue.append(_queue_item(url, depth, attempt + 1, ready_at))
                    retry_attempts[url] = attempt + 1
                    next_retry_at = ready_at
                    report(checkpoint=True)
                    continue
                terminal_failures.append(
                    _terminal_failure(
                        url,
                        attempt=attempt,
                        error_type=error_type,
                        message=str(doc.errors[0]),
                        retryable=error_type in self.config.crawl_retry_error_types,
                    )
                )

            documents.append(doc)
            visited.add(url)
            run_pages += 1
            errors.extend(f"{url}: {error}" for error in doc.errors)
            if doc.errors:
                failed_urls.append(url)

            if depth >= depth_limit:
                report(checkpoint=True, document=doc)
                if max_run_pages and run_pages >= max_run_pages and queue:
                    fairness_yielded = True
                    report(checkpoint=True)
                    break
                continue
            for link in doc.links:
                normalized = normalize_url(link, url)
                if self.config.crawl_same_domain and not same_domain(normalized, root):
                    continue
                if normalized in queued or normalized in visited:
                    continue
                if not url_allowed(normalized, include_patterns, exclude_patterns):
                    continue
                discovered.add(normalized)
                queued.add(normalized)
                queue.append(_queue_item(normalized, depth + 1))
            report(checkpoint=True, document=doc)
            if max_run_pages and run_pages >= max_run_pages and queue:
                fairness_yielded = True
                report(checkpoint=True)
                break

        report()
        return CrawlRun(
            source=source,
            documents=documents,
            visited_urls=sorted(visited),
            discovered_urls=sorted(discovered),
            errors=errors,
            metadata={
                "max_pages": page_limit,
                "max_depth": depth_limit,
                "visited": len(visited),
                "pending": len(queue),
                "failed": len(failed_urls),
                "discovered": len(discovered),
                "retries": sum(retry_attempts.values()),
                "terminal_failures": terminal_failures,
                "retry_scheduled": retry_scheduled,
                "fairness_yielded": fairness_yielded,
                "next_retry_at": next_retry_at,
                "cancelled": cancelled,
                "robots_txt": self.config.respect_robots_txt,
            },
        )

    def extract(self, source: str, prompt: str, schema: Any | None = None) -> Any:
        from .client import AgentCrawler

        return AgentCrawler(asdict(self.config)).extract(source, prompt, schema)


def _queue_item(
    url: str,
    depth: int,
    attempt: int = 0,
    ready_at: float = 0.0,
) -> dict[str, Any]:
    return {
        "url": url,
        "depth": depth,
        "attempt": attempt,
        "ready_at": ready_at,
    }


def _terminal_failure(
    url: str,
    *,
    attempt: int,
    error_type: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "url": url,
        "attempts": attempt + 1,
        "error_type": error_type,
        "message": message,
        "retryable": retryable,
        "failed_at": time.time(),
    }


def _decode_queue_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return _queue_item(
            str(item["url"]),
            int(item.get("depth", 0)),
            int(item.get("attempt", 0)),
            float(item.get("ready_at", 0.0)),
        )
    return _queue_item(str(item[0]), int(item[1]))


def _pop_ready_item(
    queue: deque[dict[str, Any]],
    now: float,
) -> tuple[dict[str, Any] | None, float | None]:
    earliest: float | None = None
    for _ in range(len(queue)):
        item = queue.popleft()
        ready_at = float(item.get("ready_at", 0.0))
        if ready_at <= now:
            return item, earliest
        earliest = ready_at if earliest is None else min(earliest, ready_at)
        queue.append(item)
    return None, earliest


def _format_document(document: ScrapeDocument, formats: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {"url": document.url, "metadata": document.metadata}
    for output_format in formats:
        if output_format == "markdown":
            payload["markdown"] = document.markdown
        elif output_format == "text":
            payload["text"] = document.text
        elif output_format == "html":
            payload["html"] = document.html
        elif output_format == "links":
            payload["links"] = document.links
        elif output_format == "metadata":
            payload["metadata"] = document.metadata
    if document.errors:
        payload["errors"] = document.errors
    return payload


def _markdown_to_text(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        cleaned = line.strip()
        cleaned = _HEADING_MARKER_RE.sub("", cleaned)
        cleaned = _LIST_MARKER_RE.sub("", cleaned)
        cleaned = _BLOCKQUOTE_MARKER_RE.sub("", cleaned)
        cleaned = _ORDERED_LIST_MARKER_RE.sub("", cleaned)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _candidate_sitemaps(source: str, config: CrawlConfig | None = None) -> list[str]:
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
    active_config = config or CrawlConfig()
    return [
        *_sitemaps_from_robots(root, active_config),
        urllib.parse.urljoin(root, "sitemap.xml"),
    ]


def _sitemaps_from_robots(root_url: str, config: CrawlConfig) -> list[str]:
    parsed = urllib.parse.urlsplit(root_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    request = urllib.request.Request(
        robots_url,
        headers={"user-agent": config.user_agent or "AgentCrawl/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
            content = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    sitemaps: list[str] = []
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "sitemap" and value.strip():
            sitemaps.append(normalize_url(value.strip(), robots_url))
    return sitemaps


def _read_sitemap(sitemap_url: str, config: CrawlConfig) -> list[str]:
    validate_remote_url(sitemap_url, allow_private_network=config.allow_private_network)
    request = urllib.request.Request(
        sitemap_url, headers={"user-agent": config.user_agent or "AgentCrawl/0.1"}
    )
    with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
        validate_remote_url(response.geturl(), allow_private_network=config.allow_private_network)
        xml_text = response.read().decode("utf-8", errors="replace")
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    is_sitemap_index = root.tag.endswith("sitemapindex")
    for element in root.iter():
        if element.tag.endswith("loc") and element.text:
            url = normalize_url(element.text.strip(), sitemap_url)
            if is_sitemap_index:
                urls.extend(_read_sitemap(url, config))
            else:
                urls.append(url)
    return urls


def _load_robots(root_url: str, config: CrawlConfig) -> urllib.robotparser.RobotFileParser | None:
    parsed = urllib.parse.urlsplit(root_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    request = urllib.request.Request(
        robots_url,
        headers={"user-agent": config.user_agent or "AgentCrawl/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
            content = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(content.splitlines())
    return parser
