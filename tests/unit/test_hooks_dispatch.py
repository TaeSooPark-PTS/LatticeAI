"""Unit tests for the v3.4.0 Hooks Dispatch execution engine.

Covers the engine itself (ordering, enable filtering, blocking gate semantics,
in-process runners, subprocess command hooks, the run log) and the two cleanest
lifecycle integrations: ``AgentRuntime`` (pre_run/post_run) and
``WorkflowEngine`` (workflow start/end).
"""

from __future__ import annotations

import shlex
import sys

import pytest

from lattice_brain.runtime.hooks import (
    HookContext,
    HookResult,
    HooksRegistry,
    hook_context,
)

# Quoted interpreter path — this checkout lives under a directory with a space,
# so command strings must quote the executable (a real user command would too).
PY = shlex.quote(sys.executable)


@pytest.fixture()
def registry(tmp_path):
    return HooksRegistry(tmp_path / "hooks.json", command_timeout=10.0)


# ── engine ──────────────────────────────────────────────────────────────────
def test_run_hooks_runs_enabled_in_order(registry):
    calls = []
    # Two built-ins of the same kind (pre_tool): order 10 then 20.
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: calls.append("gate"))
    registry.register_hook("builtin:sensitive-data-guard", lambda ctx: calls.append("guard"))
    out = registry.run_hooks("pre_tool", event="tool.read_file")
    assert out["kind"] == "pre_tool"
    assert out["ran"] == 2
    assert out["blocked"] is False
    # builtin:tool-permission-gate has order 10, sensitive-data-guard order 20.
    assert calls == ["gate", "guard"]
    assert [r["status"] for r in out["results"]] == ["ok", "ok"]


def test_disabled_hook_is_not_run(registry):
    ran = []
    registry.register_hook("builtin:redact-secrets", lambda ctx: ran.append("redact"))
    registry.set_enabled("builtin:redact-secrets", False)
    out = registry.run_hooks("pre_run", event="agent.run")
    assert "builtin:redact-secrets" not in [r["hook_id"] for r in out["results"]]
    assert ran == []


def test_runner_can_mutate_payload(registry):
    def redact(ctx):
        ctx.payload["token"] = "***"
        return {"status": "ok", "output": "redacted"}

    registry.register_hook("builtin:redact-secrets", redact)
    ctx = HookContext("pre_run", "agent.run", payload={"token": "sk-secret", "goal": "x"})
    registry.run_hooks("pre_run", ctx)
    assert ctx.payload["token"] == "***"
    assert ctx.payload["goal"] == "x"


def test_pre_run_block_short_circuits(registry):
    after = []
    registry.register_hook("builtin:redact-secrets", lambda ctx: ctx.block("policy denied"))
    # research-memory-snapshot is an "agent" kind, not pre_run — add a 2nd pre_run
    # via a custom hook to prove the gate stops before it.
    custom = registry.register(name="second gate", kind="pre_run")
    registry.register_hook(custom["id"], lambda ctx: after.append("ran"))
    out = registry.run_hooks("pre_run", event="agent.run")
    assert out["blocked"] is True
    assert out["block_reason"] == "policy denied"
    assert after == []  # the second pre_run hook never ran
    assert out["results"][0]["status"] == "blocked"


def test_command_hook_ok(registry):
    hook = registry.register(name="echo ok", kind="post_run", command=f"{PY} -c \"print('hi')\"")
    out = registry.run_hook(hook["id"], event="agent.run")
    assert out["status"] == "ok"
    assert "hi" in out["output"]
    assert out["blocked"] is False


def test_command_hook_pre_nonzero_blocks(registry):
    hook = registry.register(name="deny", kind="pre_tool", command=f"{PY} -c \"import sys; sys.exit(1)\"")
    out = registry.run_hook(hook["id"], event="tool.write_file")
    assert out["status"] == "blocked"
    assert out["blocked"] is True


def test_command_hook_post_nonzero_errors_without_blocking(registry):
    hook = registry.register(name="boom", kind="post_run", command=f"{PY} -c \"import sys; sys.exit(3)\"")
    out = registry.run_hook(hook["id"], event="agent.run")
    assert out["status"] == "error"
    assert out["blocked"] is False


def test_command_hook_receives_context_on_stdin(registry):
    # The hook echoes back stdin; we assert the event made it through.
    script = "import sys, json; d=json.load(sys.stdin); print(d['event'])"
    hook = registry.register(name="reflect", kind="post_tool", command=f"{PY} -c \"{script}\"")
    out = registry.run_hook(hook["id"], event="tool.list_dir", payload={"tool": "list_dir"})
    assert out["status"] == "ok"
    assert "tool.list_dir" in out["output"]


def test_command_hook_receives_only_minimal_environment(registry, monkeypatch):
    monkeypatch.setenv("LATTICE_TEST_SECRET", "must-not-leak")
    script = (
        "import os; "
        "print(os.environ.get('LATTICE_TEST_SECRET', 'missing')); "
        "print(bool(os.environ.get('PATH'))); "
        "print(bool(os.environ.get('LATTICE_HOOK_CONTEXT')))"
    )
    hook = registry.register(
        name="environment boundary",
        kind="post_tool",
        command=f"{PY} -c \"{script}\"",
    )

    out = registry.run_hook(hook["id"], event="tool.list_dir")

    assert out["status"] == "ok"
    assert out["output"].splitlines() == ["missing", "True", "True"]


def test_advisory_when_no_runner_and_no_command(registry):
    # research-memory-snapshot built-in has no bound runner here → advisory.
    out = registry.run_hook("builtin:research-memory-snapshot", event="agent.run")
    assert out["status"] == "advisory"
    assert out["blocked"] is False


def test_misbehaving_runner_is_isolated(registry):
    def boom(ctx):
        raise RuntimeError("kaboom")

    registry.register_hook("builtin:redact-secrets", boom)
    out = registry.run_hooks("pre_run", event="agent.run")
    res = next(r for r in out["results"] if r["hook_id"] == "builtin:redact-secrets")
    assert res["status"] == "error"
    assert "kaboom" in res["detail"]
    assert out["blocked"] is False  # an error is not a block


def test_fire_hook_never_raises_on_bad_kind(registry):
    out = registry.fire_hook("not_a_kind", "x")
    assert out["ran"] == 0
    assert "error" in out


def test_run_log_records_and_persists(tmp_path):
    reg = HooksRegistry(tmp_path / "hooks.json")
    reg.register_hook("builtin:redact-secrets", lambda ctx: {"status": "ok", "output": "ok"})
    reg.run_hooks("pre_run", event="agent.run")
    runs = reg.recent_runs()
    assert runs["total"] >= 1
    assert runs["runs"][0]["target_event"] == "agent.run"
    # Persisted across a fresh registry instance.
    reg2 = HooksRegistry(tmp_path / "hooks.json")
    assert reg2.recent_runs()["total"] >= 1


def test_recent_runs_filter_by_kind(registry):
    registry.run_hooks("pre_run", event="agent.run")
    registry.run_hooks("post_workflow", event="workflow.end")
    only = registry.recent_runs(kind="post_workflow")
    assert only["total"] >= 1
    assert all(r.get("target_kind") == "post_workflow" for r in only["runs"])


def test_legacy_kind_aliases_map_forward(registry):
    # Old "workflow"/"pipeline" kinds still accepted and mapped to the v3.4.1 pairs.
    out = registry.run_hooks("workflow", event="legacy")
    assert out["kind"] == "post_workflow"
    custom = registry.register(name="legacy pipe", kind="pipeline")
    assert custom["kind"] == "post_index"


def test_hook_context_and_result_factories():
    ctx = hook_context("pre_run", "agent.run", payload={"a": 1})
    assert ctx.kind == "pre_run" and ctx.event == "agent.run" and ctx.payload["a"] == 1
    res = HookResult(hook_id="x", status="ok")
    assert res.as_dict()["hook_id"] == "x"


# ── AgentRuntime integration ─────────────────────────────────────────────────
class _FakeResult:
    agent_id = "agent:executor"
    status = "ok"
    output = "Processed goal"
    timeline = [{"event": "end"}]
    plan = {}
    plan_review = {}
    review = {}
    roles_run = ["planner", "executor", "reviewer"]
    retries = 0
    handoffs = []
    context_packets = []
    review_history = []
    retry_history = []
    memory_snapshots = []

    def as_dict(self):
        return {"status": self.status, "output": self.output}


class _FakeOrchestrator:
    def run(self, goal, **kwargs):
        return _FakeResult()


class _FakeStore:
    def __init__(self):
        self.runs = []

    def list_agents(self, workspace_id=None):
        return {"agents": [], "runs": self.runs}

    def record_agent_run(self, **kwargs):
        run = {"id": "run-1", **kwargs}
        self.runs.insert(0, run)
        return run


def _make_runtime(registry):
    from lattice_brain.runtime.agent_runtime import AgentRuntime

    return AgentRuntime(
        store=_FakeStore(),
        orchestrator_factory=lambda u, s: _FakeOrchestrator(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        hooks=registry,
        allow_simulation_runs=True,
    )


def test_agent_runtime_fires_pre_and_post_run(registry):
    fired = []
    registry.register_hook("builtin:redact-secrets", lambda ctx: fired.append(("pre", ctx.event)))
    registry.register_hook("builtin:audit-agent-run", lambda ctx: fired.append(("post", ctx.payload.get("status"))))
    runtime = _make_runtime(registry)
    out = runtime.start("do a thing", user_email="u@x.com", scope=None)
    assert out["run"]["id"] == "run-1"
    assert ("pre", "agent.run") in fired
    assert ("post", "ok") in fired
    assert out["pre_run_hooks"]["ran"] >= 1
    assert out["post_run_hooks"]["ran"] >= 1


def test_agent_runtime_pre_run_block_aborts(registry):
    registry.register_hook("builtin:redact-secrets", lambda ctx: ctx.block("not allowed"))
    runtime = _make_runtime(registry)
    with pytest.raises(PermissionError, match="not allowed"):
        runtime.start("do a thing", user_email="u@x.com", scope=None)


# ── WorkflowEngine integration ───────────────────────────────────────────────
def test_workflow_engine_fires_lifecycle_hooks(registry):
    from lattice_brain.workflow import WorkflowEngine

    events = []
    pre = registry.register(name="wf pre", kind="pre_workflow")
    post = registry.register(name="wf post", kind="post_workflow")
    registry.register_hook(pre["id"], lambda ctx: events.append(ctx.event))
    registry.register_hook(post["id"], lambda ctx: events.append(ctx.event))
    engine = WorkflowEngine({}, hooks=registry)
    wf = {
        "id": "wf1",
        "name": "demo",
        "nodes": [
            {"id": "t", "type": "trigger", "next": "o"},
            {"id": "o", "type": "output", "config": {"value": "done"}},
        ],
    }
    run = engine.run(wf)
    assert run.status == "ok"
    assert "workflow.start" in events   # pre_workflow fired
    assert "workflow.end" in events     # post_workflow fired


def test_dispatch_tool_fires_and_blocks(registry):
    from lattice_brain.runtime.hooks import dispatch_tool

    seen = []
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: seen.append(ctx.event))
    out = dispatch_tool(registry, "read_file", {"path": "x"}, lambda: "RESULT", source="test")
    assert out == "RESULT"
    assert "tool.read_file" in seen
    # A blocking pre_tool hook makes dispatch_tool raise.
    registry.register_hook("builtin:tool-permission-gate", lambda ctx: ctx.block("denied"))
    with pytest.raises(PermissionError, match="denied"):
        dispatch_tool(registry, "write_file", {"path": "y"}, lambda: "X", source="test")
