from __future__ import annotations

import ipaddress
from typing import Any, Optional

__all__ = [
    "FORWARDED_HOST_HEADER",
    "FORWARDED_PROTO_HEADER",
    "effective_host",
    "effective_origin",
    "peer_may_forward",
    "request_external_origin",
]

FORWARDED_HOST_HEADER = "x-forwarded-host"
FORWARDED_PROTO_HEADER = "x-forwarded-proto"

_KNOWN_SCHEMES = ("https", "http")


def peer_may_forward(peer: Optional[str]) -> bool:
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(str(peer).strip())
    except ValueError:
        return False
    if address.is_loopback:
        return True
    from latticeai.core.security import _peer_is_trusted_proxy

    return _peer_is_trusted_proxy(str(peer).strip())


def _first(value: Optional[str]) -> str:
    return (value or "").split(",")[0].strip()


def effective_host(
    *,
    host: Optional[str],
    forwarded_host: Optional[str] = None,
    peer: Optional[str] = None,
) -> Optional[str]:
    if peer_may_forward(peer):
        claimed = _first(forwarded_host)
        if claimed:
            return claimed
    own = (host or "").strip()
    return own or None


def effective_origin(
    *,
    host: Optional[str],
    scheme: str = "http",
    forwarded_host: Optional[str] = None,
    forwarded_proto: Optional[str] = None,
    peer: Optional[str] = None,
) -> Optional[str]:
    authority = effective_host(host=host, forwarded_host=forwarded_host, peer=peer)
    if not authority:
        return None
    resolved = (scheme or "http").strip().lower() or "http"
    if peer_may_forward(peer):
        claimed = _first(forwarded_proto).lower()
        if claimed in _KNOWN_SCHEMES:
            resolved = claimed
    return f"{resolved}://{authority}"


def request_external_origin(
    request: Any, *, fallback: Optional[str] = None
) -> Optional[str]:
    headers = getattr(request, "headers", None) or {}
    client = getattr(request, "client", None)
    url = getattr(request, "url", None)
    origin = effective_origin(
        host=headers.get("host"),
        scheme=getattr(url, "scheme", "http") or "http",
        forwarded_host=headers.get(FORWARDED_HOST_HEADER),
        forwarded_proto=headers.get(FORWARDED_PROTO_HEADER),
        peer=getattr(client, "host", None) if client else None,
    )
    return origin or fallback
