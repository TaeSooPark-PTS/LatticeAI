"""Which host a request *really* arrived at, when something proxied it.

The desktop front door is a reverse proxy: the browser talks to the gateway on
one port and the gateway forwards to the worker on another.  ``Host`` is a
hop-by-hop header, so it is replaced on the way out and the worker sees only its
own internal authority — which is why every cookie-authenticated write through
an adopted worker was rejected as ``csrf_origin_rejected``.  The gateway states
the fact it destroyed in ``X-Forwarded-Host`` / ``X-Forwarded-Proto``; this
module decides when to believe it.

Threat model, so the rule is checkable rather than felt:

* **A browser cannot set these headers.**  ``X-Forwarded-*`` are not on the
  fetch-safelist and a page cannot add them cross-origin without a preflight
  the CORS allowlist rejects.  So no *web page* can reach this code path.
* **A local non-browser caller could already forge ``Origin``.**  curl on this
  machine can send any ``Origin`` it likes, and the loopback exemption in
  :mod:`latticeai.core.csrf` already trusts it.  Honouring a forwarded host from
  the same caller widens nothing that was not already open.
* **Anyone else is not trusted.**  Off-loopback the header is honoured only from
  a peer the operator listed in ``LATTICEAI_TRUSTED_PROXIES`` — the same
  allowlist ``latticeai.core.security.client_ip`` uses for the same reason, and
  empty by default.

The functions are pure: the caller supplies the headers and the peer address, so
the policy is testable without an ASGI app or a socket.
"""

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

#: The authority the client asked for, as stated by the proxy in front of us.
FORWARDED_HOST_HEADER = "x-forwarded-host"
#: The scheme the client used to reach that proxy.
FORWARDED_PROTO_HEADER = "x-forwarded-proto"

# Only these two are ever taken from a forwarded scheme; anything else is a
# value we would have to reason about, and the fallback is already correct.
_KNOWN_SCHEMES = ("https", "http")


def peer_may_forward(peer: Optional[str]) -> bool:
    """Whether ``X-Forwarded-*`` from this direct peer may be believed.

    Loopback, or a member of the configured trusted-proxy allowlist.  An
    unparseable or absent peer is *not* trusted: a request whose origin we
    cannot establish is exactly the one whose claims we must not take.
    """
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
    """The first entry of a possibly comma-joined forwarded header."""
    return (value or "").split(",")[0].strip()


def effective_host(
    *,
    host: Optional[str],
    forwarded_host: Optional[str] = None,
    peer: Optional[str] = None,
) -> Optional[str]:
    """The authority the client aimed at: the forwarded one when believable.

    Falls back to the request's own ``Host`` in every other case — no proxy, a
    peer we do not trust, or a forwarded header that is empty.  ``None`` only
    when there is nothing to report at all (an HTTP/1.0 request with no ``Host``
    and no proxy), which callers already have to handle.
    """
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
    """``scheme://authority`` for the front door this request came through.

    ``scheme`` is what *this* server is speaking; the forwarded scheme wins when
    the peer is trusted and states one we recognise, because a TLS-terminating
    proxy speaks plain HTTP to us while the browser is on ``https`` and a link
    built with the wrong scheme is a broken link.
    """
    authority = effective_host(host=host, forwarded_host=forwarded_host, peer=peer)
    if not authority:
        return None
    resolved = (scheme or "http").strip().lower() or "http"
    if peer_may_forward(peer):
        claimed = _first(forwarded_proto).lower()
        if claimed in _KNOWN_SCHEMES:
            resolved = claimed
    return f"{resolved}://{authority}"


def request_external_origin(request: Any, *, fallback: Optional[str] = None) -> Optional[str]:
    """:func:`effective_origin` for a live request — the *link-building* seam.

    CSRF asks "is this same-origin?"; this asks the other question: "what URL
    should I hand a person so they can come back here?" Both must read the same
    front door, or an invite link mails out the internal worker port that only
    the gateway can reach.

    ``fallback`` is returned when the request states no authority at all, so a
    caller with a configured public URL keeps it.
    """
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
