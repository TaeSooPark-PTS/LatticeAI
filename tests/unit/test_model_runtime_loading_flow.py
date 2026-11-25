"""Unit tests for model resolution, engine readiness, and prepare/load streaming."""

from __future__ import annotations

import asyncio
import io
import sys
import types
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime import engines as engines_mod
from latticeai.services.model_runtime import loading as loading_mod
from latticeai.services.model_runtime.state import (
    ModelRuntimeState,
    create_model_runtime_state,
)
from tests.unit._runtime_test_fakes import _Done, _fast_deadline, _Popen


def test_ensure_lmstudio_model_paths(monkeypatch):
    monkeypatch.setattr(engines_mod, "ensure_lmstudio_server", lambda: None)
    monkeypatch.setattr(
        engines_mod,
        "get_lmstudio_models",
        lambda force=False: [{"key": "k1", "loaded_instances": [{"id": "i1"}]}],
    )
    monkeypatch.setattr(
        engines_mod, "_find_lmstudio_model_key", lambda name, models: "k1"
    )
    out = engines_mod.ensure_lmstudio_model("k1")
    assert out["cached"] is True

    monkeypatch.setattr(
        engines_mod,
        "get_lmstudio_models",
        lambda force=False: [{"key": "k1", "loaded_instances": []}],
    )
    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: {"status": "loaded", "instance_id": "i9"},
    )
    out = engines_mod.ensure_lmstudio_model("k1")
    assert out["cached"] is False

    def bad_status(*a, **k):
        return {"status": "nope"}

    monkeypatch.setattr(engines_mod, "_json_request", bad_status)
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("k1")

    class HTTP(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x", 500, "fail", hdrs=None, fp=io.BytesIO(b"err"))

    monkeypatch.setattr(
        engines_mod, "_json_request", lambda *a, **k: (_ for _ in ()).throw(HTTP())
    )
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("k1")

    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: (_ for _ in ()).throw(OSError("x")),
    )
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("k1")

    # download path: not found, then poll
    monkeypatch.setattr(
        engines_mod, "_find_lmstudio_model_key", lambda name, models: None
    )
    monkeypatch.setattr(engines_mod, "get_lmstudio_models", lambda force=False: [])

    polls = {"n": 0}

    def json_seq(url, **k):
        if url.endswith("/download"):
            return {"status": "running", "job_id": "j1"}
        polls["n"] += 1
        if polls["n"] == 1:
            return {"status": "running"}
        return {"status": "completed"}

    monkeypatch.setattr(engines_mod, "_json_request", json_seq)
    monkeypatch.setattr(engines_mod.time, "sleep", lambda *_a, **_k: None)
    # after download, found + loaded
    calls = {"g": 0}

    def models(force=False):
        calls["g"] += 1
        if force:
            return [{"key": "new", "loaded_instances": [{"id": "i"}]}]
        return []

    monkeypatch.setattr(engines_mod, "get_lmstudio_models", models)
    monkeypatch.setattr(
        engines_mod, "_find_lmstudio_model_key", lambda name, ms: "new" if ms else None
    )
    out = engines_mod.ensure_lmstudio_model("new")
    assert out["server_ready"] is True

    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda url, **k: (
            {"status": "failed"}
            if "status" in url
            else {"status": "running", "job_id": "j"}
        ),
    )
    monkeypatch.setattr(engines_mod, "get_lmstudio_models", lambda force=False: [])
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda *a, **k: None)
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")

    _fast_deadline(monkeypatch, engines_mod)
    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda url, **k: (
            {"status": "running"}
            if "status" in url
            else {"status": "running", "job_id": "j"}
        ),
    )
    with pytest.raises(ModelRuntimeError) as exc:
        engines_mod.ensure_lmstudio_model("x")
    assert exc.value.status_code == 408

    def download_http(*a, **k):
        raise urllib.error.HTTPError(
            "http://x", 500, "fail", hdrs=None, fp=io.BytesIO(b"bad")
        )

    monkeypatch.setattr(engines_mod, "_json_request", download_http)
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")

    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: (_ for _ in ()).throw(OSError("net")),
    )
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")


def test_engine_installed_and_safe_plan(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        engines_mod.importlib.util,
        "find_spec",
        lambda n: object() if n in {"mlx", "mlx_lm"} else None,
    )
    assert engines_mod.engine_installed("local_mlx") is True
    monkeypatch.setattr(engines_mod.importlib.util, "find_spec", lambda _n: None)
    assert engines_mod.engine_installed("local_mlx") is False

    monkeypatch.setattr(engines_mod, "local_binary", lambda _n: "/bin/ollama")
    assert engines_mod.engine_installed("ollama") is True
    monkeypatch.setattr(engines_mod, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(engines_mod, "vllm_executable", lambda: "/bin/vllm")
    assert engines_mod.engine_installed("vllm") is True
    monkeypatch.setattr(engines_mod, "find_lmstudio_cli", lambda: "/bin/lms")
    assert engines_mod.engine_installed("lmstudio") is True
    monkeypatch.setattr(engines_mod, "find_lmstudio_cli", lambda: None)
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: str(self).endswith("LM Studio.app") or Path.exists(self),
    )
    # avoid patching Path.exists globally — use a dedicated missing path check
    monkeypatch.setattr(
        engines_mod.shutil,
        "which",
        lambda n: "/bin/llama-server" if n == "llama-server" else None,
    )
    assert engines_mod.engine_installed("llamacpp") is True
    monkeypatch.setattr(engines_mod, "AsyncOpenAI", object())
    assert engines_mod.engine_installed("openai") is True
    assert engines_mod.engine_installed("unknown-engine") is False

    monkeypatch.setattr(
        engines_mod, "_engine_install_plan", lambda *a, **k: {"ok": True}
    )
    assert engines_mod._safe_engine_install_plan("ollama", base_dir=tmp_path) == {
        "ok": True
    }
    monkeypatch.setattr(
        engines_mod,
        "_engine_install_plan",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    assert engines_mod._safe_engine_install_plan("ollama", base_dir=tmp_path) is None


def test_resolve_alias_and_normalize(monkeypatch):
    monkeypatch.setattr(
        loading_mod,
        "MODEL_ENGINE_ALIASES",
        {"gemma": {"local_mlx": "mlx-community/gemma", "ollama": "gemma:latest"}},
    )
    assert loading_mod._resolve_model_alias("gemma") == "mlx-community/gemma"
    assert loading_mod._resolve_model_alias("gemma", "ollama") == "ollama:gemma:latest"
    assert loading_mod._resolve_model_alias("ollama:gemma") == "ollama:gemma:latest"
    assert loading_mod._resolve_model_alias("mlx:gemma") == "mlx-community/gemma"
    assert loading_mod._resolve_model_alias("unknown") == "unknown"
    assert loading_mod._resolve_model_alias("gemma", "vllm") == "gemma"
    assert (
        loading_mod.normalize_local_model_request("local_mlx:foo", "local_mlx") == "foo"
    )
    assert loading_mod.normalize_local_model_request("mlx:foo", "mlx") == "foo"
    assert loading_mod.normalize_local_model_request("bar", "ollama") == "ollama:bar"
    assert (
        loading_mod.normalize_local_model_request("already:x", "ollama") == "already:x"
    )


def test_ensure_engine_ready_and_resolution(monkeypatch):
    state = create_model_runtime_state()
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("nope", state=state)

    monkeypatch.setattr(
        loading_mod,
        "engine_support_status",
        lambda _e: {"supported": False, "reason": "no"},
    )
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("ollama", state=state)

    monkeypatch.setattr(
        loading_mod,
        "engine_support_status",
        lambda _e: {"supported": True, "reason": None},
    )
    monkeypatch.setattr(loading_mod, "engine_installed", lambda _e: True)
    called = {"mlx": 0}
    monkeypatch.setattr(
        loading_mod, "ensure_mlx_runtime", lambda: called.__setitem__("mlx", 1)
    )
    out = loading_mod.ensure_engine_ready("mlx", state=state)
    assert out["installed"] is True and called["mlx"] == 1
    out = loading_mod.ensure_engine_ready("ollama", state=state)
    assert out["installed_now"] is False

    monkeypatch.setattr(
        loading_mod,
        "OPENAI_COMPATIBLE_PROVIDERS",
        {**loading_mod.OPENAI_COMPATIBLE_PROVIDERS, "customcloud": {}},
    )
    monkeypatch.setattr(loading_mod, "engine_installed", lambda _e: False)
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("customcloud", state=state)

    monkeypatch.setattr(
        loading_mod,
        "install_engine",
        lambda engine, state=None: {"returncode": 1, "stderr": "fail"},
    )
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("ollama", state=state)

    installed = {"now": False}

    def installed_after(_e):
        return installed["now"]

    def do_install(engine, state=None):
        installed["now"] = True
        return {"returncode": 0}

    monkeypatch.setattr(loading_mod, "engine_installed", installed_after)
    monkeypatch.setattr(loading_mod, "install_engine", do_install)
    out = loading_mod.ensure_engine_ready("local_mlx", state=state)
    assert out["installed_now"] is True

    res = loading_mod.build_model_resolution(
        "org/m", "ollama", user_email="a@b.c", display_name="M"
    )
    assert res.engine == "ollama"


def test_prepare_and_load_and_stream(monkeypatch):
    state = create_model_runtime_state()

    async def fake_load(*_a, **_k):
        return {"status": "ok"}

    async def fake_stream(*_a, **_k):
        yield "event: x\ndata: {}\n\n"

    async def _run():
        monkeypatch.setattr(
            "latticeai.services.model_loading.prepare_and_load_model", fake_load
        )
        out = await loading_mod.prepare_and_load_model(
            "org/m", request=None, state=state
        )
        assert out["status"] == "ok"

        monkeypatch.setattr(
            "latticeai.services.model_loading.prepare_and_load_model_stream",
            fake_stream,
        )
        frames = [
            frame
            async for frame in loading_mod.prepare_and_load_model_stream(
                "org/m", request=None, state=state
            )
        ]
        assert frames

        class Router:
            async def generate(self, *_a, **_k):
                return "4"

        state2 = ModelRuntimeState(router=Router())
        out = await loading_mod._smoke_test_loaded_model(
            SimpleNamespace(engine="openai", load_id="openai:x"),
            state=state2,
        )
        assert out.get("skipped") is True or "ok" in out

    asyncio.run(_run())


def test_high_leftover_engine_download_loading_lifespan(monkeypatch, tmp_path: Path):
    # cached download without progress_emit (120->129)
    monkeypatch.setattr(download_mod, "hf_model_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: tmp_path / "m")
    monkeypatch.setattr(download_mod, "hf_cache_model_dir", lambda _r: None)
    cached = download_mod.download_hf_model("org/m")
    assert cached["cached"] is True

    # emit_byte_progress without total_bytes + throttle return
    monkeypatch.setattr(download_mod.importlib.util, "find_spec", lambda _n: object())
    monkeypatch.setattr(download_mod, "hf_model_ready", lambda *_a, **_k: False)
    monkeypatch.setattr(
        download_mod,
        "hf_repo_files_with_sizes",
        lambda _r: [{"name": "a.bin", "size": 0}],
    )

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
        dest.write_bytes(b"xx")
        if tqdm_class is not None:
            bar = tqdm_class(total=100)
            bar.update(1)
            bar.update(1)
            bar.update(50)
        return str(dest)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = fake_download
    hub.HfApi = object
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    checks = {"n": 0}

    def ready_flip(*_a, **_k):
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(download_mod, "hf_model_ready", ready_flip)
    download_mod.download_hf_model("org/m", progress_emit=lambda *_a, **_k: None)

    # lmstudio candidate with empty parts
    assert engines_mod._lmstudio_candidate_keys("-")
    assert engines_mod._lmstudio_candidate_keys("--gguf")

    # download already completed — skip poll loop
    monkeypatch.setattr(engines_mod, "ensure_lmstudio_server", lambda: None)
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda *a, **k: None)
    monkeypatch.setattr(
        engines_mod,
        "get_lmstudio_models",
        lambda force=False: (
            [{"key": "done", "loaded_instances": [{"id": "i"}]}] if force else []
        ),
    )
    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: {"status": "already_downloaded"},
    )
    monkeypatch.setattr(
        engines_mod,
        "_find_lmstudio_model_key",
        lambda name, models: "done" if models else None,
    )
    out = engines_mod.ensure_lmstudio_model("done")
    assert out["server_ready"] is True


def test_high_leftover_engine_install_and_lifespan(monkeypatch, tmp_path: Path):
    # ensure_engine_ready successful install of non-mlx
    state = create_model_runtime_state()
    installed = {"now": False}
    monkeypatch.setattr(
        loading_mod,
        "engine_support_status",
        lambda _e: {"supported": True, "reason": None},
    )
    monkeypatch.setattr(loading_mod, "engine_installed", lambda _e: installed["now"])
    monkeypatch.setattr(
        loading_mod,
        "install_engine",
        lambda engine, state=None: (
            installed.__setitem__("now", True) or {"returncode": 0}
        ),
    )
    out = loading_mod.ensure_engine_ready("ollama", state=state)
    assert out["installed_now"] is True

    # lmstudio wait loop: fail then sleep then succeed
    calls = {"n": 0}

    def json_req(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("down")
        return {}

    monkeypatch.setattr(
        model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234"
    )
    monkeypatch.setattr(model_engines, "_json_request", json_req)
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: "/usr/bin/lms")
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    monkeypatch.setattr(model_engines.time, "sleep", lambda *_a, **_k: None)
    model_engines.ensure_lmstudio_server()

    # ollama wait loop: after spawn, probe raises then succeeds
    runs = {"n": 0}

    def run(*_a, **_k):
        runs["n"] += 1
        if runs["n"] == 1:
            return _Done(1)
        if runs["n"] == 2:
            raise OSError("not yet")
        return _Done(0)

    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(model_engines.subprocess, "run", run)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    model_engines.ensure_ollama_server()

    monkeypatch.setattr(
        model_engines.subprocess,
        "run",
        lambda *a, **k: _Done(0, stdout="NAME\n\nllama:latest\n"),
    )
    pulled = model_engines.get_ollama_pulled_models()
    assert "llama:latest" in pulled

    def run_exc(*a, **k):
        if a and "list" in a[0]:
            raise OSError("down")
        return _Done(0, "ok", "")

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
            "command_preview": "echo",
            "command": ["echo"],
        },
    )
    monkeypatch.setattr(
        model_engines, "require_command_confirmation", lambda *a, **k: None
    )
    monkeypatch.setattr(
        model_engines, "append_process_audit_event", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.engine_installed", lambda _e: True
    )
    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(model_engines.subprocess, "run", run_exc)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    result = model_engines.install_engine("ollama")
    assert result["daemon_started"] is True

    # idle unload prints when models are returned
    class Router:
        def unload_idle_models(self, seconds):
            return ["idle-a"]

        def unload_all(self):
            return None

        async def load_model(self, *a, **k):
            return "ok"

    runtime = build_lifespan_runtime(
        app_mode="local",
        autoload_models=False,
        is_public_mode=False,
        public_model="",
        allow_local_models=True,
        local_model="m",
        local_draft_model="",
        model_idle_unload_seconds=1,
        model_router=Router(),
        local_server_processes={},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )

    async def fake_sleep(_n):
        raise asyncio.CancelledError()

    # first sleep happens, then we cancel via CancelledError after unload
    sleeps = {"n": 0}

    async def sleep_once(_n):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("asyncio.sleep", sleep_once)

    async def _run():
        try:
            await runtime["unload_idle_models_loop"]()
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
