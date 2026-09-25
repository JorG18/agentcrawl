from __future__ import annotations

import contextvars
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import time
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable

from .airgap import AirgapViolation, AuditTrail
from .config import CrawlConfig
from .documents import markdown_from_fetched_content
from .errors import classify_error, error_metadata, sanitize_error_message
from .exceptions import FetchError
from .fetchers import _read_bounded, _safe_urlopen, fetch_source, read_deadline_seconds
from .html_tools import extract_html_facts, normalize_url, same_domain, url_allowed
from .models import CrawlRun, MapResult, ScrapeDocument
from .parsing import (
    budget_markdown,
    extraction_provenance,
    html_to_markdown,
    markdown_structure_metrics,
)
from .security import validate_remote_url
from .utils import estimate_tokens


_HEADING_MARKER_RE = re.compile(r"^#{1,6}\s+")
_LIST_MARKER_RE = re.compile(r"^[-*+]\s+")
_BLOCKQUOTE_MARKER_RE = re.compile(r"^>\s*")
_ORDERED_LIST_MARKER_RE = re.compile(r"^\d+[.)]\s+")
# Lines dominated by cookie/consent notice wording should be dropped from the
# plain-text view. Node-identity filters in parsing.py already remove
# explicitly badged nodes, but banners inside generic <section>/<p>/<div>
# pass through. Apply at the line level so the result never leaks
# boilerplate text into agent-visible output.
_COOKIE_CONSENT_LINE_RE = re.compile(
    r"(?i)^.*\b(cookies?\s+consent|we\s+use\s+cookies|"
    r"this\s+(site|website)\s+uses\s+cookies|"
    r"cookie\s+(policy|settings|preferences|notice)|"
    r"accept\s+(all\s+)?cookies|manage\s+cookies?|"
    r"by\s+continuing\s+you\s+accept|consent\s+to\s+cookies)\b.*$"
)
_BLOCKED_PAGE_PATTERNS = (
    re.compile(r"client challenge", re.IGNORECASE),
    re.compile(r"required part of this site (?:couldn[’']t|could not) load", re.IGNORECASE),
    re.compile(r"disable any ad blockers", re.IGNORECASE),
)

# Sitemap discovery limits. ``_read_sitemap`` recurses into sitemap-index
# entries; without a depth cap a self-referencing (or mutually-referencing)
# index blows the Python recursion stack, and without an entry budget a
# large index materializes an unbounded URL list in memory. When a limit
# hits, discovery keeps whatever was collected so far ("cap and continue")
# so oversized-but-legitimate sitemaps still contribute a capped URL set.
_SITEMAP_MAX_DEPTH = 4
_SITEMAP_MAX_URLS = 100_000
# Discovery bodies get their own ceiling instead of ``max_response_bytes``:
# the sitemap protocol allows a 50 MB uncompressed file, so the page cap would
# reject legitimate large sitemaps, while an unbounded read would let a hostile
# one exhaust memory. This is the protocol's own number.
_DISCOVERY_MAX_BYTES = 50 * 1024 * 1024


# Per-run discovery context: the audit trail and the host the run targets.
# A ContextVar (not extra parameters) so the discovery helpers keep their
# ``(url, config)`` signatures; ``map()`` / ``crawl()`` set it for their run.
_DISCOVERY: contextvars.ContextVar[tuple[Any, str] | None] = contextvars.ContextVar(
    "agentcrawl_discovery", default=None
)


def _guarded_urlopen(url: str, config: CrawlConfig, *, allow_private: bool | None = None):
    """Open ``url`` with exactly the guard rails of a page fetch.

    SSRF validation of the target and of every redirect hop, DNS pinning, and —
    this was missing until the 2026-09-23 audit — the airgap allowlist. Discovery
    used its own opener, so with ``airgap=True`` a ``Sitemap:`` line pointing at
    another host was fetched anyway. It now shares ``fetchers._safe_urlopen``.
    """
    allow_private_network = config.allow_private_network if allow_private is None else allow_private
    validate_remote_url(url, allow_private_network=allow_private_network)
    request = urllib.request.Request(
        url, headers={"user-agent": config.user_agent or "AgentCrawl/0.1"}
    )
    context = _DISCOVERY.get()
    trail, target_host = context if context else (None, None)
    return _safe_urlopen(
        request,
        timeout=config.timeout_ms / 1000,
        allow_private_network=allow_private_network,
        airgap=config.airgap,
        allowlist_domains=config.allowlist_domains,
        audit_trail=trail,
        target_host=target_host or (urllib.parse.urlsplit(url).hostname or ""),
    )


def _discovery_read(url: str, config: CrawlConfig) -> str:
    """Fetch a robots.txt/sitemap body and record the request in the run's trail."""
    context = _DISCOVERY.get()
    trail, target_host = context if context else (None, "")
    try:
        with _guarded_urlopen(url, config) as response:
            body = _read_bounded(
                response,
                _DISCOVERY_MAX_BYTES,
                url=url,
                deadline_seconds=read_deadline_seconds(config),
            )
            final_url = getattr(response, "geturl", lambda: url)() or url
            status = getattr(response, "status", None) or 200
    except AirgapViolation:
        raise  # the airgap handler already recorded the refusal as blocked
    except urllib.error.HTTPError as exc:
        if trail is not None:
            trail.record("GET", url, final_url=url, status=exc.code, target_host=target_host)
        raise
    except Exception:
        if trail is not None:
            trail.record("GET", url, final_url=url, status=None, target_host=target_host)
        raise
    if trail is not None:
        trail.record(
            "GET",
            url,
            final_url=final_url,
            status=status,
            bytes_count=len(body),
            target_host=target_host,
        )
    return body.decode("utf-8", errors="replace")


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
        *,
        query: str | None = None,
    ) -> ScrapeDocument | dict[str, Any]:
        requested = formats or ["markdown"]
        from .browser_retry import attempt_browser_retry  # local import keeps scrape() cheap

        try:
            html, fetch_metadata = fetch_source(source, self.config)
            blocked_reason = _blocked_page_reason(html)
            if blocked_reason:
                # If the user opted into the local browser fallback, try once
                # with a browser fetcher before giving up. The retry only
                # happens when the user explicitly enabled `browser_fallback`
                # AND the original fetcher was non-browser; it does not turn
                # Community into a Cloudflare bypass.
                retry = None
                if (
                    self.config.browser_fallback
                    and (self.config.fetcher or "http") == "http"
                    and self.config.browser_backend in {"playwright", "camofox"}
                ):
                    retry = attempt_browser_retry(
                        source,
                        original_metadata=fetch_metadata,
                        blocked_reason=blocked_reason,
                        original_config=self.config,
                        only_main_content=only_main_content,
                        requested=requested,
                        query=query,
                    )
                if retry is not None:
                    if formats is None:
                        return retry
                    return _format_document(retry, requested)
                document = ScrapeDocument(
                    url=source,
                    markdown="",
                    text="",
                    metadata={
                        **fetch_metadata,
                        "error_type": "client_challenge",
                        "error_message": sanitize_error_message(
                            f"Blocked or challenge page detected: {blocked_reason}"
                        ),
                        "blocked_reason": blocked_reason,
                        "source_url": source,
                        "final_url": str(fetch_metadata.get("final_url") or source),
                    },
                    errors=[f"Blocked or challenge page detected: {blocked_reason}"],
                )
                if formats is None:
                    return document
                return _format_document(document, requested)
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
            # The output budget is applied here, not in the parser, so the loss
            # is recorded on the document instead of disappearing silently.
            markdown_chars_full = len(markdown)
            markdown, chars_omitted, selection = budget_markdown(
                markdown,
                self.config.max_input_chars,
                query if self.config.relevance_chunking else None,
            )
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
                    "markdown_chars_full": markdown_chars_full,
                    "markdown_truncated": chars_omitted > 0,
                    "chars_omitted": chars_omitted,
                    **selection,
                    "text_chars": len(text),
                    "link_count": len(links),
                    "estimated_tokens": estimate_tokens(text),
                    "raw_html_bytes": len(html.encode("utf-8", errors="replace")),
                    "raw_html_tokens_estimate": estimate_tokens(html),
                },
            )
            if formats is None:
                return document
            return _format_document(document, requested)
        except FetchError as exc:
            message = str(exc)
            failure_metadata: dict[str, Any] = dict(error_metadata(exc))
            # Surface the audit trail accumulated across exhausted retries
            # so callers can still see which URLs were contacted and the
            # statuses returned. ``_fetch_http`` only attaches the trail
            # when ``config.audit`` is True, so this is a no-op for the
            # default config.
            failed_audit = getattr(exc, "audit_trail", None)
            if failed_audit is not None:
                failure_metadata.update(failed_audit.to_metadata())
            # When the browser fallback was attempted and also failed, say so:
            # the reported error stays the honest HTTP one, and the reason the
            # rescue did not work is still visible to the operator.
            fallback_error = getattr(exc, "browser_fallback_error", None)
            if fallback_error:
                failure_metadata["browser_fallback_error"] = sanitize_error_message(
                    str(fallback_error)
                )
            document = ScrapeDocument(
                url=source,
                markdown="",
                text="",
                metadata=failure_metadata,
                errors=[message],
            )
            if formats is None:
                return document
            return _format_document(document, requested)

    def extract_css(self, source: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Deterministic structured extraction: CSS schema in, JSON out, no LLM.

        The page goes through :meth:`scrape` (same SSRF guard, airgap, audit,
        blocked-page detection); the schema then runs on the raw HTML. A schema
        error raises ``ValueError`` before anything is fetched.
        """
        from .css_extract import extract_with_schema, validate_css_schema

        validate_css_schema(schema)
        document = self.scrape(source, formats=["html", "metadata"], only_main_content=False)
        metadata = dict(document.get("metadata") or {})
        errors = list(document.get("errors") or [])
        data: Any = None
        if not errors:
            final_url = str(metadata.get("final_url") or source)
            data = extract_with_schema(document.get("html") or "", schema, base_url=final_url)
            serialized = json.dumps(data, ensure_ascii=False)
            metadata.update(
                {
                    "extraction_strategy": "css_schema",
                    "item_count": len(data) if isinstance(data, list) else 1,
                    "estimated_tokens": estimate_tokens(serialized),
                    "llm_calls": 0,
                }
            )
        return {"url": source, "data": data, "metadata": metadata, "errors": errors}

    def scrape_many(
        self,
        sources: list[str],
        formats: list[str] | None = None,
        only_main_content: bool | None = None,
        *,
        max_workers: int | None = None,
        per_host_concurrency: int = 2,
        query: str | None = None,
    ) -> list[ScrapeDocument | dict[str, Any]]:
        """Scrape several sources concurrently, returning results in input order.

        Concurrency is bounded twice: ``max_workers`` (default
        ``config.parallelism``) overall, and ``per_host_concurrency`` per host
        so a batch of one site's pages stays polite. Every source runs through
        :meth:`scrape`, so validation, airgap/audit and error reporting are
        identical to a single call; a failing source yields a document with
        ``errors`` instead of raising.
        """
        from concurrent.futures import ThreadPoolExecutor

        source_list = list(sources)
        if not source_list:
            return []
        workers = max(1, min(max_workers or self.config.parallelism, len(source_list)))
        host_limits: dict[str, threading.BoundedSemaphore] = {}
        host_lock = threading.Lock()

        def host_slot(source: str) -> threading.BoundedSemaphore:
            host = urllib.parse.urlsplit(source).hostname or ""
            with host_lock:
                if host not in host_limits:
                    host_limits[host] = threading.BoundedSemaphore(max(1, per_host_concurrency))
                return host_limits[host]

        def run(source: str) -> ScrapeDocument | dict[str, Any]:
            with host_slot(source):
                try:
                    return self.scrape(
                        source,
                        formats=formats,
                        only_main_content=only_main_content,
                        query=query,
                    )
                except Exception as exc:  # one broken source must not sink the batch
                    document = ScrapeDocument(
                        url=source,
                        markdown="",
                        text="",
                        metadata=dict(error_metadata(exc)),
                        errors=[str(exc)],
                    )
                    return document if formats is None else _format_document(document, formats)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(run, source_list))

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
        airgap_skipped: list[str] = []

        with _discovery_run(self.config, source) as discovery_trail:
            for sitemap_url in _candidate_sitemaps(source, self.config):
                try:
                    discovered.update(_read_sitemap(sitemap_url, self.config))
                except AirgapViolation:
                    # Refused before it was sent: a sitemap on another host is
                    # skipped (and recorded as blocked), not a failed map.
                    airgap_skipped.append(sitemap_url)
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
        metadata: dict[str, Any] = {"max_urls": limit, "same_domain": True}
        if airgap_skipped:
            metadata["airgap_skipped"] = airgap_skipped
        if discovery_trail is not None:
            metadata["discovery_audit"] = discovery_trail.to_metadata()
        return MapResult(source=source, urls=urls, errors=errors, metadata=metadata)

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
        discovery_trail = None
        robots = None
        if self.config.respect_robots_txt:
            with _discovery_run(self.config, root) as discovery_trail:
                robots = _load_robots(root, self.config)
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
            if robots is not None and not robots.can_fetch(_robots_user_agent(self.config), url):
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
                # The engine already classified this failure (from the
                # exception type, status or challenge detection); re-reading
                # the message text turned ``client_challenge`` into a
                # retryable ``fetch_error``.
                error_type = (
                    doc.metadata.get("error_type") or classify_error(doc.errors[0]) or "fetch_error"
                )
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
                **(
                    {"discovery_audit": discovery_trail.to_metadata()}
                    if discovery_trail is not None
                    else {}
                ),
            },
        )

    def extract(self, source: str, prompt: str, schema: Any | None = None) -> Any:
        from .client import AgentCrawler

        # Hand the config object over as-is: ``asdict`` deep-copies every field,
        # including a caller-supplied ``llm`` client, and real clients hold
        # locks/connections that cannot be copied (TypeError: cannot pickle
        # '_thread.RLock').
        return AgentCrawler(self.config).extract(source, prompt, schema)


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
    # Peek-then-rotate: compute ``earliest`` once across the whole deque
    # so we can short-circuit when every item is delayed (no popleft/append
    # dance, avoiding O(n) work on retry_scheduled halts). When at least
    # one item is ready we rotate to find the frontmost ready entry,
    # keeping the legacy contract that callers see ``(item, earliest)``
    # where ``earliest`` is the soonest pending ready_at even after a
    # successful pop.
    if not queue:
        return None, None
    earliest = min(float(item.get("ready_at", 0.0)) for item in queue)
    if earliest > now:
        return None, earliest
    for _ in range(len(queue)):
        item = queue.popleft()
        ready_at = float(item.get("ready_at", 0.0))
        if ready_at <= now:
            return item, earliest
        queue.append(item)
    return None, None


def _blocked_page_reason(html: str) -> str:
    # ``_markdown_to_text`` only strips markdown markers; it does not strip
    # HTML tags. Passing raw HTML to ``_BLOCKED_PAGE_PATTERNS`` makes them
    # hit script contents (``<script>client challenge</script>``) or DOM
    # scaffolding rather than the user-facing challenge text. Strip the
    # ``<script>`` / ``<style>`` blocks first, then drop remaining tags,
    # then collapse whitespace before running the regex patterns. The
    # cookie-banner filter inside ``_markdown_to_text`` still runs because
    # markers there fire on plain-text lines, which is what we have now.
    text = _html_to_plain_text(html)
    for line in text.splitlines():
        cleaned = line.strip()
        if _COOKIE_CONSENT_LINE_RE.match(cleaned):
            continue
        for pattern in _BLOCKED_PAGE_PATTERNS:
            if pattern.search(cleaned):
                return pattern.pattern
    return ""


_HTML_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WHITESPACE_RE = re.compile(r"\s+")
# Block-level tags become line breaks. ``_blocked_page_reason`` is line-oriented
# on purpose (it skips cookie-consent lines first), but the old implementation
# collapsed every newline before splitting lines, so that loop saw a single line:
# the skip was dead code and the challenge patterns matched anywhere in the page.
_HTML_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|section|article|main|aside|header|footer|nav|ul|ol|li|table|tr|td|th|"
    r"h[1-6]|br|hr|form|figure|figcaption|blockquote|pre|dl|dt|dd)\b[^>]*>",
    re.IGNORECASE,
)


def _html_to_plain_text(html: str) -> str:
    """Best-effort HTML -> plain-text for blocked-page detection.

    Drops ``<script>`` / ``<style>`` contents entirely (they're noise for a
    Cloudflare / interstitial heuristic), turns block-level tags into line
    breaks, strips the rest, and collapses intra-line whitespace so the regex
    patterns see contiguous tokens on the line they came from. Not intended for
    general markdown extraction; ``parsing.py`` owns that path.
    """
    if not html:
        return ""
    cleaned = _HTML_SCRIPT_STYLE_RE.sub("\n", html)
    cleaned = _HTML_BLOCK_TAG_RE.sub("\n", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    lines = (_HTML_WHITESPACE_RE.sub(" ", part).strip() for part in cleaned.splitlines())
    return "\n".join(line for line in lines if line)


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
        if not cleaned:
            continue
        if _COOKIE_CONSENT_LINE_RE.match(cleaned):
            continue
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
    content = _robots_body(robots_url, config)
    if content is None:
        return []
    sitemaps: list[str] = []
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "sitemap" and value.strip():
            candidate = normalize_url(value.strip(), robots_url)
            # A robots.txt ``Sitemap:`` entry is site-chosen input; treat it
            # like any other discovered URL and re-validate before fetching.
            try:
                validate_remote_url(candidate, allow_private_network=config.allow_private_network)
            except Exception:
                continue
            sitemaps.append(candidate)
    return sitemaps


def _read_sitemap(
    sitemap_url: str,
    config: CrawlConfig,
    *,
    _depth: int = 0,
    _budget: list[int] | None = None,
) -> list[str]:
    # ``_guarded_urlopen`` validates the initial URL and every redirect hop
    # (via ``_SafeRedirectHandler``), so the final response URL is already
    # covered — no extra validation pass, no extra DNS lookups.
    xml_text = _discovery_read(sitemap_url, config)
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    is_sitemap_index = root.tag.endswith("sitemapindex")
    if _budget is None:
        _budget = [_SITEMAP_MAX_URLS]
    for element in root.iter():
        if not element.tag.endswith("loc") or not element.text:
            continue
        if _budget[0] <= 0:
            # Entry budget exhausted: stop consuming this file and return
            # the capped URL set instead of failing the whole discovery.
            break
        _budget[0] -= 1
        url = normalize_url(element.text.strip(), sitemap_url)
        if not is_sitemap_index:
            urls.append(url)
            continue
        if _depth >= _SITEMAP_MAX_DEPTH:
            # Deeper nesting than we trust: skip this entry. Bounded depth
            # is also what terminates cyclic sitemap-index references.
            continue
        urls.extend(_read_sitemap(url, config, _depth=_depth + 1, _budget=_budget))
    return urls


def _robots_body(robots_url: str, config: CrawlConfig) -> str | None:
    """robots.txt body, fetched at most once per discovery run (``None`` if absent).

    ``map()`` reads it for ``Sitemap:`` lines and ``crawl()`` for the rules; a
    run that needs both reuses the first fetch instead of asking the site twice.
    """
    context = _DISCOVERY.get()
    cache = _ROBOTS_CACHE.get()
    if context is not None and cache is not None and robots_url in cache:
        return cache[robots_url]
    try:
        # Bounded read: a hostile robots.txt could otherwise stream without
        # limit and take the process down during discovery.
        content: str | None = _discovery_read(robots_url, config)
    except Exception:
        content = None
    if context is not None and cache is not None:
        cache[robots_url] = content
    return content


_ROBOTS_CACHE: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "agentcrawl_robots_cache", default=None
)


@contextmanager
def _discovery_run(config: CrawlConfig, source: str):
    """Scope one map/crawl run: its audit trail, target host and robots cache."""
    trail = AuditTrail() if config.audit else None
    host = (urllib.parse.urlsplit(source).hostname or "").lower()
    token = _DISCOVERY.set((trail, host))
    cache_token = _ROBOTS_CACHE.set({})
    try:
        yield trail
    finally:
        _DISCOVERY.reset(token)
        _ROBOTS_CACHE.reset(cache_token)


def _robots_user_agent(config: CrawlConfig) -> str:
    """Product token claimed when matching ``robots.txt`` rules.

    ``RobotFileParser`` splits the agent string on ``/`` and keeps the first
    part, so handing it the browser-like ``user_agent`` (``Mozilla/5.0
    (compatible; AgentCrawl/0.1; ...)``) would match as ``mozilla`` and silently
    ignore every ``User-agent: AgentCrawl`` rule. Claim the product token.
    """
    match = re.search(r"(AgentCrawl(?:/[\d.]+)?)", config.user_agent or "")
    return match.group(1) if match else "AgentCrawl"


def _load_robots(root_url: str, config: CrawlConfig) -> urllib.robotparser.RobotFileParser | None:
    parsed = urllib.parse.urlsplit(root_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    content = _robots_body(robots_url, config)
    if content is None:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(content.splitlines())
    return parser


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------
# Cheap, deterministic token estimate. The rule of thumb ~4 chars per token
# is OpenAI's documented approximation for English-like text and is good
# enough for an extraction-time signal that consumers can compare against
# raw_html_tokens_estimate to see how much noise the extraction removed.
# We deliberately avoid tiktoken at scrape time: keeping Community
# dependency-light is more important than 5% accuracy on this metric.
#
# The implementation moved to ``utils.estimate_tokens`` so the browser-retry
# document builder can report the same fields; this alias keeps the historical
# import path working.


def _estimate_tokens(text: str) -> int:
    return estimate_tokens(text)
