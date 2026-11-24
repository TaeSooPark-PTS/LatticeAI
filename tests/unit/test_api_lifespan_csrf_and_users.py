"""Unit tests for API lifespan runtime, CSRF origin guard, and user identity migration."""

from __future__ import annotations

import asyncio
import json
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
from tests.unit._runtime_test_fakes import _Popen


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

    policy = CSRFOriginPolicy(
        trusted_origins=["https://app.example:443", "bad"],
        server_host="127.0.0.1",
        server_port=4825,
    )
    assert policy.evaluate(
        method="GET",
        origin=None,
        referer=None,
        host=None,
        cookie_header=None,
        authorization=None,
    ).allowed
    assert (
        policy.evaluate(
            method="POST",
            origin=None,
            referer=None,
            host=None,
            cookie_header="x=1",
            authorization="Bearer tok",
        ).reason
        == "bearer-auth"
    )
    assert (
        policy.evaluate(
            method="POST",
            origin=None,
            referer=None,
            host=None,
            cookie_header="",
            authorization=None,
        ).reason
        == "no-session-cookie"
    )
    assert (
        policy.evaluate(
            method="POST",
            origin="null",
            referer=None,
            host=None,
            cookie_header="session_token=a",
            authorization=None,
        ).reason
        == "opaque-origin"
    )
    assert (
        policy.evaluate(
            method="POST",
            origin=None,
            referer=None,
            host=None,
            cookie_header="session_token=a",
            authorization=None,
        ).reason
        == "no-origin-loopback-bind"
    )

    remote = CSRFOriginPolicy(
        server_host="0.0.0.0", server_port=80, bind_is_loopback=False
    )
    assert (
        remote.evaluate(
            method="POST",
            origin=None,
            referer=None,
            host=None,
            cookie_header="session_token=a",
            authorization=None,
        ).reason
        == "no-origin-reachable-bind"
    )
    assert (
        remote.evaluate(
            method="POST",
            origin=None,
            referer="https://evil.example/x",
            host="0.0.0.0",
            cookie_header="session_token=a",
            authorization=None,
        ).allowed
        is False
    )

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
    assert via_host.allowed or via_host.reason in {
        "same-site-or-trusted-origin",
        "cross-site-origin",
    }

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

    policy = CSRFOriginPolicy(
        bind_is_loopback=False, server_host="example.com", server_port=443
    )
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
