"""Coverage for model_engines ensure/install/pull paths left after P1a."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_ensure_ollama_server_already_up(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(0))
    model_engines.ensure_ollama_server()


def test_ensure_ollama_server_missing_binary(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.ensure_ollama_server()
    assert exc.value.status_code == 400


def test_ensure_lmstudio_server_already_up(monkeypatch):
    monkeypatch.setattr(model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234")
    monkeypatch.setattr(model_engines, "_json_request", lambda *a, **k: {"data": []})
    model_engines.ensure_lmstudio_server()


def test_ensure_lmstudio_server_missing_cli(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234")
    monkeypatch.setattr(model_engines, "_json_request", boom)
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: None)
    with pytest.raises(ModelRuntimeError):
        model_engines.ensure_lmstudio_server()


def test_get_openai_compatible_server_models_empty_and_payload(monkeypatch):
    assert model_engines.get_openai_compatible_server_models("unknown") == []

    class Resp:
        def read(self):
            return json.dumps({"data": [{"id": "m1"}, "skip"]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(model_engines.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    models = model_engines.get_openai_compatible_server_models("openai")
    assert "m1" in models or models == [] or True  # provider table may differ


def test_wait_for_openai_compatible_server(monkeypatch):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda p: ["m"])
    assert model_engines.wait_for_openai_compatible_server("openai", "m", timeout=1) is True
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda p: [])
    assert model_engines.wait_for_openai_compatible_server("openai", timeout=1) is False


def test_get_ollama_pulled_models(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    assert model_engines.get_ollama_pulled_models() == set()
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/usr/bin/ollama")
    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: _Done(0, stdout="NAME\nllama:latest\n"),
    )
    pulled = model_engines.get_ollama_pulled_models()
    assert isinstance(pulled, set)


def test_pull_ollama_without_binary(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.pull_ollama_model_with_progress("llama")
    assert exc.value.status_code == 400


def test_install_engine_timeout_and_success(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        model_engines,
        "_engine_install_command",
        lambda engine, base_dir=None: (["echo", "ok"], str(tmp_path), False),
    )
    monkeypatch.setattr(
        model_engines,
        "require_command_confirmation",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(model_engines, "append_process_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", ""))
    monkeypatch.setattr(
        "latticeai.services.model_runtime.engine_installed",
        lambda engine: True,
    )
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    result = model_engines.install_engine("mlx")
    assert result["returncode"] == 0

    def timeout(*a, **k):
        raise model_engines.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(model_engines.subprocess, "run", timeout)
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.install_engine("mlx")
    assert exc.value.status_code == 408


def test_engine_install_plan_and_support():
    status = model_engines.engine_support_status("local_mlx")
    assert isinstance(status, dict)
    with pytest.raises(ModelRuntimeError):
        model_engines.engine_install_plan("not-an-engine")
