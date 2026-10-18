"""Compressed transfer, charset sniffing and the default identity."""

from __future__ import annotations

import gzip
import zlib

import pytest

from agentcrawl.config import DEFAULT_USER_AGENT
from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import _decode_http_body, _decompress_body


def test_gzip_and_deflate_bodies_are_inflated() -> None:
    html = b"<html><body><p>" + b"hello " * 1000 + b"</p></body></html>"
    assert _decompress_body(gzip.compress(html), "gzip", 1_000_000) == html
    assert _decompress_body(zlib.compress(html), "deflate", 1_000_000) == html
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw_deflate = raw.compress(html) + raw.flush()
    assert _decompress_body(raw_deflate, "deflate", 1_000_000) == html
    assert _decompress_body(html, "", 1_000_000) == html


def test_decompression_bomb_is_refused() -> None:
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(bomb) < 10_000
    with pytest.raises(FetchError, match="after decompression"):
        _decompress_body(bomb, "gzip", 1_000_000)


def test_meta_charset_is_honoured_without_a_header() -> None:
    body = '<html><head><meta charset="windows-1252"></head><body>caf\xe9 — ok</body></html>'
    data = body.encode("cp1252")
    assert "café — ok" in _decode_http_body(data, None)


def test_latin1_label_decodes_like_a_browser() -> None:
    # Browsers treat iso-8859-1 as windows-1252: 0x93/0x94 are curly quotes.
    assert _decode_http_body(b"\x93quoted\x94", "iso-8859-1") == "“quoted”"


def test_default_user_agent_points_somewhere_real() -> None:
    assert "github.com/JorG18/agentcrawl" in DEFAULT_USER_AGENT
    assert "agentcrawl.local" not in DEFAULT_USER_AGENT


def test_multi_member_gzip_keeps_every_member() -> None:
    import gzip

    from agentcrawl.fetchers import _decompress_body

    body = gzip.compress(b"<p>part one</p>") + gzip.compress(b"<p>part two</p>")
    assert _decompress_body(body, "gzip", 10**6) == b"<p>part one</p><p>part two</p>"


def test_multi_member_gzip_still_honours_the_limit() -> None:
    import gzip

    import pytest

    from agentcrawl.exceptions import FetchError
    from agentcrawl.fetchers import _decompress_body

    body = gzip.compress(b"a" * 60) + gzip.compress(b"b" * 60)
    with pytest.raises(FetchError, match="limit"):
        _decompress_body(body, "gzip", 100)


def test_meta_utf16_on_an_ascii_page_is_read_as_utf8() -> None:
    from agentcrawl.fetchers import _decode_http_body

    page = '<html><head><meta charset="utf-16"></head><body>Héllo world</body></html>'
    assert "Héllo world" in _decode_http_body(page.encode("utf-8"), None)


def test_gzip_with_trailing_padding_still_decodes() -> None:
    import gzip

    from agentcrawl.fetchers import _decompress_body

    assert (
        _decompress_body(gzip.compress(b"<p>ok</p>") + b"\x00" * 8, "gzip", 10**6) == b"<p>ok</p>"
    )
