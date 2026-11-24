"""Unit tests for local model engine discovery, server lifecycle, and Ollama pull."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from typing import Any, Dict, List

import pytest

from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError
from tests.unit._runtime_test_fakes import _Done, _fast_deadline, _Popen, _UrlResp


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
    assert (
        model_engines._json_request("http://x", method="POST", payload={"a": 1})["ok"]
        is True
    )

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
    assert model_engines.lmstudio_native_api_base().endswith(
        ":9"
    ) or model_engines.lmstudio_native_api_base().endswith(":9/")


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

    monkeypatch.setattr(
        model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234"
    )
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
        lambda *_a, **_k: _UrlResp(
            json.dumps({"data": [{"id": "m1"}, {"id": None}, "x"]}).encode()
        ),
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
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: ["already"]
    )
    model_engines.ensure_vllm_server("already")

    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: []
    )
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
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_dir", lambda _n: tmp_path / "m"
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: False
    )

    running = _Popen(poll_value=None)
    model_engines.LOCAL_SERVER_PROCESSES["vllm"] = running
    monkeypatch.setattr(model_engines, "_reap_local_server", lambda *_a, **_k: None)
    monkeypatch.setattr(
        model_engines.subprocess, "Popen", lambda *a, **k: _Popen(poll_value=0)
    )
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    model_engines.ensure_vllm_server("org/m")

    model_engines.LOCAL_SERVER_PROCESSES.pop("vllm", None)
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: ["other"]
    )
    with pytest.raises(ModelRuntimeError) as exc2:
        model_engines.ensure_vllm_server("org/m")
    assert exc2.value.status_code == 409

    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: []
    )
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: "/opt/metal/python")
    spawned = {}

    def capture(cmd, **_k):
        spawned["cmd"] = cmd
        return _Popen()

    monkeypatch.setattr(model_engines.subprocess, "Popen", capture)
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    model_engines.ensure_vllm_server("org/m")
    assert "-m" in spawned["cmd"]

    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/usr/bin/vllm")
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: True
    )
    model_engines.ensure_vllm_server("org/m")
    assert spawned["cmd"][0] == "/usr/bin/vllm"

    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False
    )
    with pytest.raises(ModelRuntimeError) as exc3:
        model_engines.ensure_vllm_server("org/m")
    assert exc3.value.status_code == 500


def test_ensure_llamacpp_server_branches(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: ["ready"]
    )
    model_engines.ensure_llamacpp_server("ready")

    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: []
    )
    running = _Popen(poll_value=None)
    model_engines.LOCAL_SERVER_PROCESSES["llamacpp"] = running
    monkeypatch.setattr(model_engines, "_reap_local_server", lambda *_a, **_k: None)
    monkeypatch.setattr(model_engines.shutil, "which", lambda _n: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc.value.status_code == 400

    model_engines.LOCAL_SERVER_PROCESSES.pop("llamacpp", None)
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: ["other"]
    )
    with pytest.raises(ModelRuntimeError) as exc2:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc2.value.status_code == 409

    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda _p: []
    )
    monkeypatch.setattr(
        model_engines.shutil, "which", lambda _n: "/usr/bin/llama-server"
    )
    model_dir = tmp_path / "gguf"
    model_dir.mkdir()
    (model_dir / "other.gguf").write_bytes(b"g")
    (model_dir / "q4_k_m.gguf").write_bytes(b"g")
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_dir", lambda _n: model_dir
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.download_hf_model", lambda *_a, **_k: None
    )
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    model_engines.ensure_llamacpp_server("org/m")

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_dir", lambda _n: empty
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_ready", lambda *_a, **_k: True
    )
    with pytest.raises(ModelRuntimeError) as exc3:
        model_engines.ensure_llamacpp_server("org/m")
    assert exc3.value.status_code == 500

    monkeypatch.setattr(
        "latticeai.services.model_runtime.hf_model_dir", lambda _n: model_dir
    )
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False
    )
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
    result = model_engines.pull_ollama_model_with_progress(
        "llama", progress_emit=seen.append
    )
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
        model_engines.pull_ollama_model_with_progress(
            "llama", progress_emit=seen.append
        )


def test_get_ollama_pulled_models_exception(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no")),
    )
    assert model_engines.get_ollama_pulled_models() == set()
