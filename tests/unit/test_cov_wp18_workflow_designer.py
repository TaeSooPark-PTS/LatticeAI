"""wp18 — the workflow designer router driven through its factory.

Every endpoint of :func:`create_workflow_designer_router` is exercised over a
fake ``WorkspaceOSStore`` that records what it was asked to persist: the CRUD
surface, validation refusals, synchronous execution through the real
:class:`lattice_brain.workflow.WorkflowEngine` (including an approval pause and
its resume), the async run-executor branch, stop/replay/export/import, trigger
status, and the consent-first automation-recipe install path.

Assertions are on status codes, persisted store state, and recorded audit
events — never on "it did not raise".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.workflow import ApprovalRequired
from latticeai.api.workflow_designer import create_workflow_designer_router

USER = "user@example.com"
SCOPE = "personal"


def _valid_nodes() -> List[Dict[str, Any]]:
    return [
        {"id": "trigger", "type": "trigger", "name": "Start",
         "config": {"trigger": "manual"}, "next": "act"},
        {"id": "act", "type": "tool", "name": "Do it",
         "config": {"tool": "echo"}, "next": "output"},
        {"id": "output", "type": "output", "name": "Done",
         "config": {"value": "finished"}, "next": None},
    ]


def _broken_nodes() -> List[Dict[str, Any]]:
    return [{"id": "trigger", "type": "trigger", "config": {}, "next": "ghost"}]


class FakeStore:
    """Just enough workflow persistence for the designer router."""

    def __init__(self, workflows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.workflows: Dict[str, Dict[str, Any]] = {
            wf["id"]: wf for wf in (workflows or [])
        }
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.reads: List[Dict[str, Any]] = []
        self.recorded_runs: List[Dict[str, Any]] = []
        self.resolved: List[Dict[str, Any]] = []
        self._seq = 0

    # ── definitions ────────────────────────────────────────────────────
    def list_workflows(self, query: str = "", workspace_id: Optional[str] = None):
        self.reads.append({"op": "list", "query": query, "workspace_id": workspace_id})
        return {"workflows": list(self.workflows.values())}

    def create_workflow(self, *, name, steps, nodes, metadata, user_email, graph,
                        workspace_id):
        self._seq += 1
        workflow = {
            "id": f"wf-{self._seq}",
            "name": name,
            "steps": steps,
            "nodes": nodes,
            "metadata": metadata,
            "user_email": user_email,
            "graph": graph,
            "workspace_id": workspace_id,
        }
        self.workflows[workflow["id"]] = workflow
        return workflow

    def get_workflow(self, workflow_id, workspace_id: Optional[str] = None):
        self.reads.append({"op": "get", "id": workflow_id, "workspace_id": workspace_id})
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def update_workflow_definition(self, workflow_id, *, name=None, nodes=None,
                                   metadata=None, workspace_id=None):
        workflow = self.get_workflow(workflow_id, workspace_id=workspace_id)
        if name is not None:
            workflow["name"] = name
        if nodes is not None:
            workflow["nodes"] = nodes
        if metadata is not None:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        return workflow

    # ── runs ───────────────────────────────────────────────────────────
    def record_workflow_run(self, *, workflow_id, name, status, timeline, outputs,
                            user_email, graph, workspace_id, mode, pause):
        self._seq += 1
        run = {
            "id": f"run-{self._seq}",
            "workflow_id": workflow_id,
            "name": name,
            "status": status,
            "timeline": timeline,
            "outputs": outputs,
            "user_email": user_email,
            "workspace_id": workspace_id,
            "mode": mode,
            "pause": pause,
        }
        self.runs[run["id"]] = run
        self.recorded_runs.append(run)
        return run

    def get_workflow_run(self, run_id, workspace_id: Optional[str] = None):
        if run_id not in self.runs:
            raise FileNotFoundError(run_id)
        return self.runs[run_id]

    def list_workflow_runs(self, workflow_id=None, limit=50, workspace_id=None):
        self.reads.append({
            "op": "list_runs", "workflow_id": workflow_id,
            "limit": limit, "workspace_id": workspace_id,
        })
        runs = [
            run for run in self.runs.values()
            if workflow_id is None or run.get("workflow_id") == workflow_id
        ]
        return {"runs": runs[:limit]}

    def mark_workflow_run_resolved(self, run_id, *, resumed_run_id, approved,
                                   workspace_id=None):
        self.resolved.append({
            "run_id": run_id, "resumed_run_id": resumed_run_id,
            "approved": approved, "workspace_id": workspace_id,
        })

    def replay_workflow_run(self, run_id, workspace_id: Optional[str] = None):
        if run_id not in self.runs:
            raise FileNotFoundError(run_id)
        return {"run_id": run_id, "steps": self.runs[run_id]["timeline"]}


class FakeRunExecutor:
    """Async run-executor seam: records the queue call, echoes a queued run."""

    def __init__(self) -> None:
        self.started: List[Dict[str, Any]] = []
        self.cancelled: List[Dict[str, Any]] = []

    async def start_workflow(self, workflow, *, workflow_id, user_email, scope,
                             inputs=None):
        self.started.append({
            "workflow_id": workflow_id, "user_email": user_email,
            "scope": scope, "inputs": inputs, "name": workflow.get("name"),
        })
        return {"run": {"id": "run-queued", "status": "queued"}, "accepted": True}

    def cancel(self, run_id, *, kind=None, scope=None):
        self.cancelled.append({"run_id": run_id, "kind": kind, "scope": scope})
        return {"stopped": True, "run_id": run_id, "kind": kind}


class RecordingTriggerService:
    def describe(self):
        return {"running": True, "tick_seconds": 5.0, "tz": "UTC",
                "armed": [{"workflow_id": "wf-1", "kind": "interval"}]}


def _client(store, *, run_executor=None, trigger_service=None, runners=None,
            hooks=None):
    app = FastAPI()
    audit: List[Dict[str, Any]] = []
    built_for: List[Any] = []

    def _build_runners(user, scope):
        built_for.append((user, scope))
        return dict(runners or {})

    app.include_router(create_workflow_designer_router(
        store=store,
        require_user=lambda _request: USER,
        get_current_user=lambda _request: USER,
        gate_read=lambda _request: SCOPE,
        gate_write=lambda _request: SCOPE,
        workspace_graph=lambda: None,
        build_runners=_build_runners,
        append_audit_event=lambda event, **kw: audit.append({"event": event, **kw}),
        hooks=hooks,
        run_executor=run_executor,
        trigger_service=trigger_service,
    ))
    client = TestClient(app)
    client.audit = audit  # type: ignore[attr-defined]
    client.built_for = built_for  # type: ignore[attr-defined]
    return client


# ── page + definition CRUD ──────────────────────────────────────────────

def test_workflows_page_redirects_into_the_spa():
    client = _client(FakeStore())
    response = client.get("/workflows?focus=wf-1", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/app#/workflows?focus=wf-1"


def test_list_definitions_forwards_query_and_read_scope():
    store = FakeStore([{"id": "wf-1", "name": "Existing", "nodes": []}])
    client = _client(store)
    response = client.get("/workflows/api/definitions", params={"q": "exist"})
    assert response.status_code == 200
    assert [wf["id"] for wf in response.json()["workflows"]] == ["wf-1"]
    assert store.reads[-1] == {"op": "list", "query": "exist", "workspace_id": SCOPE}


def test_create_definition_refuses_invalid_nodes():
    store = FakeStore()
    client = _client(store)
    response = client.post(
        "/workflows/api/definitions",
        json={"name": "Broken", "nodes": _broken_nodes()},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["validation_errors"]
    assert store.workflows == {}


def test_create_definition_persists_projection_and_audits():
    store = FakeStore()
    client = _client(store)
    response = client.post(
        "/workflows/api/definitions",
        json={"name": "Nightly", "nodes": _valid_nodes(), "metadata": {"tag": "x"}},
    )
    assert response.status_code == 200
    workflow = response.json()["workflow"]
    assert workflow["name"] == "Nightly"
    assert workflow["workspace_id"] == SCOPE
    assert workflow["user_email"] == USER
    # The legacy ``steps`` projection is persisted alongside the nodes.
    assert [step["node"] for step in workflow["steps"]] == ["trigger", "act", "output"]
    assert client.audit == [  # type: ignore[attr-defined]
        {"event": "workflow_created", "user_email": USER, "workflow_id": workflow["id"]}
    ]


def test_get_definition_returns_workflow_or_404():
    store = FakeStore([{"id": "wf-1", "name": "One", "nodes": _valid_nodes()}])
    client = _client(store)
    found = client.get("/workflows/api/definitions/wf-1")
    assert found.status_code == 200
    assert found.json()["workflow"]["name"] == "One"
    missing = client.get("/workflows/api/definitions/nope")
    assert missing.status_code == 404
    assert "Workflow not found" in missing.json()["detail"]


def test_update_definition_validates_nodes_before_persisting():
    store = FakeStore([{"id": "wf-1", "name": "One", "nodes": _valid_nodes()}])
    client = _client(store)
    rejected = client.patch(
        "/workflows/api/definitions/wf-1", json={"nodes": _broken_nodes()},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["validation_errors"]
    assert store.workflows["wf-1"]["nodes"] == _valid_nodes()


def test_update_definition_applies_name_nodes_and_metadata():
    store = FakeStore([{"id": "wf-1", "name": "One", "nodes": _valid_nodes()}])
    client = _client(store)
    response = client.patch(
        "/workflows/api/definitions/wf-1",
        json={"name": "Renamed", "nodes": _valid_nodes(), "metadata": {"tag": "y"}},
    )
    assert response.status_code == 200
    assert response.json()["workflow"]["name"] == "Renamed"
    assert store.workflows["wf-1"]["metadata"] == {"tag": "y"}


def test_update_definition_unknown_workflow_is_404():
    client = _client(FakeStore())
    response = client.patch("/workflows/api/definitions/nope", json={"name": "x"})
    assert response.status_code == 404


def test_validate_endpoint_reports_errors_without_persisting():
    store = FakeStore()
    client = _client(store)
    ok = client.post("/workflows/api/validate",
                     json={"name": "Draft", "nodes": _valid_nodes()})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "errors": []}
    bad = client.post("/workflows/api/validate",
                      json={"name": "Draft", "nodes": _broken_nodes()})
    assert bad.json()["ok"] is False
    assert bad.json()["errors"]
    assert store.workflows == {}


# ── running ─────────────────────────────────────────────────────────────

def test_run_definition_unknown_workflow_is_404():
    client = _client(FakeStore())
    response = client.post("/workflows/api/definitions/nope/run", json={"inputs": {}})
    assert response.status_code == 404


def test_run_definition_queues_through_the_run_executor_when_wired():
    store = FakeStore([{"id": "wf-1", "name": "Async", "nodes": _valid_nodes()}])
    executor = FakeRunExecutor()
    client = _client(store, run_executor=executor)
    response = client.post("/workflows/api/definitions/wf-1/run",
                           json={"inputs": {"topic": "release"}})
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "queued"
    assert executor.started == [{
        "workflow_id": "wf-1", "user_email": USER, "scope": SCOPE,
        "inputs": {"topic": "release"}, "name": "Async",
    }]
    # Nothing is recorded synchronously — the executor owns the run row.
    assert store.recorded_runs == []
    assert client.audit[-1]["event"] == "workflow_run_queued"  # type: ignore[attr-defined]
    assert client.audit[-1]["status"] == "queued"  # type: ignore[attr-defined]


def test_run_definition_executes_synchronously_without_an_executor():
    store = FakeStore([{"id": "wf-1", "name": "Sync", "nodes": _valid_nodes()}])
    seen: List[Dict[str, Any]] = []

    def tool_runner(*, node, context):
        seen.append({"node": node["id"], "inputs": context.get("inputs")})
        return {"echo": context.get("topic")}

    client = _client(store, runners={"tool": tool_runner})
    response = client.post("/workflows/api/definitions/wf-1/run",
                           json={"inputs": {"topic": "release"}})
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["status"] == "ok"
    assert body["run"]["mode"] == "live"
    assert body["run"]["pause"] is None
    assert seen == [{"node": "act", "inputs": {"topic": "release"}}]
    assert client.built_for == [(USER, SCOPE)]  # type: ignore[attr-defined]
    assert store.recorded_runs[0]["status"] == "ok"
    assert client.audit[-1] == {  # type: ignore[attr-defined]
        "event": "workflow_run", "user_email": USER,
        "workflow_id": "wf-1", "status": "ok",
    }


def _approval_runner(calls: List[str]):
    def runner(*, node, context):
        calls.append(node["id"])
        if node["id"] not in set(context.get("__approved_nodes__") or []):
            raise ApprovalRequired("needs a human", tool="echo", args={"n": 1})
        return {"ran": True}
    return runner


def test_paused_run_is_recorded_then_resumed_on_approval():
    store = FakeStore([{"id": "wf-1", "name": "Governed", "nodes": _valid_nodes()}])
    calls: List[str] = []
    client = _client(store, runners={"tool": _approval_runner(calls)})

    started = client.post("/workflows/api/definitions/wf-1/run", json={"inputs": {}})
    assert started.status_code == 200
    assert started.json()["result"]["status"] == "awaiting_approval"
    paused_run = started.json()["run"]
    assert paused_run["status"] == "awaiting_approval"
    assert paused_run["pause"]["node"] == "act"
    assert paused_run["pause"]["pending"]["tool"] == "echo"

    resumed = client.post(
        "/workflows/api/runs/{0}/resume".format(paused_run["id"]),
        json={"approved": True},
    )
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["resumed_from"] == paused_run["id"]
    assert body["result"]["status"] == "ok"
    # The paused node is the only node re-entered; the run row is linked.
    assert calls == ["act", "act"]
    assert store.resolved == [{
        "run_id": paused_run["id"], "resumed_run_id": body["run"]["id"],
        "approved": True, "workspace_id": SCOPE,
    }]
    assert client.audit[-1]["event"] == "workflow_run_resume"  # type: ignore[attr-defined]
    assert client.audit[-1]["approved"] is True  # type: ignore[attr-defined]


def test_resume_refuses_a_run_that_is_not_awaiting_approval():
    store = FakeStore([{"id": "wf-1", "name": "Done", "nodes": _valid_nodes()}])
    store.runs["run-done"] = {
        "id": "run-done", "workflow_id": "wf-1", "status": "ok",
        "timeline": [], "pause": None,
    }
    client = _client(store)
    response = client.post("/workflows/api/runs/run-done/resume",
                           json={"approved": True})
    assert response.status_code == 409
    assert response.json()["detail"] == "run is not awaiting approval"
    assert store.resolved == []


def test_resume_denial_fails_the_run_honestly():
    store = FakeStore([{"id": "wf-1", "name": "Governed", "nodes": _valid_nodes()}])
    store.runs["run-paused"] = {
        "id": "run-paused", "workflow_id": "wf-1", "status": "awaiting_approval",
        "timeline": [{"node": "trigger", "status": "ok"}],
        "pause": {"node": "act", "pending": {"tool": "echo"}, "context": {"inputs": {}}},
    }
    calls: List[str] = []
    client = _client(store, runners={"tool": _approval_runner(calls)})
    response = client.post("/workflows/api/runs/run-paused/resume",
                           json={"approved": False})
    assert response.status_code == 200
    assert response.json()["result"]["status"] == "failed"
    assert calls == [], "a denied node must never execute"
    assert store.resolved[-1]["approved"] is False


# ── stop ────────────────────────────────────────────────────────────────

def test_stop_run_without_executor_says_so_honestly():
    store = FakeStore()
    store.runs["run-1"] = {"id": "run-1", "status": "running", "timeline": []}
    client = _client(store, run_executor=None)
    response = client.post("/workflows/api/runs/run-1/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["stopped"] is False
    assert body["status"] == "running"
    assert "synchronous runtime" in body["reason"]


def test_stop_unknown_run_without_executor_is_404():
    client = _client(FakeStore(), run_executor=None)
    assert client.post("/workflows/api/runs/nope/stop").status_code == 404


def test_stop_run_delegates_to_the_executor():
    executor = FakeRunExecutor()
    client = _client(FakeStore(), run_executor=executor)
    response = client.post("/workflows/api/runs/run-7/stop")
    assert response.status_code == 200
    assert response.json()["stopped"] is True
    assert executor.cancelled == [
        {"run_id": "run-7", "kind": "workflow", "scope": SCOPE}
    ]


# ── run listings, replay ────────────────────────────────────────────────

def test_run_listings_are_scoped_and_limited():
    store = FakeStore()
    store.runs["run-1"] = {"id": "run-1", "workflow_id": "wf-1", "timeline": []}
    store.runs["run-2"] = {"id": "run-2", "workflow_id": "wf-2", "timeline": []}
    client = _client(store)

    scoped = client.get("/workflows/api/definitions/wf-1/runs", params={"limit": 5})
    assert [run["id"] for run in scoped.json()["runs"]] == ["run-1"]
    assert store.reads[-1] == {
        "op": "list_runs", "workflow_id": "wf-1", "limit": 5, "workspace_id": SCOPE,
    }

    every = client.get("/workflows/api/runs", params={"limit": 9})
    assert {run["id"] for run in every.json()["runs"]} == {"run-1", "run-2"}
    assert store.reads[-1]["workflow_id"] is None
    assert store.reads[-1]["limit"] == 9


def test_replay_returns_the_timeline_or_404():
    store = FakeStore()
    store.runs["run-1"] = {"id": "run-1", "workflow_id": "wf-1",
                           "timeline": [{"node": "trigger"}]}
    client = _client(store)
    ok = client.get("/workflows/api/runs/run-1/replay")
    assert ok.status_code == 200
    assert ok.json()["replay"]["steps"] == [{"node": "trigger"}]
    assert client.get("/workflows/api/runs/nope/replay").status_code == 404


# ── triggers ────────────────────────────────────────────────────────────

def test_trigger_status_without_a_service_reports_nothing_armed():
    client = _client(FakeStore(), trigger_service=None)
    assert client.get("/workflows/api/triggers").json() == {
        "running": False, "tick_seconds": None, "armed": [],
    }


def test_trigger_status_delegates_to_the_service():
    client = _client(FakeStore(), trigger_service=RecordingTriggerService())
    body = client.get("/workflows/api/triggers").json()
    assert body["running"] is True
    assert body["armed"][0]["workflow_id"] == "wf-1"


# ── export / import ─────────────────────────────────────────────────────

def test_export_definition_returns_portable_payload_or_404():
    store = FakeStore([{"id": "wf-1", "name": "Portable", "nodes": _valid_nodes(),
                        "metadata": {"tag": "x"}}])
    client = _client(store)
    exported = client.get("/workflows/api/export/wf-1")
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["name"] == "Portable"
    assert [node["id"] for node in payload["nodes"]] == ["trigger", "act", "output"]
    assert client.get("/workflows/api/export/nope").status_code == 404


def test_import_definition_refuses_invalid_payloads():
    store = FakeStore()
    client = _client(store)
    response = client.post("/workflows/api/import",
                           json={"data": {"name": "Bad", "nodes": _broken_nodes()}})
    assert response.status_code == 400
    assert "unknown node" in response.json()["detail"]
    assert store.workflows == {}


def test_import_definition_creates_a_scoped_workflow_and_audits():
    store = FakeStore()
    client = _client(store)
    response = client.post("/workflows/api/import", json={"data": {
        "name": "Imported", "nodes": _valid_nodes(), "metadata": {"origin": "file"},
    }})
    assert response.status_code == 200
    workflow = response.json()["workflow"]
    assert workflow["name"] == "Imported"
    assert workflow["metadata"] == {"origin": "file", "imported": True}
    assert workflow["workspace_id"] == SCOPE
    assert client.audit == [  # type: ignore[attr-defined]
        {"event": "workflow_imported", "user_email": USER,
         "workflow_id": workflow["id"]}
    ]


# ── automation recipes ──────────────────────────────────────────────────

def test_automation_recipes_are_listed_consent_first():
    client = _client(FakeStore())
    body = client.get("/workflows/api/automation/recipes").json()
    assert body["principles"]["local_first"] is True
    assert {recipe["id"] for recipe in body["recipes"]} >= {"daily-memory-digest"}


def test_installing_an_unknown_recipe_is_404():
    client = _client(FakeStore())
    response = client.post("/workflows/api/automation/recipes/no-such-recipe",
                           json={"enabled": False})
    assert response.status_code == 404
    assert "Automation recipe not found" in response.json()["detail"]


def test_enabling_an_existing_recipe_draft_with_broken_nodes_is_refused():
    """The enable path re-validates the user's edited nodes before persisting."""
    store = FakeStore([{
        "id": "wf-recipe",
        "name": "Edited digest",
        "nodes": [{"id": "trigger", "type": "trigger", "config": {}, "next": "ghost"}],
        "metadata": {
            "created_from": "brain_automation_recipe",
            "recipe_id": "daily-memory-digest",
            "automation_state": "draft_disabled",
        },
    }])
    client = _client(store)
    response = client.post("/workflows/api/automation/recipes/daily-memory-digest",
                           json={"enabled": True})
    assert response.status_code == 400
    assert response.json()["detail"]["validation_errors"]
    # The draft stays disabled — a refused enable must not half-apply.
    assert store.workflows["wf-recipe"]["metadata"]["automation_state"] == "draft_disabled"


def test_install_refuses_a_recipe_whose_definition_does_not_validate(monkeypatch):
    """A recipe builder that emits a broken graph must not reach the store."""
    import latticeai.services.brain_automation as brain_automation

    monkeypatch.setattr(
        brain_automation, "build_brain_automation_workflow",
        lambda recipe_id, *, enabled=False: {
            "name": "Broken recipe", "nodes": _broken_nodes(), "metadata": {},
        },
    )
    store = FakeStore()
    client = _client(store)
    response = client.post("/workflows/api/automation/recipes/daily-memory-digest",
                           json={"enabled": False})
    assert response.status_code == 400
    assert response.json()["detail"]["validation_errors"]
    assert store.workflows == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_installing_a_recipe_creates_a_single_draft(enabled):
    store = FakeStore()
    client = _client(store)
    first = client.post("/workflows/api/automation/recipes/follow-up-radar",
                        json={"enabled": enabled})
    assert first.status_code == 200
    assert first.json()["already_installed"] is False
    assert first.json()["enabled"] is enabled
    second = client.post("/workflows/api/automation/recipes/follow-up-radar",
                         json={"enabled": enabled})
    assert second.json()["already_installed"] is True
    assert len(store.workflows) == 1
    assert client.audit[0]["event"] == "brain_automation_recipe_installed"  # type: ignore[attr-defined]
