"""wpb02 branch coverage — hardware detection in ``latticeai/setup/wizard.py``.

Detection is entirely host-shaped, so every branch is driven through the
module's own seams (``setup.platform``, ``setup._cmd``, ``setup.Path``,
``builtins.open``) — the same technique the wizard's existing tests use, so a
macOS laptop and an ubuntu runner execute identical lines.

What is exercised here is the *absent* side of each probe: an OS that is
neither Darwin, Windows nor Linux; a ``/proc/cpuinfo`` with no model or flag
line; a ``wmic`` answer that never mentions total memory; a ``hostinfo`` that
does not report memory; a ``/proc/meminfo`` with no ``MemTotal``; and a Mac
display report with no chipset line.
"""

from __future__ import annotations

import builtins
import io
import platform
from pathlib import Path
from typing import Any, Dict, Optional

from latticeai.setup import wizard as setup


class _ModuleShim:
    """Stand-in for an imported module: overrides some names, delegates the rest."""

    def __init__(self, real: Any, **overrides: Any) -> None:
        self.__dict__["_real"] = real
        self.__dict__["_overrides"] = overrides

    def __getattr__(self, name: str) -> Any:
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_real"], name)


def _fake_cmd(mapping: Dict[str, str], default: str = ""):
    """Replacement for ``setup._cmd`` that answers by substring of the argv."""

    def runner(args, timeout=10):
        joined = " ".join(str(part) for part in args)
        for needle, value in mapping.items():
            if needle in joined:
                return value
        return default

    return runner


def _patch_paths(monkeypatch, mapping: Dict[str, str]) -> None:
    """Route specific ``Path(x).read_text()`` calls to canned text."""
    real_path = Path

    class _FakeReadable:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def read_text(self, *_args: Any, **_kwargs: Any) -> str:
            return self._payload

    def factory(first, *rest):
        if not rest and str(first) in mapping:
            return _FakeReadable(mapping[str(first)])
        return real_path(first, *rest)

    factory.home = real_path.home
    monkeypatch.setattr(setup, "Path", factory)


def _patch_proc_meminfo(monkeypatch, payload: str) -> None:
    real_open = builtins.open

    def fake_open(file, *args: Any, **kwargs: Any):
        if str(file) == "/proc/meminfo":
            return io.StringIO(payload)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def _system(monkeypatch, name: str, machine: str = "x86_64", processor: str = "") -> None:
    monkeypatch.setattr(
        setup,
        "platform",
        _ModuleShim(
            platform,
            system=lambda: name,
            machine=lambda: machine,
            processor=lambda: processor,
        ),
    )


# ── _detect_chip ────────────────────────────────────────────────────────────


def test_an_unrecognized_os_falls_back_to_the_reported_processor(monkeypatch):
    _system(monkeypatch, "FreeBSD", processor="amd64-generic")
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))

    chip = setup._detect_chip()

    assert chip == {
        "name": "amd64-generic",
        "arch": "x86_64",
        "is_apple_silicon": False,
        "gen": None,
    }


def test_a_cpuinfo_without_a_model_name_line_falls_back_to_the_processor(monkeypatch):
    _system(monkeypatch, "Linux", processor="generic")
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": "processor\t: 0\ncpu MHz\t: 2400\n"})

    assert setup._detect_chip()["name"] == "generic"


# ── _detect_cpu ─────────────────────────────────────────────────────────────


def test_a_cpuinfo_without_a_flags_line_reports_no_instructions(monkeypatch):
    _system(monkeypatch, "Linux", processor="generic")
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": "processor\t: 0\ncpu MHz\t: 2400\n"})

    cpu = setup._detect_cpu()

    assert cpu["instructions"] == []
    assert cpu["model"] == "generic"


def test_an_unrecognized_os_reports_no_cpu_instructions(monkeypatch):
    _system(monkeypatch, "FreeBSD", processor="amd64-generic")
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))

    cpu = setup._detect_cpu()

    assert cpu["instructions"] == []
    assert cpu["model"] == "amd64-generic"
    assert cpu["logical_cores"] >= 1


# ── _detect_ram_gb ──────────────────────────────────────────────────────────


def test_windows_ram_falls_through_to_meminfo_when_wmic_says_nothing(monkeypatch):
    _system(monkeypatch, "Windows")
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"ComputerSystem": "Caption=DESKTOP\nDomain=WORKGROUP\n"}),
    )
    _patch_proc_meminfo(monkeypatch, "MemFree: 100 kB\nMemTotal: 16777216 kB\n")

    assert setup._detect_ram_gb() == 16.0


def test_macos_ram_is_zero_when_no_probe_reports_a_number(monkeypatch):
    _system(monkeypatch, "Darwin", machine="arm64")
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "hw.memsize": "",
                "SPHardwareDataType": "Hardware Overview:\n  Chip: Apple M9\n",
                "hostinfo": "Mach kernel version:\n  Darwin\n",
            }
        ),
    )
    _patch_proc_meminfo(monkeypatch, "MemFree: 100 kB\nSwapTotal: 0 kB\n")

    assert setup._detect_ram_gb() == 0.0


# ── _detect_gpu ─────────────────────────────────────────────────────────────


def test_a_mac_display_report_without_a_chipset_line_finds_no_gpu(monkeypatch):
    _system(monkeypatch, "Darwin", machine="arm64")
    monkeypatch.setattr(setup, "_which_any", lambda _binary: None)
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"SPDisplaysDataType": "Graphics/Displays:\n\n    Displays:\n"}),
    )

    gpu = setup._detect_gpu()

    assert gpu["devices"] == []
    assert gpu["vram_mb"] == 0


# ── catalog version filtering ───────────────────────────────────────────────


def test_a_pattern_whose_version_group_has_no_digits_is_skipped(monkeypatch):
    import re

    monkeypatch.setattr(
        setup,
        "_VERSIONED_MODEL_PATTERNS",
        (
            ("nightly", re.compile(r"\b(nightly)\b")),
            ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
        ),
    )
    row = ("nightly gemma-4 build", "mlx-community/gemma-4-12b-it-4bit", 12.0, "mlx", "chat", 16)

    detected: Optional[Any] = setup._catalog_row_family_version(row)

    assert detected == ("gemma", (4,))
