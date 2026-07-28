from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from latticeai.runtime.automation_runtime import build_automation_runtime
from latticeai.runtime.bootstrap import build_session_runtime
from latticeai.runtime.hooks_runtime import (
    bind_builtin_hook_runners,
    bind_trigger_hook_runner,
)
from latticeai.runtime.lifespan_runtime import build_lifespan_runtime
from latticeai.runtime.persistence_runtime import build_persistence_runtime
from latticeai.runtime.web_runtime import build_web_runtime
from latticeai.services.triggers import TRIGGER_HOOK_NAME

_BUILTIN_HOOK_IDS = (
    "builtin:redact-secrets",
    "builtin:audit-agent-run",
    "builtin:pipeline-index-status",
    "builtin:research-memory-snapshot",
    "builtin:tool-permission-gate",
    "builtin:sensitive-data-guard",
    "builtin:workflow-replay-log",
)


def test_trigger_runtime_executes_agent_nodes_with_ingestion_scope(tmp_path):
    calls = []

    class _Platform:
        build_orchestrator = staticmethod(lambda _user, _scope: object())
        build_workflow_runners = staticmethod(lambda _user, _scope: {})

        @staticmethod
        def run_workflow_by_id(*args, **kwargs):
            calls.append((args, kwargs))
            return {"workflow_run_id": "run-grounded"}

    store = type("Store", (), {"upsert_memory": staticmethod(lambda **_kwargs: {})})()
    runtime = build_automation_runtime(
        store=store,
        platform=_Platform(),
        data_dir=tmp_path,
        workspace_graph=lambda: None,
        append_audit_event=lambda *_args, **_kwargs: None,
        hooks=None,
    )

    result = runtime["TRIGGER_SERVICE"]._run_workflow(
        "workflow-memory",
        {
            "__trigger__": {
                "source_type": "note",
                "user_email": "owner@example.com",
                "workspace_id": "org:acme",
            }
        },
    )

    assert result["workflow_run_id"] == "run-grounded"
    assert calls[0][0] == ("workflow-memory", "owner@example.com", "org:acme")
    assert calls[0][1]["with_agent"] is True


def test_session_runtime_preserves_token_lifecycle():
    runtime = build_session_runtime(user_id_resolver=lambda email: f"user:{email}")

    token = runtime["create_session"]("owner@example.com")

    assert runtime["get_session_email"](token) == "owner@example.com"
    assert runtime["get_session_user_id"](token) == "user:owner@example.com"

    runtime["invalidate_session"](token)

    assert runtime["get_session_email"](token) is None
    assert runtime["get_session_user_id"](token) is None


def test_trigger_hook_runner_binding_is_idempotent():
    class FakeRegistry:
        def __init__(self):
            self._state = {"custom": []}
            self.bound = []

        def register(self, *, name, kind, description):
            hook = {
                "id": "hook-1",
                "name": name,
                "kind": kind,
                "description": description,
            }
            self._state["custom"].append(hook)
            return hook

        def register_hook(self, hook_id, runner):
            self.bound.append((hook_id, runner))

    class FakeTriggerService:
        def hook_runner(self):
            return "runner"

    registry = FakeRegistry()
    trigger_service = FakeTriggerService()

    first = bind_trigger_hook_runner(registry=registry, trigger_service=trigger_service)
    second = bind_trigger_hook_runner(registry=registry, trigger_service=trigger_service)

    assert first == second == "hook-1"
    assert [h["name"] for h in registry._state["custom"]] == [TRIGGER_HOOK_NAME]
    assert registry.bound == [("hook-1", "runner"), ("hook-1", "runner")]


def test_builtin_hook_runners_binding_registers_all_platform_runners():
    class FakeRegistry:
        def __init__(self):
            self.bound = []

        def register_hook(self, hook_id, runner):
            self.bound.append((hook_id, runner))

    registry = FakeRegistry()
    audit_calls = []

    bind_builtin_hook_runners(
        registry=registry,
        append_audit_event=lambda **kwargs: audit_calls.append(kwargs),
        get_tool_permission=lambda tool: {"tool": tool, "risk": "low"},
        classify_sensitive_message=lambda message, index: {
            "sensitivity": "low",
            "labels": [],
        },
    )

    assert [hook_id for hook_id, _runner in registry.bound] == list(_BUILTIN_HOOK_IDS)
    assert all(callable(runner) for _hook_id, runner in registry.bound)


def test_builtin_hook_runners_binding_is_repeatable():
    class FakeRegistry:
        def __init__(self):
            self.bound = []

        def register_hook(self, hook_id, runner):
            self.bound.append((hook_id, runner))

    registry = FakeRegistry()
    deps = {
        "append_audit_event": lambda **kwargs: None,
        "get_tool_permission": lambda tool: {"risk": "low"},
        "classify_sensitive_message": lambda message, index: {"labels": []},
    }

    bind_builtin_hook_runners(registry=registry, **deps)
    first_count = len(registry.bound)
    bind_builtin_hook_runners(registry=registry, **deps)

    assert first_count == len(_BUILTIN_HOOK_IDS)
    assert len(registry.bound) == len(_BUILTIN_HOOK_IDS) * 2


def test_web_runtime_mounts_static_assets_in_legacy_order(tmp_path):
    @asynccontextmanager
    async def lifespan(_app):
        yield

    static_dir = tmp_path / "static"
    (static_dir / "icons").mkdir(parents=True)

    runtime = build_web_runtime(
        app_mode="local",
        app_version="test-version",
        lifespan=lifespan,
        default_host="0.0.0.0",
        default_port=8080,
        cors_extra_origins=["http://example.test"],
        cors_allow_network=True,
        static_dir=static_dir,
    )

    app = runtime["app"]
    mounts = [route.path for route in app.routes if route.__class__.__name__ == "Mount"]

    assert mounts[-2:] == ["/static", "/icons"]
    assert runtime["CORS_ALLOWED_ORIGINS"] == [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://example.test",
        "http://0.0.0.0:8080",
        "https://0.0.0.0:8080",
    ]


def test_persistence_runtime_constructs_local_service_graph(tmp_path):
    class FakeHooks:
        def dispatch(self, *_args, **_kwargs):
            return []

    runtime = build_persistence_runtime(
        data_dir=tmp_path,
        base_dir=tmp_path,
        enable_graph=False,
        knowledge_graph=None,
        hooks_registry=FakeHooks(),
        history_file=tmp_path / "chat_history.json",
        conversations=None,
        user_id_for_email=lambda email: f"user:{email}" if email else None,
        audit=lambda _action, _detail, _user: None,
    )

    assert runtime["REALTIME_BUS"] is not None
    assert runtime["WORKSPACE_OS"] is not None
    assert runtime["WORKSPACE_SERVICE"] is not None
    assert runtime["MEMORY_SERVICE"] is not None
    assert runtime["INGESTION_PIPELINE"] is not None
    assert runtime["DEVICE_IDENTITY"] is not None
    assert runtime["KG_PORTABILITY"] is not None


def test_lifespan_runtime_runs_startup_and_shutdown_hooks():
    class FakeRouter:
        def __init__(self):
            self.unloaded = False

        async def load_model(self, *_args, **_kwargs):
            return "loaded"

        def unload_idle_models(self, *_args, **_kwargs):
            return []

        def unload_all(self):
            self.unloaded = True

    class FakeWatcher:
        def __init__(self):
            self.restored = False
            self.stopped = False

        def restore_enabled_sources(self):
            self.restored = True
            return {"restored": []}

        def stop_all(self):
            self.stopped = True

    class FakeLogger:
        def warning(self, *_args, **_kwargs):
            pass

    router = FakeRouter()
    watcher = FakeWatcher()
    runtime = build_lifespan_runtime(
        app_mode="local",
        enable_telegram=False,
        autoload_models=False,
        is_public_mode=False,
        public_model="openai:test",
        allow_local_models=True,
        local_model="local:test",
        local_draft_model="",
        model_idle_unload_seconds=0,
        model_router=router,
        local_kg_watcher=watcher,
        local_server_processes={},
        logger=FakeLogger(),
    )

    async def run_lifespan():
        async with runtime["lifespan"](object()):
            await asyncio.sleep(0)

    asyncio.run(run_lifespan())

    assert watcher.restored is True
    assert watcher.stopped is True
    assert router.unloaded is True
