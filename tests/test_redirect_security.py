from urllib.request import Request

import pytest

from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import _SafeRedirectHandler


class FakeHeaders:
    def __init__(self, location: str):
        self.location = location

    def get(self, name: str, default=None):
        if name.lower() == "location":
            return self.location
        return default


def test_safe_redirect_handler_rejects_private_redirect_target() -> None:
    handler = _SafeRedirectHandler(allow_private_network=False)

    with pytest.raises(FetchError, match="Private or non-global"):
        handler.redirect_request(
            Request("https://example.com/start"),
            None,
            302,
            "Found",
            FakeHeaders("http://127.0.0.1/admin"),
            "http://127.0.0.1/admin",
        )


def test_safe_redirect_handler_allows_public_redirect_target() -> None:
    handler = _SafeRedirectHandler(allow_private_network=False)

    redirected = handler.redirect_request(
        Request("https://example.com/start"),
        None,
        302,
        "Found",
        FakeHeaders("https://example.org/next"),
        "https://example.org/next",
    )

    assert redirected.full_url == "https://example.org/next"


def test_safe_redirect_handler_uses_private_network_flag() -> None:
    handler = _SafeRedirectHandler(allow_private_network=True)

    redirected = handler.redirect_request(
        Request("https://example.com/start"),
        None,
        302,
        "Found",
        FakeHeaders("http://127.0.0.1/admin"),
        "http://127.0.0.1/admin",
    )

    assert redirected.full_url == "http://127.0.0.1/admin"
