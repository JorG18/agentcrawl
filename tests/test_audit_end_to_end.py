from __future__ import annotations

from agentcrawl.config import CrawlConfig


def test_fetch_http_record_audit_when_audit_true(monkeypatch) -> None:
    """End-to-end: when audit=True on the config, the fetched document
    should expose audit_request_count and audit_records on the returned
    fetch_metadata (which is then merged into the ScrapeDocument.metadata
    by the crawler layer)."""

    from urllib.response import addinfourl
    import io

    html = "<html><body>hello world</body></html>"
    cfg = CrawlConfig(airgap=False, audit=True, allowlist_domains=())
    captured: dict = {}

    def fake_safe_urlopen(request, **kwargs):
        captured["kwargs"] = kwargs
        body = html.encode("utf-8")
        return addinfourl(io.BytesIO(body), {"Content-Type": "text/html"}, request.full_url, 200)

    import agentcrawl.fetchers as f

    monkeypatch.setattr(f, "_safe_urlopen", fake_safe_urlopen)

    body_text, meta = f._fetch_http("https://example.com/article", cfg)

    assert meta["fetcher"] == "http"
    assert meta["final_url"].startswith("https://example.com/")
    assert meta.get("audit_request_count") == 1, f"expected audit_request_count=1, got {meta}"
    assert isinstance(meta.get("audit_records"), list)
    rec = meta["audit_records"][0]
    assert rec["method"] == "GET"
    assert rec["url"].startswith("https://example.com/")
    assert rec["bytes"] == len(html)
    assert rec["status"] in (200, None)
    # The trail passed to _safe_urlopen should not be None when audit=True
    assert captured["kwargs"].get("audit_trail") is not None


def test_fetch_http_no_audit_when_flag_off(monkeypatch) -> None:
    """When audit=False (default), no audit fields should appear in
    fetch_metadata."""

    from urllib.response import addinfourl
    import io
    import agentcrawl.fetchers as f

    html = "<html><body>hello</body></html>"
    cfg = CrawlConfig(airgap=False, audit=False)

    def fake_safe_urlopen(request, **kwargs):
        body = html.encode("utf-8")
        return addinfourl(io.BytesIO(body), {"Content-Type": "text/html"}, request.full_url, 200)

    monkeypatch.setattr(f, "_safe_urlopen", fake_safe_urlopen)

    body_text, meta = f._fetch_http("https://example.com/", cfg)
    assert "audit_request_count" not in meta
    assert "audit_records" not in meta


def test_audit_records_mark_third_party_field_correctly() -> None:
    """Direct unit test: when a request records url with a different
    hostname than target_host, the AuditTrail records it as
    third_party=True; when same host, third_party=False."""
    from agentcrawl.airgap import AuditTrail

    trail = AuditTrail()
    trail.record("GET", "https://example.com/", target_host="example.com")
    trail.record("GET", "https://cdn.example.org/", target_host="example.com")
    md = trail.to_metadata()
    assert md["audit_request_count"] == 2
    assert md["audit_third_party_request_count"] == 1
    assert md["audit_records"][0]["third_party"] is False
    assert md["audit_records"][1]["third_party"] is True
