"""wp31: the ``/agents/api/*`` read, stop, snapshot and executor surfaces.

``tests/unit/test_agent_runtime_service.py`` covers the runtime façade plus the
status/health/config/run/preview endpoints; everything else in the router —
run events, stop, the page redirect, the role catalog, run listing, handoffs,
run detail, replay, memory snapshots, the ``run_executor`` delegation and the
``PermissionError`` (hook-gated) translation — never ran.

The runtime is the real :class:`~lattice_brain.runtime.agent_runtime.AgentRuntime`
over a small in-memory store, with simulation runs explicitly enabled, so the
read endpoints assert against a genuinely persisted run record.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.runtime.agent_runtime import AgentRuntime
from lattice_brain.runtime.contracts import run_record_contract
from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator
from latticeai.api.agents import create_agents_router

USER = "runner@example.com"


class MemoryRunStore:
    """The store surface the agents router and the runtime actually touch."""

    def __init__(self) -> None:
        self.runs: List[Dict[str, Any]] = []
        self.handoffs: List[Dict[str, Any]] = [
            {"id": "handoff-1", "run_id": "agent-run-0", "from": "planner", "to": "executor"},
            {"id": "handoff-2", "run_id": "agent-run-9", "from": "executor", "to": "reviewer"},
        ]
        self.snapshots: List[Dict[str, Any]] = []

    # ── runs ──────────────────────────────────────────────────────────────
    def list_agents(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        return {"agents": [], "runs": list(reversed(self.runs))}

    def record_agent_run(self, **fields: Any) -> Dict[str, Any]:
        run = {
            "id": f"agent-run-{len(self.runs)}",
            "created_at": "2026-06-07T00:00:00",
            **fields,
        }
        run["contract"] = run_record_contract(run)
        self.runs.append(run)
        return run

    def get_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        for run in self.runs:
            if run["id"] == run_id:
                return run
        raise FileNotFoundError(run_id)

    def update_agent_run(self, run_id, *, workspace_id=None, graph=None, patch=None, **fields):
        run = self.get_agent_run(run_id, workspace_id=workspace_id)
        run.update({**(patch or {}), **fields})
        run["contract"] = run_record_contract(run)
        return run

    def replay_agent_run(self, run_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        self.get_agent_run(run_id, workspace_id=workspace_id)
        return {"run_id": run_id, "frames": [{"role": "planner", "status": "ok"}]}

    # ── handoffs / snapshots ──────────────────────────────────────────────
    def list_handoffs(self, *, workspace_id: Optional[str] = None, run_id: Optional[str] = None):
        rows = [h for h in self.handoffs if run_id is None or h["run_id"] == run_id]
        return {"handoffs": rows, "total": len(rows), "workspace_id": workspace_id}

    def list_memory_snapshots(self, *, workspace_id: Optional[str] = None, limit: int = 50):
        return {"snapshots": self.snapshots[:limit], "workspace_id": workspace_id}

    def create_memory_snapshot(self, *, label, reason, memory_ids, user_email, workspace_id):
        snapshot = {
            "id": f"memory-snapshot-{len(self.snapshots)}",
            "label": label,
            "reason": reason,
            "memory_ids": memory_ids,
            "user_email": user_email,
            "workspace_id": workspace_id,
        }
        self.snapshots.append(snapshot)
        return snapshot


class RaisingRuntime:
    """Stands in for the runtime when the router's error mapping is the subject."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def start(self, *args: Any, **kwargs: Any):
        raise self.error


class RecordingExecutor:
    """Async run executor — the alternate ``/agents/api/run`` path."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def start_agent(self, goal: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"goal": goal, **kwargs})
        return {"run": {"id": "queued-run-1", "status": "queued"}, "async": True}


def build(*, runtime: Any = None, run_executor: Any = None, scope: Optional[str] = None):
    store = MemoryRunStore()
    real_runtime = runtime or AgentRuntime(
        store=store,
        orchestrator_factory=lambda user, ws: MultiAgentOrchestrator(),
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        allow_simulation_runs=True,
    )
    app = FastAPI()
    app.include_router(
        create_agents_router(
            store=store,
            orchestrator_factory=lambda user, ws: MultiAgentOrchestrator(),
            require_user=lambda request: USER,
            get_current_user=lambda request: USER,
            gate_read=lambda request: scope,
            gate_write=lambda request: scope,
            workspace_graph=lambda: None,
            append_audit_event=lambda *a, **k: None,
            agent_runtime=real_runtime,
            run_executor=run_executor,
        )
    )
    return TestClient(app), store


@pytest.fixture()
def agents():
    return build()


def _start_run(client) -> str:
    started = client.post(
        "/agents/api/run", json={"goal": "ship the agents router", "roles": ["planner"]}
    )
    assert started.status_code == 200, started.text
    return started.json()["run"]["id"]


def test_agents_page_redirects_into_the_spa(agents):
    client, _store = agents

    response = client.get("/agents?tab=runs", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/app#/agents?tab=runs"


def test_role_catalog_pairs_every_role_with_its_agent_id(agents):
    client, _store = agents

    body = client.get("/agents/api/roles").json()

    assert [role["role"] for role in body["roles"]] == [
        "researcher",
        "planner",
        "executor",
        "reviewer",
        "release",
    ]
    assert body["roles"][0]["agent_id"] == "agent:researcher"
    assert body["default_pipeline"] == ["planner", "executor", "reviewer"]


def test_run_listing_detail_events_and_replay_describe_a_real_run(agents):
    client, _store = agents
    run_id = _start_run(client)

    listing = client.get("/agents/api/runs").json()
    detail = client.get(f"/agents/api/runs/{run_id}").json()
    events = client.get(f"/agents/api/runs/{run_id}/events").json()
    replay = client.get(f"/agents/api/runs/{run_id}/replay").json()

    assert [run["id"] for run in listing["runs"]] == [run_id]
    assert listing["contracts"]
    assert detail["run"]["id"] == run_id
    assert detail["contract"]["run_id"] == run_id
    assert events["run_id"] == run_id
    assert events["is_final"] is True
    assert replay["replay"]["run_id"] == run_id
    assert replay["replay"]["frames"][0]["role"] == "planner"


@pytest.mark.parametrize("suffix", ["", "/events", "/replay"])
def test_unknown_run_ids_are_404_not_500(agents, suffix):
    client, _store = agents

    response = client.get(f"/agents/api/runs/ghost-run{suffix}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Agent run not found: ghost-run"


def test_stop_is_honest_about_a_finished_synchronous_run(agents):
    client, _store = agents
    run_id = _start_run(client)

    stopped = client.post(f"/agents/api/runs/{run_id}/stop").json()
    missing = client.post("/agents/api/runs/ghost-run/stop").json()

    assert stopped["stopped"] is False
    assert "finished" in stopped["reason"]
    assert missing["stopped"] is False
    assert missing["reason"] == "run not found"


def test_handoffs_can_be_filtered_by_run(agents):
    client, _store = agents

    everything = client.get("/agents/api/handoffs").json()
    filtered = client.get("/agents/api/handoffs", params={"run_id": "agent-run-0"}).json()

    assert everything["total"] == 2
    assert [row["id"] for row in filtered["handoffs"]] == ["handoff-1"]


def test_memory_snapshots_are_listed_after_being_created(agents):
    client, store = agents

    empty = client.get("/agents/api/memory/snapshots", params={"limit": 5}).json()
    created = client.post(
        "/agents/api/memory/snapshots",
        json={"label": "before refactor", "reason": "safety", "memory_ids": ["m-1"]},
    ).json()
    after = client.get("/agents/api/memory/snapshots").json()

    assert empty["snapshots"] == []
    assert created["snapshot"]["label"] == "before refactor"
    assert created["snapshot"]["memory_ids"] == ["m-1"]
    assert created["snapshot"]["user_email"] == USER
    assert [row["id"] for row in after["snapshots"]] == [created["snapshot"]["id"]]
    assert store.snapshots


def test_memory_snapshot_without_ids_captures_everything(agents):
    client, store = agents

    created = client.post("/agents/api/memory/snapshots", json={}).json()

    assert created["snapshot"]["label"] == "agent memory snapshot"
    # An empty id list means "no filter", not "snapshot nothing".
    assert created["snapshot"]["memory_ids"] is None
    assert store.snapshots[0]["reason"] == ""


def test_run_executor_takes_over_when_one_is_wired(agents):
    executor = RecordingExecutor()
    client, _store = build(run_executor=executor, scope="ws-async")

    response = client.post(
        "/agents/api/run",
        json={
            "goal": "queue this run",
            "roles": ["planner", "executor", "reviewer"],
            "inputs": {"ticket": "LAT-1"},
            "max_retries": 4,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "run": {"id": "queued-run-1", "status": "queued"},
        "async": True,
    }
    assert executor.calls == [
        {
            "goal": "queue this run",
            "user_email": USER,
            "scope": "ws-async",
            # The standard three-role pipeline is grounded in recall first.
            "roles": ["researcher", "planner", "executor", "reviewer"],
            "inputs": {"ticket": "LAT-1"},
            "max_retries": 4,
        }
    ]


def test_a_pre_run_hook_that_gates_the_run_becomes_a_403(agents):
    client, _store = build(
        runtime=RaisingRuntime(PermissionError("pre_run hook blocked this run"))
    )

    response = client.post("/agents/api/run", json={"goal": "blocked goal"})

    assert response.status_code == 403
    assert response.json()["detail"] == "pre_run hook blocked this run"
