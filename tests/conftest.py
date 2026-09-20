from __future__ import annotations

import socket

import pytest

from agentcrawl.server import server

# Documentation hosts used across the test suite. They must resolve for the
# SSRF validation in ``agentcrawl.security.validate_remote_url`` (which does a
# real ``socket.getaddrinfo`` to reject private IPs) to pass in sandboxes with
# filtered or absent DNS. No test relies on resolution failing, so mapping
# these to a public documentation IP keeps the suite hermetic.
_RESOLVABLE_HOSTS = {
    "example.com",
    "example.org",
    "www.example.com",
    "www.example.org",
    "docs.example.com",
    "api.example.com",
    "sub.example.com",
    "fastapi.tiangolo.com",
    "pypi.org",
    "en.wikipedia.org",
    "www.rfc-editor.org",
    "github.com",
    "raw.githubusercontent.com",
}


@pytest.fixture(autouse=True)
def stub_dns_resolution(monkeypatch):
    """Hermético: resuelve hosts de documentación sin DNS real.

    Los tests mockean el transporte (``_safe_urlopen``, Playwright, Camofox)
    pero ``validate_remote_url`` resuelve el host antes del fetch. En CI o
    contenedores sin DNS completo eso rompía 8 tests aunque el código
    estuviera bien. Hosts desconocidos caen al resolver real, preservando el
    camino de fallo honesto (``Unable to resolve target host``).
    """
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(hostname, port, *args, **kwargs):
        host = str(hostname).lower().rstrip(".")
        if host in _RESOLVABLE_HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return real_getaddrinfo(hostname, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    yield


@pytest.fixture(autouse=True)
def reset_server_security_state():
    original = (
        server.auth_enabled,
        set(server.api_keys),
        set(server.owner_api_keys),
        server.dashboard_public,
        server.allow_local_files,
        server.allow_private_network,
        server.rate_limit_per_minute,
        dict(server._rate_windows),
    )
    server.auth_enabled = False
    server.api_keys = set()
    server.owner_api_keys = set()
    server.dashboard_public = False
    server.allow_local_files = False
    server.allow_private_network = False
    try:
        yield
    finally:
        (
            server.auth_enabled,
            server.api_keys,
            server.owner_api_keys,
            server.dashboard_public,
            server.allow_local_files,
            server.allow_private_network,
            server.rate_limit_per_minute,
            rate_windows,
        ) = original
        server._rate_windows = rate_windows
