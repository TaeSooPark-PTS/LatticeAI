"""model_engines discovery helpers — where an engine lives and whether it is supported.

Every probe here reaches for something the test machine is not allowed to have:
a real ``ollama`` on PATH, a Windows install directory, an LM Studio bundle, an
HTTP endpoint. So each one is exercised against the module's *own* view of
``shutil`` / ``platform`` / ``sys`` / ``urllib`` (``monkeypatch.setattr(
model_engines, ...)``), never the stdlib's. The CI coverage leg is ubuntu, where
the darwin and Windows branches would otherwise be unreachable rather than
merely untested.
"""

from __future__ import annotations

import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError


class _Response:
    """The minimal ``urlopen`` result ``_json_request`` consumes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _fake_urllib(monkeypatch, urlopen) -> None:
    """Replace only ``model_engines``' view of urllib, never the stdlib's."""
    monkeypatch.setattr(
        model_engines,
        "urllib",
        types.SimpleNamespace(
            request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=urlopen),
            error=urllib.error,
        ),
    )


def _fake_platform(monkeypatch, *, system: str = "Linux", machine: str = "x86_64") -> None:
    monkeypatch.setattr(
        model_engines,
        "platform",
        types.SimpleNamespace(system=lambda: system, machine=lambda: machine),
    )


def _fake_sys(monkeypatch, *, sys_platform: str = "linux", version_info=(3, 12, 0)) -> None:
    monkeypatch.setattr(
        model_engines,
        "sys",
        types.SimpleNamespace(
            platform=sys_platform,
            version_info=version_info,
            executable="/fake/python",
        ),
    )


def _fake_which(monkeypatch, table: dict) -> None:
    monkeypatch.setattr(
        model_engines,
        "shutil",
        types.SimpleNamespace(which=lambda name: table.get(name)),
    )


# ── progress payload bridge ──────────────────────────────────────────────────


def test_progress_payload_delegates_to_the_runtime_formatter():
    payload = model_engines._progress_payload(
        "download", "받는 중", percent=42.44, detail="model", indeterminate=False
    )

    assert payload["stage"] == "download"
    assert payload["message"] == "받는 중"
    assert payload["percent"] == 42.4
    assert payload["detail"] == "model"


def test_progress_payload_degrades_to_empty_when_the_runtime_cannot_be_imported(monkeypatch):
    """Progress reporting must never be the reason a download fails."""
    monkeypatch.setitem(sys.modules, "latticeai.services.model_runtime", None)

    assert model_engines._progress_payload("download", "받는 중", percent=10) == {}


# ── binary discovery ─────────────────────────────────────────────────────────


def test_local_binary_prefers_whatever_is_on_path(monkeypatch):
    _fake_which(monkeypatch, {"ollama": "/usr/local/bin/ollama"})

    assert model_engines.local_binary("ollama") == "/usr/local/bin/ollama"


def test_local_binary_falls_back_to_the_windows_install_directory(monkeypatch, tmp_path):
    """Windows ships ollama.exe outside PATH, so PATH-miss is not absence."""
    exe = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    _fake_which(monkeypatch, {})
    _fake_platform(monkeypatch, system="Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert model_engines.local_binary("ollama") == str(exe)


def test_local_binary_is_none_when_no_windows_candidate_exists(monkeypatch, tmp_path):
    _fake_which(monkeypatch, {})
    _fake_platform(monkeypatch, system="Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))

    assert model_engines.local_binary("ollama") is None


def test_local_binary_is_none_off_windows_when_path_misses(monkeypatch):
    _fake_which(monkeypatch, {})
    _fake_platform(monkeypatch, system="Darwin")

    assert model_engines.local_binary("ollama") is None


def test_windows_candidates_cover_each_known_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))

    ollama = model_engines.windows_binary_candidates("ollama")
    lms = model_engines.windows_binary_candidates("lms")
    smi = model_engines.windows_binary_candidates("nvidia-smi")

    assert [p.name for p in ollama] == ["ollama.exe", "ollama.exe"]
    assert [p.name for p in lms] == ["lms.exe", "lms.exe"]
    assert [str(p.parent.parent.parent) for p in smi] == [str(tmp_path / "pf"), str(tmp_path / "pf86")]
    assert model_engines.windows_binary_candidates("wat") == []


def test_windows_candidates_drop_the_localappdata_entry_when_it_is_unset(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert len(model_engines.windows_binary_candidates("ollama")) == 1
    assert len(model_engines.windows_binary_candidates("lms")) == 1


def test_find_lmstudio_cli_prefers_the_cli_on_path(monkeypatch):
    _fake_which(monkeypatch, {"lms": "/usr/local/bin/lms"})

    assert model_engines.find_lmstudio_cli() == "/usr/local/bin/lms"


def test_find_lmstudio_cli_falls_back_to_the_app_bundle(monkeypatch, tmp_path):
    bundled = tmp_path / "lms"
    bundled.write_text("", encoding="utf-8")
    _fake_which(monkeypatch, {})
    _fake_platform(monkeypatch, system="Darwin")
    monkeypatch.setattr(model_engines, "LMSTUDIO_BUNDLED_CLI", bundled)

    assert model_engines.find_lmstudio_cli() == str(bundled)


def test_find_lmstudio_cli_is_none_without_a_cli_or_a_bundle(monkeypatch, tmp_path):
    _fake_which(monkeypatch, {})
    _fake_platform(monkeypatch, system="Darwin")
    monkeypatch.setattr(model_engines, "LMSTUDIO_BUNDLED_CLI", tmp_path / "missing")

    assert model_engines.find_lmstudio_cli() is None


def test_vllm_executable_prefers_path_then_the_metal_venv(monkeypatch, tmp_path):
    metal_bin = tmp_path / "vllm"
    metal_bin.write_text("", encoding="utf-8")
    monkeypatch.setattr(model_engines, "VLLM_METAL_BIN", metal_bin)

    _fake_which(monkeypatch, {"vllm": "/usr/local/bin/vllm"})
    assert model_engines.vllm_executable() == "/usr/local/bin/vllm"

    _fake_which(monkeypatch, {})
    assert model_engines.vllm_executable() == str(metal_bin)


def test_vllm_executable_is_none_without_either(monkeypatch, tmp_path):
    _fake_which(monkeypatch, {})
    monkeypatch.setattr(model_engines, "VLLM_METAL_BIN", tmp_path / "missing")

    assert model_engines.vllm_executable() is None


def test_vllm_metal_python_reports_only_a_real_interpreter(monkeypatch, tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_text("", encoding="utf-8")

    monkeypatch.setattr(model_engines, "VLLM_METAL_PYTHON", interpreter)
    assert model_engines.vllm_metal_python() == str(interpreter)

    monkeypatch.setattr(model_engines, "VLLM_METAL_PYTHON", tmp_path / "missing")
    assert model_engines.vllm_metal_python() is None


# ── json transport ───────────────────────────────────────────────────────────


def test_json_request_parses_a_get_response(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        seen["auth"] = req.get_header("Authorization")
        return _Response(json.dumps({"models": [1, 2]}).encode("utf-8"))

    _fake_urllib(monkeypatch, urlopen)

    payload = model_engines._json_request(
        "http://127.0.0.1:1234/api/v1/models",
        headers={"Authorization": "Bearer lmstudio"},
        timeout=2.5,
    )

    assert payload == {"models": [1, 2]}
    assert seen["method"] == "GET"
    assert seen["timeout"] == 2.5
    assert seen["auth"] == "Bearer lmstudio"


def test_json_request_encodes_a_payload_and_declares_json(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen["body"] = req.data
        seen["content_type"] = req.get_header("Content-type")
        seen["method"] = req.get_method()
        return _Response(b"")

    _fake_urllib(monkeypatch, urlopen)

    assert model_engines._json_request(
        "http://127.0.0.1:1234/x", method="POST", payload={"a": 1}
    ) == {}
    assert json.loads(seen["body"].decode("utf-8")) == {"a": 1}
    assert seen["content_type"] == "application/json"
    assert seen["method"] == "POST"


def test_json_request_treats_a_whitespace_body_as_empty(monkeypatch):
    _fake_urllib(monkeypatch, lambda req, timeout=None: _Response(b"   \n"))

    assert model_engines._json_request("http://127.0.0.1:1234/x") == {}


# ── LM Studio base URLs ──────────────────────────────────────────────────────


def test_lmstudio_api_base_uses_the_runtime_provider_table(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)

    base = model_engines.lmstudio_api_base()

    assert base.startswith("http")
    assert not base.endswith("/")


def test_lmstudio_api_base_prefers_the_environment_override(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:9999/v1/")

    assert model_engines.lmstudio_api_base() == "http://127.0.0.1:9999/v1"


def test_lmstudio_api_base_falls_back_when_the_runtime_is_unimportable(monkeypatch):
    """A circular-import failure must still yield the documented default."""
    monkeypatch.setitem(sys.modules, "latticeai.services.model_runtime", None)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)

    assert model_engines.lmstudio_api_base() == "http://localhost:1234/v1"


def test_lmstudio_native_base_drops_the_openai_suffix(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    assert model_engines.lmstudio_native_api_base() == "http://127.0.0.1:1234"

    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234")
    assert model_engines.lmstudio_native_api_base() == "http://127.0.0.1:1234"


# ── engine support matrix ────────────────────────────────────────────────────


def test_every_engine_but_vllm_is_unconditionally_supported():
    assert model_engines.engine_support_status("ollama") == {"supported": True, "reason": None}


@pytest.mark.parametrize(
    ("sys_platform", "machine", "version_info", "supported", "needle"),
    [
        ("win32", "AMD64", (3, 12, 0), False, "WSL2"),
        ("darwin", "x86_64", (3, 12, 0), False, "Apple Silicon"),
        ("darwin", "arm64", (3, 13, 0), True, "Metal"),
        ("linux", "x86_64", (3, 13, 0), False, "3.13"),
        ("linux", "x86_64", (3, 12, 0), True, None),
    ],
)
def test_vllm_support_depends_on_platform_and_python(
    monkeypatch, sys_platform, machine, version_info, supported, needle
):
    _fake_sys(monkeypatch, sys_platform=sys_platform, version_info=version_info)
    _fake_platform(monkeypatch, machine=machine)

    status = model_engines.engine_support_status("vllm")

    assert status["supported"] is supported
    if needle is None:
        assert status["reason"] is None
    else:
        assert needle in status["reason"]


# ── install command construction ─────────────────────────────────────────────


def test_unknown_engine_has_no_install_command():
    with pytest.raises(ModelRuntimeError) as err:
        model_engines._engine_install_command("not-an-engine")

    assert err.value.status_code == 400


def test_install_command_refuses_when_its_package_manager_is_missing(monkeypatch):
    _fake_which(monkeypatch, {})

    with pytest.raises(ModelRuntimeError) as err:
        model_engines._engine_install_command("ollama")

    assert err.value.status_code == 400
    assert "brew" in str(err.value.detail)


def test_vllm_on_apple_silicon_installs_the_metal_runtime_instead(monkeypatch, tmp_path):
    _fake_sys(monkeypatch, sys_platform="darwin")
    _fake_platform(monkeypatch, system="Darwin", machine="arm64")

    command, cwd, requires_admin = model_engines._engine_install_command("vllm", base_dir=tmp_path)

    assert command[:2] == ["/bin/bash", "-lc"]
    assert "vllm-metal" in command[2]
    assert cwd == str(tmp_path)
    assert requires_admin is False


def test_apt_based_installers_are_marked_as_needing_admin(monkeypatch, tmp_path):
    monkeypatch.setitem(
        model_engines.ENGINE_INSTALLERS,
        "fake-apt-engine",
        {"command": ["apt", "install", "-y", "thing"], "label": "Install thing"},
    )

    command, cwd, requires_admin = model_engines._engine_install_command(
        "fake-apt-engine", base_dir=Path(tmp_path)
    )

    assert command[0] == "apt"
    assert requires_admin is True
    assert cwd == str(tmp_path)
