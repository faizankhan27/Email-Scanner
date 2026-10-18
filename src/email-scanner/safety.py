"""Safety checks for web addresses the server is asked to fetch.

A hosted scanner must never be usable to reach private networks (for example
cloud metadata services or a company intranet), so every request, including
each redirect, is checked against these rules.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")


class UnsafeURL(ValueError):
    """Raised when an address must not be fetched."""


def normalise_start_url(raw: str) -> str:
    """Turn what a person typed into a clean http or https address."""
    url = (raw or "").strip()
    if not url:
        raise UnsafeURL("Enter a website address.")

    match = _SCHEME_RE.match(url)
    if match:
        if match.group(1).lower() not in ("http", "https"):
            raise UnsafeURL("Only http and https addresses can be scanned.")
    else:
        url = "https://" + url

    parsed = urlparse(url)
    try:
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise UnsafeURL("That address does not look valid.")
    if not host or " " in url:
        raise UnsafeURL("That address does not look valid.")
    if parsed.username or parsed.password:
        raise UnsafeURL("Addresses that contain a username or password are not allowed.")
    return url


def check_public_url(url: str) -> None:
    """Raise UnsafeURL unless the address points at the public internet."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL("Only http and https addresses can be scanned.")

    host = parsed.hostname
    if not host:
        raise UnsafeURL("That address does not look valid.")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, ValueError, UnicodeError):
        raise UnsafeURL("That website could not be found. Check the address.")
    if not infos:
        raise UnsafeURL("That website could not be found. Check the address.")

    for info in infos:
        address = info[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise UnsafeURL(
                "That address points to a private or local network, "
                "so it cannot be scanned."
            )
