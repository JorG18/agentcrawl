from __future__ import annotations

import re
import socket
import ssl
import urllib.error

# Status codes with a dedicated class; everything else stays ``fetch_error``
# (the crawl retry policy keys off these names).
_STATUS_ERROR_TYPES = {403: "blocked", 404: "not_found", 410: "not_found", 429: "rate_limited"}

ERROR_MESSAGE_MAX_CHARS = 300
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]|\x1b\[[0-9;]*[A-Za-z]")
_SPACE_RE = re.compile(r"\s+")


def sanitize_error_message(message: str | None) -> str:
    """One line, no control characters, at most ``ERROR_MESSAGE_MAX_CHARS``."""
    if not message:
        return ""
    text = _CONTROL_RE.sub(" ", str(message))
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > ERROR_MESSAGE_MAX_CHARS:
        text = text[: ERROR_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return text


def status_code_of(exc: BaseException | None) -> int | None:
    """The HTTP status carried by ``exc`` or anything in its cause chain."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        code = getattr(exc, "status_code", None)
        if isinstance(code, int):
            return code
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code
        exc = exc.__cause__ or exc.__context__
    return None


def _type_of_cause(exc: BaseException) -> str | None:
    """Classify a transport exception by its type, not its wording."""
    if isinstance(exc, urllib.error.HTTPError):
        return _STATUS_ERROR_TYPES.get(exc.code, "fetch_error")
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return _type_of_cause(exc.reason)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ssl.SSLError | ssl.CertificateError):
        return "tls_error"
    if isinstance(exc, (socket.gaierror, ConnectionError)):
        return "network_error"
    return None


def classify_exception(exc: BaseException) -> str:
    """Error class for ``exc``: explicit type, then status, then cause chain,
    then (only as a fallback) the message wording."""
    explicit = getattr(exc, "error_type", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    code = status_code_of(exc)
    if code is not None:
        return _STATUS_ERROR_TYPES.get(code, "fetch_error")
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = _type_of_cause(current)
        if found:
            return found
        current = current.__cause__ or current.__context__
    return classify_error(str(exc)) or "fetch_error"


def error_metadata(exc: BaseException) -> dict[str, object]:
    """``error_type`` / ``error_message`` / ``status_code`` for a failed fetch."""
    metadata: dict[str, object] = {
        "error_type": classify_exception(exc),
        "error_message": sanitize_error_message(str(exc)),
    }
    code = status_code_of(exc)
    if code is not None:
        metadata["status_code"] = code
    return metadata


def classify_error(message: str | None) -> str | None:
    if not message:
        return None
    text = message.lower()
    # Local-file gate refusals (security.check_local_source). Checked first so
    # "…_ROOT" or a future wording change never lands in a network bucket.
    if "local file sources are disabled" in text:
        return "local_files_disabled"
    if "local file sources must stay inside" in text:
        return "local_file_outside_root"
    if "http error 403" in text or "forbidden" in text:
        return "blocked"
    if "http error 429" in text or "too many requests" in text:
        return "rate_limited"
    if "http error 404" in text or "not found" in text:
        return "not_found"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    # Transport-level classes come before the browser bucket. "browser" is a
    # broad substring, so a certificate failure or a DNS failure inside a
    # browser fetch was classified as ``browser_error`` and lost its real cause.
    if "ssl" in text or "certificate" in text:
        return "tls_error"
    if "name or service not known" in text or "temporary failure" in text or "connection" in text:
        return "network_error"
    if "playwright" in text or "browser" in text or "chromium" in text:
        return "browser_error"
    return "fetch_error"
