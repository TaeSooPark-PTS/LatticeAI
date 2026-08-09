"""Failure paths of the request-facing guards.

Each test here drives one guard with input it cannot parse — a stored password
hash with no salt separator, a hostname where an address is expected, a peer
that is not an IP at all, a server host that was never configured. These are
the branches an attacker reaches first, and every one of them must fail closed
rather than raise or fall through.
"""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from typing import Dict

from latticeai.core import security
from latticeai.core.csrf import CSRFOriginPolicy
from latticeai.core.security import hash_password, host_is_loopback, verify_password


# ── password verification ─────────────────────────────────────────────────
def test_a_stored_hash_in_the_wrong_shape_is_rejected_not_raised():
    assert verify_password("hunter2", "no-separator-here") is False
    assert verify_password("hunter2", "") is False
    assert verify_password("hunter2", "salt:not-hex") is False


def test_a_well_formed_hash_still_verifies():
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored) is True
    assert verify_password("hunter3", stored) is False


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
def _request(peer: str, headers: Dict[str, str]):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_a_peer_that_is_not_an_address_can_never_be_a_trusted_proxy(monkeypatch):
    """A non-IP peer (a UNIX socket, a bad ASGI server) must not unlock spoofing."""
    monkeypatch.setattr(security, "_trusted_proxies", [ipaddress.ip_network("10.0.0.0/8")])
    spoofed = {"X-Forwarded-For": "203.0.113.7"}

    assert security.client_ip(_request("10.1.2.3", spoofed)) == "203.0.113.7"
    assert security.client_ip(_request("unix-socket-peer", spoofed)) == "unix-socket-peer"


def test_no_configured_proxy_means_the_header_is_ignored(monkeypatch):
    monkeypatch.setattr(security, "_trusted_proxies", [])
    assert security.client_ip(_request("203.0.113.9", {"X-Forwarded-For": "10.0.0.1"})) == "203.0.113.9"


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
