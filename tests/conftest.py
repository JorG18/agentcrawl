from __future__ import annotations

import pytest

from agentcrawl.server import server


@pytest.fixture(autouse=True)
def reset_server_security_state():
    original = (
        server.auth_enabled,
        set(server.api_keys),
        server.allow_local_files,
        server.allow_private_network,
        server.rate_limit_per_minute,
        dict(server._rate_windows),
    )
    server.auth_enabled = False
    server.api_keys = set()
    server.allow_local_files = False
    server.allow_private_network = False
    try:
        yield
    finally:
        (
            server.auth_enabled,
            server.api_keys,
            server.allow_local_files,
            server.allow_private_network,
            server.rate_limit_per_minute,
            rate_windows,
        ) = original
        server._rate_windows = rate_windows
