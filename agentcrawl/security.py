from __future__ import annotations

import http.client
import ipaddress
import os
import pathlib
import socket
import urllib.parse
import urllib.request

from .exceptions import FetchError


class LocalFileAccessError(FetchError):
    """A local-file source was refused by the access gate."""

    def __init__(self, message: str, error_type: str) -> None:
        super().__init__(message, error_type=error_type)


LOCAL_FILES_DISABLED_MESSAGE = (
    "Local file sources are disabled here (set AGENTCRAWL_ALLOW_LOCAL_FILES=true to opt in)."
)
LOCAL_FILE_OUTSIDE_ROOT_MESSAGE = "Local file sources must stay inside AGENTCRAWL_LOCAL_FILES_ROOT."


def check_local_source(source: str, *, allow: bool, root: str | None) -> None:
    """Refuse a local-file source unless allowed, and keep it inside ``root``.

    The one gate for every entrance: ``fetchers.fetch_source`` (library, CLI,
    MCP, crawl, discovery) and the API server. The check runs *before* the
    file is touched, so a refusal does not reveal whether the path exists.
    Containment uses the real path of both sides, so ``..`` segments and
    symlinks that point out of the root are refused too.
    """
    if not allow:
        raise LocalFileAccessError(LOCAL_FILES_DISABLED_MESSAGE, "local_files_disabled")
    if root is None:
        return
    real_root = os.path.realpath(root)
    candidate = pathlib.Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = pathlib.Path.cwd() / candidate
    resolved = os.path.realpath(candidate)
    if resolved != real_root and not resolved.startswith(real_root.rstrip(os.sep) + os.sep):
        raise LocalFileAccessError(LOCAL_FILE_OUTSIDE_ROOT_MESSAGE, "local_file_outside_root")


def validate_remote_url(url: str, *, allow_private_network: bool = False) -> None:
    if len(url) > 8192:
        raise FetchError("URL exceeds the 8192 character limit.")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Only absolute HTTP and HTTPS URLs are allowed.")
    try:
        port = parsed.port or 0
    except ValueError as exc:
        raise FetchError("Invalid URL port.") from exc
    if parsed.username or parsed.password:
        raise FetchError("URLs containing embedded credentials are not allowed.")
    if allow_private_network:
        return

    resolve_public_addresses(parsed.hostname, port)


# The SSRF guard is right to refuse 127.0.0.1 by default, but "not allowed" on
# its own left people testing against a local dev server with nowhere to go.
_PRIVATE_HINT = (
    " (to reach local/private targets on purpose, set allow_private_network=true:"
    " CLI --allow-private-network, env AGENTCRAWL_ALLOW_PRIVATE_NETWORK=true)"
)


def resolve_public_addresses(hostname: str, port: int | None) -> list[tuple]:
    """Resolve ``hostname`` and refuse it unless every address is global.

    Returns the ``getaddrinfo`` entries so a caller can connect to exactly the
    addresses that were validated (see ``PinnedHTTPHandler``).
    """
    hostname = hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise FetchError("Localhost targets are not allowed." + _PRIVATE_HINT)
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            raw_infos = socket.getaddrinfo(hostname, port or 0)
        except socket.gaierror as exc:
            raise FetchError(f"Unable to resolve target host: {hostname}") from exc
        # One entry per address for a TCP connect (getaddrinfo also returns
        # DGRAM/RAW duplicates when no socket type is requested).
        infos = [info for info in raw_infos if info[1] == socket.SOCK_STREAM] or [
            (info[0], socket.SOCK_STREAM, 6, info[3], info[4]) for info in raw_infos
        ]
    else:
        family = socket.AF_INET6 if ip_literal.version == 6 else socket.AF_INET
        infos = [(family, socket.SOCK_STREAM, 6, "", (str(ip_literal), port or 0))]
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError(
                f"Private or non-global target address is not allowed: {ip}" + _PRIVATE_HINT
            )
    return infos


# -- DNS pinning ---------------------------------------------------------------
# ``validate_remote_url`` resolves the host, then urllib resolves it again to
# connect. A hostile DNS server can answer the first lookup with a public
# address and the second with 127.0.0.1 / 169.254.169.254 (DNS rebinding), so
# the validation proved nothing about the socket that was actually opened. The
# pinned connections below resolve once, at connect time, validate those
# addresses and connect to them — the check and the connection now use the same
# answer. TLS is unchanged: SNI and certificate checks still use the hostname.


def _pinned_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    last_error: OSError | None = None
    for family, socktype, proto, _canon, sockaddr in resolve_public_addresses(host, port):
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect((sockaddr[0], port, *sockaddr[2:]))
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    raise last_error or OSError(f"Unable to connect to {host}:{port}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        # Through a proxy the socket goes to the operator-configured proxy,
        # which resolves the target itself; pinning does not apply.
        if req.has_proxy():
            return super().http_open(req)
        return self.do_open(_PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        if req._tunnel_host:
            return super().https_open(req)
        kwargs = {"context": self._context}
        if hasattr(self, "_check_hostname"):
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(_PinnedHTTPSConnection, req, **kwargs)


def pinned_handlers() -> list[urllib.request.BaseHandler]:
    return [PinnedHTTPHandler(), PinnedHTTPSHandler()]
