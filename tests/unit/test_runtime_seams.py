from __future__ import annotations

from contextlib import asynccontextmanager

from latticeai.runtime.bootstrap import build_session_runtime
from latticeai.runtime.hooks_runtime import (
    bind_builtin_hook_runners,
    bind_trigger_hook_runner,
)
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
