"""wp24 coverage — ``lattice_brain.runtime.agent_runtime`` (the /agents boundary).

The façade owns four things no other layer may own: whether a run may start at
all, what the durable row says while and after it runs, what a *failed* or
*cancelled* run records, and what a successful run leaves behind in the Brain.
The store, the orchestrator, the memory sink and the review sink are injected,
so all of that is exercised for real here — with a real ``HooksRegistry`` on
``tmp_path`` for the pre_run/post_run dispatch rather than a stub.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lattice_brain.runtime.agent_runtime import AgentRuntime, AgentRuntimeUnavailable
from lattice_brain.runtime.contracts import run_record_contract
from lattice_brain.runtime.hooks import HooksRegistry
from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator


class FakeStore:
    """In-memory stand-in for the workspace run store."""

    def __init__(self, rows=None):
        self.runs = list(rows or [])

    def list_agents(self, workspace_id=None):
        return {"agents": [], "runs": list(reversed(self.runs)), "workspace_id": workspace_id}

    def record_agent_run(self, **kw):
        run = {"id": f"agent-run-{len(self.runs)}", "created_at": "2026-08-09T00:00:00", **kw}
        run["contract"] = run_record_contract(run)
        self.runs.append(run)
        return run

    def get_agent_run(self, run_id, workspace_id=None):
        for run in self.runs:
            if run.get("id") == run_id:
                return run
        raise FileNotFoundError(run_id)

    def update_agent_run(self, run_id, *, workspace_id=None, graph=None, patch=None, **fields):
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        run.update({**(patch or {}), **fields})
        run["contract"] = run_record_contract(run)
        return run

    def replay_agent_run(self, run_id, workspace_id=None):
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        payload = dict(run_record_contract(run))
        payload["frames"] = list(run.get("timeline") or [])
        return payload


class _RaisingOrchestrator:
    mode = "llm"

    def __init__(self, exc):
        self._exc = exc

    def run(self, *_args, **_kwargs):
        raise self._exc


def _runtime(store=None, **kwargs):
    kwargs.setdefault("orchestrator_factory", lambda user, scope: MultiAgentOrchestrator())
    kwargs.setdefault("allow_simulation_runs", True)
    return AgentRuntime(
        store=store if store is not None else FakeStore(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        **kwargs,
    )


def _result(**kwargs):
    base = dict(
        status="ok",
        output="Implemented the loop. Verified the recall quality.",
        plan=[{"description": "verify the recall quality"}],
        review={"decision": "approved"},
        plan_review={},
        roles_run=["planner", "executor", "reviewer"],
        agent_id="agent:executor",
        retries=0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── readiness ───────────────────────────────────────────────────────────────


def test_health_reports_a_model_backed_orchestrator_as_ok():
    runtime = _runtime(
        orchestrator_factory=lambda user, scope: MultiAgentOrchestrator(mode="llm"),
        allow_simulation_runs=False,
    )

    health = runtime.health()

    assert health["status"] == "ok"
    assert health["ready"] is True
    assert health["checks"]["orchestrator"] == {"status": "ok", "mode": "llm"}


def test_preview_reports_a_missing_goal_as_a_blocking_reason():
    preview = _runtime().preview("   ", roles=["planner"], inputs={"topic": "x"})

    assert preview["can_start"] is False
    assert "goal is required" in preview["blocking_reasons"]
    assert preview["inputs_keys"] == ["topic"]


def test_a_simulation_orchestrator_is_refused_by_the_product_boundary():
    runtime = _runtime(allow_simulation_runs=False)

    assert runtime.health()["ready"] is False
    with pytest.raises(AgentRuntimeUnavailable, match="no LLM-backed model"):
        runtime.start("ship it", user_email="u@example.com", scope=None)


# ── read surfaces ───────────────────────────────────────────────────────────


def test_list_runs_and_replay_expose_the_family_contract():
    runtime = _runtime()
    started = runtime.start("ship the release", user_email="u@example.com", scope="ws-1")
    run_id = started["run"]["id"]

    listing = runtime.list_runs(scope="ws-1")
    assert [run["id"] for run in listing["runs"]] == [run_id]
    assert listing["contracts"][0]["run_id"] == run_id
    assert listing["contracts"][0]["kind"] == "agent_run"

    replay = runtime.replay(run_id, scope="ws-1")
    assert replay["replay"]["frames"]
    assert replay["contract"]["id"] == run_id
    assert replay["contract"]["runtime"] == "multi_agent"


def test_a_row_without_an_identity_yields_no_contract():
    class _Anonymous(FakeStore):
        """A legacy row: no envelope, and no id to synthesize one from."""

        def get_agent_run(self, run_id, workspace_id=None):
            return {"status": "ok", "timeline": []}

    payload = _runtime(_Anonymous()).get_run("legacy-run")

    assert payload == {"run": {"status": "ok", "timeline": []}}


# ── memory synthesis ────────────────────────────────────────────────────────


def test_synthesis_is_skipped_for_a_run_that_did_not_succeed():
    captured = []
    runtime = _runtime(memory_ingest=lambda **kw: captured.append(kw))

    runtime._synthesize_brain_memory(
        goal="ship it", result=_result(status="failed"),
        user_email="u@example.com", scope=None,
    )

    assert captured == []


def test_synthesis_of_an_empty_outcome_writes_only_the_long_term_memory():
    captured = []
    runtime = _runtime(memory_ingest=lambda **kw: captured.append(kw) or {"id": "m-1"})

    runtime._synthesize_brain_memory(
        goal="ship it", result=_result(output="", plan=[], review={}),
        user_email="u@example.com", scope=None,
    )

    assert [item["kind"] for item in captured] == ["long_term", "decisions"]
    content = captured[0]["content"]
    # No facts and no follow-ups → those sections are omitted entirely.
    assert "Key facts:" not in content
    assert "Follow-ups:" not in content
    assert "Decisions:" in content


def test_a_failing_memory_sink_never_breaks_the_run_record():
    def boom(**_kwargs):
        raise RuntimeError("memory store is down")

    runtime = _runtime(memory_ingest=boom)

    runtime._synthesize_brain_memory(
        goal="ship it", result=_result(), user_email="u@example.com", scope=None,
    )


def test_follow_ups_are_dropped_when_no_review_sink_is_wired():
    captured = []
    runtime = _runtime(memory_ingest=lambda **kw: captured.append(kw) or {"id": "m-1"})

    runtime._synthesize_brain_memory(
        goal="ship it", result=_result(), user_email="u@example.com", scope=None,
    )

    assert [item["kind"] for item in captured] == ["long_term", "decisions", "workspace"]


def test_a_failing_review_sink_does_not_stop_the_remaining_follow_ups():
    attempted = []

    def create(**kwargs):
        attempted.append(kwargs["title"])
        raise RuntimeError("review queue is down")

    runtime = _runtime(
        memory_ingest=lambda **kw: {"id": "m-1"},
        review_sink=SimpleNamespace(create=create),
    )

    runtime._synthesize_brain_memory(
        goal="ship it",
        result=_result(plan=[
            {"description": "verify the recall quality"},
            {"description": "document the follow-up"},
        ]),
        user_email="u@example.com",
        scope=None,
    )

    assert attempted == ["verify the recall quality", "document the follow-up"]


# ── reserve / complete (async executor path) ────────────────────────────────


def test_reserve_run_requires_a_goal():
    with pytest.raises(ValueError, match="goal is required"):
        _runtime().reserve_run("  ", user_email="u@example.com", scope=None)


def test_reserve_run_reports_the_pre_run_hook_dispatch(tmp_path):
    hooks = HooksRegistry(tmp_path / "hooks.json")
    seen = []
    hooks.register_hook("builtin:redact-secrets", lambda ctx: seen.append(ctx.payload) or None)
    runtime = _runtime(hooks=hooks)

    payload = runtime.reserve_run(
        "ship the release", user_email="u@example.com", scope="ws-1",
        roles=["planner", "executor"], inputs={"topic": "x"}, max_retries=99,
    )

    assert payload["run"]["status"] == "queued"
    assert payload["run"]["execution_mode"] == "async"
    # The durable row records the clamped budget, not the raw request.
    assert payload["run"]["max_retries"] == 5
    assert payload["pre_run_hooks"]["kind"] == "pre_run"
    assert payload["pre_run_hooks"]["blocked"] is False
    assert seen[0]["goal"] == "ship the release"


def test_a_failing_orchestrator_marks_the_reserved_run_failed():
    store = FakeStore()
    runtime = _runtime(store)
    reserved = runtime.reserve_run("ship it", user_email="u@example.com", scope="ws-1")
    run_id = reserved["run"]["id"]

    runtime = AgentRuntime(
        store=store,
        orchestrator_factory=lambda user, scope: _RaisingOrchestrator(RuntimeError("model died")),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        allow_simulation_runs=True,
    )
    payload = runtime.complete_reserved_run(
        run_id, "ship it", user_email="u@example.com", scope="ws-1",
    )

    assert payload["result"] == {"status": "failed", "error": "model died"}
    assert payload["run"]["status"] == "failed"
    assert payload["run"]["error"] == "model died"
    assert payload["run"]["timeline"][-1]["event"] == "execution_failed"


def test_a_cancelled_reserved_run_records_the_cancellation_after_the_step(tmp_path):
    hooks = HooksRegistry(tmp_path / "hooks.json")
    store = FakeStore()
    runtime = _runtime(store, hooks=hooks)
    reserved = runtime.reserve_run("ship it", user_email="u@example.com", scope="ws-1")
    run_id = reserved["run"]["id"]

    payload = runtime.complete_reserved_run(
        run_id, "ship it", user_email="u@example.com", scope="ws-1",
        pre_dispatch=reserved["pre_run_hooks"],
        cancel_requested=lambda: True,
    )

    assert payload["run"]["status"] == "cancelled"
    assert payload["result"]["status"] == "cancelled"
    assert payload["result"]["reason"].startswith("cancelled after")
    assert payload["result"]["completed_result"]["status"] == "ok"
    assert payload["run"]["timeline"][-1]["event"] == "execution_cancelled"
    assert payload["pre_run_hooks"]["kind"] == "pre_run"
    assert payload["post_run_hooks"]["kind"] == "post_run"


# ── stop ────────────────────────────────────────────────────────────────────


def test_stop_delegates_to_the_attached_executor():
    cancelled = []
    runtime = _runtime()
    runtime.attach_executor(SimpleNamespace(
        cancel=lambda run_id, kind, scope: cancelled.append((run_id, kind, scope)) or {"stopped": True},
    ))

    assert runtime.config()["execution_mode"] == "async"
    assert runtime.stop("agent-run-0", scope="ws-1") == {"stopped": True}
    assert cancelled == [("agent-run-0", "agent", "ws-1")]


def test_stop_is_honest_about_the_synchronous_runtime():
    store = FakeStore()
    runtime = _runtime(store)
    reserved = runtime.reserve_run("ship it", user_email="u@example.com", scope="ws-1")

    stopped = runtime.stop(reserved["run"]["id"], scope="ws-1")

    assert stopped["stopped"] is False
    assert stopped["status"] == "queued"
    assert "not supported" in stopped["reason"]
