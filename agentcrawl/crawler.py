from __future__ import annotations

import contextvars
import difflib
import hashlib
import dataclasses
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
from .airgap import _match as _airgap_match
from .challenge import ChallengeVerdict, detect_challenge
from .challenge import html_to_plain_text as _html_to_plain_text
from .config import DEFAULT_USER_AGENT, CrawlConfig
from .documents import markdown_from_fetched_content
from .errors import classify_error, error_metadata, next_step, sanitize_error_message
from .exceptions import FetchError
from .fetchers import (
    _read_bounded,
    _safe_urlopen,
    fetch_source,
    page_deadline,
    read_deadline_seconds,
)
from .html_tools import extract_html_facts, normalize_url, same_domain, url_allowed
from .models import CrawlRun, MapResult, ScrapeDocument
from .parsing import (
    budget_markdown,
    extraction_provenance,
    html_to_markdown,
    markdown_structure_metrics,
)
from .security import validate_remote_url
from .serializers import to_jsonable
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
        url, headers={"user-agent": config.user_agent or DEFAULT_USER_AGENT}
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
        section: str | None = None,
        max_tokens: int | None = None,
        max_age: float = 0,
        json_options: dict[str, Any] | None = None,
    ) -> ScrapeDocument | dict[str, Any]:
        """Read one page.

        ``formats=["outline"]`` lists the page's sections and their size;
        ``section`` (an outline id such as ``"s4"`` or heading text) returns
        only that section, and ``max_tokens`` caps the Markdown returned.
        ``max_age`` (seconds) reuses this process's fetch of the same page if
        it is younger than that, so reading outline then sections fetches once.
        ``formats=["json"]`` with ``json_options={"schema": ..., "prompt": ...}``
        adds ``json``: data extracted by the configured LLM and validated
        against the schema (``metadata.json_error`` says why it is missing).
        """
        with page_deadline(self.config):
            return self._scrape(
                source,
                formats,
                only_main_content,
                query,
                section,
                max_tokens,
                max_age,
                json_options,
            )

    def _scrape(
        self,
        source: str,
        formats: list[str] | None,
        only_main_content: bool | None,
        query: str | None,
        section: str | None,
        max_tokens: int | None,
        max_age: float,
        json_options: dict[str, Any] | None = None,
    ) -> ScrapeDocument | dict[str, Any]:
        requested = formats or ["markdown"]
        from .browser_retry import attempt_browser_retry  # local import keeps scrape() cheap

        try:
            fetch_config = self._fetch_config(source, requested)
            cached = _recent_fetch(source, fetch_config, max_age)
            if cached is not None:
                html, fetch_metadata = cached
            else:
                html, fetch_metadata = fetch_source(source, fetch_config)
            verdict = _challenge_verdict(html)
            blocked_reason = verdict.reason
            if not blocked_reason and cached is None and max_age > 0:
                _remember_fetch(source, fetch_config, html, fetch_metadata)
            if blocked_reason:
                # If the user opted into the local browser fallback, try once
                # with a browser fetcher before giving up. The retry only
                # happens when the user explicitly enabled `browser_fallback`
                # AND the original fetcher was non-browser; it does not turn
                # Community into a Cloudflare bypass.
                retry = None
                retry_diagnostics: dict[str, Any] = {}
                if (
                    self.config.browser_fallback
                    and (self.config.fetcher or "http") == "http"
                    and self.config.browser_backend in {"playwright", "camofox"}
                    # Already read in the browser (HTTP fallback): a second
                    # browser run would only wait on the same interstitial.
                    and fetch_metadata.get("fetcher") not in {"playwright", "camofox"}
                    and not fetch_metadata.get("browser_render_no_gain")
                ):
                    retry = attempt_browser_retry(
                        source,
                        blocked_reason=blocked_reason,
                        original_config=self.config,
                        only_main_content=only_main_content,
                        requested=requested,
                        query=query,
                        diagnostics=retry_diagnostics,
                    )
                if retry is not None:
                    if formats is None:
                        return retry
                    return _format_document(
                        retry, requested, query=query, chunk_tokens=self.config.chunk_tokens
                    )
                document = ScrapeDocument(
                    url=source,
                    markdown="",
                    text="",
                    metadata={
                        **fetch_metadata,
                        **retry_diagnostics,
                        "error_type": "client_challenge",
                        "next_step": next_step("client_challenge"),
                        "error_message": sanitize_error_message(
                            f"Blocked or challenge page detected: {blocked_reason}"
                        ),
                        "blocked_reason": blocked_reason,
                        "challenge_signals": list(verdict.signals),
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
                    base_url=str(fetch_metadata.get("final_url") or source),
                )
                provenance = extraction_provenance(html, only_main_content=main_content)
            else:
                provenance = {
                    "extraction_strategy": "document_passthrough",
                    "selected_content_hint": str(fetch_metadata.get("document_type") or "document"),
                }
            if section:
                from .chunks import outline_markdown, select_section

                chosen = select_section(markdown, section)
                if chosen is None:
                    message = f"No section matches {section!r}; see metadata.sections."
                    document = ScrapeDocument(
                        url=source,
                        markdown="",
                        text="",
                        metadata={
                            **fetch_metadata,
                            "error_type": "section_not_found",
                            "next_step": next_step("section_not_found"),
                            "error_message": message,
                            "sections": outline_markdown(markdown),
                        },
                        errors=[message],
                    )
                    return document if formats is None else _format_document(document, requested)
                markdown = chosen
            # The output budget is applied here, not in the parser, so the loss
            # is recorded on the document instead of disappearing silently.
            markdown_chars_full = len(markdown)
            limit = self.config.max_input_chars
            if max_tokens:
                limit = min(limit, max(1, max_tokens) * 4)  # ~4 characters a token
            markdown, chars_omitted, selection = budget_markdown(
                markdown,
                limit,
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
                    "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                    "markdown_chars_full": markdown_chars_full,
                    "markdown_truncated": chars_omitted > 0,
                    "chars_omitted": chars_omitted,
                    **selection,
                    "text_chars": len(text),
                    "link_count": len(links),
                    "estimated_tokens": estimate_tokens(text),
                    "raw_html_bytes": len(html.encode("utf-8", errors="replace")),
                    "raw_html_tokens_estimate": estimate_tokens(html),
                    **({"section": section} if section else {}),
                },
            )
            if formats is None:
                return document
            summary = self._summary(document) if "summary" in requested else None
            payload = _format_document(
                document, requested, query=query, chunk_tokens=self.config.chunk_tokens
            )
            if "summary" in requested:
                payload["summary"] = summary
            if "json" in requested:
                payload["json"] = self._json(document, json_options or {})
            return payload
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
            attempts = getattr(exc, "challenge_attempts", None)
            if attempts:
                failure_metadata["challenge_attempts"] = attempts
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

    def diff(
        self,
        source: str,
        previous: ScrapeDocument | dict[str, Any] | None = None,
        *,
        max_diff_lines: int = 400,
    ) -> dict[str, Any]:
        """Re-read ``source`` and say whether (and how) it changed since ``previous``.

        ``previous`` is an earlier scrape of the page (a ``ScrapeDocument`` or
        its JSON). Its ``etag``/``last_modified`` make the fetch conditional, so
        an unchanged page can answer ``304`` without sending the body; otherwise
        the new Markdown is hashed and compared, and a unified diff (at most
        ``max_diff_lines`` lines) shows what changed.
        """
        prev = to_jsonable(previous) if previous is not None else {}
        prev_meta = prev.get("metadata") or {}
        prev_markdown = str(prev.get("markdown") or "")
        prev_hash = prev_meta.get("markdown_sha256") or (
            hashlib.sha256(prev_markdown.encode("utf-8")).hexdigest() if prev_markdown else None
        )
        config = dataclasses.replace(
            self.config,
            if_none_match=prev_meta.get("etag"),
            if_modified_since=prev_meta.get("last_modified"),
        )
        document = AgentCrawl(config).scrape(source)
        result: dict[str, Any] = {"url": source, "previous_sha256": prev_hash}
        if document.metadata.get("error_type") == "not_modified":
            return {**result, "changed": False, "not_modified": True, "sha256": prev_hash}
        if document.errors:
            return {**result, "changed": None, "errors": document.errors}
        new_hash = document.metadata["markdown_sha256"]
        changed = new_hash != prev_hash
        result.update(
            changed=changed,
            new=prev_hash is None,
            sha256=new_hash,
            document=to_jsonable(document),
        )
        if changed and prev_markdown:
            lines = list(
                difflib.unified_diff(
                    prev_markdown.splitlines(),
                    document.markdown.splitlines(),
                    "previous",
                    "current",
                    lineterm="",
                    n=1,
                )
            )
            result["added_lines"] = sum(
                1 for line in lines if line.startswith("+") and not line.startswith("+++")
            )
            result["removed_lines"] = sum(
                1 for line in lines if line.startswith("-") and not line.startswith("---")
            )
            result["diff"] = "\n".join(lines[:max_diff_lines])
            result["diff_truncated"] = len(lines) > max_diff_lines
        return result

    def _fetch_config(self, source: str, requested: list[str]) -> CrawlConfig:
        """Config for fetching ``source``: screenshots and actions need a browser.

        Asking for them with the default HTTP fetcher switches that one fetch to
        local Playwright instead of silently returning a page without them.
        """
        wants_screenshot = "screenshot" in requested
        if not (wants_screenshot or self.config.browser_actions) or "://" not in source:
            return self.config
        changes: dict[str, Any] = {}
        if wants_screenshot:
            changes["screenshot"] = True
        if self.config.fetcher == "http":
            changes["fetcher"] = self.config.browser_backend
        return dataclasses.replace(self.config, **changes)

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
        json_options: dict[str, Any] | None = None,
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
        check_json_pages(formats, len(source_list), self.config)
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
                        json_options=json_options,
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

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        scrape: bool = True,
        formats: list[str] | None = None,
        only_main_content: bool | None = None,
    ) -> dict[str, Any]:
        """Search the web, then (by default) scrape the result pages.

        The engine comes from ``config.search_engine`` (``duckduckgo`` or
        ``serper``); ``none`` refuses with a message saying how to enable it,
        instead of returning an empty list an agent would read as "no results".
        The query doubles as the relevance query of every scrape, so long
        result pages keep their best-matching passages (BM25).

        Under ``airgap`` only allowlisted result hosts are scraped: otherwise
        the search engine, not the caller, would choose which hosts an
        airgapped run contacts. Skipped URLs are listed, never dropped silently.
        """
        from .search import search_web

        query = (query or "").strip()
        if not query:
            raise ValueError("search needs a non-empty query.")
        if self.config.search_engine not in {"duckduckgo", "serper"}:
            raise ValueError(
                "Web search is disabled (search_engine='none'). Set search_engine "
                "(AGENTCRAWL_SEARCH_ENGINE) to 'duckduckgo' or 'serper' to enable it."
            )
        config = self.config
        if limit is not None:
            if not 1 <= int(limit) <= 20:
                raise ValueError("search limit must be between 1 and 20.")
            config = dataclasses.replace(config, search_limit=int(limit))
        trail = AuditTrail() if config.audit else None
        hits = search_web(query, config, trail)[: config.search_limit]
        airgap_skipped: list[str] = []
        if config.airgap:
            allowed = []
            for hit in hits:
                host = (urllib.parse.urlsplit(hit.url).hostname or "").lower()
                if host and any(_airgap_match(host, entry) for entry in config.allowlist_domains):
                    allowed.append(hit)
                else:
                    airgap_skipped.append(hit.url)
            hits = allowed
        results: list[dict[str, Any]] = [
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet} for hit in hits
        ]
        if scrape and results:
            documents = self.scrape_many(
                [item["url"] for item in results],
                formats=formats or ["markdown", "metadata"],
                only_main_content=only_main_content,
                query=query,
            )
            for item, document in zip(results, documents):
                data = to_jsonable(document)
                item["success"] = not data.get("errors")
                item["data"] = data
        payload: dict[str, Any] = {
            "query": query,
            "engine": config.search_engine,
            "results": results,
        }
        if airgap_skipped:
            payload["airgap_skipped"] = airgap_skipped
        if trail is not None:
            payload["audit"] = trail.to_metadata()
        return payload

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
            # A site's llms.txt is its own curated list of pages worth reading.
            llms_txt_url, llms_txt_urls = _llms_txt_urls(source, self.config)
            discovered.update(llms_txt_urls)

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
        if llms_txt_url:
            metadata["llms_txt"] = llms_txt_url
        if airgap_skipped:
            metadata["airgap_skipped"] = airgap_skipped
        if discovery_trail is not None:
            metadata["discovery_audit"] = discovery_trail.to_metadata()
        return MapResult(source=source, urls=urls, errors=errors, metadata=metadata)

    def llms_txt(self, source: str, max_pages: int | None = None) -> dict[str, Any]:
        """Build an ``llms.txt`` (https://llmstxt.org) for a site from a bounded crawl.

        The title and summary come from the start page; each crawled page becomes
        one ``- [title](url): description`` line. Pages that failed are left out
        and reported under ``errors``, never listed as if they were readable.
        """
        run = self.crawl(source, max_pages=max_pages)
        root = normalize_url(source, source)
        pages = [doc for doc in run.documents if doc.ok]
        start = next((doc for doc in pages if normalize_url(doc.url, root) == root), None)
        start = start or (pages[0] if pages else None)
        host = urllib.parse.urlsplit(root).hostname or root
        title = _one_line((start.metadata.get("title") if start else "") or host)
        summary = _one_line(_page_description(start)) if start else ""
        lines = [f"# {title}", ""]
        if summary:
            lines += [f"> {summary}", ""]
        lines += ["## Pages", ""]
        for doc in pages:
            page_title = _one_line(doc.metadata.get("title") or doc.url)
            description = _one_line(_page_description(doc))
            entry = f"- [{_escape_link_text(page_title)}]({doc.url})"
            lines.append(f"{entry}: {description}" if description else entry)
        return {
            "source": source,
            "llms_txt": "\n".join(lines) + "\n",
            "pages": len(pages),
            "errors": list(run.errors),
        }

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
        query: str | None = None,
        stop_after_irrelevant: int = 3,
        previous_hashes: dict[str, str] | None = None,
    ) -> CrawlRun:
        """Crawl from ``source``. With ``query`` the crawl is adaptive.

        Adaptive means two things. The queue is ordered by how promising each
        link looks for the query (words in its URL, plus the relevance of the
        page that linked to it) instead of first-in-first-out. And the crawl
        stops early once ``stop_after_irrelevant`` pages in a row matched less
        than a third of the query terms (0 disables stopping): at that point
        it has stopped finding anything new about the question, and every
        further page costs a request without adding context.

        ``previous_hashes`` maps URL -> ``markdown_sha256`` from an earlier run;
        each page then reports ``change`` (``new``, ``changed`` or
        ``unchanged``) and the run metadata counts them, so a re-crawl says
        what moved instead of handing back the whole site again.
        """
        from .relevance import tokenize

        query_terms = set(tokenize(query)) if query else set()
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
            irrelevant_streak = int(resume_state.get("irrelevant_streak", 0))
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
            irrelevant_streak = 0
        cancelled = False
        stopped_early = False
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
                        "irrelevant_streak": irrelevant_streak,
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

            page_relevance = 0.0
            if query_terms and not doc.errors:
                page_relevance = _term_coverage(query_terms, doc.markdown)
                doc.metadata["query_relevance"] = round(page_relevance, 3)
                irrelevant_streak = 0 if page_relevance >= 1 / 3 else irrelevant_streak + 1
            if previous_hashes is not None and not doc.errors:
                before = previous_hashes.get(url)
                doc.metadata["change"] = (
                    "new"
                    if before is None
                    else "unchanged"
                    if before == doc.metadata.get("markdown_sha256")
                    else "changed"
                )
            documents.append(doc)
            visited.add(url)
            run_pages += 1
            errors.extend(f"{url}: {error}" for error in doc.errors)
            if doc.errors:
                failed_urls.append(url)
            if query_terms and stop_after_irrelevant and irrelevant_streak >= stop_after_irrelevant:
                stopped_early = True
                report(checkpoint=True, document=doc)
                break

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
                score = (
                    _term_coverage(query_terms, _url_words(normalized)) + 0.5 * page_relevance
                    if query_terms
                    else 0.0
                )
                queue.append(_queue_item(normalized, depth + 1, score=score))
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
                **({"query": query, "stopped_early": stopped_early} if query_terms else {}),
                **(
                    {
                        "changes": {
                            kind: sum(1 for d in documents if d.metadata.get("change") == kind)
                            for kind in ("new", "changed", "unchanged")
                        }
                    }
                    if previous_hashes is not None
                    else {}
                ),
                **(
                    {"discovery_audit": discovery_trail.to_metadata()}
                    if discovery_trail is not None
                    else {}
                ),
            },
        )

    def _json(self, document: ScrapeDocument, options: dict[str, Any]) -> Any:
        """The page as data, by the user's configured LLM; ``None`` with a reason.

        Same chunking, schema validation and retry as :meth:`AgentCrawler.extract`,
        on the Markdown already read. The model, its key and endpoint come from
        the configuration only, never from the request.
        """
        from .graph import CrawlGraph
        from .llm import get_llm

        schema = options.get("schema")
        prompt = str(options.get("prompt") or "").strip()
        if schema is None and not prompt:
            document.metadata["json_error"] = "json needs a schema, a prompt or both"
            return None
        try:
            get_llm(self.config)
        except ValueError:
            document.metadata["json_error"] = (
                "no LLM configured (next_step: set AGENTCRAWL_LLM_MODEL to a model you "
                "choose, a small one is enough, or extract the data from the markdown yourself)"
            )
            return None
        graph = CrawlGraph(self.config)
        state: Any = {
            "prompt": prompt or "Extract the data described by the schema from this page.",
            "schema": schema,
            "markdown": document.markdown,
        }
        state = graph.reattempt_if_needed(graph.extract(graph.chunk(state)))
        document.metadata["json_llm_calls"] = state.get("llm_calls", 0)
        if state.get("validation_error"):
            document.metadata["json_error"] = sanitize_error_message(state["validation_error"])
            return None
        return state.get("answer")

    def _summary(self, document: ScrapeDocument) -> str | None:
        """A short summary by the configured LLM; a failure is noted, not fatal."""
        from .llm import get_llm, invoke_llm

        try:
            reply = invoke_llm(
                get_llm(self.config),
                "Summarise this page for someone deciding whether to read it in full, "
                "keeping names, numbers and dates exactly as written.\n\n"
                + document.markdown[:60_000],
            )
        except Exception as exc:  # the page itself was read fine
            document.metadata["summary_error"] = sanitize_error_message(str(exc))
            return None
        return reply.strip()

    def generate_css_schema(
        self, source: str, description: str, *, max_attempts: int = 3
    ) -> dict[str, Any]:
        """Have the configured LLM write a CSS schema for ``description`` once.

        The schema is checked by running it on the page; an error or an empty
        result goes back to the model (``max_attempts`` in all). The result
        holds the ``schema``, reusable with :meth:`extract_css` on similar
        pages at no token cost, and the ``data`` it extracted here.
        """
        from .css_extract import extract_with_schema, validate_css_schema
        from .llm import get_llm, invoke_llm

        llm = get_llm(self.config)
        document = self.scrape(source, formats=["html", "metadata"], only_main_content=False)
        if document.get("errors"):
            return {"schema": None, "data": None, "errors": document["errors"]}
        html = document.get("html") or ""
        base_url = str((document.get("metadata") or {}).get("final_url") or source)
        prompt = _SCHEMA_PROMPT.format(description=description, html=_html_for_prompt(html))
        feedback = ""
        for attempt in range(1, max(1, max_attempts) + 1):
            reply = invoke_llm(llm, prompt + feedback)
            try:
                schema = json.loads(_strip_code_fence(reply))
                validate_css_schema(schema)
                data = extract_with_schema(html, schema, base_url=base_url)
            except (ValueError, TypeError) as exc:
                feedback = f"\n\nYour previous answer failed: {exc}. Reply with a corrected schema."
                continue
            if data in (None, [], {}) or (
                isinstance(data, dict) and not any(v not in (None, "", []) for v in data.values())
            ):
                feedback = (
                    f"\n\nYour previous schema {json.dumps(schema)} matched nothing on the page. "
                    "Use selectors that exist in the HTML above."
                )
                continue
            return {"schema": schema, "data": data, "attempts": attempt, "errors": []}
        return {
            "schema": None,
            "data": None,
            "attempts": max_attempts,
            "errors": [f"no working schema after {max_attempts} attempts{feedback[:300]}"],
        }

    def extract(self, source: str, prompt: str, schema: Any | None = None) -> Any:
        from .client import AgentCrawler

        # Hand the config object over as-is: ``asdict`` deep-copies every field,
        # including a caller-supplied ``llm`` client, and real clients hold
        # locks/connections that cannot be copied (TypeError: cannot pickle
        # '_thread.RLock').
        return AgentCrawler(self.config).extract(source, prompt, schema)


_SCHEMA_PROMPT = """Write a CSS extraction schema, as JSON only, that extracts: {description}

Schema format:
{{"baseSelector": "<CSS selector of each repeated item; omit for one object>",
  "fields": [{{"name": "...", "selector": "<CSS, relative to the item>",
    "type": "text|attribute|html|regex|nested|list", "attribute": "<for attribute>",
    "pattern": "<for regex>", "multiple": false,
    "transform": "strip|lower|upper|number|url", "fields": [<for nested/list>]}}]}}
Use simple selectors: tag, #id, .class, [attr], [attr=value], :nth-child(n), and
the descendant, >, + and ~ combinators. Prefer stable class names and attributes
over positions.

Page HTML (scripts and styles removed, may be cut):
{html}
"""
_PROMPT_HTML_CHARS = 40_000
_NOISE_RE = re.compile(
    r"<(script|style|svg|noscript|template)\b.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL
)


def _html_for_prompt(html: str) -> str:
    """The page's markup without scripts, styles, SVG and comments, cut to size."""
    body = html
    start = body.lower().find("<body")
    if start >= 0:
        body = body[start:]
    body = _NOISE_RE.sub("", body)
    body = re.sub(r"\s+", " ", body)
    return body[:_PROMPT_HTML_CHARS]


def _strip_code_fence(reply: str) -> str:
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _queue_item(
    url: str,
    depth: int,
    attempt: int = 0,
    ready_at: float = 0.0,
    score: float = 0.0,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "url": url,
        "depth": depth,
        "attempt": attempt,
        "ready_at": ready_at,
    }
    if score:
        # Only adaptive crawls set it; the queue then pops the best ready item.
        item["score"] = round(score, 4)
    return item


def _term_coverage(terms: set[str], text: str) -> float:
    """Share of the query ``terms`` that occur in ``text`` (0.0-1.0)."""
    from .relevance import tokenize

    if not terms:
        return 0.0
    return len(terms & set(tokenize(text))) / len(terms)


def _url_words(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return re.sub(r"[/_.\-+=&?]+", " ", urllib.parse.unquote(parsed.path + " " + parsed.query))


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
            float(item.get("score", 0.0)),
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
    if any("score" in item for item in queue):
        # Adaptive crawl: the most promising ready item first (FIFO on ties).
        best_index, best_score = None, -1.0
        for index, item in enumerate(queue):
            score = float(item.get("score", 0.0))
            if float(item.get("ready_at", 0.0)) <= now and score > best_score:
                best_index, best_score = index, score
        if best_index is not None:
            item = queue[best_index]
            del queue[best_index]
            return item, earliest
    for _ in range(len(queue)):
        item = queue.popleft()
        ready_at = float(item.get("ready_at", 0.0))
        if ready_at <= now:
            return item, earliest
        queue.append(item)
    return None, None


def _blocked_page_reason(html: str) -> str:
    """First challenge signal for ``html``, or ``""`` for a real page.

    See :mod:`agentcrawl.challenge`: a known interstitial title, or a short
    page with challenge wording or a vendor challenge script. Challenge wording
    inside a real article never counts. Cookie-consent lines are dropped from
    the readable text first so a banner cannot look like a challenge.
    """
    return _challenge_verdict(html).reason


def _challenge_verdict(html: str) -> ChallengeVerdict:
    text = _html_to_plain_text(html)
    readable = "\n".join(
        line for line in text.splitlines() if not _COOKIE_CONSENT_LINE_RE.match(line.strip())
    )
    return detect_challenge(html, readable)


# Pages fetched recently in this process, for ``scrape(max_age=...)``:
# an agent reading a long page's outline and then its sections should not
# download it again for every call.
_FETCH_CACHE: dict[tuple[str, str], tuple[float, str, dict[str, Any]]] = {}
_FETCH_CACHE_LOCK = threading.Lock()
_FETCH_CACHE_SIZE = 32  # ponytail: whole pages in memory; a byte cap if they get large


def _recent_fetch(
    source: str, config: CrawlConfig, max_age: float
) -> tuple[str, dict[str, Any]] | None:
    if max_age <= 0:
        return None
    with _FETCH_CACHE_LOCK:
        entry = _FETCH_CACHE.get((source, repr(config)))
    if entry is None or time.monotonic() - entry[0] > max_age:
        return None
    fetched_at, html, metadata = entry
    age = round(time.monotonic() - fetched_at, 1)
    return html, {**metadata, "cache_hit": True, "cache_age_s": age}


def _remember_fetch(source: str, config: CrawlConfig, html: str, metadata: dict[str, Any]) -> None:
    with _FETCH_CACHE_LOCK:
        _FETCH_CACHE[(source, repr(config))] = (time.monotonic(), html, dict(metadata))
        while len(_FETCH_CACHE) > _FETCH_CACHE_SIZE:
            _FETCH_CACHE.pop(next(iter(_FETCH_CACHE)))


def check_json_pages(formats: list[str] | None, pages: int, config: CrawlConfig) -> None:
    """Refuse a ``json`` batch over ``llm_max_pages``: each page is a paid LLM call."""
    if formats and "json" in formats and pages > config.llm_max_pages:
        raise ValueError(
            f"formats=['json'] sends each page to the LLM; {pages} pages exceed "
            f"llm_max_pages={config.llm_max_pages} (AGENTCRAWL_LLM_MAX_PAGES). "
            "Split the batch or raise the limit."
        )


def _format_document(
    document: ScrapeDocument,
    formats: list[str],
    *,
    query: str | None = None,
    chunk_tokens: int = 400,
) -> dict[str, Any]:
    metadata = document.metadata
    if "screenshot_png_base64" in metadata:
        # The image is its own output field, not metadata to scroll past.
        metadata = {k: v for k, v in metadata.items() if k != "screenshot_png_base64"}
    payload: dict[str, Any] = {"url": document.url, "metadata": metadata}
    for output_format in formats:
        if output_format == "screenshot":
            payload["screenshot"] = document.metadata.get("screenshot_png_base64")
        elif output_format == "chunks":
            from .chunks import chunk_markdown

            cite = str(document.metadata.get("final_url") or document.url)
            payload["chunks"] = chunk_markdown(
                document.markdown, cite, max_tokens=chunk_tokens, query=query
            )
        elif output_format == "outline":
            from .chunks import outline_markdown

            payload["outline"] = outline_markdown(document.markdown)
        elif output_format == "markdown":
            payload["markdown"] = document.markdown
        elif output_format == "text":
            payload["text"] = document.text
        elif output_format == "html":
            payload["html"] = document.html
        elif output_format == "links":
            payload["links"] = document.links
        elif output_format == "metadata":
            payload["metadata"] = metadata
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


# ``[title](url)`` links, optionally ``<url>`` or with a quoted title.
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _llms_txt_urls(source: str, config: CrawlConfig) -> tuple[str | None, list[str]]:
    """URLs listed in the site's ``/llms.txt`` (and its URL), or ``(None, [])``.

    Fetched like robots.txt (guarded, bounded, audited). A missing file, an
    error, or an HTML page served at that path (soft 404s) contributes nothing.
    """
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, []
    llms_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/llms.txt", "", ""))
    try:
        body = _discovery_read(llms_url, config)
    except Exception:
        return None, []
    if body.lstrip().startswith("<"):
        return None, []
    urls = [normalize_url(match.group(1), llms_url) for match in _MARKDOWN_LINK_RE.finditer(body)]
    urls = [url for url in urls if url.startswith(("http://", "https://"))]
    return (llms_url, urls) if urls else (None, [])


def _page_description(doc: ScrapeDocument | None) -> str:
    if doc is None:
        return ""
    metadata = doc.metadata
    return str(metadata.get("description") or metadata.get("og:description") or "")


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def _escape_link_text(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


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
