from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from .exceptions import FetchError


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

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise FetchError("Localhost targets are not allowed.")
    try:
        ip_literal = ipaddress.ip_address(hostname)
        addresses = {str(ip_literal)}
    except ValueError:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port)}
        except socket.gaierror as exc:
            raise FetchError(f"Unable to resolve target host: {hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise FetchError(f"Private or non-global target address is not allowed: {ip}")
