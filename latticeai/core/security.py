"""Password hashing, rate limiting, IP detection, file-magic validation."""

import hashlib
import ipaddress
import re
import secrets
import threading
import time
from typing import Dict, List, Optional

from fastapi import HTTPException


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key_hex = hashed.split(":", 1)
        key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def host_is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_ip(request) -> str:
    for header in ("CF-Connecting-IP", "X-Forwarded-For"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_FILE_MAGIC: Dict[str, List[bytes]] = {
    ".pdf":  [b"%PDF-"],
    ".docx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
    ".pptx": [b"PK\x03\x04"],
    ".zip":  [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif":  [b"GIF87a", b"GIF89a"],
}


def bytes_match_extension(data: bytes, ext: str) -> bool:
    ext = (ext or "").lower()
    signatures = _FILE_MAGIC.get(ext)
    if not signatures:
        return True
    head = data[:16]
    return any(head.startswith(sig) for sig in signatures)


def redact_secret_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(?i)(api[_ -]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{12,})['\"]?",
        r"\b(sk-[A-Za-z0-9_\-]{16,})\b",
        r"\b(xai-[A-Za-z0-9_\-]{16,})\b",
        r"\b(gsk_[A-Za-z0-9_\-]{16,})\b",
    ]
    redacted = str(text)
    for pattern in patterns:
        redacted = re.sub(pattern, lambda m: f"{m.group(1)}=[REDACTED]" if len(m.groups()) > 1 else "[REDACTED]", redacted)
    return redacted


# ── IP-based rate limiting (registration / login) ────────────────────────────
_ip_rate_windows: dict = {}
_ip_rate_lock = threading.Lock()


def check_ip_rate_limit(ip: str, action: str, max_calls: int, window_secs: float) -> None:
    key = (ip, action)
    now = time.time()
    cutoff = now - window_secs
    with _ip_rate_lock:
        calls = [t for t in _ip_rate_windows.get(key, []) if t > cutoff]
        if len(calls) >= max_calls:
            raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
        calls.append(now)
        _ip_rate_windows[key] = calls


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
