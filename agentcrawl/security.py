from __future__ import annotations

import http.client
import ipaddress
import os
import pathlib
import socket
import ssl
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
    fetch_missing_intermediate = False

    def __init__(self, *args, own_context: bool = True, **kwargs):
        # Only a context created by us, never a caller's, is extended below.
        self._own_context = own_context
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection

    def connect(self):
        try:
            super().connect()
        except ssl.SSLCertVerificationError as exc:
            # Browsers complete a chain the server sent without its
            # intermediate certificate (umn.edu); OpenSSL does not.
            if not (
                self.fetch_missing_intermediate
                and self._own_context
                and exc.verify_code == _UNABLE_TO_GET_ISSUER
            ):
                raise
            intermediate = _missing_intermediate(self.host, self.port or 443, self.timeout)
            if intermediate is None:
                raise
            # The downloaded certificate only helps build the chain: partial
            # chains stay off, so it must still lead to a trusted root.
            self._context.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
            try:
                self._context.load_verify_locations(cadata=intermediate)
            except (ssl.SSLError, ValueError):
                raise exc from None
            super().connect()


_UNABLE_TO_GET_ISSUER = 20  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
# id-ad-caIssuers (1.3.6.1.5.5.7.48.2) followed by a URI in the certificate's
# Authority Information Access extension.
_CA_ISSUERS_OID = bytes.fromhex("06082b06010505073002")
_MAX_INTERMEDIATE_BYTES = 65_536


def _missing_intermediate(host: str, port: int, timeout) -> bytes | None:
    """The intermediate certificate the server's own certificate points to."""
    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE  # read the certificate only; nothing is trusted
    try:
        with _pinned_create_connection((host, port), timeout) as raw:
            with probe.wrap_socket(raw, server_hostname=host) as tls:
                leaf = tls.getpeercert(binary_form=True) or b""
    except (OSError, FetchError):
        return None
    url = _ca_issuers_url(leaf)
    return _download_intermediate(url) if url else None


def _ca_issuers_url(der: bytes) -> str | None:
    at = der.find(_CA_ISSUERS_OID)
    if at < 0:
        return None
    at += len(_CA_ISSUERS_OID)
    if der[at : at + 1] != b"\x86":
        return None
    length = der[at + 1]
    start = at + 2
    if length == 0x81:
        length, start = der[at + 2], at + 3
    url = der[start : start + length].decode("ascii", "replace")
    return url if url.startswith(("http://", "https://")) else None


_intermediates: dict[str, bytes | None] = {}


def _download_intermediate(url: str) -> bytes | None:
    if url not in _intermediates:
        body: bytes | None = None
        try:
            # Same SSRF rules as any fetch: public addresses only, pinned DNS.
            opener = urllib.request.build_opener(*pinned_handlers())
            with opener.open(url, timeout=10) as response:
                body = response.read(_MAX_INTERMEDIATE_BYTES + 1)
        except Exception:
            body = None
        if body and len(body) > _MAX_INTERMEDIATE_BYTES:
            body = None
        elif body and body.lstrip().startswith(b"-----BEGIN CERTIFICATE"):
            body = ssl.PEM_cert_to_DER_cert(body.decode("ascii", "replace").strip())
        # DER from here on; a PKCS#7 bundle fails to load and is not used.
        _intermediates[url] = body or None
    return _intermediates[url]


def redirect_past_invalid_certificate(url: str, timeout: float) -> str | None:
    """Where a host whose certificate does not verify redirects to, if anywhere.

    Many bare domains (gamepass.com, tbank.ru) serve a certificate for another
    name and only redirect to the real site. Browsers stop there; Crawl4AI
    ignores certificate errors altogether. Here only the status line and the
    ``Location`` header are read, never the body, and only an ``https`` target
    on another host is returned: that URL is then fetched with full
    verification, so no unverified content is ever used.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE  # the redirect is followed, nothing is trusted
    connection = _PinnedHTTPSConnection(
        parts.hostname, parts.port, timeout=timeout, context=probe, own_context=False
    )
    try:
        connection.request("GET", parts.path or "/", headers={"accept": "text/html"})
        response = connection.getresponse()
        location = response.getheader("location") or ""
        status = response.status
    except (OSError, http.client.HTTPException, FetchError):
        return None
    finally:
        connection.close()
    if not (300 <= status < 400 and location):
        return None
    target = urllib.parse.urljoin(url, location.strip())
    target_parts = urllib.parse.urlsplit(target)
    if target_parts.scheme != "https" or target_parts.hostname in (None, parts.hostname):
        return None
    return target


class _AIAPinnedHTTPSConnection(_PinnedHTTPSConnection):
    fetch_missing_intermediate = True


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        # Through a proxy the socket goes to the operator-configured proxy,
        # which resolves the target itself; pinning does not apply.
        if req.has_proxy():
            return super().http_open(req)
        return self.do_open(_PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *args, fetch_missing_intermediate: bool = False, **kwargs):
        # Python 3.12+ creates the context here when none is given.
        self._own_context = not args and kwargs.get("context") is None
        super().__init__(*args, **kwargs)
        self._connection = (
            _AIAPinnedHTTPSConnection if fetch_missing_intermediate else _PinnedHTTPSConnection
        )

    def https_open(self, req):
        if req._tunnel_host:
            return super().https_open(req)
        kwargs = {"context": self._context, "own_context": self._own_context}
        if hasattr(self, "_check_hostname"):
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(self._connection, req, **kwargs)


def pinned_handlers(
    *, fetch_missing_intermediate: bool = False
) -> list[urllib.request.BaseHandler]:
    """``fetch_missing_intermediate`` completes a server's broken certificate
    chain from the address its certificate names; never under airgap."""
    return [
        PinnedHTTPHandler(),
        PinnedHTTPSHandler(fetch_missing_intermediate=fetch_missing_intermediate),
    ]
