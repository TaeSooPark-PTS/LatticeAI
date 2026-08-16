"""Token hashing, secret redaction, and the worker's per-user rate limiter.

What is *not* here any more, and why: password hashing / verification, the
``client_ip`` resolver with its per-IP login limiter, and the file-magic
extension check were all ported to ``lattice-auth`` / ``lattice-chat`` in
v11.6.0, when the front door and every write moved to Rust. They were left
behind as fully orphaned Python — no route, no service, no script called them
— and a second copy of an authentication rule that nothing enforces is worse
than no copy: it reads like a live guard.

What stays, stays because something still calls it:

* :func:`enforce_rate_limit` — charged by every ``/worker/*`` and ``/agent/*``
  seam through ``_admit``.
* :func:`redact_secret_text` / :func:`redact_secrets` — and they additionally
  generate the Rust redaction fixture via ``scripts/gen_redact_fixture.py``.
* the trusted-proxy allowlist — ``_peer_is_trusted_proxy`` is what
  :mod:`latticeai.core.http_origin` (and through it the CSRF check) asks before
  believing an ``X-Forwarded-Host``.
"""

import hashlib
import ipaddress
import re
import threading
import time
from typing import Any, Dict, List

from fastapi import HTTPException

from latticeai.core.quiet import quiet


def sha256_hex(text: str) -> str:
    """The one UTF-8 SHA-256 hex digest.

    Six modules each carried this three-token expression, and they did not
    agree on what to do with ``None``: two coerced it away, one raised. The
    helper stays **strict** on purpose — coercing here would silently give
    every caller the digest of the empty string, which is a valid-looking
    token hash for "no token at all". Each call site keeps the contract it
    already had, stated in its own body where a reader can see it.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def host_is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ── Trusted-proxy allowlist ──────────────────────────────────────────────────
# The per-IP login limiter this list was born for is ``lattice-auth``'s now, and
# ``client_ip`` went with it. The list itself is still load-bearing on this
# side: :func:`latticeai.core.http_origin.peer_may_forward` — and through it the
# CSRF front-door check — asks ``_peer_is_trusted_proxy`` whether an
# ``X-Forwarded-Host`` from this peer may be believed. Off loopback the answer
# is "only for an operator-listed proxy", which is what makes forging a front
# door from another machine impossible. ``configure_trusted_proxies`` is the one
# way that list is ever non-empty, so it stays with the guard it feeds.
_trusted_proxies: List["ipaddress._BaseNetwork"] = []


def configure_trusted_proxies(values) -> int:
    """Set the trusted-proxy allowlist from IPs / CIDRs. Returns the count parsed.

    Accepts a comma-separated string or an iterable of IPs/CIDRs. Invalid entries
    are skipped. Passing an empty value disables forwarded-header trust entirely.
    """
    global _trusted_proxies
    if isinstance(values, str):
        items = [v.strip() for v in values.split(",")]
    else:
        items = [str(v).strip() for v in (values or [])]
    networks: List["ipaddress._BaseNetwork"] = []
    for item in items:
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            quiet()
            continue
    _trusted_proxies = networks
    return len(networks)


def _peer_is_trusted_proxy(peer: str) -> bool:
    if not peer or not _trusted_proxies:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_proxies)


SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "session",
    "private_key",
    "client_secret",
    "webhook",
    "dsn",
    "credential",
)

SECRET_TEXT_PATTERNS = [
    re.compile(r"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(xai-[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(gsk_[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(ghp_[A-Za-z0-9_]{30,})\b"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]

TELEGRAM_TOKEN_WITH_BOT_RE = re.compile(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b")
TELEGRAM_TOKEN_BARE_RE = re.compile(r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")


def _is_secret_key(key: Any) -> bool:
    lowered = str(key or "").lower().replace("-", "_")
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def redact_secret_text(text: str) -> str:
    """Redact known secret shapes from user-visible text, logs, and audit data."""

    if not text:
        return ""
    redacted = str(text)
    redacted = TELEGRAM_TOKEN_WITH_BOT_RE.sub(r"bot\1:REDACTED", redacted)
    redacted = TELEGRAM_TOKEN_BARE_RE.sub(r"bot\1:REDACTED", redacted)
    for pattern in SECRET_TEXT_PATTERNS:
        def repl(match: re.Match) -> str:
            if len(match.groups()) >= 2:
                return f"{match.group(1)}=[REDACTED_SECRET]"
            return "[REDACTED_SECRET]"

        redacted = pattern.sub(repl, redacted)
    return redacted


def redact_secrets(value: Any) -> Any:
    """Recursively redact secret-like values before logs, audit, or API previews."""

    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for key, item in value.items():
            out[key] = "[REDACTED_SECRET]" if _is_secret_key(key) else redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


# ── Per-user token-bucket rate limiting ──────────────────────────────────────
_RATE_LIMITS = {
    "chat":   (30, 0.5),
    "agent":  (10, 0.1),
    "upload": (20, 0.2),
}
_rate_buckets: Dict[str, Dict[str, float]] = {}
_user_rate_lock = threading.Lock()


def enforce_rate_limit(email: str, bucket_key: str, *, enabled: bool = True) -> None:
    if not enabled or not email:
        return
    cap, refill = _RATE_LIMITS.get(bucket_key, (60, 1.0))
    key = f"{email}:{bucket_key}"
    now = time.time()
    with _user_rate_lock:
        bucket = _rate_buckets.get(key)
        if bucket is None:
            _rate_buckets[key] = {"tokens": cap - 1, "ts": now}
            return
        elapsed = now - bucket["ts"]
        bucket["tokens"] = min(cap, bucket["tokens"] + elapsed * refill)
        bucket["ts"] = now
        if bucket["tokens"] < 1:
            retry_after = max(1, int((1 - bucket["tokens"]) / refill))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {bucket_key}. Retry after {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        bucket["tokens"] -= 1
