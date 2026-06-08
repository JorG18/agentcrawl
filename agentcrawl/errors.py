from __future__ import annotations


def classify_error(message: str | None) -> str | None:
    if not message:
        return None
    text = message.lower()
    if "http error 403" in text or "forbidden" in text:
        return "blocked"
    if "http error 429" in text or "too many requests" in text:
        return "rate_limited"
    if "http error 404" in text or "not found" in text:
        return "not_found"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "playwright" in text or "browser" in text or "chromium" in text:
        return "browser_error"
    if "ssl" in text or "certificate" in text:
        return "tls_error"
    if "name or service not known" in text or "temporary failure" in text or "connection" in text:
        return "network_error"
    return "fetch_error"
