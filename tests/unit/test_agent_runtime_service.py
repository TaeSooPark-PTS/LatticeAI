"""AgentRuntime façade tests (lattice_brain.runtime.agent_runtime).

The façade wraps the existing MultiAgentOrchestrator + run store behind one
boundary (config/roles/health/status/start/events/stop) that the HTTP router —
and through it, the frontend — depends on.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agents import create_agents_router
from lattice_brain.runtime.contracts import RuntimeBoundaryProtocol, run_record_contract
from lattice_brain.runtime.multi_agent import CORE_PIPELINE, MultiAgentOrchestrator
from lattice_brain.runtime.agent_runtime import AgentRuntime, AgentRuntimeUnavailable


class FakeStore:
    """Minimal in-memory stand-in for the workspace run store."""

    def __init__(self):
        self.runs = []

    def list_agents(self, workspace_id=None):
        return {"agents": [], "runs": list(reversed(self.runs))}

    def record_agent_run(self, **kw):
        run = {"id": f"agent-run-{len(self.runs)}", "created_at": "2026-06-07T00:00:00", **kw}
        run["contract"] = run_record_contract(run)
        self.runs.append(run)
        return run

    def get_agent_run(self, run_id, workspace_id=None):
        for run in self.runs:
            if run["id"] == run_id:
                return run
        raise FileNotFoundError(run_id)

    def update_agent_run(self, run_id, *, workspace_id=None, graph=None, patch=None, **fields):
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        run.update({**(patch or {}), **fields})
        run["contract"] = run_record_contract(run)
        return run

    def replay_agent_run(self, run_id, workspace_id=None):
        return {"run_id": run_id, "frames": []}


def _runtime(*, allow_simulation_runs=True):
    return AgentRuntime(
        store=FakeStore(),
        orchestrator_factory=lambda user, scope: MultiAgentOrchestrator(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        allow_simulation_runs=allow_simulation_runs,
    )


def test_config_and_roles():
    from lattice_brain.runtime import RuntimeBoundaryProtocol as PublicRuntimeBoundaryProtocol

    assert PublicRuntimeBoundaryProtocol is RuntimeBoundaryProtocol
    rt = _runtime()
    assert isinstance(rt, RuntimeBoundaryProtocol)
    cfg = rt.config()
    assert cfg["boundary"]["schema_version"] == "runtime-boundary/v1"
    assert cfg["boundary"]["name"] == "AgentRuntime"
    assert cfg["boundary"]["runtime"] == "multi_agent"
    assert cfg["boundary"]["entrypoint"] == "lattice_brain.runtime.agent_runtime.AgentRuntime"
    assert cfg["boundary"]["surface"] == "/agents"
    assert cfg["default_pipeline"] == list(CORE_PIPELINE)
    assert cfg["execution_mode"] == "synchronous"
    roles = rt.roles()
    assert {r["role"] for r in roles} == {"researcher", "planner", "executor", "reviewer", "release"}


def test_health_ok():
    assert _runtime().health()["status"] == "ok"


def test_successful_agent_run_synthesis_splits_memory_sections():
    captured = []
    review_items = []
    review_sink = SimpleNamespace(create=lambda **kw: review_items.append(kw) or {"id": f"review-{len(review_items)}"})
    rt = AgentRuntime(
        store=FakeStore(),
        orchestrator_factory=lambda user, scope: MultiAgentOrchestrator(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        allow_simulation_runs=True,
        memory_ingest=lambda **kw: captured.append(kw) or {"id": f"memory-{len(captured)}"},
        review_sink=review_sink,
    )
    result = SimpleNamespace(
        status="ok",
        output="Implemented the workflow. Verified recall quality. Ready for review.",
        plan=[
            {"description": "implement workflow handoff"},
            {"description": "test recall quality"},
            {"description": "document follow-up actions"},
        ],
        review={"decision": "approved"},
        plan_review={},
        roles_run=["planner", "executor", "reviewer"],
    )

    rt._synthesize_brain_memory(goal="Ship action workflow", result=result, user_email="u@example.com", scope="personal")

    assert [item["kind"] for item in captured] == ["long_term", "decisions", "workspace"]
    long_term = captured[0]
    assert "Key facts:" in long_term["content"]
    assert "Decisions:" in long_term["content"]
    assert "Follow-ups:" in long_term["content"]
    assert long_term["metadata"]["synthesis_version"] == 2
    assert long_term["metadata"]["facts"]
    assert long_term["metadata"]["decisions"]
    assert captured[2]["tags"] == ["agent", "follow-up", "next-action"]
    assert review_items
    assert {item["source"] for item in review_items} == {"agent_followup"}
    assert review_items[0]["kind"] == "task_draft"
    assert review_items[0]["payload"]["followup"]


# --- Large candidate #4 slice: proactive contradiction / temporal detect (test) ---
def test_quality_detects_temporal_contradiction():
    from lattice_brain.quality import MemoryQualityManager
    mgr = MemoryQualityManager()
    mems = [
        {"content": "Use X for retrieval", "timestamp": 100},
        {"content": "Do not use X for retrieval", "timestamp": 200},
        {"content": "Use X for retrieval", "timestamp": 150},
    ]
    flagged = mgr.detect_temporal_contradictions(mems)
    # at least the negation pair should surface
    assert any("contradiction:temporal_negation" in str(f.get("proactive_flag", "")) for f in flagged)


def test_product_runtime_refuses_simulation_runs():
    rt = _runtime(allow_simulation_runs=False)
    health = rt.health()
    assert health["status"] == "unavailable"
    assert health["ready"] is False
    try:
        rt.start("Validate the runtime", user_email="dev@example.com", scope=None)
        assert False, "expected AgentRuntimeUnavailable"
    except AgentRuntimeUnavailable as exc:
        assert "Simulation mode is disabled" in str(exc)


def test_start_records_run_and_status_reflects_it():
    rt = _runtime()
    out = rt.start("Validate the runtime", user_email="dev@example.com", scope=None,
                   roles=["planner", "executor", "reviewer"])
    assert out["result"]["status"] in {"ok", "retried_ok"}
    run_id = out["run"]["id"]

    status = rt.status(scope=None)
    assert status["runtime"]["total_runs"] == 1
    assert len(status["contracts"]) == 1
    assert status["contracts"][0]["family"] == "agent-run-contract/v1"
    assert len(status["roles"]) == 5
    # roster is the canonical roles enriched with real run counts
    executor = next(a for a in status["agents"] if a["id"] == "agent:executor")
    assert executor["runs"] == 1

    events = rt.events(run_id, scope=None)
    assert events["is_final"] is True
    assert isinstance(events["timeline"], list) and events["timeline"]
    assert out["result"]["contract"]["schema_version"] == "agent-run-contract/v1"
    assert out["result"]["contract"]["runtime"] == "multi_agent"
    assert out["result"]["contract"]["run_id"] == run_id


def test_events_synthesize_contract_for_legacy_run():
    rt = _runtime()
    rt._store.runs.append({
        "id": "legacy-run",
        "status": "ok",
        "timeline": [{"event": "legacy"}],
        "handoffs": [],
    })

    events = rt.events("legacy-run", scope=None)
    assert events["is_final"] is True
    # Legacy rows persisted before the contract family still expose the
    # agent-run-contract/v1 envelope, synthesized from the raw run record.
    assert events["contract"] is not None
    assert events["contract"]["schema_version"] == "agent-run-contract/v1"
    assert events["contract"]["id"] == "legacy-run"
    assert events["contract"]["status"] == "ok"

    payload = rt.get_run("legacy-run", scope=None)
    assert payload["contract"]["id"] == "legacy-run"


def test_start_rejects_unknown_roles():
    rt = _runtime()
    try:
        rt.start("Do the thing", user_email=None, scope=None, roles=["planner", "hacker"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown roles" in str(exc)
        assert "hacker" in str(exc)
    assert rt._store.runs == []


def test_reserve_run_rejects_unknown_roles_and_clamps_retries():
    rt = _runtime()
    try:
        rt.reserve_run("Do the thing", user_email=None, scope=None, roles=["ghost"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown roles" in str(exc)
    assert rt._store.runs == []

    reserved = rt.reserve_run("Do the thing", user_email=None, scope=None, max_retries=99)
    assert reserved["run"]["max_retries"] == 5


def test_start_requires_goal():
    rt = _runtime()
    try:
        rt.start("   ", user_email=None, scope=None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_preview_explains_readiness_without_recording_run():
    rt = _runtime(allow_simulation_runs=False)
    preview = rt.preview(
        "Ship the next release",
        roles=["planner", "executor", "invalid"],
        inputs={"ticket": "T-72"},
        max_retries=99,
        scope="personal",
    )
    assert preview["ready"] is False
    assert preview["max_retries"] == 5
    assert preview["unknown_roles"] == ["invalid"]
    assert any("unknown roles" in reason for reason in preview["blocking_reasons"])
    assert any("Simulation mode is disabled" in reason or "No LLM-backed model" in reason for reason in preview["blocking_reasons"])
    assert rt.status(scope=None)["runtime"]["total_runs"] == 0


def test_stop_is_honest_for_synchronous_runs():
    rt = _runtime()
    out = rt.start("a goal", user_email=None, scope=None)
    stopped = rt.stop(out["run"]["id"], scope=None)
    assert stopped["stopped"] is False
    assert "finished" in stopped["reason"]
    missing = rt.stop("nope", scope=None)
    assert missing["stopped"] is False
    assert missing["reason"] == "run not found"


def _router_client():
    store = FakeStore()
    runtime = AgentRuntime(
        store=store,
        orchestrator_factory=lambda user, scope: MultiAgentOrchestrator(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
    )
    app = FastAPI()
    app.include_router(create_agents_router(
        store=store,
        orchestrator_factory=lambda user, scope: MultiAgentOrchestrator(),
        require_user=lambda request: "tester",
        get_current_user=lambda request: "tester",
        gate_read=lambda request: None,
        gate_write=lambda request: None,
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        agent_runtime=runtime,
    ))
    return TestClient(app)


def test_router_runtime_endpoints():
    client = _router_client()
    assert client.get("/agents/api/runtime/status").status_code == 200
    health = client.get("/agents/api/runtime/health").json()
    assert health["status"] == "unavailable"
    assert health["ready"] is False
    assert client.get("/agents/api/runtime/config").json()["default_pipeline"] == list(CORE_PIPELINE)

    run = client.post("/agents/api/run", json={"goal": "router run", "roles": ["planner", "executor", "reviewer"]})
    assert run.status_code == 409
    assert "Simulation mode is disabled" in run.text

    status = client.get("/agents/api/runtime/status").json()
    assert status["runtime"]["ready"] is False
    assert status["runtime"]["total_runs"] == 0

    preview = client.post("/agents/api/run/preview", json={"goal": "router run", "roles": ["planner"]})
    assert preview.status_code == 200
    assert preview.json()["can_start"] is False


def test_router_run_requires_goal():
    client = _router_client()
    r = client.post("/agents/api/run", json={"goal": ""})
    assert r.status_code == 400
