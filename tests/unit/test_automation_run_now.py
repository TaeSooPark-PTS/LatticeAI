"""Automation dry-run + execution log surfacing tests (backlog #6).

Covers the deterministic dry-run report, the ``/api/automation/run-now``
endpoint (dry-run first, one real run, failed run → review queue), the
``last_execution`` stamp merged into the automation overview, and the daily
briefing's last-execution slot.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.automation_intelligence import create_automation_intelligence_router
from latticeai.services.automation_execution import (
    build_last_execution,
    dry_run_report,
    is_automation_workflow,
    last_execution_view,
    summarize_workflow_run,
)
from latticeai.services.automation_intelligence import AutomationIntelligenceService
from latticeai.services.brain_automation import build_brain_automation_workflow
from latticeai.services.command_center import CommandCenterService


def _automation_workflow(workflow_id="wf-auto-1", *, enabled=False):
    workflow = build_brain_automation_workflow("daily-memory-digest", enabled=enabled)
    workflow["id"] = workflow_id
    return workflow


class FakeStore:
    """Just enough of the workspace store for run-now + overview reads."""

    def __init__(self, workflows=None):
        self.workflows = {wf["id"]: wf for wf in (workflows or [])}
        self.runs = {}
        self.metadata_updates = []

    def get_workflow(self, workflow_id, workspace_id=None):
        if workflow_id not in self.workflows:
            raise FileNotFoundError(workflow_id)
        return self.workflows[workflow_id]

    def list_workflows(self, query="", workspace_id=None):
        return {"workflows": list(self.workflows.values())}

    def update_workflow_definition(self, workflow_id, *, name=None, nodes=None,
                                   metadata=None, workspace_id=None):
        workflow = self.get_workflow(workflow_id)
        if metadata:
            workflow["metadata"] = {**(workflow.get("metadata") or {}), **metadata}
        self.metadata_updates.append({"workflow_id": workflow_id, "metadata": metadata})
        return workflow

    def get_workflow_run(self, run_id, workspace_id=None):
        if run_id not in self.runs:
            raise FileNotFoundError(run_id)
        return self.runs[run_id]

    def list_workflow_runs(self, workflow_id=None, limit=50, workspace_id=None):
        runs = [r for r in self.runs.values()
                if workflow_id is None or r.get("workflow_id") == workflow_id]
        return {"runs": list(reversed(runs))[:limit]}


class FakeRunExecutor:
    """Synchronously 'executes' a run into the fake store."""

    def __init__(self, store, *, final_status="ok", timeline=None):
        self.store = store
        self.final_status = final_status
        self.timeline = timeline or []
        self.calls = []

    async def start_workflow(self, workflow, *, workflow_id, user_email, scope, inputs=None):
        self.calls.append({"workflow_id": workflow_id, "inputs": inputs})
        run = {
            "id": f"run-{len(self.calls)}",
            "workflow_id": workflow_id,
            "status": self.final_status,
            "timeline": list(self.timeline),
            "outputs": {},
            "created_at": "2026-07-21T10:00:00",
            "completed_at": "2026-07-21T10:00:01",
        }
        self.store.runs[run["id"]] = run
        return {"run": run, "accepted": True}


class FakeReviewQueue:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        item = {"id": f"rev-{len(self.created) + 1}", **kwargs}
        self.created.append(item)
        return item


def _client(store, *, run_executor=None, review_queue=None):
    service = AutomationIntelligenceService(store=store)
    app = FastAPI()
    audit_events = []
    app.include_router(create_automation_intelligence_router(
        service=service,
        store=store,
        require_user=lambda request: "user@example.com",
        gate_read=lambda request: None,
        gate_write=lambda request: None,
        append_audit_event=lambda event, **kw: audit_events.append({"event": event, **kw}),
        workspace_graph=lambda: None,
        run_executor=run_executor,
        review_queue=review_queue,
    ))
    client = TestClient(app)
    client.audit_events = audit_events  # type: ignore[attr-defined]
    return client


# ── dry-run report (pure policy) ────────────────────────────────────────

def test_dry_run_report_describes_steps_without_executing():
    report = dry_run_report(_automation_workflow())
    assert report["mode"] == "dry_run"
    assert report["status"] == "ok"
    assert report["side_effects"] is False
    types = [step["type"] for step in report["steps"]]
    assert types == ["trigger", "agent", "output"]
    agent_step = report["steps"][1]
    assert "draft" in agent_step["would"].lower()
    assert "no external actions" in report["summary"]


def test_dry_run_report_flags_invalid_workflow():
    report = dry_run_report({"name": "broken", "nodes": [
        {"id": "trigger", "type": "trigger", "config": {}, "next": "missing"},
    ]})
    assert report["status"] == "invalid"
    assert report["validation_errors"]


def test_is_automation_workflow_only_matches_automation_provenance():
    assert is_automation_workflow(_automation_workflow()) is True
    assert is_automation_workflow({"metadata": {"created_from": "desktop-act-ui"}}) is False
    assert is_automation_workflow({}) is False


def test_summarize_workflow_run_surfaces_failure_detail():
    summary = summarize_workflow_run({
        "status": "failed",
        "timeline": [{"status": "error", "reason": "tool exploded"}],
    })
    assert "failed" in summary
    assert "tool exploded" in summary
    assert "ok" in summarize_workflow_run({"status": "ok", "timeline": [{}, {}]})


def test_last_execution_view_prefers_newest_record():
    workflow = _automation_workflow()
    workflow["metadata"]["last_execution"] = build_last_execution(
        mode="dry_run", status="ok", summary="stamped",
    )
    # Stamp is newer than the store's run (which has an old timestamp).
    store = FakeStore([workflow])
    store.runs["run-old"] = {
        "id": "run-old", "workflow_id": workflow["id"], "status": "ok",
        "timeline": [], "created_at": "2020-01-01T00:00:00",
        "completed_at": "2020-01-01T00:00:01",
    }
    view = last_execution_view(workflow, store=store)
    assert view["summary"] == "stamped"

    # A newer persisted run wins over an old stamp.
    workflow["metadata"]["last_execution"]["finished_at"] = "2020-01-01T00:00:00"
    store.runs["run-old"]["completed_at"] = "2030-01-01T00:00:00"
    view = last_execution_view(workflow, store=store)
    assert view["mode"] == "live"
    assert view["run_id"] == "run-old"


# ── run-now endpoint ────────────────────────────────────────────────────

def test_run_now_dry_run_reports_and_stamps_without_executor():
    store = FakeStore([_automation_workflow()])
    executor = FakeRunExecutor(store)
    client = _client(store, run_executor=executor)
    r = client.post("/api/automation/run-now", json={"workflow_id": "wf-auto-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["status"] == "ok"
    assert body["report"]["side_effects"] is False
    # Dry run never touches the executor; the stamp is persisted.
    assert executor.calls == []
    stamped = store.get_workflow("wf-auto-1")["metadata"]["last_execution"]
    assert stamped["mode"] == "dry_run"
    assert stamped["status"] == "ok"


def test_run_now_live_executes_once_and_stamps_result():
    store = FakeStore([_automation_workflow()])
    executor = FakeRunExecutor(store, final_status="ok", timeline=[{"status": "ok"}])
    review_queue = FakeReviewQueue()
    client = _client(store, run_executor=executor, review_queue=review_queue)
    r = client.post(
        "/api/automation/run-now",
        json={"workflow_id": "wf-auto-1", "dry_run": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["status"] == "ok"
    assert body["run_id"] == "run-1"
    assert len(executor.calls) == 1
    assert executor.calls[0]["inputs"] == {"trigger": "run_now"}
    stamped = store.get_workflow("wf-auto-1")["metadata"]["last_execution"]
    assert stamped["mode"] == "live"
    assert stamped["run_id"] == "run-1"
    # A successful run does not enqueue a failure review item.
    assert review_queue.created == []


def test_run_now_failed_execution_enqueues_review_item():
    store = FakeStore([_automation_workflow()])
    executor = FakeRunExecutor(
        store, final_status="failed",
        timeline=[{"status": "error", "reason": "draft agent crashed"}],
    )
    review_queue = FakeReviewQueue()
    client = _client(store, run_executor=executor, review_queue=review_queue)
    r = client.post(
        "/api/automation/run-now",
        json={"workflow_id": "wf-auto-1", "dry_run": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert len(review_queue.created) == 1
    item = review_queue.created[0]
    assert item["source"] == "workflow_run"
    assert item["kind"] == "automation_failure"
    assert item["payload"]["workflow_id"] == "wf-auto-1"
    assert item["payload"]["run_id"] == "run-1"
    assert body["review_item_id"] == item["id"]
    stamped = store.get_workflow("wf-auto-1")["metadata"]["last_execution"]
    assert stamped["status"] == "failed"
    assert "draft agent crashed" in stamped["summary"]


def test_run_now_unknown_or_non_automation_workflow_is_404():
    manual = {"id": "wf-manual", "name": "Manual", "nodes": [],
              "metadata": {"created_from": "desktop-act-ui"}}
    store = FakeStore([manual])
    client = _client(store, run_executor=FakeRunExecutor(store))
    assert client.post(
        "/api/automation/run-now", json={"workflow_id": "nope"}
    ).status_code == 404
    assert client.post(
        "/api/automation/run-now", json={"workflow_id": "wf-manual"}
    ).status_code == 404


def test_run_now_live_without_executor_is_503():
    store = FakeStore([_automation_workflow()])
    client = _client(store, run_executor=None)
    r = client.post(
        "/api/automation/run-now",
        json={"workflow_id": "wf-auto-1", "dry_run": False},
    )
    assert r.status_code == 503


# ── surfacing: overview + briefing ──────────────────────────────────────

def test_overview_installed_carries_last_execution():
    workflow = _automation_workflow()
    workflow["metadata"]["last_execution"] = build_last_execution(
        mode="live", status="ok", summary="ok — 3 step(s) recorded", run_id="run-9",
    )
    store = FakeStore([workflow])
    service = AutomationIntelligenceService(store=store)
    overview = service.overview()
    assert len(overview["installed"]) == 1
    last = overview["installed"][0]["last_execution"]
    assert last["status"] == "ok"
    assert last["run_id"] == "run-9"


def test_overview_last_execution_derives_from_persisted_runs():
    workflow = _automation_workflow()
    store = FakeStore([workflow])
    store.runs["run-bg"] = {
        "id": "run-bg", "workflow_id": workflow["id"], "status": "ok",
        "timeline": [{}, {}], "created_at": "2026-07-20T09:00:00",
        "completed_at": "2026-07-20T09:00:05",
    }
    overview = AutomationIntelligenceService(store=store).overview()
    last = overview["installed"][0]["last_execution"]
    assert last["mode"] == "live"
    assert last["run_id"] == "run-bg"


def test_briefing_automation_section_surfaces_latest_execution():
    newer = _automation_workflow("wf-newer")
    newer["metadata"]["last_execution"] = {
        "mode": "live", "status": "failed", "summary": "failed after 2 step(s)",
        "run_id": "run-2", "finished_at": "2026-07-21T12:00:00",
    }
    older = _automation_workflow("wf-older")
    older["metadata"]["last_execution"] = {
        "mode": "dry_run", "status": "ok", "summary": "dry",
        "run_id": None, "finished_at": "2026-07-20T12:00:00",
    }
    store = FakeStore([older, newer])
    briefing = CommandCenterService(store=store, enable_graph=False).briefing()
    section = briefing["sections"]["automations"]
    assert section["total"] == 2
    assert section["last_execution"]["workflow_id"] == "wf-newer"
    assert section["last_execution"]["status"] == "failed"


def test_briefing_automation_section_omits_slot_without_executions():
    store = FakeStore([_automation_workflow()])
    briefing = CommandCenterService(store=store, enable_graph=False).briefing()
    assert "last_execution" not in briefing["sections"]["automations"]
