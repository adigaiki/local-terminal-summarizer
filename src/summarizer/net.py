"""Endpoint safety helpers.

The local-only contract is that the configured engine endpoint points at a
server the user runs themselves, normally on loopback. Nothing here performs
network I/O; these helpers only *classify* a configured URL so the doctor and
pipeline can report honestly, and so tests can assert the absence of
accidental external endpoints.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

__all__ = ["is_loopback_host", "is_loopback_url", "endpoint_host"]

# Names that always resolve to the local machine, plus the IPv6 loopback
# literal. ``ipaddress`` covers the whole 127.0.0.0/8 range and ::1.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def endpoint_host(url: str) -> str | None:
    """Return the host component of an endpoint URL (None when unparseable)."""
    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    host = parts.hostname
    return host.lower() if host else None


def is_loopback_host(host: str | None) -> bool:
    """True when a hostname/IP refers to this machine only."""
    if not host:
        return False
    name = host.strip().strip("[]").lower()
    if not name:
        return False
    if name in _LOOPBACK_NAMES or name.endswith(".localhost"):
        return True
    # Any hostname that carries a dot other than the local aliases above is
    # treated as remote unless it parses as a loopback address.
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """True when the endpoint resolves to the local machine."""
    return is_loopback_host(endpoint_host(url))
