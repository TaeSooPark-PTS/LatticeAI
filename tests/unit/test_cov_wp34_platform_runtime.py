"""Coverage for the v2 platform runtime cross-system wiring (wp34).

``PlatformRuntime`` is the seam where workflows, plugins and agents meet the
workspace store, so the tests drive the real node runners and the real
``WorkflowEngine`` / ``MultiAgentOrchestrator`` against a recording store and
assert what was executed, refused, or recorded.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import latticeai.services.platform_runtime as pr
from lattice_brain.workflow import ApprovalRequired
from latticeai.services.platform_runtime import PlatformRuntime


class _Store:
    def __init__(self, *, memories=None, state=None, workflows=None):
        self.memories = memories if memories is not None else []
        self.state = state or {}
        self.workflows = workflows or {}
        self.skill_marks = []
        self.workflow_runs = []
        self.agent_runs = []
        self.search_error = None
        self.list_error = None

    # workspace memory surface -------------------------------------------------
    def search_memories(self, goal, *, user_email=None, workspace_id=None):
        if self.search_error:
            raise self.search_error
        return {"memories": self.memories}

    def list_memories(self, *, user_email=None, workspace_id=None):
        if self.list_error:
            raise self.list_error
        return {"memories": self.memories}

    # plugin / skill surface ---------------------------------------------------
    def mark_skill_installed(self, skill_name, *, version, metadata):
        self.skill_marks.append((skill_name, version, metadata))
        return {"skill": skill_name, "version": version}

    def load_state(self):
        return self.state

    # workflow / agent records -------------------------------------------------
    def get_workflow(self, workflow_id, *, workspace_id=None):
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def record_workflow_run(self, **kwargs):
        self.workflow_runs.append(kwargs)
        return {"id": f"wfrun-{len(self.workflow_runs)}"}

    def record_agent_run(self, **kwargs):
        self.agent_runs.append(kwargs)
        return {"id": f"agentrun-{len(self.agent_runs)}"}


class _Registry:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {"plugin": "ok"}
        self.error = error

    def execute_action(self, plugin_id, action, args, *, runners=None, workspace_id=None):
        self.calls.append((plugin_id, action, args, sorted(runners or {}), workspace_id))
        if self.error:
            raise self.error
        payload = self.result

        class _Result:
            def as_dict(self):
                return payload

        return _Result()


def _runtime(**overrides):
    kwargs = dict(
        store=_Store(),
        workspace_service=None,
        plugin_registry=_Registry(),
        get_current_user=lambda _request: "member@example.com",
        workspace_graph=lambda: None,
        workspace_scope_from_request=lambda _request: "w1",
        get_tool_permission=lambda name, args=None: {"requires_approval": False, "tool": name},
        hooks=None,
    )
    kwargs.update(overrides)
    return PlatformRuntime(**kwargs)


# ── request gating ───────────────────────────────────────────────────────────


class _Scopes:
    def __init__(self, error=None):
        self.error = error

    def resolve_read_scope(self, scope, user):
        if self.error:
            raise self.error
        return f"read:{scope}:{user}"

    def resolve_write_scope(self, scope, user):
        if self.error:
            raise self.error
        return f"write:{scope}:{user}"


def test_gates_resolve_the_requested_scope():
    runtime = _runtime(workspace_service=_Scopes())

    assert runtime.gate_read(object()) == "read:w1:member@example.com"
    assert runtime.gate_write(object()) == "write:w1:member@example.com"


def test_gates_translate_a_scope_refusal_into_403():
    runtime = _runtime(workspace_service=_Scopes(error=PermissionError("not a member")))

    for gate in (runtime.gate_read, runtime.gate_write):
        with pytest.raises(HTTPException) as excinfo:
            gate(object())
        assert excinfo.value.status_code == 403
        assert "not a member" in excinfo.value.detail


def test_plugin_skill_registration_marks_the_store():
    store = _Store()
    runtime = _runtime(store=store)

    assert runtime.register_plugin_skill("summarize", "plug-1")["version"] == "plugin:plug-1"
    assert store.skill_marks[0][2] == {"source": "plugin:plug-1"}


# ── workflow node runners ────────────────────────────────────────────────────


def test_tool_node_without_a_tool_is_refused():
    runner = _runtime()._tool_node_runner()

    with pytest.raises(ValueError, match="no tool configured"):
        runner(node={"id": "n1", "config": {}}, context={})


def test_scoped_knowledge_tool_receives_the_workspace_and_user(monkeypatch):
    seen = {}
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: seen.setdefault("call", (name, dict(args))))
    runner = _runtime()._tool_node_runner("member@example.com", "org:acme")

    result = runner(
        node={"id": "n1", "config": {"tool": "knowledge_search", "args": {"query": "릴리스"}}},
        context={},
    )

    assert seen["call"][1] == {
        "query": "릴리스",
        "workspace_id": "org:acme",
        "user_email": "member@example.com",
    }
    assert result["executed"] is True


def test_legacy_single_argument_permission_lookup_still_works(monkeypatch):
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: {"ok": True})

    def legacy_permission(name, *args):
        if args:
            raise TypeError("legacy get_tool_permission takes one argument")
        return {"requires_approval": False, "tool": name}

    runner = _runtime(get_tool_permission=legacy_permission)._tool_node_runner()

    result = runner(node={"id": "n1", "config": {"tool": "read_file"}}, context={})

    assert result["permission"] == {"requires_approval": False, "tool": "read_file"}


def test_tool_node_requiring_approval_pauses_instead_of_executing(monkeypatch):
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: pytest.fail("must not execute"))
    runner = _runtime(
        get_tool_permission=lambda name, args=None: {"requires_approval": True}
    )._tool_node_runner()

    with pytest.raises(ApprovalRequired):
        runner(node={"id": "n1", "config": {"tool": "run_command"}}, context={})


def test_skill_node_refuses_when_the_skill_is_not_installed():
    runner = _runtime(store=_Store(state={"skill_registry": {}}))._skill_node_runner()

    with pytest.raises(ValueError, match="is not installed"):
        runner(node={"id": "n1", "config": {"skill": "ghost"}}, context={})


def test_installed_skill_node_refuses_to_fake_an_llm_result():
    store = _Store(state={"skill_registry": {"summarize": {"version": "1"}}})
    runner = _runtime(store=store)._skill_node_runner()

    with pytest.raises(RuntimeError, match="refusing to fake a result"):
        runner(node={"id": "n1", "config": {"skill": "summarize"}}, context={})


def test_plugin_node_runner_delegates_to_the_registry():
    registry = _Registry(result={"plugin": "plug-1", "ok": True})
    runner = _runtime(plugin_registry=registry)._plugin_node_runner("member@example.com", "w1")

    result = runner(node={"id": "n1", "config": {"plugin_id": "plug-1", "args": {"x": 1}}}, context={})

    assert result == {"plugin": "plug-1", "ok": True}
    plugin_id, action, args, runner_names, workspace_id = registry.calls[0]
    assert (plugin_id, action, args, workspace_id) == ("plug-1", "run_skill", {"x": 1}, "w1")
    assert runner_names == ["agents", "skills", "tools", "workflows"]


# ── context provider ─────────────────────────────────────────────────────────


def test_recall_evidence_skips_non_dict_rows():
    runtime = _runtime(memory_recall=lambda *a, **k: {"results": ["junk", {"snippet": "실제 근거"}]})

    assert runtime._context_provider("u", "w1")("goal") == ["[memory] Memory: 실제 근거"]


def test_recall_failure_falls_back_to_the_store():
    def _broken(*_a, **_k):
        raise RuntimeError("recall backend down")

    store = _Store(memories=[{"content": "저장된 기억", "tags": []}])
    runtime = _runtime(store=store, memory_recall=_broken)

    assert runtime._context_provider("u", "w1")("goal") == ["저장된 기억"]


def test_agent_synthesis_enrichment_failure_keeps_the_base_context():
    store = _Store(memories=[{"content": "기본 기억", "tags": []}])
    store.list_error = RuntimeError("list unavailable")
    runtime = _runtime(store=store)

    assert runtime._context_provider("u", "w1")("goal") == ["기본 기억"]


def test_empty_search_falls_back_to_recent_memories_and_survives_failure():
    store = _Store(memories=[])
    store.list_error = RuntimeError("list unavailable")
    runtime = _runtime(store=store)

    assert runtime._context_provider("u", "w1")("goal") == []


def test_store_failure_yields_no_context():
    store = _Store()
    store.search_error = RuntimeError("store unavailable")
    runtime = _runtime(store=store)

    assert runtime._context_provider("u", "w1")("goal") == []


# ── plugin capability runners ────────────────────────────────────────────────


def test_plugin_skill_capability_refuses_to_fake_a_result():
    runners = _runtime().plugin_capability_runners("member@example.com", "w1")

    with pytest.raises(RuntimeError, match="refusing to fake a result"):
        runners["skills"](plugin_id="plug-1", action="run_skill", args={}, manifest=None)


class _Manifest:
    def __init__(self, tools=None):
        self.provides = {"tools": tools or []}


def test_plugin_tool_capability_needs_a_tool_name():
    runners = _runtime().plugin_capability_runners("member@example.com", "w1")

    with pytest.raises(ValueError, match="needs a tool name"):
        runners["tools"](plugin_id="plug-1", action="run_tool", args={}, manifest=_Manifest())


def test_plugin_tool_capability_scopes_knowledge_tools_then_defers_to_governance(monkeypatch):
    """Scoped knowledge tools get the caller's scope, and still face the policy."""
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: pytest.fail("must not execute"))
    runners = _runtime().plugin_capability_runners("member@example.com", "org:acme")

    with pytest.raises(HTTPException) as excinfo:
        runners["tools"](
            plugin_id="plug-1",
            action="run_tool",
            args={"query": "릴리스"},
            manifest=_Manifest(["knowledge_search"]),
        )

    assert excinfo.value.status_code == 403
    assert "knowledge_search" in excinfo.value.detail


def test_plugin_tool_capability_executes_an_auto_approved_tool(monkeypatch):
    seen = {}
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: seen.setdefault("call", (name, dict(args))))
    runners = _runtime().plugin_capability_runners("member@example.com", "org:acme")

    result = runners["tools"](
        plugin_id="plug-1",
        action="run_tool",
        args={"tool": "read_file", "path": "notes.md"},
        manifest=_Manifest(),
    )

    assert seen["call"][0] == "read_file"
    assert result["executed"] is True
    assert result["tool"] == "read_file"
    assert result["policy"]["auto_approve"] is True


def test_plugin_workflow_capability_skips_without_a_workflow_id():
    runtime = _runtime()
    runners = runtime.plugin_capability_runners("member@example.com", "w1")

    assert runners["workflows"](plugin_id="plug-1", action="run_workflow", args={}, manifest=None) == {
        "plugin": "plug-1",
        "skipped": "no workflow_id",
    }


def test_plugin_workflow_and_agent_capabilities_reach_the_cross_system_runs():
    runtime = _runtime()
    calls = []
    runtime.run_workflow_by_id = lambda *a, **k: calls.append(("workflow", a, k)) or {"status": "ok"}
    runtime.run_agent = lambda *a, **k: calls.append(("agent", a, k)) or {"status": "ok"}
    runners = runtime.plugin_capability_runners("member@example.com", "w1")

    runners["workflows"](plugin_id="plug-1", action="run_workflow", args={"workflow_id": "wf-1"}, manifest=None)
    runners["agents"](plugin_id="plug-1", action="run_agent", args={}, manifest=None)

    assert calls[0][1] == ("wf-1", "member@example.com", "w1")
    assert calls[0][2] == {"with_agent": False, "inputs": None}
    assert calls[1][1] == ("Plugin plug-1 agent task", "member@example.com", "w1")
    assert calls[1][2] == {"with_workflow": False, "inputs": None}


# ── cross-system runs ────────────────────────────────────────────────────────


_WORKFLOW = {
    "id": "wf-1",
    "name": "release",
    "nodes": [
        {"id": "t", "type": "trigger", "config": {"trigger": "manual"}, "next": "step"},
        {"id": "step", "type": "tool", "config": {"tool": "read_file", "args": {"path": "x"}}, "next": "out"},
        {"id": "out", "type": "output", "config": {}},
    ],
}


def test_missing_workflow_is_reported_not_raised():
    runtime = _runtime(store=_Store(workflows={}))

    assert runtime.run_workflow_by_id("ghost", "u", "w1", with_agent=True) == {
        "error": "workflow not found: ghost"
    }


def test_workflow_run_executes_nodes_and_records_the_run(monkeypatch):
    monkeypatch.setattr(pr, "execute_tool", lambda name, args: {"read": args})
    store = _Store(workflows={"wf-1": _WORKFLOW})
    runtime = _runtime(store=store)

    result = runtime.run_workflow_by_id("wf-1", "member@example.com", "w1", with_agent=True)

    assert result == {"workflow_run_id": "wfrun-1", "status": "ok"}
    recorded = store.workflow_runs[0]
    assert recorded["mode"] == "live"
    assert recorded["pause"] is None
    assert recorded["workspace_id"] == "w1"


def test_agent_run_uses_the_orchestrator_and_records_the_result():
    store = _Store(memories=[{"content": "근거", "tags": []}], workflows={"wf-1": _WORKFLOW})
    runtime = _runtime(store=store)

    result = runtime.run_agent("릴리스 준비", "member@example.com", "w1", with_workflow=True)

    assert result["agent_run_id"] == "agentrun-1"
    assert result["status"] in {"ok", "retried_ok", "failed"}
    assert isinstance(result["output"], str)
    assert store.agent_runs[0]["workspace_id"] == "w1"


def test_workflow_runner_factory_exposes_every_node_type():
    runners = _runtime().build_workflow_runners("member@example.com", "w1")

    assert sorted(runners) == ["agent", "plugin", "skill", "tool"]


# ── orchestrator factory ─────────────────────────────────────────────────────


class _AgentRegistry:
    def __init__(self, agents=None, error=None):
        self.agents = agents or []
        self.error = error

    def all(self):
        if self.error:
            raise self.error
        return self.agents


def test_custom_agents_are_loaded_from_the_registry():
    registry = _AgentRegistry(
        [
            {"id": "agent:custom:writer", "enabled": True},
            {"id": "agent:custom:disabled", "enabled": False},
            {"id": "agent:builtin:planner", "enabled": True},
        ]
    )
    orchestrator = _runtime(agent_registry=registry).build_orchestrator("u", "w1")

    assert sorted(orchestrator.custom_agents) == ["agent:custom:writer"]


def test_a_broken_agent_registry_degrades_to_no_custom_agents():
    registry = _AgentRegistry(error=RuntimeError("registry file corrupt"))

    orchestrator = _runtime(agent_registry=registry).build_orchestrator("u", "w1")

    assert orchestrator.custom_agents == {}
