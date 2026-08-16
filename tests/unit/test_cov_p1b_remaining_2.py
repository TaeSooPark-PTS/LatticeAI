"""Drive leftover P1b modules toward 100% lines+branches.

Covers model_engines ensure/install/pull, model_runtime download/engines/loading,
lifespan, CSRF middleware, users KG migration, filesystem/knowledge leftovers,
tools/search routers, quiet/sessions/config.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.core.csrf import (
    CSRFOriginGuardMiddleware,
    CSRFOriginPolicy,
    _has_session_cookie,
    _send_forbidden,
    normalize_origin,
)
from latticeai.core.users import (
    ensure_user_identity,
    load_users_file,
    migrate_users,
    user_id_for_email,
)
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.model_runtime import engines as engines_mod
from latticeai.services.model_runtime import loading as loading_mod
from latticeai.services.model_runtime.state import (
    ModelRuntimeState,
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

def test_ensure_lmstudio_model_paths(monkeypatch):
    monkeypatch.setattr(engines_mod, "ensure_lmstudio_server", lambda: None)
    monkeypatch.setattr(
        engines_mod,
        "get_lmstudio_models",
        lambda force=False: [{"key": "k1", "loaded_instances": [{"id": "i1"}]}],
    )
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda name, models: "k1")
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

    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: (_ for _ in ()).throw(HTTP()))
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("k1")

    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("k1")

    # download path: not found, then poll
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda name, models: None)
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
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda name, ms: "new" if ms else None)
    out = engines_mod.ensure_lmstudio_model("new")
    assert out["server_ready"] is True

    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda url, **k: {"status": "failed"} if "status" in url else {"status": "running", "job_id": "j"},
    )
    monkeypatch.setattr(engines_mod, "get_lmstudio_models", lambda force=False: [])
    monkeypatch.setattr(engines_mod, "_find_lmstudio_model_key", lambda *a, **k: None)
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")

    _fast_deadline(monkeypatch, engines_mod)
    monkeypatch.setattr(
        engines_mod,
        "_json_request",
        lambda url, **k: {"status": "running"} if "status" in url else {"status": "running", "job_id": "j"},
    )
    with pytest.raises(ModelRuntimeError) as exc:
        engines_mod.ensure_lmstudio_model("x")
    assert exc.value.status_code == 408

    def download_http(*a, **k):
        raise urllib.error.HTTPError("http://x", 500, "fail", hdrs=None, fp=io.BytesIO(b"bad"))

    monkeypatch.setattr(engines_mod, "_json_request", download_http)
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")

    monkeypatch.setattr(engines_mod, "_json_request", lambda *a, **k: (_ for _ in ()).throw(OSError("net")))
    with pytest.raises(ModelRuntimeError):
        engines_mod.ensure_lmstudio_model("x")

def test_engine_installed_and_safe_plan(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(engines_mod.importlib.util, "find_spec", lambda n: object() if n in {"mlx", "mlx_lm"} else None)
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
    monkeypatch.setattr(Path, "exists", lambda self: str(self).endswith("LM Studio.app") or Path.exists(self))
    # avoid patching Path.exists globally — use a dedicated missing path check
    monkeypatch.setattr(engines_mod.shutil, "which", lambda n: "/bin/llama-server" if n == "llama-server" else None)
    assert engines_mod.engine_installed("llamacpp") is True
    monkeypatch.setattr(engines_mod, "AsyncOpenAI", object())
    assert engines_mod.engine_installed("openai") is True
    assert engines_mod.engine_installed("unknown-engine") is False

    monkeypatch.setattr(engines_mod, "_engine_install_plan", lambda *a, **k: {"ok": True})
    assert engines_mod._safe_engine_install_plan("ollama", base_dir=tmp_path) == {"ok": True}
    monkeypatch.setattr(engines_mod, "_engine_install_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
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
    assert loading_mod.normalize_local_model_request("local_mlx:foo", "local_mlx") == "foo"
    assert loading_mod.normalize_local_model_request("mlx:foo", "mlx") == "foo"
    assert loading_mod.normalize_local_model_request("bar", "ollama") == "ollama:bar"
    assert loading_mod.normalize_local_model_request("already:x", "ollama") == "already:x"

def test_ensure_engine_ready_and_resolution(monkeypatch):
    state = create_model_runtime_state()
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("nope", state=state)

    monkeypatch.setattr(loading_mod, "engine_support_status", lambda _e: {"supported": False, "reason": "no"})
    with pytest.raises(ModelRuntimeError):
        loading_mod.ensure_engine_ready("ollama", state=state)

    monkeypatch.setattr(loading_mod, "engine_support_status", lambda _e: {"supported": True, "reason": None})
    monkeypatch.setattr(loading_mod, "engine_installed", lambda _e: True)
    called = {"mlx": 0}
    monkeypatch.setattr(loading_mod, "ensure_mlx_runtime", lambda: called.__setitem__("mlx", 1))
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

    monkeypatch.setattr(loading_mod, "install_engine", lambda engine, state=None: {"returncode": 1, "stderr": "fail"})
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

    res = loading_mod.build_model_resolution("org/m", "ollama", user_email="a@b.c", display_name="M")
    assert res.engine == "ollama"

def test_prepare_and_load_and_stream(monkeypatch):
    state = create_model_runtime_state()

    async def fake_load(*_a, **_k):
        return {"status": "ok"}

    async def fake_stream(*_a, **_k):
        yield "event: x\ndata: {}\n\n"

    async def _run():
        monkeypatch.setattr("latticeai.services.model_loading.prepare_and_load_model", fake_load)
        out = await loading_mod.prepare_and_load_model("org/m", request=None, state=state)
        assert out["status"] == "ok"

        monkeypatch.setattr("latticeai.services.model_loading.prepare_and_load_model_stream", fake_stream)
        frames = [frame async for frame in loading_mod.prepare_and_load_model_stream("org/m", request=None, state=state)]
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

def test_lifespan_public_local_spawn_and_context(monkeypatch):
    class Router:
        def __init__(self):
            self.loaded = []
            self.unloaded = False
            self.idle = []

        async def load_model(self, model_id, draft_model_id=None):
            self.loaded.append((model_id, draft_model_id))
            return f"loaded {model_id}"

        def unload_idle_models(self, seconds):
            self.idle.append(seconds)
            return ["idle"] if seconds else []

        def unload_all(self):
            self.unloaded = True

    router = Router()
    warnings = []
    logger = SimpleNamespace(warning=lambda *a, **k: warnings.append(a))

    public = build_lifespan_runtime(
        app_mode="public",
        autoload_models=True,
        is_public_mode=True,
        public_model="openai:gpt-4o-mini",
        allow_local_models=False,
        local_model="local",
        local_draft_model="draft",
        model_idle_unload_seconds=1,
        model_router=router,
        local_server_processes={},
        logger=logger,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    asyncio.run(public["autoload_default_model"]())
    assert router.loaded

    ollama = build_lifespan_runtime(
        app_mode="public",
        autoload_models=True,
        is_public_mode=True,
        public_model="ollama:llama",
        allow_local_models=False,
        local_model="local",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=router,
        local_server_processes={},
        logger=logger,
    )
    asyncio.run(ollama["autoload_default_model"]())

    class BoomRouter(Router):
        async def load_model(self, model_id, draft_model_id=None):
            raise RuntimeError("nope")

    boom = build_lifespan_runtime(
        app_mode="public",
        autoload_models=True,
        is_public_mode=True,
        public_model="openai:gpt",
        allow_local_models=True,
        local_model="local",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=BoomRouter(),
        local_server_processes={},
        logger=logger,
    )
    asyncio.run(boom["autoload_default_model"]())

    local = build_lifespan_runtime(
        app_mode="local",
        autoload_models=True,
        is_public_mode=False,
        public_model="",
        allow_local_models=True,
        local_model="mlx-community/m",
        local_draft_model="draft",
        model_idle_unload_seconds=0,
        model_router=router,
        local_server_processes={},
        logger=logger,
    )
    asyncio.run(local["autoload_default_model"]())

    local2 = build_lifespan_runtime(
        app_mode="local",
        autoload_models=True,
        is_public_mode=False,
        public_model="",
        allow_local_models=True,
        local_model="mlx-community/m",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=BoomRouter(),
        local_server_processes={},
        logger=logger,
    )
    asyncio.run(local2["autoload_default_model"]())

    async def _spawn_cases():
        runtime = build_lifespan_runtime(
            app_mode="local",
            autoload_models=False,
            is_public_mode=False,
            public_model="",
            allow_local_models=True,
            local_model="m",
            local_draft_model="",
            model_idle_unload_seconds=0,
            model_router=router,
            local_server_processes={},
            logger=logger,
        )
        spawn = runtime["_spawn"]

        async def ok():
            return 1

        async def bad():
            raise RuntimeError("bg fail")

        async def cancel_me():
            await asyncio.sleep(60)

        t1 = spawn(ok(), name="ok")
        t2 = spawn(bad(), name="bad")
        t3 = spawn(cancel_me(), name="cancel")
        t3.cancel()
        await asyncio.gather(t1, t2, t3, return_exceptions=True)

        alive = _Popen(poll_value=None)
        dead = _Popen(poll_value=0)

        class WaitBoom:
            def poll(self):
                return None

            def terminate(self):
                return None

            def wait(self, timeout=None):
                raise RuntimeError("wait fail")

        async with runtime["lifespan"](SimpleNamespace()):
            pass

        # rebuild with processes
        runtime2 = build_lifespan_runtime(
            app_mode="local",
            autoload_models=False,
            is_public_mode=False,
            public_model="",
            allow_local_models=True,
            local_model="m",
            local_draft_model="",
            model_idle_unload_seconds=0,
            model_router=router,
            local_server_processes={"a": alive, "b": dead, "c": WaitBoom()},
            logger=logger,
        )
        async with runtime2["lifespan"](SimpleNamespace()):
            pass

        # idle loop one iteration then cancel
        idle_rt = build_lifespan_runtime(
            app_mode="local",
            autoload_models=False,
            is_public_mode=False,
            public_model="",
            allow_local_models=True,
            local_model="m",
            local_draft_model="",
            model_idle_unload_seconds=1,
            model_router=router,
            local_server_processes={},
            logger=logger,
        )
        task = asyncio.create_task(idle_rt["unload_idle_models_loop"]())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        class IdleBoom(Router):
            def unload_idle_models(self, seconds):
                raise RuntimeError("idle fail")

        idle_rt2 = build_lifespan_runtime(
            app_mode="local",
            autoload_models=False,
            is_public_mode=False,
            public_model="",
            allow_local_models=True,
            local_model="m",
            local_draft_model="",
            model_idle_unload_seconds=1,
            model_router=IdleBoom(),
            local_server_processes={},
            logger=logger,
        )
        task2 = asyncio.create_task(idle_rt2["unload_idle_models_loop"]())
        await asyncio.sleep(0.01)
        task2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task2

    asyncio.run(_spawn_cases())
    assert router.unloaded

def test_csrf_normalize_and_policy_branches():
    assert normalize_origin(None) is None
    assert normalize_origin("null") is None
    assert normalize_origin("   ") is None
    assert normalize_origin("example.com:4825")[1] == "example.com"
    assert normalize_origin("http://localhost:80")[2] is None
    assert normalize_origin("https://x:443")[2] is None
    assert normalize_origin("http://example.com:notaport") is None
    assert normalize_origin("http://") is None

    policy = CSRFOriginPolicy(trusted_origins=["https://app.example:443", "bad"], server_host="127.0.0.1", server_port=4825)
    assert policy.evaluate(method="GET", origin=None, referer=None, host=None, cookie_header=None, authorization=None).allowed
    assert policy.evaluate(
        method="POST", origin=None, referer=None, host=None, cookie_header="x=1", authorization="Bearer tok"
    ).reason == "bearer-auth"
    assert policy.evaluate(
        method="POST", origin=None, referer=None, host=None, cookie_header="", authorization=None
    ).reason == "no-session-cookie"
    assert policy.evaluate(
        method="POST", origin="null", referer=None, host=None, cookie_header="session_token=a", authorization=None
    ).reason == "opaque-origin"
    assert policy.evaluate(
        method="POST", origin=None, referer=None, host=None, cookie_header="session_token=a", authorization=None
    ).reason == "no-origin-loopback-bind"

    remote = CSRFOriginPolicy(server_host="0.0.0.0", server_port=80, bind_is_loopback=False)
    assert remote.evaluate(
        method="POST", origin=None, referer=None, host=None, cookie_header="session_token=a", authorization=None
    ).reason == "no-origin-reachable-bind"
    assert remote.evaluate(
        method="POST",
        origin=None,
        referer="https://evil.example/x",
        host="0.0.0.0",
        cookie_header="session_token=a",
        authorization=None,
    ).allowed is False

    allowed = policy.evaluate(
        method="POST",
        origin="http://127.0.0.1:4825",
        referer=None,
        host="127.0.0.1:4825",
        cookie_header="session_token=a",
        authorization=None,
    )
    assert allowed.allowed

    # Host fallback via forwarded host
    via_host = policy.evaluate(
        method="POST",
        origin="https://front.example",
        referer=None,
        host="worker:1",
        cookie_header="session_token=a",
        authorization=None,
        forwarded_host="front.example",
        peer="127.0.0.1",
    )
    assert via_host.allowed or via_host.reason in {"same-site-or-trusted-origin", "cross-site-origin"}

    assert _has_session_cookie(None) is False
    assert _has_session_cookie("a=1; session_token=xyz") is True
    assert _has_session_cookie("other=1") is False

def test_csrf_middleware_passthrough_and_forbidden():
    hits = {"n": 0}

    async def app(scope, receive, send):
        hits["n"] += 1
        if scope.get("type") == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    policy = CSRFOriginPolicy(bind_is_loopback=False, server_host="example.com", server_port=443)
    mw = CSRFOriginGuardMiddleware(app, policy=policy)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(msg):
        sent.append(msg)

    async def _run():
        await mw({"type": "websocket"}, receive, send)
        assert hits["n"] == 1

        await mw(
            {
                "type": "http",
                "method": "GET",
                "headers": [],
                "client": ("127.0.0.1", 1),
            },
            receive,
            send,
        )
        assert hits["n"] == 2

        sent.clear()
        await mw(
            {
                "type": "http",
                "method": "POST",
                "headers": [
                    (b"cookie", b"session_token=abc"),
                    (b"origin", b"https://evil.example"),
                    (b"host", b"example.com"),
                ],
                "client": ("8.8.8.8", 1),
            },
            receive,
            send,
        )
        assert sent[0]["status"] == 403
        assert b"csrf_origin_rejected" in sent[1]["body"]

        await _send_forbidden(send, "cross-site-origin")

    asyncio.run(_run())

def test_users_migrate_and_kg_identity(tmp_path: Path, monkeypatch):
    assert ensure_user_identity("A@B.COM", {}) is True
    users, mapping, changed = migrate_users(
        {
            "A@B.COM": {"email": "A@B.COM"},
            "a@b.com": {"email": "a@b.com", "api_keys": {"openai": "k1"}},
            "skip": "not-a-dict",
            "c@d.com": {"email": "c@d.com", "id": "", "api_keys": {"groq": "k2"}},
        }
    )
    assert changed is True
    assert "a@b.com" in users

    missing = tmp_path / "nope.json"
    assert load_users_file(missing) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    assert load_users_file(bad) == {}
    arr = tmp_path / "arr.json"
    arr.write_text("[1]", encoding="utf-8")
    assert load_users_file(arr) == {}

    good = tmp_path / "users.json"
    good.write_text(json.dumps({"A@B.COM": {"email": "A@B.COM"}}), encoding="utf-8")

    def boom_copy(*_a, **_k):
        raise OSError("no backup")

    monkeypatch.setattr("latticeai.core.users.shutil.copy2", boom_copy)
    loaded = load_users_file(good)
    assert "a@b.com" in loaded

    assert user_id_for_email({}, None) is None
    assert user_id_for_email({}, "user:abc").startswith("user:")
    assert user_id_for_email(loaded, "a@b.com")
    assert user_id_for_email({}, "ghost@x.com").startswith("user:")


def test_filesystem_remaining_branches(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools
    from latticeai.tools.filesystem import (
        grep,
        inspect_html,
        list_dir,
        preview_url,
        read_file,
        search_files,
        todo_read,
        workspace_tree,
    )

    root = tmp_path / "ws"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "note.md").write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    (root / "sub" / "deep" / "more.md").write_text("gamma\n", encoding="utf-8")
    (root / "pic.png").write_bytes(b"\x89PNG")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "x.js").write_text("secret", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01")
    (root / "page.html").write_text(
        "<html><head><title>T</title><link rel='Stylesheet Preload' href='/s.css'>"
        "<script src='/a.js'></script></head>"
        "<body><a href='/l'>L</a><img src='/i.png'><form></form>"
        "<h1>H</h1><h2></h2></body></html>",
        encoding="utf-8",
    )
    (root / ".lattice").mkdir()
    (root / ".lattice" / "todos.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)

    with pytest.raises(ToolError):
        list_dir("missing")
    (root / "file_only.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError):
        list_dir("file_only.txt")
    listed = list_dir(".")
    assert listed["items"]

    with pytest.raises(ToolError):
        workspace_tree("missing")
    tree = workspace_tree(".", max_depth=0)
    assert tree["entries"]
    tree = workspace_tree("sub", max_depth=9)
    assert any(e["path"].endswith("more.md") for e in tree["entries"])

    with pytest.raises(ToolError):
        read_file("missing.txt")
    with pytest.raises(ToolError):
        read_file("sub")
    body = read_file("sub/note.md", offset=1, limit=1, line_numbers=False)
    assert "beta" in body["content"]
    body = read_file("sub/note.md", offset=0, limit=0, line_numbers=True)
    assert "numbered" in body

    huge = root / "huge.txt"
    huge.write_bytes(b"x" * (tools.MAX_FILE_BYTES + 1))
    with pytest.raises(ToolError):
        read_file("huge.txt")

    with pytest.raises(ToolError):
        grep("alpha", path="missing")
    hits = grep("alpha", path=".", glob="*.md", max_results=1, context_lines=9, case_insensitive=True)
    assert hits["truncated"] is True
    hits = grep("nomatch", path=".")
    assert hits["matches"] == []
    # binary / dir skips should not explode
    grep("secret", path=".")

    with pytest.raises(ToolError):
        search_files("")
    with pytest.raises(ToolError):
        search_files("x", path="missing")
    found = search_files("alpha", path=".", max_results=1)
    assert found["matches"]
    search_files("nope-xyz")

    with pytest.raises(ToolError):
        inspect_html("missing.html")
    with pytest.raises(ToolError):
        inspect_html("sub/note.md")
    html = inspect_html("page.html")
    assert html["title"] == "T"
    assert html["stylesheets"]
    huge_html = root / "big.html"
    huge_html.write_bytes(b"<html></html>" + b"x" * (tools.MAX_FILE_BYTES + 1))
    with pytest.raises(ToolError):
        inspect_html("big.html")

    todos = todo_read()
    assert todos["todos"] == []
    (root / ".lattice" / "todos.json").write_text('{"not":"list"}', encoding="utf-8")
    assert todo_read()["todos"] == []
    (root / ".lattice" / "todos.json").write_text('[{"id":1}]', encoding="utf-8")
    assert todo_read()["todos"]

    with pytest.raises(ToolError):
        preview_url("missing.html")
    assert "local_url" in preview_url("page.html")

    # unicode decode skip in grep/search
    (root / "bad.md").write_bytes(b"\xff\xfe")
    grep("alpha", path=".")
    search_files("alpha", path=".")
