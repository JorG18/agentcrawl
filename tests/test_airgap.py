from __future__ import annotations

import urllib.request

import pytest

from agentcrawl.airgap import (
    AirgapViolation,
    AuditTrail,
    _AirgapHandler,
    airgap_from_env,
)
from agentcrawl.config import CrawlConfig


def test_audit_trail_records_basic_request() -> None:
    trail = AuditTrail()
    trail.record(
        "GET", "https://example.com/", status=200, bytes_count=42, target_host="example.com"
    )
    trail.record(
        "GET", "https://cdn.example.org/", status=200, bytes_count=10, target_host="example.com"
    )
    md = trail.to_metadata()
    assert md["audit_request_count"] == 2
    assert md["audit_third_party_request_count"] == 1
    assert md["audit_total_bytes"] == 52
    assert md["audit_records"][0]["url"] == "https://example.com/"
    assert md["audit_records"][1]["third_party"] is True


def test_airgap_blocks_off_target_request() -> None:
    opener = urllib.request.build_opener(
        _AirgapHandler(
            target="https://example.com/",
            allowlist=(),
            audit=None,
            target_host="example.com",
            enforce=True,
        )
    )
    with pytest.raises(AirgapViolation) as exc:
        opener.open("https://cdn.example.org/")
    assert "airgap blocked" in str(exc.value)


def test_airgap_from_env_parses_bool_and_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_AIRGAP", "true")
    monkeypatch.setenv("AGENTCRAWL_AIRGAP_ALLOWLIST", "foo.com, *.bar.com")
    enabled, allowlist = airgap_from_env()
    assert enabled
    assert allowlist == ("foo.com", "*.bar.com")


def test_crawlconfig_exposes_airgap_and_audit_and_allowlist() -> None:
    cfg = CrawlConfig.from_dict(
        {"airgap": True, "allowlist_domains": ["api.example.com"], "audit": True}
    )
    assert cfg.airgap is True
    assert cfg.allowlist_domains == ("api.example.com",)
    assert cfg.audit is True
