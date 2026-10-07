from urllib.request import Request

import pytest

from agentcrawl.exceptions import FetchError
from agentcrawl.fetchers import _SafeRedirectHandler
from agentcrawl.security import redirect_past_invalid_certificate


def _stub_probe(monkeypatch, status: int, location: str) -> None:
    """Answer the unverified probe request with a canned redirect."""
    from agentcrawl import security

    response_cls = type(
        "_Response",
        (),
        {
            "status": status,
            "getheader": staticmethod(
                lambda name, default=None: location if name.lower() == "location" else default
            ),
        },
    )

    class _Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return response_cls()

        def close(self):
            pass

    monkeypatch.setattr(security, "_PinnedHTTPSConnection", _Connection)


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


def test_redirect_past_certificate_refuses_a_target_with_credentials(monkeypatch) -> None:
    """A hostile Location can smuggle embedded credentials (user:pass@host) —
    the same class the input validator refuses must not slip in sideways."""
    _stub_probe(monkeypatch, 301, "https://user:pass@www.example.org/")
    assert redirect_past_invalid_certificate("https://broken.example/", 1.0) is None


def test_redirect_past_certificate_refuses_an_out_of_range_port(monkeypatch) -> None:
    """A target whose port the URL parser rejects cannot be fetched; None
    keeps the honest tls_error instead of following the smuggled hop."""
    _stub_probe(monkeypatch, 301, "https://www.example.org:99999/")
    assert redirect_past_invalid_certificate("https://broken.example/", 1.0) is None


def test_redirect_past_certificate_still_follows_a_legitimate_target(monkeypatch) -> None:
    """A plain https redirect to another host keeps working (gamepass.com)."""
    _stub_probe(monkeypatch, 301, "https://www.example.org/")
    target = redirect_past_invalid_certificate("https://broken.example/", 1.0)
    assert target == "https://www.example.org/"


def _redirect(monkeypatch, newurl: str):
    monkeypatch.setattr("agentcrawl.fetchers.validate_remote_url", lambda *a, **k: None)
    request = Request(
        "https://google.serper.dev/search", headers={"X-API-KEY": "k", "User-Agent": "ua"}
    )
    return _SafeRedirectHandler().redirect_request(request, None, 302, "Found", {}, newurl)


def test_cross_host_redirect_drops_credentials(monkeypatch) -> None:
    new = _redirect(monkeypatch, "https://other.example/x")
    assert "X-api-key" not in new.headers
    assert new.headers["User-agent"] == "ua"


def test_same_host_redirect_keeps_headers(monkeypatch) -> None:
    new = _redirect(monkeypatch, "https://google.serper.dev/other")
    assert new.headers["X-api-key"] == "k"


def test_https_to_http_redirect_on_the_same_host_drops_credentials(monkeypatch) -> None:
    new = _redirect(monkeypatch, "http://google.serper.dev/search")
    assert "X-api-key" not in new.headers


def test_http_to_https_upgrade_keeps_headers(monkeypatch) -> None:
    monkeypatch.setattr("agentcrawl.fetchers.validate_remote_url", lambda *a, **k: None)
    request = Request("http://google.serper.dev/search", headers={"X-API-KEY": "k"})
    new = _SafeRedirectHandler().redirect_request(
        request, None, 301, "Moved", {}, "https://google.serper.dev/search"
    )
    assert new.headers["X-api-key"] == "k"
