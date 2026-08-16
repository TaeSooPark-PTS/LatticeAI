"""Drive leftover P1b modules toward 100% lines+branches.

Covers model_engines ensure/install/pull, model_runtime download/engines/loading,
lifespan, CSRF middleware, users migration, filesystem/knowledge leftovers,
the search router, quiet/sessions/config.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.core.config import (
    Config,
    _bool,
    _int,
    _port,
    _str,
    _value,
    default_data_dir,
)
from latticeai.core.quiet import quiet
from latticeai.core.sessions import (
    SessionStore,
    _entry_created_at,
    _entry_email,
    _entry_subject,
    _hash_token,
    _looks_hashed,
    _sessions_file,
    load_sessions,
    persist_sessions,
)
from latticeai.core.users import (
    migrate_users,
)
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.services import model_engines
from latticeai.services.model_runtime import download as download_mod
from latticeai.services.model_runtime import engines as engines_mod
from latticeai.services.model_runtime import loading as loading_mod
from latticeai.services.model_runtime.state import (
    create_model_runtime_state,
)
from latticeai.tools import ToolError

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

def test_knowledge_remaining_branches(tmp_path: Path, monkeypatch):
    from latticeai.tools.knowledge import (
        knowledge_scope_root,
        knowledge_search,
        knowledge_tree,
        obsidian_search,
        obsidian_tree,
    )

    brain = tmp_path / "brain"
    brain.mkdir()
    monkeypatch.setattr("latticeai.tools.knowledge.BRAIN_DIR", brain)
    monkeypatch.setattr("latticeai.tools.knowledge.STRUCTURE", {"10_Wiki": "x", "00_Raw": "y"})

    assert knowledge_scope_root() == brain
    with pytest.raises(ToolError):
        knowledge_scope_root(workspace_id="ws")
    scoped = knowledge_scope_root(workspace_id="ws", user_email="a@b.c")
    assert ".lattice-scopes" in str(scoped)

    with pytest.raises(ToolError):
        knowledge_search("")
    (brain / "10_Wiki").mkdir()
    (brain / "10_Wiki" / "hit.md").write_text("remember this", encoding="utf-8")
    (brain / "10_Wiki" / "name-only.md").write_text("zzz", encoding="utf-8")
    (brain / "10_Wiki" / "bad.md").write_bytes(b"\xff\xfe")
    hits = knowledge_search("remember", max_results=1)
    assert hits["results"]
    hits = knowledge_search("name-only", max_results=20)
    assert hits["results"]

    tree = knowledge_tree()
    assert tree["entries"]
    scoped_tree = knowledge_tree(workspace_id="ws", user_email="a@b.c")
    assert scoped_tree["root"]
    obs = obsidian_search("remember")
    assert "vault_root" in obs
    assert obsidian_tree()["root"]

def test_search_router_remaining_lines():
    from latticeai.api.search import create_search_router

    class Svc:
        def embeddings_status(self, resolved=None, refresh=False):
            if refresh:
                raise ValueError("no embedder")
            return {"ok": True, "resolved": resolved}

    app = FastAPI()
    app.include_router(
        create_search_router(
            service=Svc(),
            require_user=lambda _r: "u",
            embedding_info=None,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    ok = client.get("/api/embeddings/status")
    assert ok.status_code == 200
    err = client.get("/api/embeddings/status", params={"refresh": True})
    assert err.status_code == 404

    app2 = FastAPI()
    app2.include_router(
        create_search_router(
            service=Svc(),
            require_user=lambda _r: "u",
            embedding_info=lambda: {"profiles": [{"id": "hash"}], "active_provider": "hash"},
        )
    )
    client2 = TestClient(app2, raise_server_exceptions=False)
    resolved = client2.get("/api/embeddings/status")
    assert resolved.json()["resolved"]["active_provider"] == "hash"

def test_quiet_remaining():
    from latticeai.core import quiet as quiet_mod

    quiet()

    quiet_mod.logger.setLevel(logging.DEBUG)

    def inner():
        raise ValueError("deep-fail")

    def outer():
        inner()

    try:
        outer()
    except ValueError:
        quiet("optional probe")
        quiet()

def test_sessions_remaining(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "latticeai.core.config.Config.from_env",
        lambda: (_ for _ in ()).throw(RuntimeError("no cfg")),
    )
    monkeypatch.delenv("LATTICEAI_DATA_DIR", raising=False)
    path = _sessions_file(None)
    assert path.name == "sessions.json"

    data_dir = tmp_path / "sess"
    raw = {
        "not-a-hash-token": ["user@x.com", time.time(), "user@x.com"],
        _hash_token("already"): ["b@c.d", time.time(), "b@c.d"],
    }
    (data_dir).mkdir()
    (data_dir / "sessions.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_sessions(data_dir)
    assert all(_looks_hashed(k) for k in loaded)

    (data_dir / "sessions.json").write_text("not-json", encoding="utf-8")
    assert load_sessions(data_dir) == {}

    monkeypatch.setattr(
        "latticeai.core.sessions.atomic_write_json",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")),
    )
    persist_sessions({}, data_dir)

    assert _entry_subject(()) is None
    assert _entry_email(("s",)) == "s"
    assert _entry_email(("s", 1.0, "e@x")) == "e@x"
    assert _entry_created_at(()) == 0.0
    assert _entry_created_at(("s", 3.0)) == 3.0
    assert _looks_hashed("0" * 64) is True
    assert _looks_hashed("zz") is False

    store_dir = tmp_path / "store"
    store = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=0)
    token = store.create("sub", email="e@x.com")
    assert store.get_email(token) == "e@x.com"
    assert store.get_subject(token) == "sub"
    assert store.get_email("missing") is None

    store2 = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=10**9)
    token2 = store2.create("s2", email="f@x.com")
    assert store2.get_subject(token2)

    expired = SessionStore(store_dir, ttl_seconds=1, refresh_threshold_seconds=0)
    token3 = expired.create("s3")
    expired._sessions[_hash_token(token3)] = ("s3", time.time() - 10, "s3")
    assert expired.get_subject(token3) is None
    expired.invalidate(token3)

def test_config_remaining(tmp_path: Path, monkeypatch):
    assert _value({}, "K", "d") == "d"
    assert _str({"K": ""}, "K", "d") == ""
    assert _str({}, "K", "d") == "d"
    assert _bool({}, "K", True) is True
    assert _bool({"K": "YES"}, "K") is True
    assert _bool({"K": "off"}, "K") is False
    assert _bool({"K": "maybe"}, "K", True) is True
    assert _int({}, "K", 3) == 3
    assert _int({"K": "  "}, "K", 3) == 3
    assert _int({"K": "9"}, "K", 3) == 9
    assert _int({"K": "no"}, "K", 3) == 3
    assert _port({"K": "0"}, "K", 80) == 80
    assert _port({"K": "443"}, "K", 80) == 443

    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "from-env"))
    assert "from-env" in str(default_data_dir(None))

    packaged = tmp_path / "prefix" / "static"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "prefix"))
    cfg = Config.from_env(
        {
            "LATTICEAI_MODE": "weird",
            "LATTICEAI_HOST": "0.0.0.0",
            "LATTICEAI_PORT": "99999",
            "LATTICEAI_ENABLE_TELEGRAM": "maybe",
            "LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS": "nope",
            "LATTICE_TZ": "",
            "LATTICEAI_STATIC_DIR": str(tmp_path / "missing-static"),
            "LATTICEAI_CORS_ALLOWED_ORIGINS": "http://a.com, http://b.com",
            "LATTICEAI_CSRF_TRUSTED_ORIGINS": "https://app.example",
            "LATTICEAI_ADMIN_EMAILS": "A@B.COM",
            "LATTICEAI_TRUSTED_PROXIES": "10.0.0.1",
            "LATTICEAI_RATE_LIMIT": "0",
            "LATTICEAI_DATA_DIR": str(tmp_path / "data"),
            "LATTICEAI_REQUIRE_AUTH": "false",
            "LATTICEAI_OPEN_REGISTRATION": "true",
            "LATTICEAI_STORAGE_ENGINE": "  ",
        },
        base_dir=tmp_path,
    )
    assert cfg.app_mode == "local"
    assert cfg.network_exposed is True
    assert cfg.require_auth is True
    assert cfg.open_registration is False
    assert cfg.rate_limit_enabled is False
    assert cfg.timezone == "UTC"
    assert cfg.static_dir == packaged
    assert cfg.storage_engine == "sqlite"

    public = Config.from_env({"LATTICEAI_MODE": "public", "LATTICEAI_HOST": "127.0.0.1"}, base_dir=tmp_path)
    assert public.is_public is True
    assert public.require_auth is True

    live = Config.from_env(None, base_dir=tmp_path)
    assert live.app_mode in {"local", "public"}

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
    monkeypatch.setattr(download_mod, "hf_repo_files_with_sizes", lambda _r: [{"name": "a.bin", "size": 0}])

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
        lambda force=False: [{"key": "done", "loaded_instances": [{"id": "i"}]}] if force else [],
    )
    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda *a, **k: {"status": "already_downloaded"},
    )
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda name, models: "done" if models else None)
    out = engines_mod.ensure_lmstudio_model("done")
    assert out["server_ready"] is True

    # ensure_engine_ready successful install of non-mlx
    state = create_model_runtime_state()
    installed = {"now": False}
    monkeypatch.setattr(loading_mod, "engine_support_status", lambda _e: {"supported": True, "reason": None})
    monkeypatch.setattr(loading_mod, "engine_installed", lambda _e: installed["now"])
    monkeypatch.setattr(
        loading_mod,
        "install_engine",
        lambda engine, state=None: installed.__setitem__("now", True) or {"returncode": 0},
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

    monkeypatch.setattr(model_engines, "lmstudio_native_api_base", lambda: "http://127.0.0.1:1234")
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
        lambda engine, base_dir=None: {"command_hash": "h", "command_preview": "echo", "command": ["echo"]},
    )
    monkeypatch.setattr(model_engines, "require_command_confirmation", lambda *a, **k: None)
    monkeypatch.setattr(model_engines, "append_process_audit_event", lambda *a, **k: None)
    monkeypatch.setattr("latticeai.services.model_runtime.engine_installed", lambda _e: True)
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

def test_core_leftovers_users_sessions_config_quiet_permission(tmp_path: Path, monkeypatch):
    # merge without api_keys dicts
    migrated, _, changed = migrate_users(
        {
            "a@b.com": {"email": "a@b.com", "id": "user:1"},
            "A@B.COM": {"email": "A@B.COM", "id": "user:2"},
        }
    )
    assert changed is True
    assert migrated["a@b.com"]["id"] == "user:1"

    # sessions refresh path (0 is falsy in ctor)
    store = SessionStore(tmp_path / "s", ttl_seconds=1000, refresh_threshold_seconds=1)
    store._refresh_threshold_seconds = 0
    token = store.create("sub", email="e@x.com")
    assert store.get_subject(token) == "sub"

    # static dir missing and packaged static also missing
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "noprefix"))
    cfg = Config.from_env(
        {"LATTICEAI_STATIC_DIR": str(tmp_path / "no-static"), "LATTICEAI_DATA_DIR": str(tmp_path / "d")},
        base_dir=tmp_path,
    )
    assert cfg.static_dir.name == "no-static"

    # quiet with exception but no traceback
    from latticeai.core import quiet as quiet_mod

    monkeypatch.setattr(quiet_mod.sys, "exc_info", lambda: (ValueError, ValueError("x"), None))
    quiet_mod.logger.setLevel(logging.DEBUG)
    quiet("no-tb")

    from latticeai.core.permission_mode import effective_auto_approve

    assert (
        effective_auto_approve(
            "trusted",
            "write_file",
            {"risk": "exec", "sandbox": "workspace"},
            change_class="other",
        )
        is False
    )
    assert (
        effective_auto_approve(
            "trusted",
            "write_file",
            {"risk": "destructive", "sandbox": "workspace"},
        )
        is False
    )
    assert (
        effective_auto_approve(
            "bypass",
            "shell_exec",
            {"risk": "exec", "sandbox": "system"},
        )
        is False
    )

def test_filesystem_knowledge_documents_local_and_commands(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools
    from latticeai.tools.commands import git_diff
    from latticeai.tools.documents import document_output_target, read_document
    from latticeai.tools.filesystem import inspect_html
    from latticeai.tools.knowledge import knowledge_search
    from latticeai.tools.local_files import local_list, local_read

    root = tmp_path / "ws"
    root.mkdir()
    (root / "page.html").write_text("<html><link rel='stylesheet'></html>", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    inspect_html("page.html")

    brain = tmp_path / "brain"
    (brain / "10_Wiki").mkdir(parents=True)
    (brain / "10_Wiki" / "a.md").write_text("remember one", encoding="utf-8")
    (brain / "10_Wiki" / "b.md").write_text("remember two", encoding="utf-8")
    monkeypatch.setattr("latticeai.tools.knowledge.BRAIN_DIR", brain)
    monkeypatch.setattr("latticeai.tools.knowledge.STRUCTURE", {"10_Wiki": "x"})
    hits = knowledge_search("remember", max_results=1)
    assert len(hits["results"]) == 1

    assert document_output_target("nope", "x") is None
    with pytest.raises(ToolError):
        read_document(str(tmp_path / "missing.txt"))
    with pytest.raises(ToolError):
        read_document(str(root))
    huge = root / "huge.txt"
    from latticeai.tools import DOCUMENT_MAX_READ_BYTES

    huge.write_bytes(b"x" * (DOCUMENT_MAX_READ_BYTES + 1))
    with pytest.raises(ToolError):
        read_document(str(huge))

    bad_pdf = root / "bad.pdf"
    bad_pdf.write_bytes(b"not-a-pdf")
    with pytest.raises(ToolError):
        read_document(str(bad_pdf))
    bad_docx = root / "bad.docx"
    bad_docx.write_bytes(b"not-docx")
    with pytest.raises(ToolError):
        read_document(str(bad_docx))
    bad_xlsx = root / "bad.xlsx"
    bad_xlsx.write_bytes(b"not-xlsx")
    with pytest.raises(ToolError):
        read_document(str(bad_xlsx))
    bad_pptx = root / "bad.pptx"
    bad_pptx.write_bytes(b"not-pptx")
    with pytest.raises(ToolError):
        read_document(str(bad_pptx))

    note = root / "ok.md"
    note.write_text("hello", encoding="utf-8")
    assert read_document(str(note))["content"]
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(ToolError):
        read_document(str(note))

    with pytest.raises(ToolError):
        local_list(str(tmp_path / "missing-dir"))
    with pytest.raises(ToolError):
        local_list(str(note))
    with pytest.raises(ToolError):
        local_read(str(tmp_path / "missing.txt"))

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    from latticeai.tools import commands as command_tools

    monkeypatch.setattr(command_tools.subprocess, "run", lambda *a, **k: Done())
    (root / "notes.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    assert git_diff(path="notes.md")["returncode"] == 0
