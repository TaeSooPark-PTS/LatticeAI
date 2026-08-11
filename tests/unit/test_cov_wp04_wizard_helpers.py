"""wp04 — helper coverage for latticeai/setup/wizard/.

The command probe, the action plans and their confirmation tokens, the
Windows import branch, the PATH repair and the binary discovery/verification
helpers — plus the model-catalog helpers that pick a row. The environment
detectors themselves are the twin suite,
``test_cov_wp04_wizard_detect.py``; both share the split shim and probe
doubles in ``_wizard_common``.
"""

import asyncio
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from latticeai.setup import wizard as setup
from latticeai.setup.wizard import paths as wizard_paths

from ._wizard_common import _ModuleShim, _patch

# ── _cmd ──────────────────────────────────────────────────────────────────────

def test_cmd_returns_empty_string_when_the_probe_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("no such binary")

    _patch(monkeypatch, "subprocess", _ModuleShim(subprocess, run=_boom))

    assert setup._cmd(["definitely-missing-binary"]) == ""


def test_cmd_falls_back_to_stderr_when_stdout_is_empty(monkeypatch):
    _patch(
        monkeypatch,
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
    """Execute the wizard's ``paths`` submodule under a throwaway module name.

    Import-time branches (the Windows PATH block) cannot be reached by calling
    a function, and reloading the shared module would leave every other test
    holding Windows constants.

    v11.3.0: the block moved with ``COMMON_PATH_DIRS`` into
    ``latticeai/setup/wizard/paths.py``. Executing the package ``__init__``
    would only re-import the already-loaded submodule, so this points at the
    file that actually owns the branch. ``paths`` deliberately imports no
    sibling submodule, which is what keeps it executable without a package.
    """
    spec = importlib.util.spec_from_file_location(name, wizard_paths.__file__)
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
    _patch(monkeypatch, "_project_env_file", lambda: env_file)
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")

    setup._persist_extra_path([str(real_dir), str(real_dir), str(tmp_path / "missing")])

    assert os.environ["LATTICEAI_EXTRA_PATH"] == str(real_dir)
    assert env_file.read_text(encoding="utf-8") == "LATTICEAI_EXTRA_PATH=" + str(real_dir) + "\n"


def test_persist_extra_path_writes_nothing_without_real_dirs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    _patch(monkeypatch, "_project_env_file", lambda: env_file)
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")

    setup._persist_extra_path([str(tmp_path / "nope")])

    assert env_file.exists() is False


def test_project_env_file_points_at_the_repo_root():
    assert setup._project_env_file().name == ".env"


def test_repair_path_for_persists_when_a_binary_becomes_visible(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    _patch(monkeypatch, "_project_env_file", lambda: env_file)
    _patch(monkeypatch, "COMMON_PATH_DIRS", [str(bin_dir)])
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LATTICEAI_EXTRA_PATH", "")
    seen = iter([None, str(bin_dir / "ollama")])
    _patch(monkeypatch, "_which_any", lambda binary: next(seen))

    paths = setup.repair_path_for("ollama")

    assert str(bin_dir) in paths
    assert os.environ["LATTICEAI_EXTRA_PATH"] == str(bin_dir)
    assert "LATTICEAI_EXTRA_PATH=" + str(bin_dir) in env_file.read_text(encoding="utf-8")


# ── binary discovery + verification ───────────────────────────────────────────

def test_which_any_falls_back_to_windows_candidates(tmp_path, monkeypatch):
    real_exe = tmp_path / "ollama.exe"
    real_exe.write_text("stub", encoding="utf-8")
    _patch(monkeypatch, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    _patch(monkeypatch, "shutil", _ModuleShim(shutil, which=lambda binary: None))
    _patch(
        monkeypatch,
        "WINDOWS_BINARY_CANDIDATES",
        {"ollama": ["", str(tmp_path / "missing.exe"), str(real_exe)]},
    )

    assert setup._which_any("ollama") == str(real_exe)
    assert setup._which_any("lms") is None


def test_which_any_returns_the_path_found_on_path(monkeypatch):
    _patch(monkeypatch, "shutil", _ModuleShim(shutil, which=lambda binary: "/usr/local/bin/" + binary))

    assert setup._which_any("ollama") == "/usr/local/bin/ollama"


def test_which_detail_and_component_detail_report_modules(monkeypatch):
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/git" if binary == "git" else None)

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
    _patch(monkeypatch, "repair_path_for", lambda binary=None: repaired.append(binary) or [])
    _patch(monkeypatch, "_which_any", lambda binary: None)

    ok, message = setup._verify_binary("ollama")

    assert ok is False
    assert "ollama" in message
    assert repaired == ["ollama"]


def test_verify_binary_reads_the_version_banner(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/ollama")
    seen = []

    def _run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="ollama version 1.2.3\ntrailing", stderr="")

    _patch(monkeypatch, "subprocess", _ModuleShim(subprocess, run=_run))

    assert setup._verify_binary("ollama") == (True, "ollama version 1.2.3")
    assert seen == [["/usr/bin/ollama", "--version"]]


def test_verify_binary_uses_the_resolved_path_when_output_is_empty(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/lms")
    _patch(
        monkeypatch,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    assert setup._verify_binary("lms", ["version"]) == (True, "/usr/bin/lms")


def test_verify_binary_reports_a_non_zero_exit(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/nvcc")
    _patch(
        monkeypatch,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="broken toolchain")),
    )

    assert setup._verify_binary("nvcc") == (False, "broken toolchain")


def test_verify_binary_reports_a_returncode_when_there_is_no_output(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/nvcc")
    _patch(
        monkeypatch,
        "subprocess",
        _ModuleShim(subprocess, run=lambda *a, **k: SimpleNamespace(returncode=3, stdout="", stderr="")),
    )

    assert setup._verify_binary("nvcc") == (False, "returncode=3")


def test_verify_binary_returns_the_error_when_spawning_fails(monkeypatch):
    _patch(monkeypatch, "repair_path_for", lambda binary=None: [])
    _patch(monkeypatch, "_which_any", lambda binary: "/usr/bin/ollama")

    def _boom(*args, **kwargs):
        raise PermissionError("denied")

    _patch(monkeypatch, "subprocess", _ModuleShim(subprocess, run=_boom))

    assert setup._verify_binary("ollama") == (False, "denied")


def test_wait_for_binary_returns_on_the_first_successful_probe(monkeypatch):
    ticks = iter([0.0, 1.0])
    _patch(monkeypatch, "time", _ModuleShim(time, time=lambda: next(ticks)))
    _patch(monkeypatch, "_verify_binary", lambda binary: (True, "ollama 1.2.3"))

    assert asyncio.run(setup._wait_for_binary("ollama")) == (True, "ollama 1.2.3")


def test_wait_for_binary_retries_until_the_binary_appears(monkeypatch):
    ticks = iter([0.0, 1.0, 2.0])
    results = iter([(False, "not yet"), (True, "ollama 1.2.3")])
    slept = []

    async def _sleep(delay):
        slept.append(delay)

    _patch(monkeypatch, "time", _ModuleShim(time, time=lambda: next(ticks)))
    _patch(monkeypatch, "asyncio", _ModuleShim(asyncio, sleep=_sleep))
    _patch(monkeypatch, "_verify_binary", lambda binary: next(results))

    assert asyncio.run(setup._wait_for_binary("ollama")) == (True, "ollama 1.2.3")
    assert slept == [2]


def test_wait_for_binary_gives_up_at_the_deadline(monkeypatch):
    ticks = iter([100.0, 100.0])
    _patch(monkeypatch, "time", _ModuleShim(time, time=lambda: next(ticks)))
    _patch(monkeypatch, "_verify_binary", lambda binary: (True, "never asked"))

    ok, message = asyncio.run(setup._wait_for_binary("ollama", seconds=0))

    assert ok is False
    assert "ollama" in message


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
