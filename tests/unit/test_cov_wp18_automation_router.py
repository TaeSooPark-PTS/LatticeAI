"""wp18 — the question-driven automation router driven through its factory.

Covers the Act-feed normalizers (``source`` discriminator, truncation meta),
the run-now wait-budget parser, every read endpoint, the consent-first install
contract (404 / idempotent reuse / validation refusal / fresh install), and the
live run-now polling loop including its "run row vanished" escape.

The service is the real :class:`AutomationIntelligenceService` over a fake
conversation store, so suggestion ids are the real deterministic ones.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.automation_intelligence import create_automation_intelligence_router
from latticeai.services.automation_intelligence import AutomationIntelligenceService

USER = "user@example.com"
SCOPE = "personal"


class FakeConversations:
    def __init__(self, items: Optional[List[Dict[str, Any]]] = None) -> None:
        self.items = items or []

    def history(self, **_kwargs):
        return self.items


def _recurring_history() -> List[Dict[str, Any]]:
    return [
        {"role": "user", "content": "오늘 기억 정리해줘", "timestamp": "2026-07-18T08:00:00"},
        {"role": "user", "content": "오늘 기억 정리해줘 부탁", "timestamp": "2026-07-19T08:00:00"},
    ]


class FakeStore:
    """Workflow + run persistence with an opt-in combined-run listing."""

    def __init__(self, workflows: Optional[List[Dict[str, Any]]] = None, *,
                 combined: Any = None, run_rows: Optional[List[Any]] = None) -> None:
        self.workflows: Dict[str, Dict[str, Any]] = {
            wf["id"]: wf for wf in (workflows or [])
        }
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.combined = combined
        self.run_rows = run_rows
        self.metadata_updates: List[Dict[str, Any]] = []
        self.update_fails = False
        self._seq = 0

    def list_workflows(self, workspace_id: Optional[str] = None, **_kwargs):
        return {"workflows": list(self.workflows.values())}

    def create_workflow(self, *, name, steps, nodes, metadata, user_email, graph,
                        workspace_id):
        self._seq += 1
        workflow = {
            "id": f"wf-{self._seq}", "name": name, "steps": steps, "nodes": nodes,
            "metadata": metadata, "user_email": user_email,
            "workspace_id": workspace_id, "graph": graph,
        }
        self.workflows[workflow["id"]] = workflow
        return workflow

    def get_workflow(self, workflow_id, workspace_id: Optional[str] = None):
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def update_workflow_definition(self, workflow_id, *, name=None, nodes=None,
                                   metadata=None, workspace_id=None):
        if self.update_fails:
            raise RuntimeError("workflow metadata write failed")
        workflow = self.get_workflow(workflow_id, workspace_id=workspace_id)
        if metadata:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        self.metadata_updates.append({"workflow_id": workflow_id, "metadata": metadata})
        return workflow

    def get_workflow_run(self, run_id, workspace_id: Optional[str] = None):
        if run_id not in self.runs:
            raise FileNotFoundError(run_id)
        return self.runs[run_id]

    def list_workflow_runs(self, workflow_id=None, limit=50, workspace_id=None):
        if self.run_rows is not None:
            return {"runs": list(self.run_rows)[:limit]}
        runs = [
            run for run in self.runs.values()
            if workflow_id is None or run.get("workflow_id") == workflow_id
        ]
        return {"runs": runs[:limit]}


class CombinedStore(FakeStore):
    """Store that implements the combined-run listing helper."""

    def list_combined_runs(self, limit=20, workspace_id=None):
        self.combined_calls = getattr(self, "combined_calls", [])
        self.combined_calls.append({"limit": limit, "workspace_id": workspace_id})
        return self.combined


class ScriptedExecutor:
    """Queues a run, then walks the store row through scripted statuses."""

    def __init__(self, store: FakeStore, *, run_id: str = "run-1",
                 statuses: Optional[List[str]] = None, persist: bool = True) -> None:
        self.store = store
        self.run_id = run_id
        self.statuses = list(statuses or ["ok"])
        self.persist = persist
        self.started: List[Dict[str, Any]] = []

    async def start_workflow(self, workflow, *, workflow_id, user_email, scope,
                             inputs=None):
        self.started.append({"workflow_id": workflow_id, "inputs": inputs,
                             "user_email": user_email, "scope": scope})
        run = {"id": self.run_id, "workflow_id": workflow_id, "status": "queued",
               "timeline": [], "outputs": {}, "completed_at": "2026-07-21T10:00:01"}
        if self.persist:
            self.store.runs[self.run_id] = run
            self._arm_polling()
        return {"run": run, "accepted": True}

    def _arm_polling(self) -> None:
        statuses = self.statuses
        store = self.store
        run_id = self.run_id
        original = store.get_workflow_run

        def _polled(rid, workspace_id=None):
            run = original(rid, workspace_id=workspace_id)
            if rid == run_id and statuses:
                run["status"] = statuses.pop(0)
            return run

        store.get_workflow_run = _polled  # type: ignore[method-assign]


class FakeReviewQueue:
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        item = {"id": f"rev-{len(self.created) + 1}", **kwargs}
        self.created.append(item)
        return item


def _client(store, *, service=None, run_executor=None, review_queue=None,
            conversations=None):
    app = FastAPI()
    audit: List[Dict[str, Any]] = []
    live_service = service or AutomationIntelligenceService(
        conversation_store=conversations, store=store,
    )
    app.include_router(create_automation_intelligence_router(
        service=live_service,
        store=store,
        require_user=lambda _request: USER,
        gate_read=lambda _request: SCOPE,
        gate_write=lambda _request: SCOPE,
        append_audit_event=lambda event, **kw: audit.append({"event": event, **kw}),
        workspace_graph=lambda: None,
        run_executor=run_executor,
        review_queue=review_queue,
    ))
    client = TestClient(app)
    client.audit = audit  # type: ignore[attr-defined]
    client.service = live_service  # type: ignore[attr-defined]
    return client


# ── Act-feed row normalization ──────────────────────────────────────────

def test_non_dict_rows_become_neutral_agent_rows():
    store = CombinedStore(combined={"runs": ["not-a-row", 17]})
    client = _client(store)
    rows = client.get("/api/activity/runs").json()["runs"]
    assert rows == [
        {"source": "agent", "id": None, "title": "", "status": ""},
        {"source": "agent", "id": None, "title": "", "status": ""},
    ]


def test_rows_without_either_id_keep_a_legacy_source_or_fall_back_to_agent():
    store = CombinedStore(combined={"runs": [
        {"id": "r1", "source": "legacy-batch"},
        {"id": "r2", "source": ""},
        {"id": "r3", "workflow_id": "wf-1"},
        {"id": "r4", "agent_id": "ag-1"},
        {"id": "r5", "source": "WORKFLOW"},
    ]})
    client = _client(store)
    rows = client.get("/api/activity/runs").json()["runs"]
    assert [row["source"] for row in rows] == [
        "legacy-batch", "agent", "workflow", "agent", "workflow",
    ]


def test_combined_payload_that_is_not_a_mapping_degrades_to_an_empty_feed():
    store = CombinedStore(combined=["unexpected"])
    client = _client(store)
    assert client.get("/api/activity/runs").json() == {
        "runs": [], "total": 0, "truncated": False,
    }


def test_unparsable_total_falls_back_to_the_visible_row_count():
    store = CombinedStore(combined={
        "runs": [{"id": "r1", "agent_id": "a"}, {"id": "r2", "agent_id": "b"}],
        "total": "not-a-number",
    })
    client = _client(store)
    body = client.get("/automations/runs/combined", params={"limit": 5}).json()
    assert body["total"] == 2
    assert body["truncated"] is False
    assert store.combined_calls == [{"limit": 5, "workspace_id": SCOPE}]


def test_declared_total_larger_than_the_page_marks_the_feed_truncated():
    store = CombinedStore(combined={"runs": [{"id": "r1", "agent_id": "a"}],
                                    "total": 40})
    client = _client(store)
    body = client.get("/api/activity/runs").json()
    assert body["total"] == 40
    assert body["truncated"] is True


# ── read endpoints ──────────────────────────────────────────────────────

def test_patterns_suggestions_and_overview_report_the_same_evidence():
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))

    patterns = client.get("/api/automation/patterns").json()
    assert patterns["questions_scanned"] == 2
    assert patterns["patterns"][0]["count"] == 2

    suggestions = client.get("/api/automation/suggestions").json()
    assert suggestions["suggestions"][0]["kind"] == "recurring_question"
    assert suggestions["consent"]["requires_user_enable"] is True

    overview = client.get("/api/automation/overview").json()
    assert overview["installed"] == []
    assert overview["questions_scanned"] == 2
    assert [s["id"] for s in overview["suggestions"]] == [
        s["id"] for s in suggestions["suggestions"]
    ]


# ── install ─────────────────────────────────────────────────────────────

def test_installing_an_unknown_suggestion_is_404():
    client = _client(FakeStore(), conversations=FakeConversations())
    response = client.post("/api/automation/install",
                           json={"suggestion_id": "sug-q-nope"})
    assert response.status_code == 404
    assert "Automation suggestion not found" in response.json()["detail"]


def _suggestion_id(client) -> str:
    return client.get("/api/automation/suggestions").json()["suggestions"][0]["id"]


def test_install_creates_a_disabled_draft_and_audits_provenance():
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    suggestion_id = _suggestion_id(client)

    response = client.post("/api/automation/install",
                           json={"suggestion_id": suggestion_id})
    assert response.status_code == 200
    body = response.json()
    assert body["already_installed"] is False
    assert body["enabled"] is False
    workflow = body["workflow"]
    assert workflow["metadata"]["created_from"] == "automation_suggestion"
    assert workflow["metadata"]["suggestion_id"] == suggestion_id
    assert workflow["metadata"]["automation_state"] == "draft_disabled"
    assert workflow["workspace_id"] == SCOPE
    assert [step["node"] for step in workflow["steps"]] == ["trigger", "draft", "output"]
    assert client.audit[-1]["event"] == "automation_suggestion_installed"  # type: ignore[attr-defined]
    assert client.audit[-1]["suggestion_kind"] == "recurring_question"  # type: ignore[attr-defined]


def test_installing_twice_reuses_the_existing_suggestion_workflow():
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    suggestion_id = _suggestion_id(client)
    first = client.post("/api/automation/install",
                        json={"suggestion_id": suggestion_id}).json()
    second = client.post("/api/automation/install",
                         json={"suggestion_id": suggestion_id, "enabled": True}).json()
    assert second["already_installed"] is True
    assert second["workflow"]["id"] == first["workflow"]["id"]
    assert second["enabled"] is False, "reuse reports the stored state, not the request"
    assert len(store.workflows) == 1


def test_install_reuses_an_already_installed_starter_recipe():
    """Suggestion install is idempotent on recipe provenance too."""
    store = FakeStore([{
        "id": "wf-recipe", "name": "Daily Memory Digest",
        "metadata": {
            "created_from": "brain_automation_recipe",
            "recipe_id": "daily-memory-digest",
            "automation_state": "enabled",
        },
    }])
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    suggestion = client.get("/api/automation/suggestions").json()["suggestions"][0]
    assert suggestion["recipe_id"] == "daily-memory-digest"

    body = client.post("/api/automation/install",
                       json={"suggestion_id": suggestion["id"]}).json()
    assert body["already_installed"] is True
    assert body["enabled"] is True
    assert body["workflow"]["id"] == "wf-recipe"
    assert len(store.workflows) == 1


def test_install_refuses_a_definition_that_does_not_validate(monkeypatch):
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    suggestion_id = _suggestion_id(client)
    monkeypatch.setattr(
        client.service, "build_suggestion_workflow",  # type: ignore[attr-defined]
        lambda suggestion, *, enabled=False: {
            "name": "Broken",
            "nodes": [{"id": "trigger", "type": "trigger", "config": {},
                       "next": "ghost"}],
            "metadata": {},
        },
    )
    response = client.post("/api/automation/install",
                           json={"suggestion_id": suggestion_id})
    assert response.status_code == 400
    assert response.json()["detail"]["validation_errors"]
    assert store.workflows == {}


# ── run now ─────────────────────────────────────────────────────────────

def _installed_automation(client, store) -> str:
    suggestion_id = _suggestion_id(client)
    body = client.post("/api/automation/install",
                       json={"suggestion_id": suggestion_id}).json()
    return body["workflow"]["id"]


def test_dry_run_stamp_failure_never_undoes_the_report():
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    workflow_id = _installed_automation(client, store)
    store.update_fails = True

    response = client.post("/api/automation/run-now",
                           json={"workflow_id": workflow_id})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["status"] == "ok"
    assert body["last_execution"]["mode"] == "dry_run"
    # The write failed, so nothing was persisted — but the report survived.
    assert "last_execution" not in store.workflows[workflow_id]["metadata"]


def test_live_run_polls_until_the_run_reaches_a_terminal_status(monkeypatch):
    monkeypatch.setenv("LATTICEAI_AUTOMATION_RUN_NOW_WAIT", "not-a-number")
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    workflow_id = _installed_automation(client, store)
    executor = ScriptedExecutor(store, statuses=["running", "ok"])
    review_queue = FakeReviewQueue()
    client = _client(store, run_executor=executor, review_queue=review_queue,
                     conversations=FakeConversations(_recurring_history()))

    response = client.post("/api/automation/run-now",
                           json={"workflow_id": workflow_id, "dry_run": False})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False
    assert body["status"] == "ok"
    assert body["run_id"] == "run-1"
    assert executor.started[0]["inputs"] == {"trigger": "run_now"}
    assert store.workflows[workflow_id]["metadata"]["last_execution"]["mode"] == "live"
    assert review_queue.created == []


def test_live_run_stops_polling_when_the_run_row_disappears():
    store = FakeStore()
    client = _client(store, conversations=FakeConversations(_recurring_history()))
    workflow_id = _installed_automation(client, store)
    executor = ScriptedExecutor(store, run_id="run-ghost", persist=False)
    client = _client(store, run_executor=executor,
                     conversations=FakeConversations(_recurring_history()))

    response = client.post("/api/automation/run-now",
                           json={"workflow_id": workflow_id, "dry_run": False})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["last_execution"]["summary"].startswith("started")
    assert body["run_id"] == "run-ghost"
