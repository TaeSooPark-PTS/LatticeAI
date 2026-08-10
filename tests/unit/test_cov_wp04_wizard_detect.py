"""wp04 — helper + environment-detection coverage for latticeai/setup/wizard.py.

The wizard probes hardware through `subprocess`, `/proc`, `platform` and
`shutil`, so almost every branch is gated on the host it runs on. These tests
drive each branch through the module's own seams (`setup.platform`,
`setup._cmd`, `setup.Path`, ...) so a macOS laptop and an ubuntu CI runner
execute the same lines.
"""

import asyncio
import builtins
import importlib.util
import io
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from latticeai.setup import wizard as setup


class _ModuleShim:
    """Stand-in for an imported module: overrides some names, delegates the rest."""

    def __init__(self, real, **overrides):
        self.__dict__["_real"] = real
        self.__dict__["_overrides"] = overrides

    def __getattr__(self, name):
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_real"], name)


def _fake_cmd(mapping, default=""):
    """Replacement for `setup._cmd` that answers by substring of the argv."""

    def runner(args, timeout=10):
        joined = " ".join(str(part) for part in args)
        for needle, value in mapping.items():
            if needle in joined:
                return value
        return default

    return runner


def _patch_paths(monkeypatch, mapping):
    """Route specific `Path(x).read_text()` calls to canned text or an error."""
    real_path = Path

    class _FakeReadable:
        def __init__(self, payload):
            self._payload = payload

        def read_text(self, *args, **kwargs):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    def factory(first, *rest):
        if not rest and str(first) in mapping:
            return _FakeReadable(mapping[str(first)])
        return real_path(first, *rest)

    factory.home = real_path.home
    monkeypatch.setattr(setup, "Path", factory)


def _patch_proc_meminfo(monkeypatch, payload):
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file) == "/proc/meminfo":
            if isinstance(payload, Exception):
                raise payload
            return io.StringIO(payload)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


# ── _cmd ──────────────────────────────────────────────────────────────────────

def test_cmd_returns_empty_string_when_the_probe_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, run=_boom))

    assert setup._cmd(["definitely-missing-binary"]) == ""


def test_cmd_falls_back_to_stderr_when_stdout_is_empty(monkeypatch):
    monkeypatch.setattr(
        setup,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(stdout="", stderr="  err  \n")),
    )

    assert setup._cmd(["probe"]) == "err"


# ── action plans / confirmation tokens ────────────────────────────────────────

def test_action_commands_cover_pip_brew_and_unknown_types():
    pip_commands = setup._action_commands({"type": "pip", "packages": ["mlx-vlm", "vllm"]})
    assert pip_commands == [
        [sys.executable, "-m", "pip", "install", "--upgrade", "mlx-vlm"],
        [sys.executable, "-m", "pip", "install", "--upgrade", "vllm"],
    ]
    assert setup._action_commands({"type": "brew", "package": "ollama"}) == [["brew", "install", "ollama"]]
    assert setup._action_commands({"type": "brew", "package": ""}) == []
    assert setup._action_commands({"type": "url", "url": "https://example.test"}) == []


def test_attach_action_plan_hydrates_only_executable_actions():
    hydrated = setup._attach_action_plan({"type": "brew", "package": "ollama"}, name="engine_ollama")

    assert hydrated["command_plan"]["command_count"] == 1
    assert hydrated["command_plan"]["metadata"] == {"action_type": "brew"}
    assert hydrated["confirmation_token"] == hydrated["command_plan"]["confirmation_token"]

    url_action = {"type": "url", "url": "https://example.test"}
    assert setup._attach_action_plan(url_action, name="x") == url_action
    assert setup._attach_action_plan(None, name="x") is None
    assert setup._action_command_plan({"type": "url"}, name="x") is None


def test_hydrate_install_actions_skips_non_list_groups():
    groups = {
        "components": None,
        "engines": ["not-a-dict", {"id": "engine_ollama", "action": {"type": "brew", "package": "ollama"}}],
        "models": [{"name": "no-id", "action": None}],
    }

    result = setup._hydrate_install_actions(groups)

    assert result["components"] is None
    assert result["engines"][0] == "not-a-dict"
    assert result["engines"][1]["action"]["confirmation_token"]
    assert result["models"][0]["action"] is None


def test_verify_action_confirmation_compares_against_the_plan_token():
    action = {"type": "pip", "packages": ["mlx-vlm"]}
    token = setup._action_command_plan(action, name="engine_mlx")["confirmation_token"]

    assert setup._verify_action_confirmation(action, token, name="engine_mlx") is True
    assert setup._verify_action_confirmation(action, "  " + token + " ", name="engine_mlx") is True
    assert setup._verify_action_confirmation(action, "wrong", name="engine_mlx") is False
    assert setup._verify_action_confirmation(action, None, name="engine_mlx") is False
    # No command to run means nothing to confirm.
    assert setup._verify_action_confirmation({"type": "url"}, None, name="x") is True


# ── module import on Windows ──────────────────────────────────────────────────

def _exec_wizard_clone(name):
    """Execute wizard.py again under a throwaway module name.

    Import-time branches (the Windows PATH block) cannot be reached by calling
    a function, and reloading the shared module would leave every other test
    holding Windows constants.
    """
    spec = importlib.util.spec_from_file_location(name, setup.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_import_extends_common_path_dirs(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/tester/AppData/Local")
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", "C:/Program Files (x86)")

    clone = _exec_wizard_clone("_wp04_wizard_windows_clone")

    assert str(Path("C:/Users/tester/AppData/Local") / "Programs" / "Ollama") in clone.COMMON_PATH_DIRS
    assert str(Path("C:/Program Files") / "Ollama") in clone.COMMON_PATH_DIRS
    assert str(Path("C:/Program Files") / "LM Studio") in clone.COMMON_PATH_DIRS
    assert str(Path("C:/Program Files (x86)") / "NVIDIA Corporation" / "NVSMI") in clone.COMMON_PATH_DIRS
    assert "" not in clone.COMMON_PATH_DIRS
    assert clone.WINDOWS_BINARY_CANDIDATES["ollama"][0].endswith("ollama.exe")


def test_windows_import_drops_the_empty_localappdata_entry(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")

    clone = _exec_wizard_clone("_wp04_wizard_windows_clone_no_localappdata")

    assert "" not in clone.COMMON_PATH_DIRS
    assert str(Path("C:/Program Files") / "Ollama") in clone.COMMON_PATH_DIRS


# ── PATH repair ───────────────────────────────────────────────────────────────

def test_update_env_file_replaces_then_appends(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=1\nLATTICEAI_EXTRA_PATH=/old\n", encoding="utf-8")

    setup._update_env_file(env_file, "LATTICEAI_EXTRA_PATH", "/new")
    setup._update_env_file(env_file, "BRAND_NEW_KEY", "yes")

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["KEEP=1", "LATTICEAI_EXTRA_PATH=/new", "BRAND_NEW_KEY=yes"]


def test_update_env_file_creates_a_missing_file(tmp_path):
    env_file = tmp_path / "fresh.env"

    setup._update_env_file(env_file, "LATTICEAI_EXTRA_PATH", "/opt/bin")

    assert env_file.read_text(encoding="utf-8") == "LATTICEAI_EXTRA_PATH=/opt/bin\n"


def test_persist_extra_path_records_new_existing_dirs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    real_dir = tmp_path / "bin"
    real_dir.mkdir()
    monkeypatch.setattr(setup, "_project_env_file", lambda: env_file)
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")

    setup._persist_extra_path([str(real_dir), str(real_dir), str(tmp_path / "missing")])

    assert os.environ["LATTICEAI_EXTRA_PATH"] == str(real_dir)
    assert env_file.read_text(encoding="utf-8") == "LATTICEAI_EXTRA_PATH=" + str(real_dir) + "\n"


def test_persist_extra_path_writes_nothing_without_real_dirs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(setup, "_project_env_file", lambda: env_file)
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")

    setup._persist_extra_path([str(tmp_path / "nope")])

    assert env_file.exists() is False


def test_project_env_file_points_at_the_repo_root():
    assert setup._project_env_file().name == ".env"


def test_repair_path_for_persists_when_a_binary_becomes_visible(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    monkeypatch.setattr(setup, "_project_env_file", lambda: env_file)
    monkeypatch.setattr(setup, "COMMON_PATH_DIRS", [str(bin_dir)])
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")
    seen = iter([None, str(bin_dir / "ollama")])
    monkeypatch.setattr(setup, "_which_any", lambda binary: next(seen))

    paths = setup.repair_path_for("ollama")

    assert str(bin_dir) in paths
    assert os.environ["LATTICEAI_EXTRA_PATH"] == str(bin_dir)
    assert "LATTICEAI_EXTRA_PATH=" + str(bin_dir) in env_file.read_text(encoding="utf-8")


# ── binary discovery + verification ───────────────────────────────────────────

def test_which_any_falls_back_to_windows_candidates(tmp_path, monkeypatch):
    real_exe = tmp_path / "ollama.exe"
    real_exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: None))
    monkeypatch.setattr(
        setup,
        "WINDOWS_BINARY_CANDIDATES",
        {"ollama": ["", str(tmp_path / "missing.exe"), str(real_exe)]},
    )

    assert setup._which_any("ollama") == str(real_exe)
    assert setup._which_any("lms") is None


def test_which_any_returns_the_path_found_on_path(monkeypatch):
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/usr/local/bin/" + binary))

    assert setup._which_any("ollama") == "/usr/local/bin/ollama"


def test_which_detail_and_component_detail_report_modules(monkeypatch):
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/git" if binary == "git" else None)

    assert setup._which_detail("git") == {"installed": True, "path": "/usr/bin/git"}

    detail = setup._component_detail("mlx", module="json")
    assert detail["module_available"] is True
    assert detail["installed"] is True
    assert detail["official_url"] == setup.OFFICIAL_DOWNLOADS["mlx"]

    missing = setup._component_detail("ollama", "ollama", module="not_a_real_module_wp04")
    assert missing["installed"] is False


def test_package_module_maps_known_names_and_derives_the_rest():
    assert setup._package_module("mlx-vlm") == "mlx_vlm"
    assert setup._package_module("huggingface_hub[cli]") == "huggingface_hub"
    assert setup._package_module("openai-whisper") == "whisper"
    assert setup._package_module("some-other-pkg[extra]") == "some_other_pkg"


def test_verify_binary_reports_a_missing_executable(monkeypatch):
    repaired = []
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: repaired.append(binary) or [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: None)

    ok, message = setup._verify_binary("ollama")

    assert ok is False
    assert "ollama" in message
    assert repaired == ["ollama"]


def test_verify_binary_reads_the_version_banner(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/ollama")
    seen = []

    def _run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="ollama version 1.2.3\ntrailing", stderr="")

    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, run=_run))

    assert setup._verify_binary("ollama") == (True, "ollama version 1.2.3")
    assert seen == [["/usr/bin/ollama", "--version"]]


def test_verify_binary_uses_the_resolved_path_when_output_is_empty(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/lms")
    monkeypatch.setattr(
        setup,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    assert setup._verify_binary("lms", ["version"]) == (True, "/usr/bin/lms")


def test_verify_binary_reports_a_non_zero_exit(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/nvcc")
    monkeypatch.setattr(
        setup,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="broken toolchain")),
    )

    assert setup._verify_binary("nvcc") == (False, "broken toolchain")


def test_verify_binary_reports_a_returncode_when_there_is_no_output(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/nvcc")
    monkeypatch.setattr(
        setup,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=3, stdout="", stderr="")),
    )

    assert setup._verify_binary("nvcc") == (False, "returncode=3")


def test_verify_binary_returns_the_error_when_spawning_fails(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/ollama")

    def _boom(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, run=_boom))

    assert setup._verify_binary("ollama") == (False, "denied")


def test_wait_for_binary_returns_on_the_first_successful_probe(monkeypatch):
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(setup, "time", _ModuleShim(time, time=lambda: next(ticks)))
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: (True, "ollama 1.2.3"))

    assert asyncio.run(setup._wait_for_binary("ollama")) == (True, "ollama 1.2.3")


def test_wait_for_binary_retries_until_the_binary_appears(monkeypatch):
    ticks = iter([0.0, 1.0, 2.0])
    results = iter([(False, "not yet"), (True, "ollama 1.2.3")])
    slept = []

    async def _sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(setup, "time", _ModuleShim(time, time=lambda: next(ticks)))
    monkeypatch.setattr(setup, "asyncio", _ModuleShim(asyncio, sleep=_sleep))
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: next(results))

    assert asyncio.run(setup._wait_for_binary("ollama")) == (True, "ollama 1.2.3")
    assert slept == [2]


def test_wait_for_binary_gives_up_at_the_deadline(monkeypatch):
    ticks = iter([100.0, 100.0])
    monkeypatch.setattr(setup, "time", _ModuleShim(time, time=lambda: next(ticks)))
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: (True, "never asked"))

    ok, message = asyncio.run(setup._wait_for_binary("ollama", seconds=0))

    assert ok is False
    assert "ollama" in message


# ── chip / cpu ────────────────────────────────────────────────────────────────

def test_detect_chip_reads_the_apple_profiler(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"system_profiler": "      Chip: Apple M3\n"}))

    assert setup._detect_chip() == {
        "name": "Apple M3",
        "arch": "arm64",
        "is_apple_silicon": True,
        "gen": 3,
    }


def test_detect_chip_defaults_to_generation_one_without_a_profiler_match(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))

    chip = setup._detect_chip()

    assert chip["name"] == "Apple Silicon"
    assert chip["gen"] == 1


def test_detect_chip_uses_sysctl_on_intel_macs(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "x86_64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"machdep.cpu.brand_string": "Intel(R) Core(TM) i9-9880H"}))

    chip = setup._detect_chip()

    assert chip["name"] == "Intel(R) Core(TM) i9-9880H"
    assert chip["is_apple_silicon"] is False
    assert chip["gen"] is None


def test_detect_chip_parses_wmic_on_windows(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"get Name /value": "\r\nName=AMD Ryzen 9 7950X\r\n"}))

    assert setup._detect_chip()["name"] == "AMD Ryzen 9 7950X"


def test_detect_chip_falls_back_to_processor_when_wmic_is_silent(monkeypatch):
    monkeypatch.setattr(
        setup,
        "platform",
        _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64", processor=lambda: ""),
    )
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))

    assert setup._detect_chip()["name"] == "Unknown CPU"


def test_detect_chip_reads_proc_cpuinfo_on_linux(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": "processor\t: 0\nmodel name\t: Intel Xeon Gold\n"})

    assert setup._detect_chip()["name"] == "Intel Xeon Gold"


def test_detect_chip_survives_an_unreadable_cpuinfo(monkeypatch):
    monkeypatch.setattr(
        setup,
        "platform",
        _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64", processor=lambda: "generic"),
    )
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": OSError("permission denied")})

    assert setup._detect_chip()["name"] == "generic"


def test_detect_cpu_reads_sysctl_on_darwin(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "machdep.cpu.features": "FPU AVX2 FMA NEON UNRELATED",
                "hw.physicalcpu": "8",
                "hw.logicalcpu": "16",
                "system_profiler": "Chip: Apple M3\n",
            }
        ),
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "Apple M3"
    assert cpu["physical_cores"] == 8
    assert cpu["logical_cores"] == 16
    assert cpu["instructions"] == ["avx2", "fma", "neon"]


def test_detect_cpu_keeps_defaults_when_sysctl_counts_are_unparsable(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin", machine=lambda: "arm64"))
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"machdep.cpu.features": "", "hw.physicalcpu": "many", "system_profiler": ""}),
    )

    cpu = setup._detect_cpu()

    assert cpu["physical_cores"] == (os.cpu_count() or 0)
    assert cpu["logical_cores"] == (os.cpu_count() or 0)


def test_detect_cpu_reads_flags_from_proc_cpuinfo(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(
        monkeypatch,
        {"/proc/cpuinfo": "model name\t: Intel Xeon Gold\nflags\t\t: fpu avx avx2 fma sse4_2\n"},
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "Intel Xeon Gold"
    assert cpu["instructions"] == ["avx", "avx2", "fma", "sse4_2"]


def test_detect_cpu_survives_an_unreadable_cpuinfo(monkeypatch):
    monkeypatch.setattr(
        setup,
        "platform",
        _ModuleShim(platform, system=lambda: "Linux", machine=lambda: "x86_64", processor=lambda: "generic"),
    )
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_paths(monkeypatch, {"/proc/cpuinfo": OSError("permission denied")})

    assert setup._detect_cpu()["instructions"] == []


def test_detect_cpu_parses_wmic_and_processor_features_on_windows(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "NumberOfCores": (
                    "Name=AMD Ryzen 9 7950X\n"
                    "NumberOfCores=16\n"
                    "NumberOfLogicalProcessors=32\n"
                    "UnrelatedLine\n"
                ),
                "get Name /value": "Name=AMD Ryzen 9 7950X\n",
            }
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(IsProcessorFeaturePresent=lambda code: code in {6, 10})
            )
        ),
    )

    cpu = setup._detect_cpu()

    assert cpu["model"] == "AMD Ryzen 9 7950X"
    assert cpu["physical_cores"] == 16
    assert cpu["logical_cores"] == 32
    assert cpu["instructions"] == ["sse", "sse2"]


def test_detect_cpu_survives_unparsable_windows_counts_and_missing_ctypes(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows", machine=lambda: "AMD64"))
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "NumberOfCores": "NumberOfCores=lots\nNumberOfLogicalProcessors=plenty\n",
                "get Name /value": "",
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "ctypes", None)

    cpu = setup._detect_cpu()

    assert cpu["physical_cores"] == (os.cpu_count() or 0)
    assert cpu["logical_cores"] == (os.cpu_count() or 0)
    assert cpu["instructions"] == []


# ── ram / disk ────────────────────────────────────────────────────────────────

def test_detect_ram_gb_reads_windows_total_physical_memory(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"TotalPhysicalMemory": "TotalPhysicalMemory=34359738368\n"}))

    assert setup._detect_ram_gb() == 32.0


def test_detect_ram_gb_falls_through_when_windows_reports_garbage(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"TotalPhysicalMemory": "TotalPhysicalMemory=NaN\n"}))
    _patch_proc_meminfo(monkeypatch, "MemFree: 1 kB\nMemTotal:       16384000 kB\n")

    assert setup._detect_ram_gb() == 15.6


def test_detect_ram_gb_reads_sysctl_memsize(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({"hw.memsize": "17179869184"}))

    assert setup._detect_ram_gb() == 16.0


def test_detect_ram_gb_reads_the_darwin_profiler_in_every_unit(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))

    for profiler_text, expected in (
        ("Memory: 1 TB", 1024.0),
        ("Memory: 64 GB", 64.0),
        ("Memory: 512 MB", 0.5),
    ):
        monkeypatch.setattr(
            setup,
            "_cmd",
            _fake_cmd({"hw.memsize": "not-a-number", "system_profiler": profiler_text}),
        )
        assert setup._detect_ram_gb() == expected


def test_detect_ram_gb_falls_back_to_hostinfo(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"hostinfo": "Primary memory available: 96.00 gigabytes"}),
    )

    assert setup._detect_ram_gb() == 96.0


def test_detect_ram_gb_reads_proc_meminfo_on_linux(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_proc_meminfo(monkeypatch, "MemTotal:       16384000 kB\n")

    assert setup._detect_ram_gb() == 15.6


def test_detect_ram_gb_returns_zero_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))
    _patch_proc_meminfo(monkeypatch, OSError("no /proc"))

    assert setup._detect_ram_gb() == 0.0


def test_detect_disk_free_gb_uses_the_windows_system_drive(monkeypatch):
    seen = []

    def _usage(path):
        seen.append(path)
        return SimpleNamespace(free=100 * 1_073_741_824)

    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, disk_usage=_usage))

    assert setup._detect_disk_free_gb() == 100.0
    assert seen == ["C:\\"]


def test_detect_disk_free_gb_returns_zero_when_the_probe_fails(monkeypatch):
    def _boom(path):
        raise OSError("no such volume")

    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, disk_usage=_boom))

    assert setup._detect_disk_free_gb() == 0.0


# ── gpu / cuda / wsl / tools ──────────────────────────────────────────────────

def test_detect_gpu_reads_nvidia_smi_and_skips_malformed_rows(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/nvidia-smi" if binary == "nvidia-smi" else None)
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"query-gpu": "NVIDIA GeForce RTX 4090, 24576\nbroken-row\n\n"}),
    )

    gpu = setup._detect_gpu()

    assert gpu["vendor"] == "nvidia"
    assert gpu["name"] == "NVIDIA GeForce RTX 4090"
    assert gpu["vram_mb"] == 24576
    assert gpu["vram_gb"] == 24.0
    assert gpu["backend"] == "cuda"
    assert len(gpu["devices"]) == 1


def test_detect_gpu_uses_powershell_on_windows_and_dedupes(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(
        setup,
        "_which_any",
        lambda binary: {"nvidia-smi": "/nvidia-smi", "powershell": "/powershell"}.get(binary),
    )
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "query-gpu": "NVIDIA GeForce RTX 4090, 24576",
                "Win32_VideoController": (
                    '[{"Name":"NVIDIA GeForce RTX 4090","AdapterRAM":25769803776},'
                    '{"Name":"Intel UHD Graphics 770","AdapterRAM":1073741824}]'
                ),
            }
        ),
    )

    gpu = setup._detect_gpu()

    names = [device["name"] for device in gpu["devices"]]
    assert names == ["NVIDIA GeForce RTX 4090", "Intel UHD Graphics 770"]
    assert gpu["devices"][1]["vendor"] == "intel"
    assert gpu["devices"][1]["backend"] == "directml/vulkan"
    assert gpu["vendor"] == "nvidia"


def test_detect_gpu_falls_back_to_wmic_when_no_shell_is_available(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "_which_any", lambda binary: None)
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "win32_VideoController": (
                    "Name=AMD Radeon RX 7900\n"
                    "AdapterRAM=21474836480\n"
                    "Name=NVIDIA GeForce RTX 4090\n"
                    "AdapterRAM=25769803776\n"
                    "Name=Mystery Display Device\n"
                    "AdapterRAM=0\n"
                )
            }
        ),
    )

    gpu = setup._detect_gpu()

    assert [device["vendor"] for device in gpu["devices"]] == ["amd", "nvidia", "unknown"]
    assert gpu["devices"][0]["backend"] == "directml/vulkan"
    assert gpu["devices"][1]["backend"] == "cuda"
    assert gpu["devices"][2]["backend"] == "cpu"
    assert gpu["vendor"] == "nvidia"


def test_detect_gpu_reads_the_darwin_display_profiler(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    monkeypatch.setattr(setup, "_which_any", lambda binary: None)
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"SPDisplaysDataType": "Graphics/Displays:\n      Chipset Model: Apple M3\n      Type: GPU\n"}),
    )

    gpu = setup._detect_gpu()

    assert gpu["vendor"] == "apple"
    assert gpu["name"] == "Apple M3"
    assert gpu["backend"] == "metal/mlx"


def test_detect_gpu_reads_lspci_on_linux(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "_which_any", lambda binary: None)
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd(
            {
                "lspci": (
                    "00:1f.3 Audio device: Intel Corporation HDA\n"
                    "01:00.0 VGA compatible controller: NVIDIA Corporation GA102\n"
                    "02:00.0 Display controller: Advanced Micro Devices Radeon\n"
                    "03:00.0 3D controller: Intel Corporation Arc A770\n"
                    "04:00.0 VGA compatible controller: Matrox G200\n"
                )
            }
        ),
    )

    gpu = setup._detect_gpu()

    vendors = [device["vendor"] for device in gpu["devices"]]
    assert vendors == ["nvidia", "amd", "intel"]
    assert gpu["vram_gb"] == 0.0
    assert gpu["backend"] == "cuda"


def test_detect_gpu_reports_nothing_when_no_probe_answers(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "_which_any", lambda binary: None)
    monkeypatch.setattr(setup, "_cmd", _fake_cmd({}))

    assert setup._detect_gpu() == {
        "devices": [],
        "vendor": "none",
        "name": "",
        "vram_mb": 0,
        "vram_gb": 0.0,
        "backend": "cpu",
    }


def test_detect_cuda_reports_driver_and_release_versions(monkeypatch):
    monkeypatch.setattr(
        setup,
        "_which_any",
        lambda binary: {"nvidia-smi": "/nvidia-smi", "nvcc": "/nvcc"}.get(binary),
    )
    monkeypatch.setattr(
        setup,
        "_cmd",
        _fake_cmd({"driver_version": "550.54.15\n", "--version": "Cuda compilation tools, release 12.4, V12.4.99"}),
    )

    cuda = setup._detect_cuda()

    assert cuda["available"] is True
    assert cuda["version"] == "12.4"
    assert cuda["nvidia_smi"] == "/nvidia-smi"


def test_detect_wsl_recognises_a_microsoft_kernel(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    _patch_paths(monkeypatch, {"/proc/version": "Linux version 5.15.0-microsoft-standard-WSL2"})

    assert setup._detect_wsl() == {"is_wsl": True, "version": "2"}


def test_detect_wsl_survives_a_missing_proc_version(monkeypatch):
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    _patch_paths(monkeypatch, {"/proc/version": OSError("no /proc")})

    assert setup._detect_wsl() == {"is_wsl": False, "version": ""}


def test_detect_tools_and_mlx_and_api_keys(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/usr/bin/git" if binary == "git" else None)
    monkeypatch.setattr(setup, "_module_available", lambda module: module == "mlx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

    tools = setup._detect_tools()

    assert tools["git"] is True
    assert tools["ollama"] is False
    assert setup._detect_mlx() == {"available": True, "mlx_vlm": False}
    assert setup._detect_api_keys() == {
        "openai": True,
        "openrouter": False,
        "groq": False,
        "together": False,
    }


def test_scan_environment_composes_every_detector(monkeypatch):
    monkeypatch.setattr(
        setup,
        "platform",
        _ModuleShim(platform, system=lambda: "Darwin", mac_ver=lambda: ("15.3", ("", "", ""), "arm64")),
    )
    monkeypatch.setattr(setup, "_detect_chip", lambda: {"name": "Apple M3", "is_apple_silicon": True})
    monkeypatch.setattr(setup, "_detect_cpu", lambda: {"model": "Apple M3", "logical_cores": 12})
    monkeypatch.setattr(setup, "_detect_gpu", lambda: {"vendor": "apple", "vram_gb": 0.0})
    monkeypatch.setattr(setup, "_detect_cuda", lambda: {"available": False, "version": ""})
    monkeypatch.setattr(setup, "_detect_wsl", lambda: {"is_wsl": False, "version": ""})
    monkeypatch.setattr(setup, "_detect_tools", lambda: {"python3": True, "brew": True})
    monkeypatch.setattr(setup, "_detect_ram_gb", lambda: 36.0)
    monkeypatch.setattr(setup, "_detect_disk_free_gb", lambda: 210.0)
    monkeypatch.setattr(setup, "_which_any", lambda binary: "/opt/homebrew/bin/" + binary)
    monkeypatch.setattr(setup, "_module_available", lambda module: module == "mlx")
    monkeypatch.setenv("PATH", "/opt/homebrew/bin")
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "/opt/extra")

    env = setup.scan_environment()

    assert env["os"] == "Darwin"
    assert env["os_version"] == "15.3"
    assert env["ram_gb"] == 36.0
    assert env["disk_free_gb"] == 210.0
    assert env["components"]["python"]["path"] == "/opt/homebrew/bin/python3"
    assert env["components"]["mlx"]["installed"] is True
    assert env["components"]["mlx_vlm"]["module_available"] is False
    assert env["components"]["cuda"]["available"] is False
    assert env["path"] == {"active": "/opt/homebrew/bin", "extra": "/opt/extra"}
    assert env["mlx"] == {"available": True, "mlx_vlm": False}


# ── model catalog helpers ─────────────────────────────────────────────────────

def test_catalog_row_family_version_ignores_unversioned_rows():
    assert setup._catalog_row_family_version(
        ("mlx-community/gemma-4-12b-it-4bit", "Gemma 4 12B", 7.6, "VLM", "d", 16)
    ) == ("gemma", (4,))
    assert setup._catalog_row_family_version(("acme/mystery-model", "Mystery", 1.0, "t", "d", 4)) is None


def test_filter_lower_family_versions_keeps_the_newest_of_each_family():
    rows = [
        ("acme/mystery-model", "Mystery", 1.0, "t", "d", 4),
        ("vendor/gemma-3-12b", "Gemma 3 12B", 7.0, "VLM", "d", 16),
        ("vendor/gemma-4-12b", "Gemma 4 12B", 7.6, "VLM", "d", 16),
        ("vendor/qwen3-vl-8b", "Qwen3-VL 8B", 4.8, "VLM", "d", 16),
    ]

    kept = [row[0] for row in setup._filter_lower_family_versions(rows)]

    assert kept == ["acme/mystery-model", "vendor/gemma-4-12b", "vendor/qwen3-vl-8b"]


def test_version_tuple_ignores_non_numeric_parts():
    assert setup._version_tuple("4.1") == (4, 1)
    assert setup._version_tuple("v4") == ()


def test_best_model_for_engine_falls_back_to_the_first_row():
    small = "ollama:hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M"
    mid = "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"
    rows = [
        (small, "LFM2.5 2.6B", 1.7, "LLM", "d", 4),
        (mid, "Gemma 4 12B", 7.9, "VLM", "d", 16),
    ]

    # 32GB clears the 24GB tier, and that tier's model is on offer here.
    assert setup._best_model_for_engine("ollama", 32, rows) == mid
    # No tier table for the engine, or no tier this machine clears: first row.
    assert setup._best_model_for_engine("engine-with-no-tiers", 32, rows) == small
    assert setup._best_model_for_engine("ollama", 2, rows) == small
    assert setup._best_model_for_engine("ollama", 32, []) == ""
