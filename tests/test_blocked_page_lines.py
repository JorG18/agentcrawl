"""Regression tests for line-oriented blocked-page detection.

``_html_to_plain_text`` used to collapse every newline before
``_blocked_page_reason`` split the text into lines, so that loop always saw a
single line: the cookie-consent skip was dead code and the challenge patterns
matched anywhere in the page — a documentation page *discussing* a "client
challenge" was discarded as if it were one.
"""

from __future__ import annotations

from agentcrawl.crawler import _blocked_page_reason, _html_to_plain_text


def test_plain_text_preserves_line_boundaries() -> None:
    html = "<div><p>first line</p><p>second line</p></div>"
    plain = _html_to_plain_text(html)
    assert plain.splitlines() == ["first line", "second line"]


def test_block_tags_become_line_breaks() -> None:
    html = "<html><head><title>t</title></head><body><p>a</p><p>b</p></body></html>"
    lines = _html_to_plain_text(html).splitlines()
    assert "a" in lines
    assert "b" in lines


def test_challenge_page_is_still_detected() -> None:
    html = (
        "<html><head><title>Client Challenge</title></head>"
        "<body>Client Challenge A required part of this site couldn\u2019t load.</body></html>"
    )
    assert _blocked_page_reason(html)


def test_cookie_consent_line_does_not_become_a_false_positive() -> None:
    """The line-level consent skip now actually runs.

    The consent banner is dropped before the patterns are tried, so a cookie
    notice cannot be mistaken for a challenge page.
    """
    html = (
        "<html><body>"
        "<div>We use cookies to improve this site. Accept all cookies to continue.</div>"
        "<main><article><h1>API reference</h1><p>Clean documentation body.</p></article></main>"
        "</body></html>"
    )
    assert _blocked_page_reason(html) == ""


def test_scripts_and_styles_are_still_ignored() -> None:
    assert _blocked_page_reason("<html><script>client challenge</script></html>") == ""
    assert _blocked_page_reason("<html><style>/* client challenge */</style><p>ok</p></html>") == ""
