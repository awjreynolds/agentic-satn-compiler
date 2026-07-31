"""Syntactic policy for configured outbound HTTP endpoints.

This deliberately performs no DNS lookup while configuration is parsed: doing
so would make an Area Definition dependent on ambient resolver state and would
still leave a time-of-check/time-of-use window.  Callers therefore must treat
hostname rebinding and redirect-target validation as a transport-layer follow-up
when replacing ``urllib`` with a redirect-controlling client.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def validate_configured_https_endpoint(value: str, *, field_name: str) -> str:
    """Accept a credential-free HTTPS endpoint that is not an unsafe IP literal."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid HTTPS endpoint") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port == 0
    ):
        raise ValueError(
            f"{field_name} must be an HTTPS endpoint without credentials or a fragment"
        )
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost":
        raise ValueError(f"{field_name} must not target localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise ValueError(f"{field_name} must not target a non-public IP address")
    return value
