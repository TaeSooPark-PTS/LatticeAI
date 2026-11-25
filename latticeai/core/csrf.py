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

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

SESSION_COOKIE_NAME = "session_token"

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}

_DENIED_DETAIL = (
    "요청 출처를 확인할 수 없어 거부했습니다. 다른 사이트에서 보낸 요청일 수 있습니다."
)


def normalize_origin(value: Optional[str]) -> Optional[Tuple[str, str, Optional[int]]]:
    if not value:
        return None
    candidate = value.strip()
    if not candidate or candidate.lower() == "null":
        return None
    if "//" not in candidate:
        candidate = "//" + candidate
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if not host:
        return None
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    return (scheme, host, port)


def _same_site(
    left: Tuple[str, str, Optional[int]], right: Tuple[str, str, Optional[int]]
) -> bool:
    if left[1] != right[1] or left[2] != right[2]:
        return False
    if left[0] and right[0]:
        return left[0] == right[0]
    return True


@dataclass(frozen=True)
class CSRFDecision:
    allowed: bool
    reason: str


class CSRFOriginPolicy:
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
        if method.upper() in SAFE_METHODS:
            return CSRFDecision(True, "safe-method")
        if (authorization or "").strip().lower().startswith("bearer "):
            return CSRFDecision(True, "bearer-auth")
        if not _has_session_cookie(cookie_header):
            return CSRFDecision(True, "no-session-cookie")

        stated = normalize_origin(origin)
        if stated is None and origin:
            return CSRFDecision(False, "opaque-origin")
        if stated is None:
            stated = normalize_origin(referer)
            if stated is None:
                if self.bind_is_loopback:
                    return CSRFDecision(True, "no-origin-loopback-bind")
                return CSRFDecision(False, "no-origin-reachable-bind")

        if self._origin_is_trusted(stated, host, forwarded_host, peer):
            return CSRFDecision(True, "same-site-or-trusted-origin")
        return CSRFDecision(False, "cross-site-origin")


def _has_session_cookie(cookie_header: Optional[str]) -> bool:
    if not cookie_header:
        return False
    for pair in cookie_header.split(";"):
        name, _, _value = pair.partition("=")
        if name.strip() == SESSION_COOKIE_NAME:
            return True
    return False


class CSRFOriginGuardMiddleware:
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
