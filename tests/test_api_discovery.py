from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentcrawl.api_discovery import ApiCollector, json_shape, redact_url, same_site

PAGE = "https://www.shop.com/"


def _response(
    url,
    body=b"{}",
    *,
    method="GET",
    resource_type="xhr",
    content_type="application/json",
    status=200,
    post_data=None,
):
    return SimpleNamespace(
        url=url,
        status=status,
        headers={"content-type": content_type},
        request=SimpleNamespace(method=method, resource_type=resource_type, post_data=post_data),
        body=lambda: body,
    )


def _collect(*responses, budget_ms=10_000):
    collector = ApiCollector(PAGE)
    for response in responses:
        collector.on_response(response)
    return collector.finish(budget_ms)


def test_shape_has_types_not_values():
    result = json_shape({"id": 7, "name": "Ana", "tags": ["a"], "x": None, "ok": True})
    assert result == {
        "id": "number",
        "name": "string",
        "tags": ["string"],
        "x": "null",
        "ok": "boolean",
    }
    assert "Ana" not in json.dumps(result)


def test_shape_merges_list_items_and_stops_at_limits():
    assert json_shape([{"a": 1}, {"b": "x"}]) == [{"a": "number", "b": "string"}]
    assert json_shape([]) == []
    nested = 1
    for _ in range(8):
        nested = {"k": nested}
    shape = json_shape(nested)
    for _ in range(6):
        shape = shape["k"]
    assert shape == "…"


def test_redact_url_hides_secret_params_only():
    assert (
        redact_url("https://api.x.com/p?page=2&api_key=abc&sessionId=9")
        == "https://api.x.com/p?page=2&api_key=REDACTED&sessionId=REDACTED"
    )
    assert (
        redact_url("https://x.com/s?keyword=shoes&access_token=t")
        == "https://x.com/s?keyword=shoes&access_token=REDACTED"
    )


@pytest.mark.parametrize(
    ("url", "page", "expected"),
    [
        ("https://api.shop.com/v1", "https://www.shop.com/", True),
        ("https://shop.com/x", "https://www.shop.com/", True),
        ("https://www.google-analytics.com/g", "https://www.shop.com/", False),
        ("https://api.stripe.com/v1", "https://www.shop.com/", False),
        ("https://evilshop.com/x", "https://shop.com/", False),
    ],
)
def test_same_site_rule(url, page, expected):
    assert same_site(url, page) is expected


def test_collector_keeps_same_site_json_xhr_only():
    endpoints, truncated = _collect(
        _response("https://www.shop.com/api/items", b'{"items": []}'),
        _response("https://www.shop.com/page", resource_type="document"),
        _response("https://www.shop.com/frag", content_type="text/html"),
        _response("https://api.stripe.com/v1/x", resource_type="fetch"),
    )
    assert truncated is False
    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert set(endpoint) == {
        "method",
        "url",
        "status",
        "content_type",
        "bytes",
        "count",
        "shape",
        "request_shape",
    }
    assert endpoint["shape"] == {"items": []}
    assert endpoint["request_shape"] is None


def test_collector_dedupes_graphql_posts():
    def post(variables):
        return _response(
            "https://www.shop.com/graphql",
            b'{"data": {}}',
            method="POST",
            resource_type="fetch",
            post_data=json.dumps({"query": "q", "variables": variables}),
        )

    endpoints, _ = _collect(post({"id": 1}), post({"id": 2}))
    assert len(endpoints) == 1
    assert endpoints[0]["count"] == 2
    assert endpoints[0]["request_shape"] == {
        "query": "string",
        "variables": {"id": "number"},
    }


def test_bad_json_body_keeps_the_endpoint():
    endpoints, _ = _collect(
        _response("https://www.shop.com/a", b")]}'\n{}"),
        _response("https://www.shop.com/b", b"", status=204),
    )
    assert len(endpoints) == 2
    assert all(endpoint["shape"] is None for endpoint in endpoints)


def test_unreadable_body_keeps_the_endpoint():
    response = _response("https://www.shop.com/a")

    def boom():
        raise RuntimeError("context closed")

    response.body = boom
    endpoints, _ = _collect(response)
    assert endpoints[0]["shape"] is None


def test_huge_body_is_not_parsed():
    endpoints, _ = _collect(_response("https://www.shop.com/a", b" " * 2_000_001))
    assert endpoints[0]["shape"] is None
    assert endpoints[0]["too_large"] is True


def test_limit_of_30_endpoints():
    responses = [_response(f"https://www.shop.com/api/{i}") for i in range(35)]
    endpoints, truncated = _collect(*responses)
    assert len(endpoints) == 30
    assert truncated is True


def test_no_budget_leaves_shapes_unread():
    endpoints, _ = _collect(_response("https://www.shop.com/a"), budget_ms=0)
    assert endpoints[0]["shape"] is None
