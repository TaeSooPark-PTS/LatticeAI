"""Unit tests for engine installation, smoke tests, and Hugging Face model downloads."""

from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime import engines as engines_mod
from latticeai.services.process_audit import CommandConfirmationError
from tests.unit._runtime_test_fakes import _Done, _Popen


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

    monkeypatch.setattr(
        model_engines.shutil, "which", lambda _n: "/opt/homebrew/bin/brew"
    )
    monkeypatch.setattr(model_engines.sys, "platform", "darwin")
    monkeypatch.setattr(model_engines.platform, "machine", lambda: "arm64")
    cmd, cwd, admin = model_engines._engine_install_command("vllm", base_dir=tmp_path)
    assert cmd[0] == "/bin/bash"
    assert admin is False

    monkeypatch.setattr(
        model_engines,
        "ENGINE_INSTALLERS",
        {
            **model_engines.ENGINE_INSTALLERS,
            "apteng": {"command": ["apt-get", "install", "x"]},
        },
    )
    cmd, _cwd, admin = model_engines._engine_install_command(
        "apteng", base_dir=tmp_path
    )
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
    monkeypatch.setattr(
        model_engines, "append_process_audit_event", lambda *a, **k: None
    )
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.install_engine("ollama")
    assert exc.value.status_code == 403

    monkeypatch.setattr(
        model_engines, "require_command_confirmation", lambda *a, **k: None
    )

    def explode(*_a, **_k):
        raise RuntimeError("spawn fail")

    monkeypatch.setattr(model_engines.subprocess, "run", explode)
    with pytest.raises(RuntimeError):
        model_engines.install_engine("ollama")

    monkeypatch.setattr(
        model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", "")
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.engine_installed", lambda _e: True
    )
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(
        model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", "")
    )
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

    monkeypatch.setattr(
        model_engines.subprocess, "run", lambda *a, **k: _Done(0, "ok", "")
    )
    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: (
            _Done(1, "", "") if (a and "list" in a[0]) else _Done(0, "ok", "")
        ),
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
            if name.startswith("latticeai.core.model_compat") or name.startswith(
                "latticeai.services.model_runtime"
            ):
                raise ImportError("blocked")
            return real_import(name, *a, **k)

        monkeypatch.setattr("builtins.__import__", boom_import)
        out = await model_engines._smoke_test_loaded_model(
            SimpleNamespace(engine="ollama", load_id="x")
        )
        assert out.get("skipped") is True

    asyncio.run(_run())


def test_hf_model_ready_cache_and_tokenizers(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: missing)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cache = (
        tmp_path
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--org--m"
        / "snapshots"
        / "abc"
    )
    cache.mkdir(parents=True)
    monkeypatch.setattr(
        download_mod, "hf_cache_model_dir", lambda _r: tmp_path / "cached"
    )
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
    monkeypatch.setattr(
        download_mod, "hf_model_ready", lambda *_a, **_k: ready_state["ok"]
    )

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
    result = download_mod.download_hf_model(
        "org/m", provider="llamacpp", progress_emit=seen.append
    )
    assert result["cached"] is False

    checks["n"] = 0
    monkeypatch.setattr(
        download_mod,
        "hf_repo_files_with_sizes",
        lambda _r: [{"name": "only.bin", "size": 1}],
    )
    with pytest.raises(ModelRuntimeError):
        download_mod.download_hf_model("org/m", provider="llamacpp")

    # tqdm import failure still downloads
    def bad_import(name, *a, **k):
        if name.startswith("tqdm"):
            raise ImportError("no tqdm")
        return orig_import(name, *a, **k)

    orig_import = __import__
    monkeypatch.setattr("builtins.__import__", bad_import)
    monkeypatch.setattr(
        download_mod,
        "hf_repo_files_with_sizes",
        lambda _r: [{"name": "a.bin", "size": 2}],
    )
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
    monkeypatch.setattr(
        engines_mod, "_json_request", lambda *a, **k: {"models": [{"key": "fresh"}]}
    )
    assert engines_mod.get_lmstudio_models(force=True)[0]["key"] == "fresh"

    monkeypatch.setattr(
        engines_mod, "_json_request", lambda *a, **k: {"models": "nope"}
    )
    assert engines_mod.get_lmstudio_models(force=True) == []

    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: (_ for _ in ()).throw(OSError("x")),
    )
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
