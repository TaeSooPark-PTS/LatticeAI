"""Deterministic multi-agent / workflow harness scenarios (review Wave 3.1).

End-to-end behaviours of the two orchestration runtimes, driven with injected
fakes only (no LLM, no network, no filesystem):

* happy path       — a multi-node run completes with a per-node result trail;
* failing node     — a raising node marks the run ``failed`` honestly; the
                     WorkflowEngine keeps walking for observability (its
                     documented semantics), while the MultiAgentOrchestrator
                     stops downstream roles outright;
* pause + resume   — ``ApprovalRequired`` suspends with a JSON-durable cursor;
                     resume re-enters at the paused node carrying pre-pause
                     context and never re-executes completed nodes;
* permission deny  — a ``PermissionError`` inside a runner is recorded, never
                     a crash; an approval denial on a fresh engine executes
                     nothing;
* recovery         — a failed run leaves no orphan state: definitions stay
                     unmutated and both fresh and reused instances re-run
                     cleanly.
"""

import copy
import json

from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator, OrchestrationContext
from lattice_brain.workflow import ApprovalRequired, WorkflowEngine


def _wf(nodes):
    return {"id": "wf-scn", "name": "scenario", "nodes": nodes}


def _chain(*tool_names):
    """trigger → one tool node per name → output, linearly linked."""
    nodes = [{"id": "t", "type": "trigger", "config": {"trigger": "manual"}, "next": tool_names[0]}]
    for index, name in enumerate(tool_names):
        nxt = tool_names[index + 1] if index + 1 < len(tool_names) else "out"
        nodes.append({"id": name, "type": "tool", "config": {"tool": name}, "next": nxt})
    nodes.append({"id": "out", "type": "output", "config": {}})
    return _wf(nodes)


def _recording_runner(executed, *, fail_on=(), deny_on=(), approval_for=()):
    """Injected fake tool runner: records executions; can gate, deny, or fail."""

    def runner(*, node, context):
        nid = node.get("id")
        name = (node.get("config") or {}).get("tool")
        approved = set(context.get("__approved_nodes__") or [])
        if name in approval_for and nid not in approved:
            raise ApprovalRequired(
                f"{name} requires approval", tool=name,
                args=(node.get("config") or {}).get("args") or {},
                permission={"requires_approval": True},
            )
        if name in deny_on:
            raise PermissionError(f"role 'viewer' may not execute {name}")
        if name in fail_on:
            raise RuntimeError(f"{name} exploded")
        executed.append(nid)
        return {"node": nid, "tool": name, "executed": True}

    return runner


# ── (1) happy path ───────────────────────────────────────────────────────────

def test_happy_path_multi_node_run_records_per_node_results():
    executed = []
    engine = WorkflowEngine({"tool": _recording_runner(executed)})
    run = engine.run(_chain("fetch", "transform", "save"), inputs={"topic": "mlx"})

    assert run.status == "ok"
    assert executed == ["fetch", "transform", "save"]
    tool_entries = {e["node"]: e for e in run.timeline if e.get("type") == "tool"}
    assert set(tool_entries) == {"fetch", "transform", "save"}
    for nid, entry in tool_entries.items():
        assert entry["status"] == "ok"
        assert entry["result"] == {"node": nid, "tool": nid, "executed": True}
    # The output node captured the last node's result, and the serialized run
    # reports an honest step count (trigger + 3 tools + output).
    assert run.outputs["out"] == {"node": "save", "tool": "save", "executed": True}
    assert run.as_dict()["step_count"] == 5


# ── (2) failing node ─────────────────────────────────────────────────────────

def test_failing_node_marks_run_failed_with_no_fake_result():
    """WorkflowEngine semantics: a raising node is recorded as an ``error``
    and the run finishes ``failed`` — never ``ok``/``partial`` — while later
    nodes still run for observability (documented continue-for-visibility
    design; role-level early stop is the orchestrator's job, covered next).
    The failing node itself must contribute no result anywhere."""
    executed = []
    engine = WorkflowEngine({"tool": _recording_runner(executed, fail_on=("transform",))})
    run = engine.run(_chain("fetch", "transform", "save"))

    assert run.status == "failed"
    failed_entry = next(e for e in run.timeline if e.get("node") == "transform")
    assert failed_entry["status"] == "error"
    assert "transform exploded" in failed_entry["reason"]
    assert "result" not in failed_entry
    # No fabricated output from the failed node: it never executed, and the
    # output node echoes the last SUCCESSFUL result instead.
    assert executed == ["fetch", "save"]
    assert run.outputs["out"] == {"node": "save", "tool": "save", "executed": True}


def test_failing_role_stops_downstream_roles_and_fails_run():
    """MultiAgentOrchestrator semantics: a role exception is terminal — the
    reviewer never sees (and can never approve) missing executor output."""
    ran_roles = []

    def runner(role, ctx: OrchestrationContext):
        ran_roles.append(role)
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "step", "status": "planned"}]
            return {"role": role, "steps": 1}
        if role == "executor":
            raise RuntimeError("executor lost its sandbox")
        return {"role": role}

    res = MultiAgentOrchestrator(role_runner=runner).run("goal", max_retries=2)
    assert res.status == "failed"
    assert res.roles_run == ["planner", "executor"]
    assert "reviewer" not in ran_roles, "downstream role must not execute after a failure"
    assert res.review.get("verdict") == "fail"
    failures = [t for t in res.timeline if t.get("event") == "execution_failed"]
    assert failures and "executor lost its sandbox" in failures[0]["reason"]


# ── (3) pause + resume ───────────────────────────────────────────────────────

def test_pause_resume_preserves_context_and_stitches_timeline():
    gated = _chain("first", "danger", "after")
    executed = []
    seen_by_after = {}
    base = _recording_runner(executed, approval_for={"danger"})

    def runner(*, node, context):
        if node.get("id") == "after":
            # What the post-gate node can actually see after resume.
            seen_by_after["first_result"] = context.get("first")
        return base(node=node, context=context)

    engine = WorkflowEngine({"tool": runner})
    paused = engine.run(gated, inputs={"topic": "mlx"})
    assert paused.status == "awaiting_approval"
    assert paused.paused_node == "danger"
    assert executed == ["first"]

    # Persist the cursor exactly as production would: a full JSON round-trip.
    durable = json.loads(json.dumps({
        "paused_node": paused.paused_node,
        "paused_context": paused.paused_context,
        "timeline": paused.timeline,
    }))

    resumed = engine.resume(
        gated,
        paused_node=durable["paused_node"],
        paused_context=durable["paused_context"],
        approved=True,
        prior_timeline=durable["timeline"],
    )
    assert resumed.status == "ok"
    assert executed == ["first", "danger", "after"], "pre-pause node must not re-run"
    # The resumed context carried the pre-pause node's result across the
    # JSON persistence boundary.
    assert seen_by_after["first_result"] == {"node": "first", "tool": "first", "executed": True}
    # Stitched timeline: the gated node appears paused (prior half) then ok
    # (resumed half); the pre-pause node's ok entry appears exactly once.
    danger_statuses = [e["status"] for e in resumed.timeline if e.get("node") == "danger"]
    assert danger_statuses == ["awaiting_approval", "ok"]
    ok_tools = [e["node"] for e in resumed.timeline
                if e.get("type") == "tool" and e.get("status") == "ok"]
    assert ok_tools == ["first", "danger", "after"]


# ── (4) permission / role deny ───────────────────────────────────────────────

def test_permission_denied_node_is_recorded_without_crashing_engine():
    executed = []
    engine = WorkflowEngine({"tool": _recording_runner(executed, deny_on=("transform",))})
    run = engine.run(_chain("fetch", "transform", "save"))

    assert run.status == "failed"
    denied = next(e for e in run.timeline if e.get("node") == "transform")
    assert denied["status"] == "error"
    assert "may not execute transform" in denied["reason"]
    assert "transform" not in executed, "a denied node must never execute"
    # The engine instance survives the denial: a workflow that avoids the
    # denied tool still runs clean on the very same instance.
    ok_run = engine.run(_chain("fetch", "save"))
    assert ok_run.status == "ok"


def test_denied_approval_on_fresh_engine_executes_nothing():
    """Simulates a restart: the denial decision reaches a brand-new engine
    holding nothing but the persisted cursor — it must record the denial and
    execute zero nodes."""
    gated = _chain("first", "danger")
    executed = []
    paused = WorkflowEngine(
        {"tool": _recording_runner(executed, approval_for={"danger"})}
    ).run(gated)
    assert paused.status == "awaiting_approval"

    fresh_executed = []
    fresh = WorkflowEngine({"tool": _recording_runner(fresh_executed, approval_for={"danger"})})
    denied = fresh.resume(
        gated,
        paused_node=paused.paused_node,
        paused_context=json.loads(json.dumps(paused.paused_context)),
        approved=False,
        prior_timeline=paused.timeline,
    )
    assert denied.status == "failed"
    assert any(e.get("status") == "denied" for e in denied.timeline)
    assert fresh_executed == [], "denial must execute nothing on the fresh engine"


# ── (5) recovery / rollback ──────────────────────────────────────────────────

def test_failed_run_leaves_definition_unmutated_and_rerun_succeeds():
    definition = _chain("fetch", "transform", "save")
    pristine = copy.deepcopy(definition)

    failed = WorkflowEngine(
        {"tool": _recording_runner([], fail_on=("transform",))}
    ).run(definition)
    assert failed.status == "failed"
    assert definition == pristine, "a failed run must not mutate the stored definition"

    # A fresh engine over the same stored definition recovers cleanly — the
    # failed run left no orphan state behind.
    executed = []
    rerun = WorkflowEngine({"tool": _recording_runner(executed)}).run(definition)
    assert rerun.status == "ok"
    assert executed == ["fetch", "transform", "save"]
    # The failed run's record stays an honest, untouched artifact.
    assert failed.status == "failed"
    assert failed.finished_at is not None


def test_failed_multi_agent_run_does_not_poison_the_next_run():
    """Per-run OrchestrationContext isolation: a failed run on an orchestrator
    instance leaves nothing behind that could taint the next run."""
    state = {"attempts": 0}

    def flaky(role, ctx: OrchestrationContext):
        if role == "planner":
            ctx.plan = [{"index": 0, "description": "s", "status": "planned"}]
            return {"role": role}
        if role == "executor":
            state["attempts"] += 1
            if state["attempts"] == 1:
                raise RuntimeError("first run dies")
            ctx.executed = [{"index": 0, "status": "done"}]
            ctx.output = "done"
            return {"role": role}
        if role == "reviewer":
            ctx.review = {"verdict": "pass", "reason": "ok"}
            return {"role": role}
        return {"role": role}

    orch = MultiAgentOrchestrator(role_runner=flaky)
    first = orch.run("goal", max_retries=0)
    assert first.status == "failed"

    second = orch.run("goal", max_retries=0)
    assert second.status == "ok"
    assert second.retries == 0
    # The failed record is untouched by the successful re-run, and the two
    # runs share no mutable state.
    assert first.status == "failed"
    assert first.timeline is not second.timeline
