"""Trusted-proxy allowlist — a forwarded front door must not be spoofable.

``client_ip`` and the per-IP login limiter it keyed left for ``lattice-auth``
with the rest of the front door (v11.6.0, removed here in 11.8.0). The
allowlist they shared did **not**: it is still what
:func:`latticeai.core.http_origin.peer_may_forward` — and through it the CSRF
origin check — asks before believing an ``X-Forwarded-Host``. If that answer
were unconditional, any machine that can reach this worker could name itself
the front door and pass a cross-origin write off as same-origin.

These tests pin the rule on the code that now enforces it: loopback is
believed, an operator-listed proxy is believed, and nothing else is —
including a peer that is not an address at all.
"""

import ipaddress

import pytest

from latticeai.core import security
from latticeai.core.http_origin import effective_host, peer_may_forward
from latticeai.core.security import configure_trusted_proxies


@pytest.fixture(autouse=True)
def _reset_trusted_proxies():
    configure_trusted_proxies("")  # default: trust nothing
    yield
    configure_trusted_proxies("")


# ── default (no trusted proxy): only loopback may forward ────────────────────
def test_loopback_may_forward_without_any_configuration():
    assert peer_may_forward("127.0.0.1") is True
    assert peer_may_forward("::1") is True


def test_an_off_loopback_peer_may_not_forward_by_default():
    assert peer_may_forward("203.0.113.9") is False
    assert effective_host(
        host="worker.local",
        forwarded_host="evil.example",
        peer="203.0.113.9",
    ) == "worker.local"


def test_an_absent_or_unparseable_peer_may_never_forward():
    """A request whose origin we cannot establish is the one to distrust."""
    assert peer_may_forward(None) is False
    assert peer_may_forward("") is False
    assert peer_may_forward("unix-socket-peer") is False


# ── configured proxy: the forwarded authority is honoured ────────────────────
def test_a_listed_proxy_may_forward_by_exact_ip():
    configure_trusted_proxies("203.0.113.9")
    assert peer_may_forward("203.0.113.9") is True
    assert effective_host(
        host="worker.local",
        forwarded_host="app.example",
        peer="203.0.113.9",
    ) == "app.example"


def test_a_listed_proxy_may_forward_by_cidr():
    configure_trusted_proxies("10.0.0.0/8, 192.168.0.0/16")
    assert peer_may_forward("10.4.5.6") is True
    assert peer_may_forward("192.168.1.1") is True


def test_a_peer_outside_the_list_still_may_not_forward():
    configure_trusted_proxies("10.0.0.0/8")
    assert peer_may_forward("203.0.113.9") is False


def test_a_non_address_peer_cannot_match_a_configured_network(monkeypatch):
    """A UNIX socket or a bad ASGI server must not unlock spoofing."""
    monkeypatch.setattr(security, "_trusted_proxies", [ipaddress.ip_network("10.0.0.0/8")])
    assert security._peer_is_trusted_proxy("10.1.2.3") is True
    assert security._peer_is_trusted_proxy("unix-socket-peer") is False
    assert security._peer_is_trusted_proxy("") is False


# ── parsing ──────────────────────────────────────────────────────────────────
def test_configure_skips_invalid_entries_and_empty_disables_trust():
    assert configure_trusted_proxies("10.0.0.0/8, nonsense, 1.1.1.1") == 2
    assert configure_trusted_proxies("") == 0
    assert security._trusted_proxies == []
    assert configure_trusted_proxies(["10.0.0.0/8", "", "nope"]) == 1
    assert configure_trusted_proxies(None) == 0


# ── the actual anti-spoofing guarantee ───────────────────────────────────────
def test_rotating_the_forwarded_host_never_moves_the_front_door():
    """An attacker rotating X-Forwarded-Host keeps the SAME effective host."""
    hosts = {
        effective_host(host="worker.local", forwarded_host=claim, peer="203.0.113.50")
        for claim in ("a.example", "b.example", "c.example", "d.example")
    }
    assert hosts == {"worker.local"}
