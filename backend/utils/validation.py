"""
ResearchForge URL validation.
Blocks SSRF attempts by resolving hostnames and rejecting requests
to private, loopback, link-local, and cloud metadata addresses.
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hostnames that must always be blocked regardless of resolved IP.
# Cloud metadata endpoints are reachable via link-local but may also
# be accessible by name in some environments.
BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
})

# IP networks that are never valid targets for outbound user-supplied URLs.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # private class A
    ipaddress.ip_network("172.16.0.0/12"),      # private class B
    ipaddress.ip_network("192.168.0.0/16"),     # private class C
    ipaddress.ip_network("169.254.0.0/16"),     # link-local / cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),      # shared address space (RFC 6598)
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def _is_ip_blocked(ip_str: str) -> bool:
    """Return True if the IP address falls within any blocked network."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in network for network in BLOCKED_NETWORKS)
    except ValueError:
        # Unparseable IP — treat as blocked to be safe
        return True


def validate_url(url: str) -> tuple[bool, str]:
    """
    Validate a user-supplied URL against SSRF attack vectors.

    Returns:
        (True, "") if the URL is safe to fetch.
        (False, reason) if the URL should be rejected.

    Checks performed:
        1. URL must have http or https scheme.
        2. Hostname must be present.
        3. Hostname must not be in BLOCKED_HOSTNAMES.
        4. Hostname resolves to at least one IP address.
        5. All resolved IP addresses must be public (not in BLOCKED_NETWORKS).
    """
    # Scheme check
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"URL parse error: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed — use http or https"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname"

    # Hostname blocklist check
    if hostname.lower() in BLOCKED_HOSTNAMES:
        return False, f"Hostname '{hostname}' is not allowed"

    # DNS resolution check
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"Could not resolve hostname '{hostname}': {e}"

    if not results:
        return False, f"Hostname '{hostname}' resolved to no addresses"

    # Check every resolved IP
    for result in results:
        ip_str = result[4][0]
        if _is_ip_blocked(ip_str):
            return False, (
                f"Hostname '{hostname}' resolves to blocked address '{ip_str}' "
                f"(private, loopback, or cloud metadata range)"
            )

    return True, ""
