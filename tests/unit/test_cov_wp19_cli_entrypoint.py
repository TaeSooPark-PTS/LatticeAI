"""wp19: the ``LTCAI`` command line entrypoint.

Everything here is I/O the CLI performs *around* the server: address discovery,
the startup banner, the ``doctor`` preflight, the cloudflared tunnel bootstrap,
and argument handling in ``main``. None of it may run for real in a test, so
each collaborating module is swapped at the *entrypoint module's* own binding
(``entrypoint.socket``, ``entrypoint.subprocess``, ...). That keeps the stub
local to the test — no global ``sys.platform`` or ``shutil.which`` rewiring —
and ``monkeypatch`` restores the real module afterwards.

Two seams matter for determinism: the tunnel poll loop is driven by a virtual
clock instead of ``time.sleep``, and ``uvicorn.run`` is replaced through
``sys.modules`` (the idiom ``tests/unit/test_cli_privacy.py`` established), so
no test ever binds a port or spawns a process.
"""

from __future__ import annotations

import os
import runpy
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List

import pytest

import latticeai.cli.entrypoint as entrypoint
from latticeai.cli.runtime import _apply_extra_path, _load_env_file


# ── stubs ────────────────────────────────────────────────────────────────────
class _FakeUdpSocket:
    """Just enough socket for the ``connect to 8.8.8.8`` local-address probe."""

    def __init__(self, sockname=("10.1.2.3", 51234)) -> None:
        self._sockname = sockname
        self.connected: List[Any] = []
        self.closed = False

    def connect(self, address) -> None:
        self.connected.append(address)

    def getsockname(self):
        return self._sockname

    def close(self) -> None:
        self.closed = True


def _socket_stub(*, getaddrinfo, socket_factory=None) -> SimpleNamespace:
    return SimpleNamespace(
        AF_INET="af_inet",
        SOCK_DGRAM="sock_dgram",
        gethostname=lambda: "workstation.local",
        getaddrinfo=getaddrinfo,
        socket=socket_factory or (lambda family, kind: _FakeUdpSocket()),
    )


def _addrinfo(*addresses):
    return [(2, 1, 6, "", (address, 0)) for address in addresses]


class _VirtualClock:
    """A monotonic clock the tunnel poll loop advances by sleeping."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: List[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _RecordingThread:
    def __init__(self, started: List[Dict[str, Any]], **kwargs: Any) -> None:
        self._started = started
        self._kwargs = kwargs

    def start(self) -> None:
        self._started.append(self._kwargs)


def _threading_stub(started: List[Dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        Thread=lambda **kwargs: _RecordingThread(started, **kwargs)
    )


def _urllib_stub(*, urlopen=None, urlretrieve=None) -> SimpleNamespace:
    return SimpleNamespace(
        parse=SimpleNamespace(
            urlencode=lambda mapping: "&".join(
                f"{key}={value}" for key, value in mapping.items()
            )
        ),
        request=SimpleNamespace(
            Request=lambda url, data=None: SimpleNamespace(url=url, data=data),
            urlopen=urlopen or (lambda request, timeout=None: None),
            urlretrieve=urlretrieve or (lambda url, dest: None),
        ),
    )


# ── cli.runtime helpers ──────────────────────────────────────────────────────
def test_load_env_file_fills_only_unset_keys_and_skips_noise(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "not-an-assignment",
                'LATTICEAI_WP19_QUOTED="quoted value"',
                "  LATTICEAI_WP19_PLAIN = plain  ",
                "LATTICEAI_WP19_PRESET=from-file",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("LATTICEAI_WP19_QUOTED", raising=False)
    monkeypatch.delenv("LATTICEAI_WP19_PLAIN", raising=False)
    monkeypatch.setenv("LATTICEAI_WP19_PRESET", "from-environment")

    _load_env_file(env_file)

    assert os.environ["LATTICEAI_WP19_QUOTED"] == "quoted value"
    assert os.environ["LATTICEAI_WP19_PLAIN"] == "plain"
    # An existing environment value always wins over the file.
    assert os.environ["LATTICEAI_WP19_PRESET"] == "from-environment"
    monkeypatch.delenv("LATTICEAI_WP19_QUOTED")
    monkeypatch.delenv("LATTICEAI_WP19_PLAIN")


def test_load_env_file_is_a_no_op_when_the_file_is_absent(tmp_path):
    before = dict(os.environ)

    _load_env_file(tmp_path / "does-not-exist.env")

    assert dict(os.environ) == before


def test_apply_extra_path_returns_early_without_the_env_var(monkeypatch):
    monkeypatch.delenv("LATTICEAI_EXTRA_PATH", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    _apply_extra_path()

    assert os.environ["PATH"] == "/usr/bin"


def test_apply_extra_path_prepends_existing_directories_in_order(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", str(second)]))
    monkeypatch.setenv(
        "LATTICEAI_EXTRA_PATH",
        os.pathsep.join([str(first), str(second), str(tmp_path / "absent")]),
    )

    _apply_extra_path()

    # `first` is new so it is prepended; `second` is already on PATH and the
    # missing directory is dropped entirely.
    assert os.environ["PATH"].split(os.pathsep) == [
        str(first),
        "/usr/bin",
        str(second),
    ]


# ── _local_ips ───────────────────────────────────────────────────────────────
def test_local_ips_keeps_routable_v4_addresses_once(monkeypatch):
    monkeypatch.setattr(
        entrypoint,
        "socket",
        _socket_stub(
            getaddrinfo=lambda host, port: _addrinfo(
                "127.0.0.1", "192.168.0.9", "192.168.0.9", "fe80::1", "10.0.0.4"
            )
        ),
    )

    # Loopback and IPv6 are dropped, and the repeat is not listed twice.
    assert entrypoint._local_ips() == ["192.168.0.9", "10.0.0.4"]


def test_local_ips_falls_back_to_the_outbound_socket_when_lookup_fails(monkeypatch):
    probe = _FakeUdpSocket(sockname=("172.16.5.6", 40000))

    def exploding_getaddrinfo(host, port):
        raise OSError("name resolution unavailable")

    monkeypatch.setattr(
        entrypoint,
        "socket",
        _socket_stub(
            getaddrinfo=exploding_getaddrinfo,
            socket_factory=lambda family, kind: probe,
        ),
    )

    assert entrypoint._local_ips() == ["172.16.5.6"]
    assert probe.connected == [("8.8.8.8", 80)]
    assert probe.closed is True


def test_local_ips_is_empty_when_both_strategies_fail(monkeypatch):
    def refuse(family, kind):
        raise OSError("network unreachable")

    monkeypatch.setattr(
        entrypoint,
        "socket",
        _socket_stub(
            getaddrinfo=lambda host, port: _addrinfo("127.0.0.1"),
            socket_factory=refuse,
        ),
    )

    assert entrypoint._local_ips() == []


# ── _print_banner ────────────────────────────────────────────────────────────
def test_banner_lists_lan_addresses_when_bound_to_all_interfaces(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "_local_ips", lambda: ["192.168.0.9"])

    entrypoint._print_banner("0.0.0.0", 4825)

    out = capsys.readouterr().out
    assert "http://localhost:4825" in out
    assert "Network:  http://192.168.0.9:4825" in out
    assert "Add to Home Screen" in out
    assert "Tunnel:" not in out


def test_banner_shows_the_tunnel_url_and_no_lan_hint_on_loopback(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "_local_ips", lambda: ["192.168.0.9"])

    entrypoint._print_banner("127.0.0.1", 4830, "https://demo.trycloudflare.com")

    out = capsys.readouterr().out
    assert "Network:" not in out
    assert "Tunnel:   https://demo.trycloudflare.com" in out
    assert "Anyone on the internet" in out


# ── doctor ───────────────────────────────────────────────────────────────────
def test_doctor_reports_zero_when_every_required_check_passes(
    monkeypatch, tmp_path, capsys
):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    monkeypatch.setattr(entrypoint, "_has_module", lambda name: True)
    monkeypatch.setattr(entrypoint, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LATTICEAI_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for key in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    assert entrypoint.doctor() == 0

    out = capsys.readouterr().out
    assert "[OK] FastAPI" in out
    # `ollama` is absent but optional, so it must not fail the run.
    assert "[OPTIONAL] Ollama binary" in out
    assert "Cloud keys configured: OPENAI_API_KEY" in out


def test_doctor_reports_one_when_a_required_dependency_is_missing(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(entrypoint, "_has_module", lambda name: name != "fastapi")
    monkeypatch.setattr(
        entrypoint, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/ollama")
    )
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LATTICEAI_STATIC_DIR", str(tmp_path / "missing-static"))
    for key in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert entrypoint.doctor() == 1

    out = capsys.readouterr().out
    assert "[MISS] FastAPI" in out
    assert "[MISS] Static UI" in out
    assert "[OK] Ollama binary" in out
    assert "Cloud keys configured: none" in out


# ── cloudflared download plumbing ────────────────────────────────────────────
@pytest.mark.parametrize(
    ("platform_name", "machine", "expected"),
    [
        ("darwin", "arm64", "cloudflared-darwin-arm64"),
        ("darwin", "x86_64", "cloudflared-darwin-amd64"),
        ("win32", "amd64", "cloudflared-windows-amd64.exe"),
        ("linux", "aarch64", "cloudflared-linux-arm64"),
        ("linux", "x86_64", "cloudflared-linux-amd64"),
    ],
)
def test_cloudflared_url_matches_the_host_platform(
    monkeypatch, platform_name, machine, expected
):
    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(platform=platform_name))
    monkeypatch.setattr(
        entrypoint, "platform", SimpleNamespace(machine=lambda: machine.upper())
    )

    url = entrypoint._cloudflared_url()

    assert url.endswith("/" + expected)
    assert url.startswith("https://github.com/cloudflare/cloudflared/releases")


def test_cloudflared_bin_only_carries_an_exe_suffix_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(platform="linux"))
    assert entrypoint._cloudflared_bin().name == "cloudflared"

    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(platform="win32"))
    windows_path = entrypoint._cloudflared_bin()
    assert windows_path.name == "cloudflared.exe"
    assert windows_path.parent.name == "bin"


def test_ensure_cloudflared_prefers_a_binary_already_on_path(monkeypatch):
    monkeypatch.setattr(
        entrypoint, "shutil", SimpleNamespace(which=lambda name: "/opt/bin/cloudflared")
    )

    assert entrypoint._ensure_cloudflared() == "/opt/bin/cloudflared"


def test_ensure_cloudflared_reuses_a_previous_download(monkeypatch, tmp_path):
    dest = tmp_path / "bin" / "cloudflared"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"binary")
    monkeypatch.setattr(entrypoint, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setattr(entrypoint, "_cloudflared_bin", lambda: dest)

    assert entrypoint._ensure_cloudflared() == str(dest)


def test_ensure_cloudflared_downloads_and_marks_the_binary_executable(
    monkeypatch, tmp_path, capsys
):
    dest = tmp_path / "bin" / "cloudflared"
    downloaded: List[Any] = []

    def fake_urlretrieve(url, target):
        downloaded.append(url)
        Path(target).write_bytes(b"#!/bin/sh\n")

    monkeypatch.setattr(entrypoint, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setattr(entrypoint, "_cloudflared_bin", lambda: dest)
    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        entrypoint, "platform", SimpleNamespace(machine=lambda: "x86_64")
    )
    monkeypatch.setattr(entrypoint, "urllib", _urllib_stub(urlretrieve=fake_urlretrieve))

    assert entrypoint._ensure_cloudflared() == str(dest)
    assert downloaded and downloaded[0].endswith("cloudflared-linux-amd64")
    assert dest.exists()
    assert os.access(dest, os.X_OK), "the downloaded binary must be executable"
    assert "cloudflared installed at" in capsys.readouterr().out


def test_ensure_cloudflared_returns_empty_when_the_download_fails(
    monkeypatch, tmp_path, capsys
):
    def boom(url, target):
        raise OSError("github unreachable")

    monkeypatch.setattr(entrypoint, "shutil", SimpleNamespace(which=lambda name: None))
    monkeypatch.setattr(
        entrypoint, "_cloudflared_bin", lambda: tmp_path / "bin" / "cloudflared"
    )
    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        entrypoint, "platform", SimpleNamespace(machine=lambda: "x86_64")
    )
    monkeypatch.setattr(entrypoint, "urllib", _urllib_stub(urlretrieve=boom))

    assert entrypoint._ensure_cloudflared() == ""

    out = capsys.readouterr().out
    assert "cloudflared download failed: github unreachable" in out
    assert "Install manually" in out


# ── _send_telegram ───────────────────────────────────────────────────────────
def test_send_telegram_posts_the_message_to_the_bot_api(monkeypatch):
    sent: List[Any] = []
    monkeypatch.setattr(
        entrypoint,
        "urllib",
        _urllib_stub(urlopen=lambda request, timeout=None: sent.append((request, timeout))),
    )

    entrypoint._send_telegram("tok-1", "chat-9", "hello")

    request, timeout = sent[0]
    assert request.url == "https://api.telegram.org/bottok-1/sendMessage"
    assert request.data == b"chat_id=chat-9&text=hello"
    assert timeout == 10


def test_send_telegram_swallows_transport_failures(monkeypatch):
    def refuse(request, timeout=None):
        raise OSError("telegram unreachable")

    monkeypatch.setattr(entrypoint, "urllib", _urllib_stub(urlopen=refuse))

    # A failed notification must never take the server start down with it.
    assert entrypoint._send_telegram("tok", "chat", "hi") is None


# ── _start_tunnel ────────────────────────────────────────────────────────────
def _tunnel_env(monkeypatch, tmp_path, *, token="", chat_id=""):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LATTICEAI_TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("LATTICEAI_TELEGRAM_CHAT_ID", chat_id)


def test_start_tunnel_gives_up_when_cloudflared_is_unavailable(monkeypatch, tmp_path):
    _tunnel_env(monkeypatch, tmp_path)
    monkeypatch.setattr(entrypoint, "_ensure_cloudflared", lambda: "")

    assert entrypoint._start_tunnel(4825) is None


def test_start_tunnel_publishes_the_url_and_notifies_telegram(monkeypatch, tmp_path):
    _tunnel_env(monkeypatch, tmp_path, token="tok-2", chat_id="chat-3")
    clock = _VirtualClock()
    started: List[Dict[str, Any]] = []
    launched: List[List[str]] = []

    def fake_popen(command, stdout=None, stderr=None):
        launched.append(command)
        # Stand in for cloudflared: announce the public URL on its log stream.
        stdout.write("INF |  https://calm-fox-12.trycloudflare.com  |\n")
        stdout.close()
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(entrypoint, "_ensure_cloudflared", lambda: "/opt/cloudflared")
    monkeypatch.setattr(
        entrypoint, "subprocess", SimpleNamespace(Popen=fake_popen, STDOUT="stdout")
    )
    monkeypatch.setattr(entrypoint, "time", clock)
    monkeypatch.setattr(entrypoint, "threading", _threading_stub(started))

    url = entrypoint._start_tunnel(4825)

    assert url == "https://calm-fox-12.trycloudflare.com"
    assert launched == [
        ["/opt/cloudflared", "tunnel", "--url", "http://localhost:4825"]
    ]
    assert (tmp_path / ".latticeai" / "tunnel.log").exists()
    # One poll only: the URL was already in the log.
    assert clock.sleeps == [0.5]
    assert started[0]["target"] is entrypoint._send_telegram
    assert started[0]["args"][:2] == ("tok-2", "chat-3")
    assert "https://calm-fox-12.trycloudflare.com" in started[0]["args"][2]
    assert started[0]["daemon"] is True


def test_start_tunnel_skips_the_notification_without_telegram_credentials(
    monkeypatch, tmp_path
):
    _tunnel_env(monkeypatch, tmp_path, token="tok-only", chat_id="")
    clock = _VirtualClock()
    started: List[Dict[str, Any]] = []

    def fake_popen(command, stdout=None, stderr=None):
        stdout.write("https://quiet-owl-7.trycloudflare.com\n")
        stdout.close()
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(entrypoint, "_ensure_cloudflared", lambda: "/opt/cloudflared")
    monkeypatch.setattr(
        entrypoint, "subprocess", SimpleNamespace(Popen=fake_popen, STDOUT="stdout")
    )
    monkeypatch.setattr(entrypoint, "time", clock)
    monkeypatch.setattr(entrypoint, "threading", _threading_stub(started))

    assert entrypoint._start_tunnel(4825) == "https://quiet-owl-7.trycloudflare.com"
    assert started == []


def test_start_tunnel_returns_none_when_the_log_never_yields_a_url(
    monkeypatch, tmp_path
):
    _tunnel_env(monkeypatch, tmp_path, token="tok", chat_id="chat")
    clock = _VirtualClock()
    started: List[Dict[str, Any]] = []

    def fake_popen(command, stdout=None, stderr=None):
        # cloudflared died before writing anything and took its log with it.
        stdout.close()
        Path(tmp_path / ".latticeai" / "tunnel.log").unlink()
        return SimpleNamespace(pid=2)

    monkeypatch.setattr(entrypoint, "_ensure_cloudflared", lambda: "/opt/cloudflared")
    monkeypatch.setattr(
        entrypoint, "subprocess", SimpleNamespace(Popen=fake_popen, STDOUT="stdout")
    )
    monkeypatch.setattr(entrypoint, "time", clock)
    monkeypatch.setattr(entrypoint, "threading", _threading_stub(started))

    assert entrypoint._start_tunnel(4825) is None
    # It polled for the full 30s budget before giving up, and told nobody.
    assert sum(clock.sleeps) == pytest.approx(30.0)
    assert started == []


# ── main ─────────────────────────────────────────────────────────────────────
def _stable_process(monkeypatch, tmp_path) -> List[Dict[str, Any]]:
    """Pin cwd, env and uvicorn so ``main`` cannot escape the test."""
    served: List[Dict[str, Any]] = []
    monkeypatch.chdir(Path.cwd())
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.delenv("LATTICEAI_EXTRA_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for key, value in (
        ("LATTICEAI_HOST", "127.0.0.1"),
        ("LATTICEAI_PORT", "4825"),
        ("LATTICEAI_TUNNEL", "false"),
        ("LATTICEAI_CORS_ALLOW_NETWORK", "false"),
        ("LATTICEAI_REQUIRE_AUTH", "false"),
        ("LATTICEAI_ENABLE_TELEGRAM", "false"),
        ("LATTICEAI_TELEGRAM_BOT_TOKEN", ""),
        ("LATTICEAI_TELEGRAM_CHAT_ID", ""),
    ):
        monkeypatch.setenv(key, value)
    # A real module object, not a namespace: ``doctor`` probes it through
    # ``importlib.util.find_spec``, which rejects a module without a __spec__.
    fake_uvicorn = ModuleType("uvicorn")
    fake_uvicorn.__spec__ = ModuleSpec("uvicorn", loader=None)
    fake_uvicorn.run = lambda app, **kwargs: served.append({"app": app, **kwargs})
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return served


def test_main_doctor_subcommand_exits_with_the_doctor_status(monkeypatch, tmp_path):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(entrypoint, "doctor", lambda: 3)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "doctor"])

    with pytest.raises(SystemExit) as exited:
        entrypoint.main()

    assert exited.value.code == 3
    assert served == [], "doctor must never start the server"


def test_main_warns_that_the_tunnel_env_flag_is_ignored(monkeypatch, tmp_path, capsys):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setenv("LATTICEAI_TUNNEL", "yes")
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--port", "4901"])
    monkeypatch.setattr(entrypoint, "_start_tunnel", lambda port: pytest.fail("no tunnel"))

    entrypoint.main()

    assert "LATTICEAI_TUNNEL is ignored" in capsys.readouterr().out
    assert served[0]["host"] == "127.0.0.1"
    assert served[0]["port"] == 4901


def test_main_tunnel_flag_rebinds_to_all_interfaces_and_hardens_defaults(
    monkeypatch, tmp_path, capsys
):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--tunnel", "--port", "4902"])
    monkeypatch.setattr(
        entrypoint, "_start_tunnel", lambda port: "https://sunny-cat-3.trycloudflare.com"
    )

    entrypoint.main()

    assert served == [
        {
            "app": "server:app",
            "host": "0.0.0.0",
            "port": 4902,
            "reload": False,
            "log_level": "info",
        }
    ]
    assert os.environ["LATTICEAI_HOST"] == "0.0.0.0"
    out = capsys.readouterr().out
    assert "Starting Cloudflare tunnel" in out
    assert "https://sunny-cat-3.trycloudflare.com" in out


def test_main_starts_without_a_tunnel_when_the_url_is_not_obtained(
    monkeypatch, tmp_path, capsys
):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--tunnel", "--reload"])
    monkeypatch.setattr(entrypoint, "_start_tunnel", lambda port: None)

    entrypoint.main()

    assert served[0]["reload"] is True
    assert served[0]["host"] == "0.0.0.0"
    assert "Tunnel URL not obtained" in capsys.readouterr().out


def test_main_sends_the_local_startup_notification_when_telegram_is_enabled(
    monkeypatch, tmp_path
):
    served = _stable_process(monkeypatch, tmp_path)
    started: List[Dict[str, Any]] = []
    monkeypatch.setenv("LATTICEAI_ENABLE_TELEGRAM", "on")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_BOT_TOKEN", "tok-4")
    monkeypatch.setenv("LATTICEAI_TELEGRAM_CHAT_ID", "chat-5")
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--port", "4903"])
    monkeypatch.setattr(entrypoint, "threading", _threading_stub(started))

    entrypoint.main()

    assert served[0]["port"] == 4903
    assert started[0]["target"] is entrypoint._send_telegram
    assert started[0]["args"][:2] == ("tok-4", "chat-5")
    assert "http://localhost:4903" in started[0]["args"][2]


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_running_the_module_as_a_script_reaches_main(monkeypatch, tmp_path):
    """``python -m latticeai.cli.entrypoint`` must call ``main``.

    ``runpy`` re-executes the module, so the freshly bound ``main`` is the real
    one — the ``doctor`` subcommand is used because it is the only path that
    terminates before any server bootstrap.
    """
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setenv("LATTICEAI_STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["LTCAI", "doctor"])

    with pytest.raises(SystemExit) as exited:
        runpy.run_module("latticeai.cli.entrypoint", run_name="__main__")

    assert exited.value.code in (0, 1)
    assert served == []
