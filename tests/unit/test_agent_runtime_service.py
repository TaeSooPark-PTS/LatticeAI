"""AgentRuntime façade tests (lattice_brain.runtime.agent_runtime).

The façade wraps the existing MultiAgentOrchestrator + run store behind one
boundary (config/roles/health/status/start/events/stop) that the HTTP router —
and through it, the frontend — depends on.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.agents import create_agents_router
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
        self.runs.append(run)
        return run

    def get_agent_run(self, run_id, workspace_id=None):
        for run in self.runs:
            if run["id"] == run_id:
                return run
        raise FileNotFoundError(run_id)

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
    rt = _runtime()
    cfg = rt.config()
    assert cfg["default_pipeline"] == list(CORE_PIPELINE)
    assert cfg["execution_mode"] == "synchronous"
    roles = rt.roles()
    assert {r["role"] for r in roles} == {"researcher", "planner", "executor", "reviewer", "release"}


def test_health_ok():
    assert _runtime().health()["status"] == "ok"


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
    assert len(status["roles"]) == 5
    # roster is the canonical roles enriched with real run counts
    executor = next(a for a in status["agents"] if a["id"] == "agent:executor")
    assert executor["runs"] == 1

    events = rt.events(run_id, scope=None)
    assert events["is_final"] is True
    assert isinstance(events["timeline"], list) and events["timeline"]


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
