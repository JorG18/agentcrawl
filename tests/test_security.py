import socket

import pytest

from agentcrawl.exceptions import FetchError
from agentcrawl.security import validate_remote_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:bad/path",
        "http://example.com:99999/path",
    ],
)
def test_validate_remote_url_rejects_invalid_ports_even_when_private_allowed(url) -> None:
    with pytest.raises(FetchError, match="Invalid URL port"):
        validate_remote_url(url, allow_private_network=True)


def test_validate_remote_url_rejects_ipv6_loopback_literal() -> None:
    with pytest.raises(FetchError, match="Private or non-global"):
        validate_remote_url("http://[::1]/")


def test_validate_remote_url_checks_all_resolved_addresses(monkeypatch) -> None:
    def fake_getaddrinfo(_hostname, _port):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(FetchError, match="127.0.0.1"):
        validate_remote_url("https://example.com/")


def test_validate_remote_url_rejects_private_ipv4_literal_without_dns(monkeypatch) -> None:
    def fake_getaddrinfo(_hostname, _port):
        raise AssertionError("IP literals should not require DNS resolution")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(FetchError, match="10.0.0.1"):
        validate_remote_url("http://10.0.0.1/")
