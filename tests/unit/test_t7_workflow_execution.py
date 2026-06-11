"""T7a: workflow nodes execute for real, with a genuine approval gate.

The pre-v4 production runners returned {recorded: true} for every tool and
an existence check for skills — runs finished "ok" having done nothing.
Now: auto-approve tools execute through dispatch_tool; approval-requiring
tools pause the run (awaiting_approval) with a serializable cursor; resume
re-enters at the paused node without re-executing completed nodes; denial
fails the run honestly; skills refuse instead of pretending.
"""

from latticeai.core.workflow_engine import ApprovalRequired, WorkflowEngine


def _wf(nodes):
    return {"id": "wf-1", "name": "test", "nodes": nodes}


def _tool_runner(executed, *, approval_for=()):
    """A governed runner: tools in approval_for require approval."""
    def runner(*, node, context):
        cfg = node.get("config") or {}
        name = cfg.get("tool")
        approved = set(context.get("__approved_nodes__") or [])
        if name in approval_for and node.get("id") not in approved:
            raise ApprovalRequired(
                f"{name} requires approval", tool=name,
                args=cfg.get("args") or {}, permission={"requires_approval": True},
            )
        executed.append(node.get("id"))
        return {"tool": name, "executed": True}
    return runner


NODES = [
    {"id": "t", "type": "trigger", "config": {"trigger": "manual"}, "next": "safe"},
    {"id": "safe", "type": "tool", "config": {"tool": "read_file"}, "next": "danger"},
    {"id": "danger", "type": "tool", "config": {"tool": "run_command", "args": {"command": "ls"}}, "next": "out"},
    {"id": "out", "type": "output", "config": {}},
]


def test_auto_approve_tools_execute():
    executed = []
    engine = WorkflowEngine({"tool": _tool_runner(executed)})
    run = engine.run(_wf(NODES))
    assert run.status == "ok"
    assert executed == ["safe", "danger"]
    assert all(e.get("result", {}).get("executed") for e in run.timeline if e["type"] == "tool")


def test_approval_pauses_run_with_serializable_cursor():
    executed = []
    engine = WorkflowEngine({"tool": _tool_runner(executed, approval_for={"run_command"})})
    run = engine.run(_wf(NODES))
    assert run.status == "awaiting_approval"
    assert run.paused_node == "danger"
    assert run.pending_approval["tool"] == "run_command"
    assert executed == ["safe"], "nodes before the gate executed; the gated one did not"
    import json
    json.dumps(run.paused_context)  # must be durable


def test_resume_executes_only_from_paused_node():
    executed = []
    engine = WorkflowEngine({"tool": _tool_runner(executed, approval_for={"run_command"})})
    paused = engine.run(_wf(NODES))
    executed_before_resume = list(executed)

    resumed = engine.resume(
        _wf(NODES),
        paused_node=paused.paused_node,
        paused_context=paused.paused_context,
        approved=True,
    )
    assert resumed.status == "ok"
    assert executed == executed_before_resume + ["danger"], (
        "resume must not re-execute the 'safe' node"
    )


def test_denial_fails_run_honestly():
    executed = []
    engine = WorkflowEngine({"tool": _tool_runner(executed, approval_for={"run_command"})})
    paused = engine.run(_wf(NODES))
    denied = engine.resume(
        _wf(NODES),
        paused_node=paused.paused_node,
        paused_context=paused.paused_context,
        approved=False,
    )
    assert denied.status == "failed"
    assert any(e.get("status") == "denied" for e in denied.timeline)
    assert executed == ["safe"], "a denied node must never execute"


def test_production_tool_runner_executes_real_tools(tmp_path, monkeypatch):
    """The real platform runner calls execute_tool for auto-approve tools and
    raises ApprovalRequired for governed ones — no {recorded: true} theater."""
    from latticeai.services.platform_runtime import PlatformRuntime

    calls = {}

    def fake_execute(name, args):
        calls["tool"] = (name, args)
        return {"ok": True}

    import latticeai.services.platform_runtime as pr
    monkeypatch.setattr(pr, "execute_tool", fake_execute)

    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.hooks = None
    runtime.get_tool_permission = lambda name, args=None: {
        "tool": name, "risk": "low",
        "requires_approval": name == "run_command", "network": False,
    }
    runner = runtime._tool_node_runner()

    result = runner(node={"id": "n1", "config": {"tool": "read_file", "args": {"path": "x"}}}, context={})
    assert result["executed"] is True and calls["tool"][0] == "read_file"

    try:
        runner(node={"id": "n2", "config": {"tool": "run_command", "args": {}}}, context={})
        raise AssertionError("governed tool must not execute without approval")
    except ApprovalRequired as pause:
        assert pause.tool == "run_command"

    # An approved resume context lets it through.
    result = runner(
        node={"id": "n2", "config": {"tool": "run_command", "args": {}}},
        context={"__approved_nodes__": ["n2"]},
    )
    assert result["executed"] is True


def test_skill_runner_refuses_honestly(tmp_path):
    from latticeai.services.platform_runtime import PlatformRuntime

    class _Store:
        @staticmethod
        def load_state():
            return {"skill_registry": {"summarize": {"enabled": True}}}

    runtime = PlatformRuntime.__new__(PlatformRuntime)
    runtime.store = _Store()
    runner = runtime._skill_node_runner()
    try:
        runner(node={"id": "s1", "config": {"skill": "summarize"}}, context={})
        raise AssertionError("skill node must refuse, not fake a result")
    except RuntimeError as exc:
        assert "refusing to fake" in str(exc)
