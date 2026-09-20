"""Regression tests for ``classify_error`` ordering.

``"browser"`` is a broad substring and was tested before the transport classes,
so a TLS or DNS failure that happened inside a browser fetch was reported as
``browser_error`` — which is also the classification the crawl retry policy
looks at, so the reported cause and the retry decision were both wrong.
"""

from __future__ import annotations

import pytest

from agentcrawl.errors import classify_error


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Transport failures win over the browser bucket.
        (
            "browser fetch failed: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            "tls_error",
        ),
        ("browser fetch failed: Name or service not known", "network_error"),
        ("browser fetch failed: Connection reset by peer", "network_error"),
        # Browser-specific failures keep their own class.
        ("Playwright is not installed", "browser_error"),
        ("chromium launch failed", "browser_error"),
        ("browser backend unavailable", "browser_error"),
        # The rest of the table is unchanged.
        ("HTTP Error 403: Forbidden", "blocked"),
        ("HTTP Error 429: Too Many Requests", "rate_limited"),
        ("HTTP Error 404: Not Found", "not_found"),
        ("HTTP fetch failed for https://x: timed out", "timeout"),
        ("something else entirely", "fetch_error"),
    ],
)
def test_classify_error(message: str, expected: str) -> None:
    assert classify_error(message) == expected


def test_classify_error_handles_empty_input() -> None:
    assert classify_error(None) is None
    assert classify_error("") is None
