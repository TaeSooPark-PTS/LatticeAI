"""wpb02 branch coverage — the agent runtime (hooks, contracts, orchestration).

Each test takes the side of a guard the happy path never reaches: a registry
file that holds valid JSON of the wrong shape, a disabled hook in the counters,
a mutation that has to scan past a non-matching custom hook, a hook run whose
caller already built the context, a records list with a member that carries no
family contract, a roster whose second run for one agent must not overwrite the
first, an agent run that failed (so nothing is written to the Brain), a handoff
that was only created, and a retry the pipeline cannot route because it has no
executor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from lattice_brain.runtime.agent_runtime import AgentRuntime
from lattice_brain.runtime.contracts import (
    RuntimeBoundaryProtocol,
    contract_views,
    run_record_contract,
)
from lattice_brain.runtime.hooks import HookContext, HooksRegistry
from lattice_brain.runtime.multi_agent import (
    AgentRunResult,
    MultiAgentOrchestrator,
    OrchestrationContext,
)

# ── hooks.py ────────────────────────────────────────────────────────────────


def test_a_registry_file_holding_a_json_list_degrades_to_the_builtin_set(tmp_path: Path):
    path = tmp_path / "hooks.json"
    path.write_text("[]", encoding="utf-8")

    registry = HooksRegistry(path)

    assert registry._state == {"custom": [], "overrides": {}}
    assert registry.list()["total"] > 0


def test_a_run_log_holding_a_json_object_degrades_to_an_empty_history(tmp_path: Path):
    (tmp_path / "hooks_runs.json").write_text('{"runs": []}', encoding="utf-8")

    registry = HooksRegistry(tmp_path / "hooks.json")

    assert registry.recent_runs()["runs"] == []


def test_a_disabled_hook_counts_toward_its_kind_but_not_toward_enabled(tmp_path: Path):
    registry = HooksRegistry(tmp_path / "hooks.json")
    target = registry.list()["hooks"][0]
    registry.set_enabled(target["id"], False)

    listing = registry.list()

    counts = listing["counts"][target["kind"]]
    assert counts["enabled"] == counts["total"] - 1
    assert listing["enabled"] == listing["total"] - 1


def test_disabling_the_second_custom_hook_leaves_the_first_one_alone(tmp_path: Path):
    registry = HooksRegistry(tmp_path / "hooks.json")
    first = registry.register(name="First", kind="pre_tool", command="echo one")
    second = registry.register(name="Second", kind="pre_tool", command="echo two")

    updated = registry.set_enabled(second["id"], False)

    assert updated["enabled"] is False
    assert registry.get(first["id"])["enabled"] is True


def test_running_one_hook_reuses_a_context_the_caller_already_built(tmp_path: Path):
    registry = HooksRegistry(tmp_path / "hooks.json")
    hook = registry.register(name="Ping", kind="post_run", enabled=False)
    context = HookContext("post_run", "run.finished", {"run_id": "r1"}, workspace_id="ws-1")

    result = registry.run_hook(hook["id"], context)

    assert result["status"] == "skipped"
    assert result["detail"] == "hook disabled"
    # The caller's context was reused verbatim, not rebuilt from the kwargs:
    # ``run_hook`` was given no ``event=``, yet the run log carries the one the
    # caller put on the context.
    logged = registry.recent_runs()["runs"][0]
    assert logged["target_event"] == "run.finished"
    assert logged["target_kind"] == "post_run"


# ── contracts.py ────────────────────────────────────────────────────────────


def test_contract_views_skips_records_that_carry_no_family_contract():
    run = {"id": "agent-run-1", "agent_id": "agent:planner", "status": "ok"}
    contract = run_record_contract(run)

    views = contract_views([{"nothing": "here"}, {"contract": contract}])

    assert len(views) == 1
    assert views[0]["run_id"] == "agent-run-1"


def test_the_runtime_boundary_protocol_stubs_are_callable_and_return_nothing():
    assert RuntimeBoundaryProtocol.boundary(object()) is None
    assert RuntimeBoundaryProtocol.config(object()) is None


# ── agent_runtime.py ────────────────────────────────────────────────────────


class _Store:
    """In-memory stand-in for the workspace run store."""

    def __init__(self) -> None:
        self.runs: List[Dict[str, Any]] = []

    def list_agents(self, workspace_id=None):
        return {"agents": [], "runs": list(reversed(self.runs))}

    def record_agent_run(self, **kw):
        run = {"id": f"agent-run-{len(self.runs)}", "created_at": "2026-08-09T00:00:00", **kw}
        self.runs.append(run)
        return run


def _runtime(store: _Store, **kwargs: Any) -> AgentRuntime:
    kwargs.setdefault("orchestrator_factory", lambda user, scope: MultiAgentOrchestrator())
    kwargs.setdefault("allow_simulation_runs", True)
    return AgentRuntime(
        store=store,
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        **kwargs,
    )


def test_the_roster_keeps_the_newest_run_when_an_agent_has_several():
    roster = _runtime(_Store())._roster(
        [
            {"agent_id": "agent:planner", "status": "ok", "created_at": "2026-08-09T10:00:00"},
            {"agent_id": "agent:planner", "status": "failed", "created_at": "2026-08-08T10:00:00"},
        ]
    )

    planner = next(entry for entry in roster if entry["id"] == "agent:planner")
    assert planner["runs"] == 2
    assert planner["last_status"] == "ok"
    assert planner["last_at"] == "2026-08-09T10:00:00"


def test_a_synthesis_without_decisions_writes_no_decisions_memory(monkeypatch):
    ingested: List[Dict[str, Any]] = []
    runtime = _runtime(_Store(), memory_ingest=lambda **kw: ingested.append(kw))
    monkeypatch.setattr(
        runtime,
        "_agent_synthesis_sections",
        lambda **_kw: {
            "facts": ["Recall improved"],
            "decisions": [],
            "followups": ["verify the recall quality"],
            "plan_steps": ["verify the recall quality"],
        },
    )
    result = AgentRunResult(
        agent_id="agent:executor",
        status="ok",
        output="Recall improved.",
        timeline=[],
        plan=[],
        review={},
        roles_run=["executor"],
    )

    runtime._synthesize_brain_memory(
        goal="tighten recall", result=result, user_email="a@example.com", scope="ws-1"
    )

    kinds = [entry["kind"] for entry in ingested]
    assert "decisions" not in kinds
    assert kinds == ["long_term", "workspace"]


def test_a_failed_run_is_recorded_but_never_enriches_the_brain():
    ingested: List[Dict[str, Any]] = []
    store = _Store()
    failed = AgentRunResult(
        agent_id="agent:executor",
        status="failed",
        output="the executor could not finish",
        timeline=[],
        plan=[],
        review={"outcome": "reject"},
        roles_run=["executor"],
    )

    class _FailingOrchestrator:
        mode = "llm"

        @staticmethod
        def run(*_args: Any, **_kwargs: Any) -> AgentRunResult:
            return failed

    runtime = _runtime(
        store,
        orchestrator_factory=lambda user, scope: _FailingOrchestrator(),
        memory_ingest=lambda **kw: ingested.append(kw),
    )

    payload = runtime.start("ship the release", user_email="a@example.com", scope="ws-1")

    assert payload["run"]["status"] == "failed"
    assert payload["result"]["status"] == "failed"
    assert ingested == []


# ── multi_agent.py ──────────────────────────────────────────────────────────


def test_a_created_handoff_records_neither_acceptance_nor_completion():
    ctx = OrchestrationContext(goal="index the design folder")

    ctx.handoff("planner", "executor", note="take it from here", status="created")

    events = [item["event"] for item in ctx.timeline]
    assert events == ["handoff_created", "handoff"]
    assert ctx.handoffs[0]["accepted_at"] is None
    assert ctx.handoffs[0]["completed_at"] is None


def test_a_retry_cannot_be_routed_when_the_pipeline_has_no_executor():
    def _runner(role: str, ctx: OrchestrationContext) -> Dict[str, Any]:
        ctx.review = {"outcome": "retry", "reason": "needs another pass", "notes": ["thin"]}
        ctx.output = "reviewed"
        return {"status": "ok", "review": ctx.review}

    orchestrator = MultiAgentOrchestrator(role_runner=_runner)

    result = orchestrator.run("tighten recall", roles=["reviewer"], max_retries=1)

    assert result.retries == 1
    assert result.retry_history[0]["reason"] == "needs another pass"
    # No executor to hand back to, so no retry handoff was ever created.
    assert [h["status"] for h in result.handoffs] == []
    assert result.roles_run == ["reviewer"]


def test_the_orchestrator_reports_its_declared_mode():
    assert MultiAgentOrchestrator(mode="llm").mode == "llm"


@pytest.mark.parametrize("status", ["created", "completed"])
def test_every_handoff_status_still_appends_the_compact_event(status: str):
    ctx = OrchestrationContext(goal="g")

    ctx.handoff("planner", "executor", status=status)

    assert ctx.timeline[-1]["event"] == "handoff"
