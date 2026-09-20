"""Regression tests for the 2026-09 unvalidated-client-input findings.

Before this fix a caller could send a config value of the wrong type and get an
HTTP 500, or a 200 whose ``error_type`` blamed the *network*, or a string
``"false"`` silently treated as true (the opposite of what it says). Patterns in
``include`` / ``exclude`` could crash a request with an uncompilable regex, and
``max_urls`` accepted a negative value.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcrawl.config import CrawlConfig
from agentcrawl.html_tools import url_allowed, validate_url_patterns
from agentcrawl.server import _ALLOWED_CONFIG_OVERRIDES, app, server

client = TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("override", "expected_fragment"),
    [
        ({"timeout_ms": "abc"}, "timeout_ms must be an integer"),
        ({"max_input_chars": "x"}, "max_input_chars must be an integer"),
        ({"http_retries": "2"}, "http_retries must be an integer"),
        ({"max_response_bytes": "x"}, "max_response_bytes must be an integer"),
        ({"headless": "false"}, "headless must be true or false"),
        ({"respect_robots_txt": "false"}, "respect_robots_txt must be true or false"),
        ({"browser_fallback": "false"}, "browser_fallback must be true or false"),
        ({"crawl_max_pages": 0}, "crawl_max_pages must be between"),
        ({"user_agent": 123}, "user_agent must be a string or null"),
        ({"browser_fallback_statuses": "403"}, "Write [403] instead"),
        ({"allowlist_domains": "example.com"}, "must be a list of strings"),
    ],
)
def test_wrong_config_values_raise_a_named_value_error(
    override: dict[str, object], expected_fragment: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        CrawlConfig.from_dict(override)
    assert expected_fragment in str(excinfo.value)


def test_browser_fallback_statuses_accepts_scalars_and_lists() -> None:
    assert CrawlConfig.from_dict({"browser_fallback_statuses": 403}).browser_fallback_statuses == (
        403,
    )
    assert CrawlConfig.from_dict(
        {"browser_fallback_statuses": [403, 429]}
    ).browser_fallback_statuses == (403, 429)


@pytest.mark.parametrize(
    "override",
    [
        {"timeout_ms": "abc"},
        {"max_input_chars": "x"},
        {"http_retries": "2"},
        {"headless": "false"},
    ],
)
def test_api_answers_400_for_a_wrong_config_type(override: dict[str, object]) -> None:
    response = client.post("/v1/scrape", json={"url": "https://example.com/", "config": override})
    assert response.status_code == 400, response.text
    # The detail must name the offending key so the caller can fix it.
    assert list(override)[0] in response.text


def test_api_rejects_an_uncompilable_include_pattern() -> None:
    response = client.post("/v1/map", json={"url": "https://example.com/", "include": ["docs("]})
    assert response.status_code == 422, response.text
    assert "Invalid regular expression" in response.text


@pytest.mark.parametrize("max_urls", [-1, 0, 10**9])
def test_api_bounds_max_urls(max_urls: int) -> None:
    response = client.post("/v1/map", json={"url": "https://example.com/", "max_urls": max_urls})
    assert response.status_code == 422, response.text


def test_url_allowed_never_raises_on_an_uncompilable_pattern() -> None:
    assert url_allowed("https://example.com/docs", ["docs("], []) is False
    assert url_allowed("https://example.com/docs", [], ["docs("]) is True
    assert url_allowed("https://example.com/docs", ["docs"], []) is True


def test_validate_url_patterns_names_the_offending_pattern() -> None:
    assert validate_url_patterns(None) is None
    assert validate_url_patterns(["docs"]) == ["docs"]
    with pytest.raises(ValueError) as excinfo:
        validate_url_patterns(["docs("])
    assert "docs(" in str(excinfo.value)


def test_server_default_config_satisfies_the_new_contract() -> None:
    """The server's own defaults must pass the validation it now enforces."""
    config = CrawlConfig.from_dict(server.default_config)
    assert config.timeout_ms == server.default_config["timeout_ms"]


def test_every_allowed_override_is_a_real_config_field() -> None:
    """A typo in the allowlist must not silently accept an unknown key."""
    known = set(CrawlConfig.__dataclass_fields__)
    assert _ALLOWED_CONFIG_OVERRIDES <= known
