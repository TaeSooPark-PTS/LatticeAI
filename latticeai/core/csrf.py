"""Origin/Referer guard for cookie-authenticated state changes.

The ``session_token`` cookie is ``SameSite=Lax``, which stops cross-site
*top-level GET-like* navigations from carrying it but does **not** stop a
cross-site ``POST``/``PUT``/``PATCH``/``DELETE`` issued by script from a page
the browser considers a different site in every configuration we ship (Lax
blocks non-GET cross-site sends in current browsers, but the cookie is also
the only credential a browser attaches automatically, and Lax is not a
security boundary we should rely on alone). Every mutating endpoint accepted
that cookie with no second check, so a non-loopback deployment was one
malicious page away from forged state changes.

This module is the *decision*; :mod:`latticeai.runtime.web_runtime` is the
wiring. Keeping the two apart means the policy is testable without an ASGI
app, and the middleware stays small enough to audit.

Threat model, stated so the exemptions are checkable rather than vibes:

* **Only ambient credentials are CSRF-able.** ``Authorization: Bearer`` is set
  by the caller's own code; an attacker's page cannot add it to a cross-site
  request without a CORS preflight that our ``CORSMiddleware`` allowlist
  rejects. Bearer-authenticated requests are therefore exempt.
* **No cookie, nothing to forge.** A request without ``session_token`` cannot
  be authenticated by cookie, so it is not this guard's business.
* **``Origin`` is attacker-honest.** A browser sets it; a page cannot lie about
  it. ``Referer`` is the fallback for the handful of clients that omit
  ``Origin``.
* **Neither header present** means the caller is not a browser (curl, the CLI,
  the desktop shell, the VS Code extension). That is trusted only while the
  server is bound to loopback, where "not a browser" and "already on this
  machine" coincide. On a reachable bind it fails closed.
* **Behind a proxy, ``Host`` is not the front door.** The desktop gateway
  replaces it with the worker's internal authority, so the ``Origin == Host``
  fallback below compared a browser's real origin against a port the browser
  has never heard of and refused every write. The front door's own name comes
  from :mod:`latticeai.core.http_origin`, which believes ``X-Forwarded-Host``
  only from a peer that may forward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    List,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import urlsplit

from latticeai.core.http_origin import (
    FORWARDED_HOST_HEADER,
    effective_host,
)

__all__ = [
    "CSRFDecision",
    "CSRFOriginPolicy",
    "CSRFOriginGuardMiddleware",
    "SAFE_METHODS",
    "SESSION_COOKIE_NAME",
    "normalize_origin",
]

# The ASGI contract, spelled out locally so the policy stays importable (and
# unit-testable) without pulling in a web framework.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Methods that must not change state. Everything else is guarded.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

SESSION_COOKIE_NAME = "session_token"

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}

_DENIED_DETAIL = (
    "요청 출처를 확인할 수 없어 거부했습니다. "
    "다른 사이트에서 보낸 요청일 수 있습니다."
)


def normalize_origin(value: Optional[str]) -> Optional[Tuple[str, str, Optional[int]]]:
    """``"HTTP://Localhost:80/x"`` → ``("http", "localhost", None)``.

    Returns ``None`` for anything that is not a usable origin (empty, the
    literal ``"null"`` that sandboxed iframes and ``file://`` pages send, or a
    value with no host). ``"null"`` is deliberately *not* normalized into a
    matchable origin: it is what an opaque origin sends, and opaque origins are
    exactly the ones we must not trust.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or candidate.lower() == "null":
        return None
    if "//" not in candidate:
        # A bare authority ("example.com:4825") — the Host header shape.
        candidate = "//" + candidate
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if not host:
        return None
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        # Malformed port ("example.com:notaport"): unusable, so untrusted.
        return None
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    return (scheme, host, port)


def _same_site(left: Tuple[str, str, Optional[int]], right: Tuple[str, str, Optional[int]]) -> bool:
    """Host+port equality, ignoring scheme when one side does not carry one.

    The ``Host`` header has no scheme, and a TLS-terminating reverse proxy
    speaks ``http`` to us while the browser reports ``https``. Comparing the
    authority is what makes "this page came from this server" answerable in
    both deployments; the scheme is only compared when both sides state one.
    """
    if left[1] != right[1] or left[2] != right[2]:
        return False
    if left[0] and right[0]:
        return left[0] == right[0]
    return True


@dataclass(frozen=True)
class CSRFDecision:
    """Why a request was allowed or refused — the reason is the audit trail."""

    allowed: bool
    reason: str


class CSRFOriginPolicy:
    """Decide whether one request may change state with a cookie credential."""

    def __init__(
        self,
        *,
        trusted_origins: Iterable[str] = (),
        server_host: str = "127.0.0.1",
        server_port: int = 4825,
        bind_is_loopback: bool = True,
    ) -> None:
        self.bind_is_loopback = bool(bind_is_loopback)
        self._trusted: List[Tuple[str, str, Optional[int]]] = []
        for origin in self._default_origins(server_host, server_port):
            self._add(origin)
        for origin in trusted_origins:
            self._add(origin)

    @staticmethod
    def _default_origins(server_host: str, server_port: int) -> List[str]:
        """The server's own origin plus loopback, in both schemes."""
        hosts = [server_host, "localhost", "127.0.0.1", "[::1]"]
        origins: List[str] = []
        for host in hosts:
            if not host:
                continue
            for scheme in ("http", "https"):
                origins.append(f"{scheme}://{host}:{server_port}")
        return origins

    def _add(self, origin: str) -> None:
        normalized = normalize_origin(origin)
        if normalized is not None and normalized not in self._trusted:
            self._trusted.append(normalized)

    @property
    def trusted_origins(self) -> Sequence[Tuple[str, str, Optional[int]]]:
        return tuple(self._trusted)

    def _origin_is_trusted(
        self,
        origin: Tuple[str, str, Optional[int]],
        host_header: Optional[str],
        forwarded_host: Optional[str] = None,
        peer: Optional[str] = None,
    ) -> bool:
        if any(_same_site(origin, trusted) for trusted in self._trusted):
            return True
        # Same-origin by the request's own Host. A browser sets Host from the
        # URL it is fetching, so `Origin == Host` can only be produced by a
        # page this server actually served — which is precisely "not cross
        # site". This is what keeps reverse-proxied hostnames working without
        # every operator having to enumerate them.
        #
        # "The request's own Host" is the *front door's*, not the internal one a
        # proxy hop rewrote it to, which is why the forwarded authority is
        # resolved first (and only from a peer that may forward).
        own = normalize_origin(
            effective_host(host=host_header, forwarded_host=forwarded_host, peer=peer)
        )
        return own is not None and _same_site(origin, own)

    def evaluate(
        self,
        *,
        method: str,
        origin: Optional[str],
        referer: Optional[str],
        host: Optional[str],
        cookie_header: Optional[str],
        authorization: Optional[str],
        forwarded_host: Optional[str] = None,
        peer: Optional[str] = None,
    ) -> CSRFDecision:
        """Allow/deny one request. Pure: no I/O, no app state."""
        if method.upper() in SAFE_METHODS:
            return CSRFDecision(True, "safe-method")
        if (authorization or "").strip().lower().startswith("bearer "):
            # Not ambient: a cross-site page cannot attach this header.
            return CSRFDecision(True, "bearer-auth")
        if not _has_session_cookie(cookie_header):
            return CSRFDecision(True, "no-session-cookie")

        stated = normalize_origin(origin)
        if stated is None and origin:
            # An opaque origin ("null") explicitly claims *untrusted* provenance.
            return CSRFDecision(False, "opaque-origin")
        if stated is None:
            stated = normalize_origin(referer)
            if stated is None:
                if self.bind_is_loopback:
                    # Non-browser client on the same machine (CLI, desktop
                    # shell, curl). Nothing on the network can reach this bind.
                    return CSRFDecision(True, "no-origin-loopback-bind")
                return CSRFDecision(False, "no-origin-reachable-bind")

        if self._origin_is_trusted(stated, host, forwarded_host, peer):
            return CSRFDecision(True, "same-site-or-trusted-origin")
        return CSRFDecision(False, "cross-site-origin")


def _has_session_cookie(cookie_header: Optional[str]) -> bool:
    """Whether the raw Cookie header carries ``session_token``.

    Parsed by hand rather than through ``http.cookies`` so a malformed pair
    cannot make the whole header unreadable — a request whose cookie jar we
    cannot parse must still be treated as cookie-bearing.
    """
    if not cookie_header:
        return False
    for pair in cookie_header.split(";"):
        name, _, _value = pair.partition("=")
        if name.strip() == SESSION_COOKIE_NAME:
            return True
    return False


class CSRFOriginGuardMiddleware:
    """Pure-ASGI guard: inspects request headers, never touches the response.

    Written against the raw ASGI interface instead of ``BaseHTTPMiddleware``
    on purpose — this app streams (SSE chat, live agent steps), and wrapping
    the response path to make a decision that only needs request headers would
    put a buffering layer in front of every stream for no benefit.
    """

    def __init__(self, app: ASGIApp, *, policy: CSRFOriginPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        # ``client`` is ``(host, port)`` under every ASGI server, and absent
        # under a test transport that does not model a socket.
        client = scope.get("client") or ()
        decision = self.policy.evaluate(
            method=str(scope.get("method") or "GET"),
            origin=headers.get("origin"),
            referer=headers.get("referer"),
            host=headers.get("host"),
            cookie_header=headers.get("cookie"),
            authorization=headers.get("authorization"),
            forwarded_host=headers.get(FORWARDED_HOST_HEADER),
            peer=client[0] if client else None,
        )
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        await _send_forbidden(send, decision.reason)


async def _send_forbidden(send: Send, reason: str) -> None:
    body = json.dumps(
        {"detail": _DENIED_DETAIL, "error": "csrf_origin_rejected", "reason": reason},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
