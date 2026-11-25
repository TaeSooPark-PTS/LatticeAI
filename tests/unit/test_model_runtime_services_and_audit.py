from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime.state import ModelRuntimeState


def test_service_state_status_process_audit_dispatch_stages(
    monkeypatch, tmp_path: Path
):
    from dataclasses import dataclass

    from latticeai.runtime.stages import RuntimeStage
    from latticeai.services.model_runtime.service import (
        ModelRuntimeService,
        build_model_runtime,
        configure_model_runtime,
    )
    from latticeai.services.model_runtime.state import (
        _engine_install_block,
        _missing_current_user,
        _missing_user_api_key,
    )
    from latticeai.services.model_runtime.status import engine_status, install_engine
    from latticeai.services.process_audit import (
        _preview_text,
        _text_hash,
        append_process_audit_event,
        require_command_confirmation,
        verify_command_confirmation,
    )
    from latticeai.services.tool_dispatch import ToolDispatchService

    assert _missing_current_user(None) is None
    assert _missing_user_api_key("e", "openai") is None
    with pytest.raises(ModelRuntimeError):
        _engine_install_block("ollama")

    svc = configure_model_runtime()
    assert isinstance(svc, ModelRuntimeService)
    assert isinstance(build_model_runtime(), ModelRuntimeService)

    async def fake_load(*a, **k):
        return {"ok": True}

    async def fake_stream(*a, **k):
        yield "e"

    monkeypatch.setattr(
        "latticeai.services.model_runtime.service.prepare_and_load_model", fake_load
    )
    monkeypatch.setattr(
        "latticeai.services.model_runtime.service.prepare_and_load_model_stream",
        fake_stream,
    )

    async def _run():
        assert (await svc.prepare_and_load_model("m", None))["ok"] is True
        frames = [f async for f in svc.prepare_and_load_model_stream("m", None)]
        assert frames

    asyncio.run(_run())

    class Router:
        def detected_cloud_models(self):
            return []

    state = ModelRuntimeState(router=Router())
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status.get_lmstudio_models",
        lambda: [
            {"key": ""},
            {"key": "k1", "loaded_instances": []},
            {"key": "k2", "display_name": "D"},
        ],
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
    status = engine_status(state=state)
    assert status
    monkeypatch.setattr(
        "latticeai.services.model_runtime.status._install_engine",
        lambda *a, **k: {"ok": True},
    )
    assert install_engine("ollama", state=state)["ok"] is True

    assert verify_command_confirmation(["echo"], None) is False
    with pytest.raises(Exception):
        require_command_confirmation(["echo"], None)
    assert _preview_text(None) is None
    assert "REDACTED" in (_preview_text("api_key=secret") or "")
    assert _text_hash(None) is None

    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("no audit")),
    )
    append_process_audit_event("x", plan={"command_hash": "h"}, status="ok")

    dispatch = ToolDispatchService()
    assert dispatch.tool_governance
    assert dispatch.permissions()
    assert (
        dispatch._governed_path_exists("write_file", str(tmp_path / "nope.txt"))
        is False
    )

    def boom_exists(*_a, **_k):
        raise OSError("bad")

    monkeypatch.setattr(
        "latticeai.services.tool_dispatch.document_output_target", boom_exists
    )
    assert dispatch._governed_path_exists("write_file", "x") is False

    @dataclass
    class Stage(RuntimeStage):
        foo: int = 1
        bar: str = "x"

    stage = Stage()
    assert "foo" in stage
    assert stage["foo"] == 1
    assert len(stage) == 2
    assert list(stage) == ["foo", "bar"]
    with pytest.raises(KeyError):
        _ = stage["missing"]


def test_resolution_compat_governor_registry_local_models_origin(
    tmp_path: Path, monkeypatch
):
    from latticeai.core.embedding_providers.base import EmbeddingProvider
    from latticeai.core.embedding_providers.profiles import resolve_embedding_profile
    from latticeai.core.http_origin import effective_origin
    from latticeai.core.model_compat import _local_model_type, fast_postprocess
    from latticeai.core.model_resolution import (
        ModelResolution,
        PrepareState,
        transition_log,
    )
    from latticeai.core.security import configure_trusted_proxies, enforce_rate_limit
    from latticeai.core.tool_governor import classify_tool_call
    from latticeai.models.router.local_models import _resolve_local_hf_model
    from latticeai.tools import DEFAULT_TOOL_REGISTRY

    res = ModelResolution.from_request(
        "custom:thing",
        engine="local_mlx",
        engine_aliases={"thing": {"local_mlx": "mapped/thing"}},
        alias_resolver=lambda model, provider: "ollama:resolved",
    )
    assert res.provider == "ollama"
    res2 = ModelResolution.from_request(
        "plain",
        alias_resolver=lambda model, provider: "just-name",
    )
    assert res2.resolved_model == "just-name"
    res3 = ModelResolution.from_request(
        "plain",
        alias_resolver=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    assert res3.resolved_model == "plain"
    res.update_after_load(actual_current=None)
    res.update_after_load(actual_current="lmstudio:inst")
    assert transition_log(PrepareState.READY, "ok", extra={"a": 1})["extra"]

    assert fast_postprocess("hi", {"postprocess": ["no-such"]}) == "hi"
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "latticeai.core.model_compat._hf_model_dir",
        lambda raw: tmp_path,
    )
    try:
        _local_model_type("org/m")
    except (OSError, ValueError, TypeError, RuntimeError):
        pass

    classify_tool_call("mystery", {}, policy={"risk": "write"})
    classify_tool_call("mystery", {}, policy={"risk": "other"})
    assert DEFAULT_TOOL_REGISTRY.permissions()

    model_dir = tmp_path / "hf"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"x")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "latticeai.models.router.local_models.hf_model_dir", lambda _i: model_dir
    )
    assert _resolve_local_hf_model("org/m") == str(model_dir)

    origin = effective_origin(host="front:9", forwarded_proto="ftp", peer="127.0.0.1")
    assert origin and origin.startswith("http")

    with pytest.raises(ValueError):
        resolve_embedding_profile("nope")
    assert resolve_embedding_profile("") == {}

    class P(EmbeddingProvider):
        dim = 2

        def embed(self, texts):
            return [[0.0, 0.0] for _ in texts]

    p = P()
    packed = p.encode([1.0, 2.0])
    assert p.decode(packed, dim=99)

    from fastapi import HTTPException

    configure_trusted_proxies(["not-a-cidr", "10.0.0.0/8"])
    for _ in range(3):
        try:
            enforce_rate_limit("rl@x.com", "chat-extra", enabled=True)
        except HTTPException:
            break


def test_kg_hooks_access_readiness_and_profiles(tmp_path: Path, monkeypatch):
    from fastapi import HTTPException

    from lattice_brain.graph import _kg_fsutil as fs
    from lattice_brain.graph._kg_common.relations import (
        _classify_node_type,
        _infer_edge,
    )
    from lattice_brain.graph._kg_common.text import _chunks
    from lattice_brain.runtime.hooks import dispatch_tool
    from latticeai.runtime.access_runtime import build_access_runtime
    from latticeai.services.product_readiness import (
        _evidence_resolves,
    )

    assert _infer_edge("we use the tool") == "사용함"
    assert _classify_node_type("ValueError", "") == "Error"
    assert _classify_node_type("helper", "def helper():\n    return 1") == "Code"

    assert _chunks("abc", size=10, overlap=0)

    now = __import__("datetime").datetime.now()
    iso = now.isoformat()
    assert fs._recency_score(iso, now=now) >= 0
    assert fs._slug("Hello World!")
    assert fs._path_fingerprint(tmp_path)
    assert fs._file_category(".py")
    assert fs._node_type_for_category("code") == "CodeFile"
    assert fs._size_limit_for_category("nope")
    linux_path = Path("/proc")
    fs._excluded_directory_reason(linux_path, os_type="linux")
    assert fs._sensitive_file_reason(tmp_path / ".env")
    assert fs._root_warning(tmp_path, "macos") is None

    class BoomArgs:
        def keys(self):
            raise RuntimeError("no keys")

    class Hooks:
        def fire_hook(self, *a, **k):
            return {}

    with pytest.raises(RuntimeError):
        dispatch_tool(
            Hooks(),
            "t",
            BoomArgs(),
            lambda: (_ for _ in ()).throw(RuntimeError("tool fail")),
        )

    users = {
        "a@b.c": {"id": "user:abc", "email": "a@b.c"},
        "rawkey": {"id": "user:raw", "email": "r@x.com"},
        "dis@x.com": {"id": "user:d", "disabled": True},
    }
    runtime = build_access_runtime(
        config=SimpleNamespace(admin_emails=[], is_public=False, network_exposed=False),
        require_auth=False,
        http_exception=HTTPException,
        request_type=object,
        load_users=lambda: users,
        get_session_email=lambda t: t,
        user_id_for_email=lambda users, email: None,
    )

    def req(token):
        return SimpleNamespace(headers={"Authorization": f"Bearer {token}"}, cookies={})

    assert runtime["get_current_user"](SimpleNamespace(headers={}, cookies={})) is None
    assert runtime["get_current_user"](req("rawkey"))
    assert runtime["get_current_user"](req("user:abc"))
    assert runtime["get_current_user"](req("dis@x.com")) is None

    blocked = tmp_path / "blocked.txt"
    blocked.write_text("needle", encoding="utf-8")
    assert _evidence_resolves(tmp_path, "blocked.txt::needle") is True
    assert _evidence_resolves(tmp_path, "blocked.txt::missing") is False
    assert _evidence_resolves(tmp_path, "nope.txt::x") is False

    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    assert _evidence_resolves(tmp_path, "blocked.txt::needle") is False


def test_phase_platform_selects_the_gpu_device_when_mlx_imports(monkeypatch):
    from latticeai.runtime.build_phases.foundation import phase_platform
    from latticeai.runtime.runtime_context import RuntimeContext

    chosen = []
    core = types.ModuleType("mlx.core")
    core.gpu = object()
    core.set_default_device = chosen.append
    package = types.ModuleType("mlx")
    package.core = core
    monkeypatch.setitem(sys.modules, "mlx", package)
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    ctx = RuntimeContext()
    phase_platform(ctx)

    assert chosen == [core.gpu]
    assert ctx.mx is core
