import hashlib
import ipaddress
import logging
import re
import threading
import time
from typing import Any, Dict, List

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def host_is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_trusted_proxies: List["ipaddress._BaseNetwork"] = []


def configure_trusted_proxies(values) -> int:
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
        except ValueError as exc:
            logger.warning("Invalid trusted proxy entry %r ignored: %s", item, exc)
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
    re.compile(
        r"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization|bearer|client[_ -]?secret|webhook|dsn)\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(xai-[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(gsk_[A-Za-z0-9_\-]{16,})\b"),
    re.compile(r"\b(ghp_[A-Za-z0-9_]{30,})\b"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(postgres(?:ql)?://[^@\s]+:[^@\s]+@[^\s]+)"),
    re.compile(
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"
    ),
]

TELEGRAM_TOKEN_WITH_BOT_RE = re.compile(r"\bbot(\d{5,20}):[A-Za-z0-9_-]{8,}\b")
TELEGRAM_TOKEN_BARE_RE = re.compile(
    r"(?<![A-Za-z0-9_:-])(\d{5,20}):[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)


def _is_secret_key(key: Any) -> bool:
    lowered = str(key or "").lower().replace("-", "_")
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def redact_secret_text(text: str) -> str:

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

    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for key, item in value.items():
            out[key] = (
                "[REDACTED_SECRET]" if _is_secret_key(key) else redact_secrets(item)
            )
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


_RATE_LIMITS = {
    "chat": (30, 0.5),
    "agent": (10, 0.1),
    "upload": (20, 0.2),
}
_MAX_RATE_BUCKETS = 10_000
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
            if len(_rate_buckets) >= _MAX_RATE_BUCKETS:
                stale = [k for k, v in _rate_buckets.items() if now - v["ts"] >= 3600.0]
                for k in stale:
                    _rate_buckets.pop(k, None)
                if len(_rate_buckets) >= _MAX_RATE_BUCKETS:
                    oldest = min(_rate_buckets.items(), key=lambda item: item[1]["ts"])[
                        0
                    ]
                    _rate_buckets.pop(oldest, None)
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
