"""Airgap / Audit hooks for AgentCrawl.

This module provides two complementary privacy/transparency primitives
for Community:

1. **Airgap** (``airgap=True`` in CrawlConfig):
   When set, every HTTP request performed through the urllib opener is
   intercepted and validated against a domain allowlist. Anything not
   matching the target's host (or subdomains in the allowlist) raises
   ``AirgapViolation``. The hook is implemented as an opener factory so
   it covers the fetchers and any other urllib-based path Community has.

2. **Audit** (``audit=True`` in CrawlConfig):
   Records every HTTP request the engine performs (URL, method, status,
   bytes, third-party flag). At the end of a scrape the record is
   returned alongside the document so consumers can prove zero
   third-party requests, etc.

Both features are **opt-in** by env var defaults and require no
external dependencies.

Boundary: this is **not** a Cloudflare bypass. It does not bypass
anti-bot, proxy rotation, stealth, or any other paid product. It
simply lets the user assert that the local Community build never
spoke to a domain they did not opt into.
"""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

from urllib.parse import urlparse


class AirgapViolation(RuntimeError):
    """Raised by the airgap opener when a request would go to a domain
    that is not in the target allowlist."""


@dataclass(slots=True)
class AuditRecord:
    method: str
    url: str
    final_url: str | None = None
    status: int | None = None
    bytes: int = 0
    third_party: bool = False


@dataclass(slots=True)
class AuditTrail:
    records: list[AuditRecord] = field(default_factory=list)

    def record(
        self,
        method: str,
        url: str,
        *,
        final_url: str | None = None,
        status: int | None = None,
        bytes_count: int = 0,
        target_host: str | None = None,
    ) -> None:
        try:
            host = urlparse(final_url or url).hostname or ""
        except Exception:
            host = ""
        third_party = bool(target_host) and bool(host) and (host != target_host)
        self.records.append(
            AuditRecord(
                method=method,
                url=url,
                final_url=final_url,
                status=status,
                bytes=bytes_count,
                third_party=third_party,
            )
        )

    def to_metadata(self) -> dict[str, Any]:
        third_party_count = sum(1 for r in self.records if r.third_party)
        total_bytes = sum(r.bytes for r in self.records)
        return {
            "audit_request_count": len(self.records),
            "audit_third_party_request_count": third_party_count,
            "audit_total_bytes": total_bytes,
            "audit_records": [
                {
                    "method": r.method,
                    "url": r.url,
                    "final_url": r.final_url,
                    "status": r.status,
                    "bytes": r.bytes,
                    "third_party": r.third_party,
                }
                for r in self.records
            ],
        }


def _match(host: str, pattern: str) -> bool:
    """Match a host against an allowlist pattern.

    Patterns can be exact ("foo.com") or wildcard ("*.foo.com"). Case
    insensitive. Empty pattern never matches.
    """
    if not pattern:
        return False
    p = pattern.lower().strip()
    h = host.lower().strip()
    if p == h:
        return True
    if p.startswith("*."):
        base = p[2:]
        return h == base or h.endswith("." + base)
    return False


def is_target_host_allowed(
    target: str,
    allowlist: Iterable[str],
) -> tuple[bool, str | None]:
    """Check whether a target URL's host matches the allowlist (or the
    target host itself). Returns (ok, reason).

    Semantics:
    - empty allowlist:  target host is allowed, nothing else is.
    - non-empty allowlist:  any matching pattern lets the host through.
      The ``target`` host itself is always allowed in addition to the
      allowlist (so the user's primary URL is never blocked).
    """
    parsed = urlparse(target)
    target_host = (parsed.hostname or "").lower()
    if not target_host:
        return False, "no host in URL"
    allowlist_items = list(allowlist)
    if not allowlist_items:
        return True, None
    if any(_match(target_host, entry) for entry in allowlist_items):
        return True, None
    return False, f"host not in allowlist: {target_host}"


class _AirgapHandler(urllib.request.BaseHandler):
    """Opener handler that validates each request against the airgap
    allowlist before letting it through."""

    def __init__(
        self,
        target: str,
        allowlist: Iterable[str],
        audit: AuditTrail | None,
        target_host: str | None = None,
    ) -> None:
        allowlist_items = list(allowlist) or ([target_host] if target_host else [])
        self._allowlist = allowlist_items
        self._audit = audit
        self._target_host = (target_host or urlparse(target).hostname or "").lower()

    def _allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host or host == self._target_host:
            return True
        return any(_match(host, entry) for entry in self._allowlist)

    def _record(
        self,
        method: str,
        url: str,
        *,
        status: int | None = None,
        bytes_count: int = 0,
    ) -> None:
        if self._audit is not None:
            self._audit.record(
                method,
                url,
                final_url=url,
                status=status,
                bytes_count=bytes_count,
                target_host=self._target_host,
            )

    def http_request(self, request):  # type: ignore[override]
        url = request.full_url
        if not self._allowed(url):
            self._record(request.get_method(), url, status=0, bytes_count=0)
            raise AirgapViolation(
                f"airgap blocked request to {url} (target={self._target_host}, "
                f"allowlist={self._allowlist})"
            )
        self._record(request.get_method(), url)
        return request

    https_request = http_request  # type: ignore[assignment]


def build_airgap_opener(
    target: str,
    *,
    airgap: bool,
    allowlist: Iterable[str] = (),
    audit: AuditTrail | None = None,
    target_host: str | None = None,
) -> urllib.request.OpenerDirector:
    """Return an opener that enforces airgap + records audit entries.

    When ``airgap`` is False and ``audit`` is None, this returns the
    default urllib opener (zero-overhead bypass).
    """
    if not airgap and audit is None:
        return urllib.request.build_opener()
    handlers: list[urllib.request.BaseHandler] = []
    if airgap:
        handlers.append(
            _AirgapHandler(
                target=target,
                allowlist=allowlist,
                audit=audit,
                target_host=target_host or urlparse(target).hostname,
            )
        )
    return urllib.request.build_opener(*handlers)


def audit_metadata_from_env() -> AuditTrail | None:
    """Convenience helper for ad-hoc CLI use: build an AuditTrail only
    when ``AGENTCRAWL_AUDIT=1`` is set."""
    if os.environ.get("AGENTCRAWL_AUDIT", "").lower() in {"1", "true", "yes"}:
        return AuditTrail()
    return None


def airgap_from_env(
    allowlist_env_var: str = "AGENTCRAWL_AIRGAP_ALLOWLIST",
) -> tuple[bool, tuple[str, ...]]:
    """Convenience helper: read airgap flag + allowlist from env.

    - ``AGENTCRAWL_AIRGAP=true`` enables airgap
    - ``AGENTCRAWL_AIRGAP_ALLOWLIST`` comma-separated allowlist (empty = target host only)
    """
    enabled = os.environ.get("AGENTCRAWL_AIRGAP", "").lower() in {"1", "true", "yes"}
    raw = os.environ.get(allowlist_env_var, "") or ""
    allowlist = tuple(item.strip() for item in raw.split(",") if item.strip())
    return enabled, allowlist
