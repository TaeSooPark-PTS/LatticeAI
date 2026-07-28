"""client_ip trusted-proxy handling — forwarded headers must not be spoofable.

client_ip is the per-IP rate-limit key. If X-Forwarded-For were trusted
unconditionally, anyone could rotate the header to reset their rate limit. These
tests pin the rule: forwarded headers are honoured ONLY from a configured
trusted proxy; otherwise the peer address wins.
"""

import pytest
from fastapi import HTTPException

from latticeai.core import security
from latticeai.core.security import (
    check_ip_rate_limit,
    client_ip,
    configure_trusted_proxies,
)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, peer, headers=None):
        self.client = _FakeClient(peer) if peer else None
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _reset_trusted_proxies():
    configure_trusted_proxies("")  # default: trust nothing
    yield
    configure_trusted_proxies("")


# ── default (no trusted proxy): forwarded headers are ignored ─────────────────
def test_spoofed_xff_ignored_by_default():
    req = _FakeRequest("203.0.113.9", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(req) == "203.0.113.9"


def test_spoofed_cf_connecting_ip_ignored_by_default():
    req = _FakeRequest("203.0.113.9", {"CF-Connecting-IP": "1.2.3.4"})
    assert client_ip(req) == "203.0.113.9"


def test_peer_used_when_no_headers():
    req = _FakeRequest("198.51.100.7")
    assert client_ip(req) == "198.51.100.7"


def test_unknown_when_no_client():
    req = _FakeRequest(None)
    assert client_ip(req) == "unknown"


# ── trusted proxy: forwarded client IP is honoured ────────────────────────────
def test_trusted_proxy_honored_exact_ip():
    configure_trusted_proxies("203.0.113.9")
    req = _FakeRequest("203.0.113.9", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(req) == "1.2.3.4"


def test_trusted_proxy_honored_cidr():
    configure_trusted_proxies("10.0.0.0/8, 192.168.0.0/16")
    req = _FakeRequest("10.4.5.6", {"X-Forwarded-For": "1.2.3.4, 10.4.5.6"})
    assert client_ip(req) == "1.2.3.4"


def test_untrusted_peer_ignores_xff_even_with_proxies_configured():
    configure_trusted_proxies("10.0.0.0/8")
    req = _FakeRequest("203.0.113.9", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(req) == "203.0.113.9"


def test_trusted_proxy_with_garbage_xff_falls_back_to_peer():
    configure_trusted_proxies("203.0.113.9")
    req = _FakeRequest("203.0.113.9", {"X-Forwarded-For": "not-an-ip"})
    assert client_ip(req) == "203.0.113.9"


# ── parsing ───────────────────────────────────────────────────────────────────
def test_configure_skips_invalid_entries():
    assert configure_trusted_proxies("10.0.0.0/8, nonsense, 1.1.1.1") == 2
    assert configure_trusted_proxies("") == 0
    assert security._trusted_proxies == []


# ── the actual anti-bypass guarantee ──────────────────────────────────────────
def test_rate_limit_cannot_be_bypassed_by_rotating_xff():
    """An attacker rotating X-Forwarded-For keeps the SAME rate-limit key."""
    keys = set()
    for spoof in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4", "5.5.5.5", "6.6.6.6"):
        req = _FakeRequest("203.0.113.50", {"X-Forwarded-For": spoof})
        keys.add(client_ip(req))
    assert keys == {"203.0.113.50"}  # every request maps to one key

    # And that key trips the limit after max_calls, regardless of the spoofing.
    ip = "203.0.113.50"
    for _ in range(5):
        check_ip_rate_limit(ip, "login-bypass-test", max_calls=5, window_secs=300)
    with pytest.raises(HTTPException) as exc:
        check_ip_rate_limit(ip, "login-bypass-test", max_calls=5, window_secs=300)
    assert exc.value.status_code == 429
