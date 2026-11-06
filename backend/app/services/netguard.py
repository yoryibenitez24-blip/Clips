"""Protects outbound fetches from SSRF: only public http(s) hosts on standard ports."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.services.errors import InvalidURL

_ALLOWED_PORTS = {80, 443}


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None
            and not _is_public_ip(str(addr.ipv4_mapped)))
    )


def validate_public_http_url(url: str, *, resolve: bool = True) -> str:
    """Return the normalised URL or raise InvalidURL if it points anywhere internal."""
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise InvalidURL("Malformed URL") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidURL("Only http(s) URLs are supported")
    host = parts.hostname
    if not host:
        raise InvalidURL("URL has no host")
    if parts.username or parts.password:
        raise InvalidURL("Credentials in URLs are not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise InvalidURL("Only standard http(s) ports are allowed")
    lowered = host.lower().rstrip(".")
    if lowered in ("localhost",) or lowered.endswith(".localhost") or lowered.endswith(".local") \
            or lowered.endswith(".internal"):
        raise InvalidURL("Internal hostnames are not allowed")
    try:
        ipaddress.ip_address(lowered)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False
    if is_literal_ip:
        if not _is_public_ip(lowered):
            raise InvalidURL("Internal network addresses are not allowed")
    elif resolve:
        try:
            infos = socket.getaddrinfo(lowered, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise InvalidURL(f"Could not resolve host {lowered}") from exc
        for info in infos:
            ip = info[4][0]
            if not _is_public_ip(ip):
                raise InvalidURL("Host resolves to an internal network address")
    return parts.geturl()
