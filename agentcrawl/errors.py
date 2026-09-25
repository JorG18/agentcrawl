from __future__ import annotations


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
