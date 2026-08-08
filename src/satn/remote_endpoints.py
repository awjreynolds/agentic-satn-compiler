"""Policy and redirect-safe transport for configured outbound HTTPS endpoints."""

from __future__ import annotations

import ipaddress
import urllib.error
import urllib.request
from contextlib import AbstractContextManager
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlsplit


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent a configured remote service from retargeting an outbound request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"configured HTTPS endpoint must not redirect to {newurl}",
            headers,
            fp,
        )


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


def open_configured_https(
    request: urllib.request.Request,
    *,
    timeout: int,
) -> AbstractContextManager[IO[bytes]]:
    """Open one validated HTTPS request without following remote redirects."""
    validate_configured_https_endpoint(request.full_url, field_name="outbound request url")
    opener = urllib.request.build_opener(_RejectRedirects())
    return opener.open(request, timeout=timeout)
