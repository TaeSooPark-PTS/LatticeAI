"""Failure paths of the request-facing guards.

Each test here drives one guard with input it cannot parse — a hostname where
an address is expected, a peer that is not an IP at all, a server host that
was never configured. These are the branches an attacker reaches first, and
every one of them must fail closed rather than raise or fall through.

Password hashing left with the front door (``lattice-auth`` owns credentials
since v11.6.0); the ``client_ip`` resolver went with it in 11.8.0. What
remains of the forwarded-header rule is the allowlist itself, pinned here and
in ``test_proxy_trust.py``.
"""

from __future__ import annotations

import ipaddress

from latticeai.core import security
from latticeai.core.csrf import CSRFOriginPolicy
from latticeai.core.security import host_is_loopback


# ── loopback detection ────────────────────────────────────────────────────
def test_a_hostname_that_is_not_an_address_is_not_loopback():
    assert host_is_loopback("example.com") is False
    assert host_is_loopback("") is False
    assert host_is_loopback("127.0.0.1.evil.example") is False


def test_the_real_loopback_addresses_still_pass():
    assert host_is_loopback("localhost") is True
    assert host_is_loopback("127.0.0.1") is True
    assert host_is_loopback("::1") is True
    assert host_is_loopback("127.5.5.5") is True


# ── forwarded-header trust ────────────────────────────────────────────────
def test_a_peer_that_is_not_an_address_can_never_be_a_trusted_proxy(monkeypatch):
    """A non-IP peer (a UNIX socket, a bad ASGI server) must not unlock spoofing."""
    monkeypatch.setattr(security, "_trusted_proxies", [ipaddress.ip_network("10.0.0.0/8")])

    assert security._peer_is_trusted_proxy("10.1.2.3") is True
    assert security._peer_is_trusted_proxy("unix-socket-peer") is False


def test_no_configured_proxy_means_the_header_is_ignored(monkeypatch):
    monkeypatch.setattr(security, "_trusted_proxies", [])
    assert security._peer_is_trusted_proxy("203.0.113.9") is False


# ── CSRF origin policy ────────────────────────────────────────────────────
def test_a_blank_server_host_is_skipped_rather_than_trusted_as_an_origin():
    policy = CSRFOriginPolicy(server_host="", server_port=4825)

    hosts = {host for _scheme, host, _port in policy.trusted_origins}
    assert "" not in hosts, "an empty host would match every origin with no host"
    assert {"localhost", "127.0.0.1", "::1"} <= hosts

    denied = policy.evaluate(
        method="POST",
        origin="https://evil.example",
        referer=None,
        host="127.0.0.1:4825",
        cookie_header="session_token=abc123",
        authorization=None,
    )
    assert denied.allowed is False
    assert denied.reason == "cross-site-origin"

    allowed = policy.evaluate(
        method="POST",
        origin="http://localhost:4825",
        referer=None,
        host="localhost:4825",
        cookie_header="session_token=abc123",
        authorization=None,
    )
    assert allowed.allowed is True
    assert allowed.reason == "same-site-or-trusted-origin"
