"""wp13 coverage — ``latticeai.tools.network``.

``network_status`` is a probe: it shells out to four macOS/BSD binaries and
opens a UDP socket toward 8.8.8.8 to learn which interface the default route
uses. None of that may happen in a test, so both seams are replaced — a fake
``subprocess.run`` that returns canned stdout per argv, and a fake socket that
reports a fixed source address. What is exercised for real is the parsing:
which ``ifconfig`` lines become interfaces, which ``inet`` lines become
addresses, when the default-route guess is added, and which address wins as
``local_ip``.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.tools import network as network_module
from latticeai.tools.network import _run_network_command, network_status

_IFCONFIG = """en0: flags=8863<UP,BROADCAST,SMART,RUNNING>
\tinet6 fe80::1%en0 prefixlen 64
\tinet 192.168.0.42 netmask 0xffffff00 broadcast 192.168.0.255
lo0: flags=8049<UP,LOOPBACK,RUNNING>
\tinet 127.0.0.1 netmask 0xff000000
utun3: flags=8051<UP,POINTOPOINT,RUNNING>
\tinet 10.8.0.2 --> 10.8.0.1 netmask 0xffffffff
"""


def _fake_runner(table: Dict[str, str], *, exit_code: int = 0):
    """Return a ``subprocess.run`` stand-in keyed by the first argv element."""

    def fake_run(parts: List[str], **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] > 0
        stdout = table.get(parts[0], "")
        code = 0 if parts[0] in table else exit_code
        return subprocess.CompletedProcess(parts, code, stdout, "")

    return fake_run


class _FakeSocket:
    """Context-manager socket whose ``getsockname`` reports a fixed address."""

    def __init__(self, family, kind, source="192.168.0.42"):
        self.family = family
        self.kind = kind
        self._source = source
        self.connected_to = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def connect(self, address):
        self.connected_to = address

    def getsockname(self):
        return (self._source, 51234)


# ── _run_network_command ─────────────────────────────────────────────────────


def test_run_network_command_returns_trimmed_stdout(monkeypatch) -> None:
    monkeypatch.setattr(
        network_module.subprocess, "run", _fake_runner({"ipconfig": "  10.0.0.9  \n"})
    )

    assert _run_network_command(["ipconfig", "getifaddr", "en0"]) == "10.0.0.9"


def test_run_network_command_returns_blank_on_nonzero_exit(monkeypatch) -> None:
    def fake_run(parts, **kwargs):
        return subprocess.CompletedProcess(parts, 1, "should be discarded", "boom")

    monkeypatch.setattr(network_module.subprocess, "run", fake_run)

    assert _run_network_command(["ifconfig"]) == ""


def test_run_network_command_swallows_a_missing_binary(monkeypatch) -> None:
    def fake_run(parts, **kwargs):
        raise FileNotFoundError(parts[0])

    monkeypatch.setattr(network_module.subprocess, "run", fake_run)

    assert _run_network_command(["networksetup", "-getinfo", "Wi-Fi"], timeout=1) == ""


# ── network_status ───────────────────────────────────────────────────────────


def test_network_status_merges_ipconfig_ifconfig_and_default_route(monkeypatch) -> None:
    monkeypatch.setattr(
        network_module.subprocess,
        "run",
        _fake_runner(
            {
                "ipconfig": "192.168.0.42",
                "ifconfig": _IFCONFIG,
                "curl": "203.0.113.7",
                "networksetup": "IP address: 192.168.0.42",
            }
        ),
    )
    monkeypatch.setattr(network_module.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(
        network_module.socket,
        "socket",
        lambda family, kind: _FakeSocket(family, kind, "10.8.0.2"),
    )

    result = network_status()

    assert result["hostname"] == "test-host"
    # ipconfig answered for every probed interface, so en0/en1/bridge100 are set.
    assert result["local_ips"]["en0"] == "192.168.0.42"
    assert result["local_ips"]["bridge100"] == "192.168.0.42"
    # ifconfig contributed only interfaces ipconfig did not already claim, and
    # never the loopback address.
    assert result["local_ips"]["utun3"] == "10.8.0.2"
    assert "lo0" not in result["local_ips"]
    # The guessed default-route address was already known, so no extra key.
    assert "default_route" not in result["local_ips"]
    assert result["local_ip"] == "192.168.0.42"
    assert result["public_ip"] == "203.0.113.7"
    assert result["wifi_info"].startswith("IP address")
    assert result["ifconfig_available"] is True


def test_network_status_records_an_unseen_default_route(monkeypatch) -> None:
    monkeypatch.setattr(
        network_module.subprocess, "run", _fake_runner({"ifconfig": _IFCONFIG})
    )
    monkeypatch.setattr(network_module.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(
        network_module.socket,
        "socket",
        lambda family, kind: _FakeSocket(family, kind, "172.16.3.4"),
    )

    result = network_status()

    assert result["local_ips"]["default_route"] == "172.16.3.4"
    # en0 came from ifconfig parsing, not ipconfig, and still wins local_ip.
    assert result["local_ip"] == "192.168.0.42"
    assert result["public_ip"] == ""
    assert result["wifi_info"] == ""


def test_network_status_survives_a_refused_udp_probe(monkeypatch) -> None:
    """No route at all: every probe fails and the report is still well formed."""

    def fake_run(parts, **kwargs):
        return subprocess.CompletedProcess(parts, 1, "", "unavailable")

    def refuse(family, kind):
        raise OSError("network is unreachable")

    monkeypatch.setattr(network_module.subprocess, "run", fake_run)
    monkeypatch.setattr(network_module.socket, "gethostname", lambda: "offline-host")
    monkeypatch.setattr(network_module.socket, "socket", refuse)

    result = network_status()

    assert result["hostname"] == "offline-host"
    assert result["local_ips"] == {}
    assert result["local_ip"] == ""
    assert result["ifconfig_available"] is False
    assert "public_ip" in result and result["public_ip"] == ""


def test_network_status_probe_uses_a_datagram_socket(monkeypatch) -> None:
    """The probe must not open a TCP connection to 8.8.8.8."""
    seen: List[_FakeSocket] = []

    def make_socket(family, kind):
        sock = _FakeSocket(family, kind, "192.0.2.55")
        seen.append(sock)
        return sock

    monkeypatch.setattr(network_module.subprocess, "run", _fake_runner({}))
    monkeypatch.setattr(network_module.socket, "gethostname", lambda: "probe-host")
    monkeypatch.setattr(network_module.socket, "socket", make_socket)

    result = network_status()

    assert len(seen) == 1
    assert seen[0].family == socket.AF_INET
    assert seen[0].kind == socket.SOCK_DGRAM
    assert seen[0].connected_to == ("8.8.8.8", 80)
    assert result["local_ip"] == "192.0.2.55"


@pytest.mark.parametrize("noise", ["", "no addresses here", "\tinet6 fe80::1%en0"])
def test_network_status_ignores_ifconfig_lines_without_an_ipv4_address(
    monkeypatch, noise: str
) -> None:
    monkeypatch.setattr(
        network_module.subprocess, "run", _fake_runner({"ifconfig": noise})
    )
    monkeypatch.setattr(network_module.socket, "gethostname", lambda: "quiet-host")
    monkeypatch.setattr(
        network_module.socket,
        "socket",
        lambda family, kind: _FakeSocket(family, kind, "198.51.100.2"),
    )

    result = network_status()

    assert result["local_ips"] == {"default_route": "198.51.100.2"}
