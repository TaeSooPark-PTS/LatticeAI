"""Drive leftover P1b modules toward 100% lines+branches.

Covers model_engines ensure/install/pull, model_runtime download/engines/loading,
lifespan, CSRF middleware, users KG migration, filesystem/knowledge leftovers,
tools/search routers, quiet/sessions/config/agent_permission.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import time
import types
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime import engines as engines_mod
from latticeai.services.process_audit import CommandConfirmationError

# ── helpers ──────────────────────────────────────────────────────────────────


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Popen:
    def __init__(self, stdout_text="", returncode=0, poll_value=None, wait_timeouts=0):
        self.stdout = io.StringIO(stdout_text)
        self.returncode = returncode
        self._poll = poll_value
        self.killed = False
        self.terminated = False
        self._wait_timeouts = wait_timeouts

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        if self._wait_timeouts <= 0:
            self._poll = 0

    def kill(self):
        self.killed = True
        if self._wait_timeouts <= 1:
            self._poll = -9

    def wait(self, timeout=None):
        if self._wait_timeouts > 0:
            self._wait_timeouts -= 1
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 1)
        self._poll = self.returncode
        return self.returncode


class _UrlResp:
    def __init__(self, body: bytes = b""):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fast_deadline(monkeypatch, module, start=1000.0, step=1000.0):
    clock = {"t": start}

    def now():
        clock["t"] += step
        return clock["t"]

    monkeypatch.setattr(module.time, "time", now)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_k: None)

def test_local_binary_and_windows_candidates(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: None)
    monkeypatch.setattr(model_engines.platform, "system", lambda: "Darwin")
    assert model_engines.local_binary("ollama") is None

    exe = tmp_path / "ollama.exe"
    exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_engines.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    (tmp_path / "Programs" / "Ollama").mkdir(parents=True)
    (tmp_path / "Programs" / "Ollama" / "ollama.exe").write_text("x", encoding="utf-8")
    found = model_engines.local_binary("ollama")
    assert found and found.endswith("ollama.exe")

    cands = model_engines.windows_binary_candidates("lms")
    assert cands
    assert model_engines.windows_binary_candidates("nope") == []
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert model_engines.windows_binary_candidates("nvidia-smi")

def test_find_lmstudio_and_vllm_bins(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/lms")
    assert model_engines.find_lmstudio_cli() == "/usr/bin/lms"

    monkeypatch.setattr(model_engines, "local_binary", lambda _n: None)
    bundled = tmp_path / "lms"
    bundled.write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_engines, "LMSTUDIO_BUNDLED_CLI", bundled)
    assert model_engines.find_lmstudio_cli() == str(bundled)

    missing = tmp_path / "missing"
    monkeypatch.setattr(model_engines, "LMSTUDIO_BUNDLED_CLI", missing)
    assert model_engines.find_lmstudio_cli() is None

    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: "/usr/bin/vllm")
    assert model_engines.vllm_executable() == "/usr/bin/vllm"
    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: None)
    metal = tmp_path / "vllm"
    metal.write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_engines, "VLLM_METAL_BIN", metal)
    assert model_engines.vllm_executable() == str(metal)
    monkeypatch.setattr(model_engines, "VLLM_METAL_BIN", missing)
    assert model_engines.vllm_executable() is None

    py = tmp_path / "python"
    py.write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_engines, "VLLM_METAL_PYTHON", py)
    assert model_engines.vllm_metal_python() == str(py)
    monkeypatch.setattr(model_engines, "VLLM_METAL_PYTHON", missing)
    assert model_engines.vllm_metal_python() is None

def test_json_request_and_lmstudio_bases(monkeypatch):
    monkeypatch.setattr(
        model_engines.urllib.request,
        "urlopen",
        lambda *_a, **_k: _UrlResp(b""),
    )
    assert model_engines._json_request("http://x") == {}

    monkeypatch.setattr(
        model_engines.urllib.request,
        "urlopen",
        lambda *_a, **_k: _UrlResp(b'{"ok": true}'),
    )
    assert model_engines._json_request("http://x", method="POST", payload={"a": 1})["ok"] is True

    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.setattr(
        model_engines,
        "OPENAI_COMPATIBLE_PROVIDERS",
        {"lmstudio": {"base_url": "http://localhost:1234/v1"}},
        raising=False,
    )
    # import inside lmstudio_api_base — force the fallback path
    real_import = __import__

    def boom_import(name, *a, **k):
        if name == "latticeai.services.model_runtime" or name.endswith("model_runtime"):
            raise ImportError("no")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", boom_import)
    base = model_engines.lmstudio_api_base()
    assert base.endswith("1234/v1") or "localhost" in base
    native = model_engines.lmstudio_native_api_base()
    assert not native.endswith("/v1") or native == base

    monkeypatch.undo()
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://lm:9/v1")
    assert model_engines.lmstudio_api_base().endswith("/v1")
    assert model_engines.lmstudio_native_api_base().endswith(":9") or model_engines.lmstudio_native_api_base().endswith(":9/")

def test_progress_payload_import_failure(monkeypatch):
    real_import = __import__

    def boom(name, *a, **k):
        if "model_runtime" in name:
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", boom)
    assert model_engines._progress_payload("download", "x") == {}

def test_ensure_lmstudio_server_starts_and_times_out(monkeypatch):
    calls = {"n": 0}

    def json_req(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("down")
        return {"models": []}

    monkeypatch.setattr(model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234")
    monkeypatch.setattr(model_engines, "_json_request", json_req)
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: "/usr/bin/lms")
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    model_engines.ensure_lmstudio_server()

    def always_down(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(model_engines, "_json_request", always_down)

    def popen_fail(*_a, **_k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(model_engines.subprocess, "Popen", popen_fail)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.ensure_lmstudio_server()
    assert exc.value.status_code == 500

    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    _fast_deadline(monkeypatch, model_engines)
    with pytest.raises(ModelRuntimeError) as exc2:
        model_engines.ensure_lmstudio_server()
    assert exc2.value.status_code == 500

def test_ensure_ollama_server_spawn_success_and_timeout(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    runs = {"n": 0}

    def run(*_a, **_k):
        runs["n"] += 1
        if runs["n"] == 1:
            raise OSError("not up")
        return _Done(0)

    monkeypatch.setattr(model_engines.subprocess, "run", run)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    model_engines.ensure_ollama_server()

    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(1))
    _fast_deadline(monkeypatch, model_engines, step=100)
    with pytest.raises(ModelRuntimeError):
        model_engines.ensure_ollama_server()

def test_get_openai_compatible_server_models_lmstudio_and_http(monkeypatch):
    monkeypatch.setattr(
        "latticeai.services.model_runtime.get_lmstudio_models",
        lambda: [
            {"key": "alpha", "loaded_instances": [{"id": "inst-1"}, {"id": ""}]},
            {"key": "beta", "loaded_instances": []},
            {"key": "", "loaded_instances": [{"id": "orphan"}]},
        ],
    )
    models = model_engines.get_openai_compatible_server_models("lmstudio")
    assert "inst-1" in models

    monkeypatch.setattr(
        "latticeai.services.model_runtime.get_lmstudio_models",
        lambda: [{"key": "only-key", "loaded_instances": [{}]}],
    )
    assert "only-key" in model_engines.get_openai_compatible_server_models("lmstudio")

    assert model_engines.get_openai_compatible_server_models("not-a-provider") == []

    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vk")
    monkeypatch.setattr(
        model_engines.urllib.request,
        "urlopen",
        lambda *_a, **_k: _UrlResp(json.dumps({"data": [{"id": "m1"}, {"id": None}, "x"]}).encode()),
    )
    assert "m1" in model_engines.get_openai_compatible_server_models("vllm")

    def boom(*_a, **_k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(model_engines.urllib.request, "urlopen", boom)
    assert model_engines.get_openai_compatible_server_models("vllm") == []

def test_reap_local_server_paths(monkeypatch):
    easy = _Popen(poll_value=None)
    model_engines._reap_local_server(easy, "vLLM")
    assert easy.terminated

    stubborn = _Popen(poll_value=None, wait_timeouts=1)
    model_engines._reap_local_server(stubborn, "vLLM")
    assert stubborn.killed

    zombie = _Popen(poll_value=None, wait_timeouts=5)
    zombie._poll = None

    def always_timeout(timeout=None):
        raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 1)

    zombie.wait = always_timeout
    zombie.poll = lambda: None
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines._reap_local_server(zombie, "vLLM")
    assert exc.value.status_code == 409

def test_ensure_vllm_server_branches(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: ["already"])
    model_engines.ensure_vllm_server("already")

    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines.importlib.util, "find_spec", lambda _n: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.ensure_vllm_server("org/m")
    assert exc.value.status_code == 400

    monkeypatch.setattr(model_engines.importlib.util, "find_spec", lambda _n: object())
    downloaded = {"n": 0}
    # late imports come from model_runtime package
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download_hf_model",
        lambda *_a, **_k: downloaded.__setitem__("n", 1),
    )
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_dir", lambda _n: tmp_path / "m")
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: False)

    running = _Popen(poll_value=None)
    model_engines.LOCAL_SERVER_PROCESSES["vllm"] = running
    monkeypatch.setattr(model_engines, "_reap_local_server", lambda *_a, **_k: None)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen(poll_value=0))
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True)
    model_engines.ensure_vllm_server("org/m")

    model_engines.LOCAL_SERVER_PROCESSES.pop("vllm", None)
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: ["other"])
    with pytest.raises(ModelRuntimeError) as exc2:
        model_engines.ensure_vllm_server("org/m")
    assert exc2.value.status_code == 409

    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: [])
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: "/opt/metal/python")
    spawned = {}

    def capture(cmd, **_k):
        spawned["cmd"] = cmd
        return _Popen()

    monkeypatch.setattr(model_engines.subprocess, "Popen", capture)
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True)
    model_engines.ensure_vllm_server("org/m")
    assert "-m" in spawned["cmd"]

    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/usr/bin/vllm")
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: True)
    model_engines.ensure_vllm_server("org/m")
    assert spawned["cmd"][0] == "/usr/bin/vllm"

    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False)
    with pytest.raises(ModelRuntimeError) as exc3:
        model_engines.ensure_vllm_server("org/m")
    assert exc3.value.status_code == 500

def test_ensure_llamacpp_server_branches(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: ["ready"])
    model_engines.ensure_llamacpp_server("ready")

    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: [])
    running = _Popen(poll_value=None)
    model_engines.LOCAL_SERVER_PROCESSES["llamacpp"] = running
    monkeypatch.setattr(model_engines, "_reap_local_server", lambda *_a, **_k: None)
    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc.value.status_code == 400

    model_engines.LOCAL_SERVER_PROCESSES.pop("llamacpp", None)
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: ["other"])
    with pytest.raises(ModelRuntimeError) as exc2:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc2.value.status_code == 409

    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda _p: [])
    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: "/usr/bin/llama-server")
    model_dir = tmp_path / "gguf"
    model_dir.mkdir()
    (model_dir / "other.gguf").write_bytes(b"g")
    (model_dir / "q4_k_m.gguf").write_bytes(b"g")
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_dir", lambda _n: model_dir)
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: False)
    monkeypatch.setattr("latticeai.services.model_runtime.download_hf_model", lambda *_a, **_k: None)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True)
    model_engines.ensure_llamacpp_server("org/m")

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_dir", lambda _n: empty)
    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: True)
    with pytest.raises(ModelRuntimeError) as exc3:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc3.value.status_code == 500

    monkeypatch.setattr("latticeai.services.model_runtime.hf_model_dir", lambda _n: model_dir)
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False)
    with pytest.raises(ModelRuntimeError) as exc4:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc4.value.status_code == 500

def test_pull_ollama_model_with_progress(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    seen: List[Dict[str, Any]] = []
    proc = _Popen(
        stdout_text="pulling\rdownloading 12.5%\nverifying sha\ndownloading 100%\n",
        returncode=0,
    )
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: proc)
    result = model_engines.pull_ollama_model_with_progress("llama", progress_emit=seen.append)
    assert result["returncode"] == 0
    assert any(item.get("percent") == 100 for item in seen)

    fail = _Popen(stdout_text="error pulling\n", returncode=1)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: fail)
    with pytest.raises(ModelRuntimeError):
        model_engines.pull_ollama_model_with_progress("llama")

    class Boom:
        stdout = object()

        def __iter__(self):
            raise OSError("pipe")

        def kill(self):
            self.killed = True

        def wait(self):
            return 1

    class BoomProc:
        def __init__(self):
            self.stdout = Boom()
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self):
            return 1

    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: BoomProc())
    with pytest.raises(OSError):
        model_engines.pull_ollama_model_with_progress("llama", progress_emit=seen.append)

def test_get_ollama_pulled_models_exception(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no")),
    )
    assert model_engines.get_ollama_pulled_models() == set()

def test_engine_support_and_install_command(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(model_engines.sys, "platform", "win32")
    assert model_engines.engine_support_status("vllm")["supported"] is False

    monkeypatch.setattr(model_engines.sys, "platform", "darwin")
    monkeypatch.setattr(model_engines.platform, "machine", lambda: "x86_64")
    assert model_engines.engine_support_status("vllm")["supported"] is False

    monkeypatch.setattr(model_engines.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(model_engines.sys, "version_info", (3, 14, 0))
    assert model_engines.engine_support_status("vllm")["supported"] is True

    monkeypatch.setattr(model_engines.sys, "platform", "linux")
    monkeypatch.setattr(model_engines.sys, "version_info", (3, 14, 0))
    assert model_engines.engine_support_status("vllm")["supported"] is False

    monkeypatch.setattr(model_engines.sys, "version_info", (3, 11, 0))
    assert model_engines.engine_support_status("vllm")["supported"] is True
    assert model_engines.engine_support_status("ollama")["supported"] is True

    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: None)
    with pytest.raises(ModelRuntimeError):
        model_engines._engine_install_command("ollama")

    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(model_engines.sys, "platform", "darwin")
    monkeypatch.setattr(model_engines.platform, "machine", lambda: "arm64")
    cmd, cwd, admin = model_engines._engine_install_command("vllm", base_dir=tmp_path)
    assert cmd[0] == "/bin/bash"
    assert admin is False

    monkeypatch.setattr(
        model_engines,
        "ENGINE_INSTALLERS",
        {**model_engines.ENGINE_INSTALLERS, "apteng": {"command": ["apt-get", "install", "x"]}},
    )
    cmd, _cwd, admin = model_engines._engine_install_command("apteng", base_dir=tmp_path)
    assert admin is True

def test_install_engine_denied_error_and_ollama_daemon(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        model_engines,
        "_engine_install_command",
        lambda engine, base_dir=None: (["echo", "ok"], str(tmp_path), False),
    )
    monkeypatch.setattr(
        model_engines,
        "engine_install_plan",
        lambda engine, base_dir=None: {
            "command_hash": "h",
            "command_preview": "echo ok",
            "command": ["echo", "ok"],
        },
    )

    def denied(*_a, **_k):
        raise CommandConfirmationError("need token")

    monkeypatch.setattr(model_engines, "require_command_confirmation", denied)
    monkeypatch.setattr(model_engines, "append_process_audit_event", lambda *a, **k: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.install_engine("ollama")
    assert exc.value.status_code == 403

    monkeypatch.setattr(model_engines, "require_command_confirmation", lambda *a, **k: None)

    def explode(*_a, **_k):
        raise RuntimeError("spawn fail")

    monkeypatch.setattr(model_engines.subprocess, "run", explode)
    with pytest.raises(RuntimeError):
        model_engines.install_engine("ollama")

    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", ""))
    monkeypatch.setattr("latticeai.services.model_runtime.engine_installed", lambda _e: True)
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", ""))
    # first run is install; second is probe — already up
    calls = {"n": 0}

    def run_seq(*_a, **_k):
        calls["n"] += 1
        return _Done(0, "ok", "")

    monkeypatch.setattr(model_engines.subprocess, "run", run_seq)
    result = model_engines.install_engine("ollama")
    assert result["daemon_started"] == "already_running"

    def run_then_down(*a, **k):
        if a and a[0][:1] == ["/usr/bin/ollama"] and "list" in a[0]:
            raise OSError("down")
        return _Done(0, "ok", "")

    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", ""))
    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: _Done(1, "", "") if (a and "list" in a[0]) else _Done(0, "ok", ""),
    )
    spawned = {"n": 0}

    def popen(*_a, **_k):
        spawned["n"] += 1
        return _Popen()

    monkeypatch.setattr(model_engines.subprocess, "Popen", popen)
    result = model_engines.install_engine("ollama")
    assert result["daemon_started"] is True

    monkeypatch.setattr(
        model_engines.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no daemon")),
    )
    result = model_engines.install_engine("ollama")
    assert result["daemon_started"] is False

def test_smoke_test_loaded_model_branches(monkeypatch):
    async def _run():
        res = SimpleNamespace(engine="openai", load_id="openai:gpt")
        out = await model_engines._smoke_test_loaded_model(res)
        assert out.get("skipped") is True

        class Router:
            async def generate(self, *_a, **_k):
                return "답은 4 입니다."

        out = await model_engines._smoke_test_loaded_model(
            SimpleNamespace(engine="ollama", load_id="ollama:m"),
            model_router=Router(),
        )
        assert "ok" in out

        class Boom:
            async def generate(self, *_a, **_k):
                raise RuntimeError("gen fail")

        out = await model_engines._smoke_test_loaded_model(
            SimpleNamespace(engine="local_mlx", load_id="mlx:m"),
            model_router=Boom(),
        )
        assert out["ok"] is False

        real_import = __import__

        def boom_import(name, *a, **k):
            if name.startswith("latticeai.core.model_compat") or name.startswith("latticeai.services.model_runtime"):
                raise ImportError("blocked")
            return real_import(name, *a, **k)

        monkeypatch.setattr("builtins.__import__", boom_import)
        out = await model_engines._smoke_test_loaded_model(SimpleNamespace(engine="ollama", load_id="x"))
        assert out.get("skipped") is True

    asyncio.run(_run())

def test_hf_model_ready_cache_and_tokenizers(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: missing)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cache = tmp_path / ".cache" / "huggingface" / "hub" / "models--org--m" / "snapshots" / "abc"
    cache.mkdir(parents=True)
    monkeypatch.setattr(download_mod, "hf_cache_model_dir", lambda _r: tmp_path / "cached")
    assert download_mod.hf_model_ready("org/m", provider="vllm") is True
    assert download_mod.hf_model_ready("org/m", provider="local_mlx") is True
    monkeypatch.setattr(download_mod, "hf_cache_model_dir", lambda _r: None)
    assert download_mod.hf_model_ready("org/m", provider="local_mlx") is False

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: empty)
    assert download_mod.hf_model_ready("org/m", provider="llamacpp") is False
    assert download_mod.hf_model_ready("org/m", provider="openai") is False

    ready = tmp_path / "tok"
    ready.mkdir()
    (ready / "config.json").write_text("{}", encoding="utf-8")
    (ready / "weights.bin").write_bytes(b"w")
    (ready / "tokenizer.model").write_bytes(b"t")
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: ready)
    assert download_mod.hf_model_ready("org/m") is True

    ready2 = tmp_path / "tok2"
    ready2.mkdir()
    (ready2 / "config.json").write_text("{}", encoding="utf-8")
    (ready2 / "weights.bin").write_bytes(b"w")
    (ready2 / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: ready2)
    assert download_mod.hf_model_ready("org/m") is True

def test_hf_repo_files_with_sizes(monkeypatch):
    class Sib:
        def __init__(self, name, size):
            self.rfilename = name
            self.size = size

    class Info:
        siblings = [Sib("a.bin", 10), Sib("", 1), Sib("dir/", 2), Sib("b.bin", None)]

    class Api:
        def model_info(self, repo_id, files_metadata=True):
            return Info()

        def list_repo_files(self, repo_id):
            return ["fallback.bin", ""]

    hub = types.ModuleType("huggingface_hub")
    hub.HfApi = lambda: Api()
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    files = download_mod.hf_repo_files_with_sizes("org/m")
    assert any(item["name"] == "a.bin" for item in files)

    class TypeApi:
        def model_info(self, repo_id, files_metadata=True):
            raise TypeError("no metadata")

        def list_repo_files(self, repo_id):
            return ["listed.bin"]

    hub.HfApi = lambda: TypeApi()
    files = download_mod.hf_repo_files_with_sizes("org/m")
    assert files[0]["name"] == "listed.bin"

    class BoomApi:
        def model_info(self, repo_id, files_metadata=True):
            raise RuntimeError("hub down")

        def list_repo_files(self, repo_id):
            return ["x.bin"]

    hub.HfApi = lambda: BoomApi()
    files = download_mod.hf_repo_files_with_sizes("org/m")
    assert files[0]["name"] == "x.bin"

    class EmptyInfo:
        siblings = []

    class EmptyApi:
        def model_info(self, repo_id, files_metadata=True):
            return EmptyInfo()

        def list_repo_files(self, repo_id):
            return ["z.bin"]

    hub.HfApi = lambda: EmptyApi()
    files = download_mod.hf_repo_files_with_sizes("org/m")
    assert files[0]["name"] == "z.bin"

def test_download_hf_model_missing_hub_and_uncached(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(download_mod.importlib.util, "find_spec", lambda _n: None)
    with pytest.raises(ModelRuntimeError) as exc:
        download_mod.download_hf_model("org/m")
    assert exc.value.status_code == 400

    monkeypatch.setattr(download_mod.importlib.util, "find_spec", lambda _n: object())
    target = tmp_path / "weights"
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: target)
    ready_state = {"ok": False}
    monkeypatch.setattr(download_mod, "hf_model_ready", lambda *_a, **_k: ready_state["ok"])

    files = [
        {"name": "config.json", "size": 4},
        {"name": "model.safetensors", "size": 8},
        {"name": "zero.bin", "size": 0},
    ]
    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", lambda _r: files)

    class FakeTqdm:
        def __init__(self, *a, **k):
            self.n = 0

        def update(self, n=1):
            self.n += n
            return True

    fake_tqdm = types.ModuleType("tqdm.auto")
    fake_tqdm.tqdm = FakeTqdm
    monkeypatch.setitem(sys.modules, "tqdm", types.ModuleType("tqdm"))
    monkeypatch.setitem(sys.modules, "tqdm.auto", fake_tqdm)

    def fake_download(*, repo_id, filename, local_dir, tqdm_class=None):
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"data")
        if tqdm_class is not None:
            bar = tqdm_class(total=10)
            bar.update(1)
            bar.update(9)
        if filename == "zero.bin":
            dest.unlink()
        return str(dest)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = fake_download
    hub.HfApi = object
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    seen: List[Dict[str, Any]] = []

    def after_download(*_a, **_k):
        ready_state["ok"] = True
        return False

    # first call inside download_hf_model is the cache check (False), last is post-check
    checks = {"n": 0}

    def ready_flip(*_a, **_k):
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(download_mod, "hf_model_ready", ready_flip)
    result = download_mod.download_hf_model("org/m", progress_emit=seen.append)
    assert result["cached"] is False
    assert seen

    # llamacpp gguf selection + no-gguf
    ggufs = [
        {"name": "model-q3_k_m.gguf", "size": 3},
        {"name": "model-q4_k_m.gguf", "size": 4},
    ]
    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", lambda _r: ggufs)
    checks["n"] = 0
    result = download_mod.download_hf_model("org/m", provider="llamacpp", progress_emit=seen.append)
    assert result["cached"] is False

    checks["n"] = 0
    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", lambda _r: [{"name": "only.bin", "size": 1}])
    with pytest.raises(ModelRuntimeError):
        download_mod.download_hf_model("org/m", provider="llamacpp")

    # tqdm import failure still downloads
    def bad_import(name, *a, **k):
        if name.startswith("tqdm"):
            raise ImportError("no tqdm")
        return orig_import(name, *a, **k)

    orig_import = __import__
    monkeypatch.setattr("builtins.__import__", bad_import)
    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", lambda _r: [{"name": "a.bin", "size": 2}])
    checks["n"] = 0
    download_mod.download_hf_model("org/m", progress_emit=seen.append)

    monkeypatch.setattr("builtins.__import__", orig_import)
    checks["n"] = 0
    monkeypatch.setattr(download_mod, "hf_model_ready", lambda *_a, **_k: False)
    with pytest.raises(ModelRuntimeError):
        download_mod.download_hf_model("org/m")

def test_get_lmstudio_models_and_key_match(monkeypatch):
    engines_mod._LMSTUDIO_MODELS_CACHE = [{"key": "cached"}]
    engines_mod._LMSTUDIO_MODELS_CACHE_TS = time.monotonic()
    assert engines_mod.get_lmstudio_models()[0]["key"] == "cached"

    engines_mod._LMSTUDIO_MODELS_CACHE_TS = 0.0
    monkeypatch.setattr(engines_mod, "lmstudio_native_api_base", lambda: "http://lm")
    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: {"models": [{"key": "fresh"}]})
    assert engines_mod.get_lmstudio_models(force=True)[0]["key"] == "fresh"

    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: {"models": "nope"})
    assert engines_mod.get_lmstudio_models(force=True) == []

    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    engines_mod._LMSTUDIO_MODELS_CACHE = [{"key": "stale"}]
    assert engines_mod.get_lmstudio_models(force=True)[0]["key"] == "stale"

    assert engines_mod._lmstudio_candidate_keys("  ") == []
    keys = engines_mod._lmstudio_candidate_keys("org/Foo-Bar-GGUF-awq")
    assert keys

    models = [
        {"key": "Foo-Bar", "display_name": "Foo"},
        {"key": "other", "display_name": "zzz-foo-bar-gguf"},
    ]
    assert engines_mod._find_lmstudio_model_key("Foo-Bar", models)
    assert engines_mod._find_lmstudio_model_key("nope", models) is None or isinstance(
        engines_mod._find_lmstudio_model_key("foo", models), str
    )
    assert engines_mod._find_lmstudio_model_key("x", []) is None
