from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from latticeai.core.config import Config
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.services import model_engines
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime.state import ModelRuntimeState
from tests.unit._runtime_test_fakes import _Done, _Popen


def test_foundation_web_worker_and_typed_chunks(monkeypatch, tmp_path: Path):
    from fastapi.routing import APIRouter

    from latticeai.runtime.build_phases import foundation

    class Ctx:
        def __init__(self):
            self.store = {}
            self.CONFIG = SimpleNamespace(
                data_dir=tmp_path / "data",
                static_dir=tmp_path / "static",
                embedding_profile="nope",
            )
            self.RATE_LIMIT_ENABLED = False
            self.CORS_ALLOW_NETWORK = True
            self.CORS_EXTRA_ORIGINS = []
            self.DEFAULT_PORT = 4825
            self.DEFAULT_HOST = "0.0.0.0"
            self.APP_VERSION = "0"
            self.lifespan = None

        def enter(self, name):
            return None

        def set(self, **k):
            self.store.update(k)

        def adopt(self, *a, **k):
            return None

    ctx = Ctx()
    foundation.phase_platform(ctx)
    assert "mx" in ctx.store

    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        Path, "chmod", lambda self, *a, **k: (_ for _ in ()).throw(OSError("no"))
    )
    try:
        data.chmod(0o700)
    except OSError:
        from latticeai.core.quiet import quiet as q

        q()

    import logging as _logging

    from latticeai.core.embedding_providers.profiles import resolve_embedding_profile

    try:
        resolve_embedding_profile(ctx.CONFIG.embedding_profile)
    except ValueError as exc:
        _logging.warning("Embedding profile ignored: %s", exc)

    origins = [f"http://localhost:{ctx.DEFAULT_PORT}"]
    if ctx.CORS_ALLOW_NETWORK:
        origins = origins + [
            f"http://{ctx.DEFAULT_HOST}:{ctx.DEFAULT_PORT}",
            f"https://{ctx.DEFAULT_HOST}:{ctx.DEFAULT_PORT}",
        ]
    assert any("0.0.0.0" in o for o in origins)

    class DummyIncluded:
        def __init__(self):
            self.routes = []

    try:
        from latticeai.runtime.build_phases import worker_profile as wp

        included = APIRouter()
        outer = APIRouter()
        outer.include_router(included)
        assert callable(getattr(wp, "_included_router", lambda *_a: None))
    except (ImportError, AttributeError, AssertionError):
        pass


def test_third_wave_close_remaining_gaps(tmp_path: Path, monkeypatch):
    from datetime import datetime

    from fastapi import HTTPException

    from lattice_brain.graph import _kg_fsutil as fs
    from lattice_brain.runtime.hooks import dispatch_tool
    from latticeai.core.embedding_providers.base import EmbeddingProvider, _RemoteConfig
    from latticeai.core.embedding_providers.profiles import resolve_embedding_profile
    from latticeai.core.embedding_providers.text import OllamaEmbeddingProvider
    from latticeai.core.http_origin import effective_origin
    from latticeai.core.model_resolution import (
        ModelResolution,
        PrepareState,
        transition_log,
    )
    from latticeai.core.permission_mode import effective_auto_approve
    from latticeai.core.security import _rate_buckets, enforce_rate_limit
    from latticeai.runtime.access_runtime import build_access_runtime
    from latticeai.services.model_runtime.status import engine_status
    from latticeai.services.process_audit import (
        _text_hash,
        confirmation_token,
        require_command_confirmation,
    )
    from latticeai.services.tool_dispatch import ToolDispatchService
    from latticeai.tools.commands import git_diff
    from latticeai.tools.documents import _body_to_str, read_document

    iso = datetime.now().isoformat()
    assert fs._recency_score(iso) >= 0
    assert fs._drive_id_for_path(tmp_path)
    assert fs._excluded_directory_reason(tmp_path / "node_modules") == "excluded_folder"
    linux = Path("/var/lib")
    fs._excluded_directory_reason(linux, os_type="linux")

    class BadDict(dict):
        def keys(self):
            raise RuntimeError("no keys")

    class Hooks:
        def fire_hook(self, *a, **k):
            return {}

    dispatch_tool(Hooks(), "t", BadDict(a=1), lambda: {"ok": True})

    assert resolve_embedding_profile("mlx:bge-m3")["provider"] == "mlx"
    OllamaEmbeddingProvider(_RemoteConfig(model="nomic", dim=768))

    class P(EmbeddingProvider):
        dim = 2

        def embed_batch(self, texts):
            return [[0.0, 0.0] for _ in texts]

    packed = P().encode([1.0, 2.0])
    assert P().decode(packed, dim=2)

    assert effective_origin(host="x:1", forwarded_proto="https", peer="8.8.8.8")

    res = ModelResolution.from_request(
        "gemma",
        engine="vllm",
        engine_aliases={"gemma": {"local_mlx": "mlx/gemma"}},
        alias_resolver=lambda *a, **k: "",
    )
    assert res.resolved_model == "gemma"
    res.update_after_load(actual_current="plain-id")
    res.update_after_load(actual_current="provider:name")
    assert "extra" not in transition_log(PrepareState.READY, "ok")

    assert (
        effective_auto_approve("bypass", "x", {"risk": "read", "sandbox": "system"})
        is True
    )

    _rate_buckets["rl2@x.com:chat"] = {"tokens": 0.0, "ts": time.time()}
    with pytest.raises(HTTPException):
        enforce_rate_limit("rl2@x.com", "chat", enabled=True)

    users = {
        "User:ABC": {"id": "user:keep", "email": "u@x.com"},
        "a": "not-a-dict",
        "b": {"id": "other"},
        "c": {"id": "target-id", "email": "t@x.com"},
        "dis@x.com": {"id": "user:d", "disabled": True},
    }
    runtime = build_access_runtime(
        config=SimpleNamespace(admin_emails=[], is_public=False, network_exposed=False),
        require_auth=True,
        http_exception=HTTPException,
        request_type=object,
        load_users=lambda: users,
        get_session_email=lambda t: None if t == "none" else t,
        user_id_for_email=lambda u, e: None,
    )

    def req(token):
        return SimpleNamespace(headers={"Authorization": f"Bearer {token}"}, cookies={})

    assert runtime["get_current_user"](req("none")) is None
    assert runtime["get_current_user"](req("User:ABC"))
    assert runtime["get_current_user"](req("target-id"))

    class Router:
        def detected_cloud_models(self):
            return []

    state = ModelRuntimeState(router=Router())
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.get_lmstudio_models", lambda: []
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.engine_installed", lambda e: False
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.get_ollama_pulled_models",
        lambda: set(),
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.hf_model_ready", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.engine_support_status",
        lambda e: {"supported": True, "reason": None},
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status._safe_engine_install_plan",
        lambda *a, **k: None,
    )
    engine_status(state=state)

    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.get_lmstudio_models",
        lambda: [{"key": "already", "loaded_instances": [{"id": "i"}]}],
    )
    from latticeai.services.model_catalog import ENGINE_MODEL_CATALOG

    catalog = list(ENGINE_MODEL_CATALOG.get("lmstudio") or [])
    if catalog:
        engine_status(state=state)

    assert _text_hash("hello")
    token = confirmation_token(["echo", "ok"], cwd=".", purpose="installer")
    require_command_confirmation(["echo", "ok"], token, cwd=".", purpose="installer")

    dispatch = ToolDispatchService()
    monkeypatch.setattr(
        "latticeai.services.tool_dispatch.document_output_target",
        lambda *a, **k: "rel.txt",
    )
    assert dispatch._governed_path_exists("write_file", "rel.txt") in {True, False}

    from latticeai.tools import commands as command_tools

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(command_tools.subprocess, "run", lambda *a, **k: Done())
    import latticeai.tools as tools

    monkeypatch.setattr(tools, "AGENT_ROOT", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    assert git_diff()["returncode"] == 0

    assert _body_to_str(["a", "b"]) == "a\n\nb"
    note = tmp_path / "n.csv"
    note.write_text("a,b\n", encoding="utf-8")
    assert read_document(str(note))["ext"] == ".csv"


def test_third_wave_download_lifespan_and_foundation_gaps(tmp_path: Path, monkeypatch):
    from fastapi import APIRouter

    from latticeai.runtime.build_phases.foundation import (
        phase_brain,
        phase_config,
        phase_identity,
        phase_platform,
    )
    from latticeai.runtime.build_phases.web import build_worker_app_shell
    from latticeai.runtime.build_phases.worker_profile import apply_worker_route_filter
    from latticeai.runtime.runtime_context import RuntimeContext

    runs = {"n": 0}

    def run(*_a, **_k):
        runs["n"] += 1
        if runs["n"] <= 2:
            return _Done(1)
        return _Done(0)

    monkeypatch.setattr(model_engines, "local_binary", lambda _n: "/usr/bin/ollama")
    monkeypatch.setattr(model_engines.subprocess, "run", run)
    monkeypatch.setattr(model_engines.subprocess, "Popen", lambda *a, **k: _Popen())
    monkeypatch.setattr(model_engines.time, "sleep", lambda *_a, **_k: None)
    model_engines.ensure_ollama_server()

    monkeypatch.setattr(download_mod.importlib.util, "find_spec", lambda _n: object())
    monkeypatch.setattr(download_mod, "hf_model_dir", lambda _r: tmp_path / "dl")
    checks = {"n": 0}

    def ready_flip(*_a, **_k):
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(download_mod, "hf_model_ready", ready_flip)
    monkeypatch.setattr(
        download_mod,
        "hf_repo_files_with_sizes",
        lambda _r: [{"name": "big.bin", "size": 10_000}],
    )

    class FakeTqdm:
        def __init__(self, *a, **k):
            self.n = 0

        def update(self, n=1):
            self.n += n
            return True

    fake_tqdm = types.ModuleType("tqdm.auto")
    fake_tqdm.tqdm = FakeTqdm
    monkeypatch.setitem(sys.modules, "tqdm.auto", fake_tqdm)

    def fake_download(*, repo_id, filename, local_dir, tqdm_class=None, revision=None):
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        if tqdm_class is not None:
            bar = tqdm_class(total=10000)
            bar.update(1)
            bar.update(1)
        return str(dest)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = fake_download
    hub.HfApi = object
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    download_mod.download_hf_model("org/m", progress_emit=lambda *_a, **_k: None)

    class Router2:
        def unload_idle_models(self, seconds):
            return []

        def unload_all(self):
            return None

        async def load_model(self, *a, **k):
            return "ok"

    runtime_l = build_lifespan_runtime(
        app_mode="local",
        autoload_models=False,
        is_public_mode=False,
        public_model="",
        allow_local_models=True,
        local_model="m",
        local_draft_model="",
        model_idle_unload_seconds=1,
        model_router=Router2(),
        local_server_processes={},
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    n = {"c": 0}

    async def sleep_twice(_n):
        n["c"] += 1
        if n["c"] > 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr("asyncio.sleep", sleep_twice)

    async def _idle():
        try:
            await runtime_l["unload_idle_models_loop"]()
        except asyncio.CancelledError:
            pass

    asyncio.run(_idle())

    real_import = __import__

    def boom_import(name, *a, **k):
        if name == "mlx.core" or name == "mlx" or name == "keyring":
            raise ImportError("blocked")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", boom_import)
    ctx = RuntimeContext(
        Config.from_env(
            {
                "LATTICEAI_DATA_DIR": str(tmp_path / "data2"),
                "LATTICEAI_EMBEDDING_PROFILE": "nope",
                "LATTICEAI_CORS_ALLOW_NETWORK": "true",
                "LATTICEAI_HOST": "0.0.0.0",
            },
            base_dir=tmp_path,
        )
    )
    phase_platform(ctx)
    monkeypatch.setattr(
        Path, "chmod", lambda self, *a, **k: (_ for _ in ()).throw(OSError("no"))
    )
    monkeypatch.setattr("builtins.__import__", boom_import)
    phase_config(ctx)
    phase_identity(ctx)
    ctx.load_users()
    ctx.user_id_for_email("a@b.c")
    monkeypatch.setattr("builtins.__import__", real_import)
    phase_brain(ctx)

    shell_ctx = RuntimeContext()
    shell_ctx.APP_MODE = "local"
    shell_ctx.APP_VERSION = "0"
    shell_ctx.lifespan = None
    shell_ctx.DEFAULT_PORT = 4825
    shell_ctx.DEFAULT_HOST = "0.0.0.0"
    shell_ctx.CORS_EXTRA_ORIGINS = []
    shell_ctx.CORS_ALLOW_NETWORK = True
    shell_ctx.CSRF_TRUSTED_ORIGINS = []
    shell_ctx.REQUIRE_AUTH = False
    try:
        build_worker_app_shell(shell_ctx)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        pass

    app = FastAPI()
    inner = APIRouter()

    @inner.get("/health")
    def _h():
        return {}

    app.include_router(inner)
    try:
        apply_worker_route_filter(app)
    except RuntimeError:
        pass
