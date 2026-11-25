import ipaddress

import pytest

from latticeai.core import security
from latticeai.core.http_origin import effective_host, peer_may_forward
from latticeai.core.security import configure_trusted_proxies


@pytest.fixture(autouse=True)
def _reset_trusted_proxies():
    configure_trusted_proxies("")
    yield
    configure_trusted_proxies("")


def test_loopback_may_forward_without_any_configuration():
    assert peer_may_forward("127.0.0.1") is True
    assert peer_may_forward("::1") is True


def test_an_off_loopback_peer_may_not_forward_by_default():
    assert peer_may_forward("203.0.113.9") is False
    assert (
        effective_host(
            host="worker.local",
            forwarded_host="evil.example",
            peer="203.0.113.9",
        )
        == "worker.local"
    )


def test_an_absent_or_unparseable_peer_may_never_forward():
    assert peer_may_forward(None) is False
    assert peer_may_forward("") is False
    assert peer_may_forward("unix-socket-peer") is False


def test_a_listed_proxy_may_forward_by_exact_ip():
    configure_trusted_proxies("203.0.113.9")
    assert peer_may_forward("203.0.113.9") is True
    assert (
        effective_host(
            host="worker.local",
            forwarded_host="app.example",
            peer="203.0.113.9",
        )
        == "app.example"
    )


def test_a_listed_proxy_may_forward_by_cidr():
    configure_trusted_proxies("10.0.0.0/8, 192.168.0.0/16")
    assert peer_may_forward("10.4.5.6") is True
    assert peer_may_forward("192.168.1.1") is True


def test_a_peer_outside_the_list_still_may_not_forward():
    configure_trusted_proxies("10.0.0.0/8")
    assert peer_may_forward("203.0.113.9") is False


def test_a_non_address_peer_cannot_match_a_configured_network(monkeypatch):
    monkeypatch.setattr(
        security, "_trusted_proxies", [ipaddress.ip_network("10.0.0.0/8")]
    )
    assert security._peer_is_trusted_proxy("10.1.2.3") is True
    assert security._peer_is_trusted_proxy("unix-socket-peer") is False
    assert security._peer_is_trusted_proxy("") is False


def test_configure_skips_invalid_entries_and_empty_disables_trust():
    assert configure_trusted_proxies("10.0.0.0/8, nonsense, 1.1.1.1") == 2
    assert configure_trusted_proxies("") == 0
    assert security._trusted_proxies == []
    assert configure_trusted_proxies(["10.0.0.0/8", "", "nope"]) == 1
    assert configure_trusted_proxies(None) == 0


def test_rotating_the_forwarded_host_never_moves_the_front_door():
    hosts = {
        effective_host(host="worker.local", forwarded_host=claim, peer="203.0.113.50")
        for claim in ("a.example", "b.example", "c.example", "d.example")
    }
    assert hosts == {"worker.local"}
