"""Brain review queue (5.6.0) — checkpoint #2.

Covers create/list workspace scoping, approve/dismiss/snooze transitions,
the 409 on illegal transitions, snooze-expiry read semantics, run_now's
back-link (status untouched), ``/automation/reviews`` routes, ``source`` field
filtering, and opt-in ``review_sink`` enqueue from TriggerService/RunExecutor.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.review_queue import create_review_queue_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.review_queue import InvalidReviewTransition, ReviewQueueService


def _store(tmp_path):
    return WorkspaceOSStore(tmp_path / "data")


def _service(tmp_path, clock=None):
    store = _store(tmp_path)
    svc = ReviewQueueService(store=store, clock=clock or datetime.now)
    return store, svc


def _provenance():
    return {
        "trigger_id": "trig-1",
        "workflow_id": "wf-digest",
        "run_id": "workflow-run-seed",
        "source_detail": "interval:daily",
    }


def _api_client(tmp_path):
    store = _store(tmp_path)
    service = ReviewQueueService(store=store)
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=service,
        require_user=lambda _req: "u@x.com",
        gate_read=lambda _req: "personal",
        gate_write=lambda _req: "personal",
        run_review_item=lambda *_a, **_k: {"run": {"id": "run-api"}},
        append_audit_event=lambda *_a, **_k: None,
    ))
    return TestClient(app), service


def _workflow(*, review_queue: bool):
    return {
        "id": "wf-digest",
        "name": "Daily digest",
        "nodes": [
            {
                "type": "trigger",
                "config": {"trigger": "interval", "interval_seconds": 3600, "review_queue": review_queue},
            },
        ],
    }


# ── create / list scope ──────────────────────────────────────────────────
def test_create_defaults_to_pending_with_open_payload(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(
        title="Daily digest ready",
        summary="3 decisions, 1 open question",
        source="trigger",
        kind="digest",
        payload={"workflow_id": "wf-digest"},
        provenance=_provenance(),
        user_email="u@x.com",
    )
    assert item["status"] == "pending"
    assert item["effective_status"] == "pending"
    assert item["source"] == "trigger"
    for key in ("trigger_id", "workflow_id", "run_id", "source_detail"):
        assert item["provenance"][key] == _provenance()[key]
    assert item["id"].startswith("review-")


def test_create_requires_title(tmp_path):
    _, svc = _service(tmp_path)
    with pytest.raises(ValueError):
        svc.create(title="   ")


def test_create_rejects_unknown_source(tmp_path):
    _, svc = _service(tmp_path)
    with pytest.raises(ValueError):
        svc.create(title="x", source="manual")


def test_list_is_workspace_scoped(tmp_path):
    store, svc = _service(tmp_path)
    org = store.create_organization_workspace(name="Acme", owner_user_id="owner@acme.com")
    wid = org["workspace"]["id"] if "workspace" in org else org["id"]

    svc.create(title="personal item", workspace_id="personal")
    svc.create(title="org item", workspace_id=wid)

    personal = svc.list(workspace_id="personal")["items"]
    scoped = svc.list(workspace_id=wid)["items"]
    assert [i["title"] for i in personal] == ["personal item"]
    assert [i["title"] for i in scoped] == ["org item"]


def test_list_filters_by_effective_status(tmp_path):
    _, svc = _service(tmp_path)
    a = svc.create(title="a")
    svc.create(title="b")
    svc.dismiss(a["id"])
    pending = svc.list(status="pending")["items"]
    dismissed = svc.list(status="dismissed")["items"]
    assert {i["title"] for i in pending} == {"b"}
    assert {i["title"] for i in dismissed} == {"a"}


def test_list_filters_by_source(tmp_path):
    _, svc = _service(tmp_path)
    svc.create(title="run", source="workflow_run")
    svc.create(title="triggered", source="trigger")
    svc.create(title="digest", source="kg_change_digest")

    assert {i["title"] for i in svc.list(source="trigger")["items"]} == {"triggered"}
    assert {i["title"] for i in svc.list(source="kg_change_digest")["items"]} == {"digest"}


# ── transitions ──────────────────────────────────────────────────────────
def test_approve_and_dismiss_transitions(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x")
    approved = svc.approve(item["id"])
    assert approved["status"] == "approved"
    with pytest.raises(InvalidReviewTransition):
        svc.dismiss(item["id"])


def test_illegal_transition_raises(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x")
    svc.dismiss(item["id"])
    with pytest.raises(InvalidReviewTransition):
        svc.approve(item["id"])
    with pytest.raises(InvalidReviewTransition):
        svc.snooze(item["id"], until="2999-01-01T00:00:00")


def test_snooze_then_expiry_reads_as_pending(tmp_path):
    now = {"t": datetime(2026, 1, 1, 9, 0, 0)}
    _, svc = _service(tmp_path, clock=lambda: now["t"])
    item = svc.create(title="later")
    until = (now["t"] + timedelta(hours=2)).isoformat()
    snoozed = svc.snooze(item["id"], until=until)
    assert snoozed["status"] == "snoozed"
    assert snoozed["effective_status"] == "snoozed"

    now["t"] = datetime(2026, 1, 1, 12, 0, 0)
    refreshed = svc.get(item["id"])
    assert refreshed["status"] == "snoozed"
    assert refreshed["effective_status"] == "pending"
    assert svc.approve(item["id"])["status"] == "approved"


def test_approve_clears_snooze_timer(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x")
    svc.snooze(item["id"], until="2999-01-01T00:00:00")
    approved = svc.approve(item["id"])
    assert approved["snoozed_until"] is None


# ── run_now back-link (NOT a status change) ──────────────────────────────
def test_run_now_backlinks_without_changing_status(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(
        title="regen me",
        payload={"workflow_id": "wf-digest"},
        provenance=_provenance(),
    )
    calls = []

    def runner(stored):
        calls.append(stored["id"])
        return {"run": {"id": "workflow-run-fresh"}}

    updated = svc.run_now(item["id"], runner=runner)
    assert calls == [item["id"]]
    assert updated["status"] == "pending"
    assert updated["payload"]["last_run_id"] == "workflow-run-fresh"
    assert updated["provenance"]["run_id"] == "workflow-run-fresh"
    assert updated["updated_at"] >= item["updated_at"]


def test_run_now_accepts_plain_run_id(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x", payload={"workflow_id": "wf"})
    updated = svc.run_now(item["id"], runner=lambda _stored: "run-xyz")
    assert updated["payload"]["last_run_id"] == "run-xyz"
    assert updated["status"] == "pending"


def test_run_now_illegal_on_dismissed(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x", payload={"workflow_id": "wf"})
    svc.dismiss(item["id"])
    with pytest.raises(InvalidReviewTransition):
        svc.run_now(item["id"], runner=lambda _stored: "run-1")


def test_run_now_illegal_on_approved(tmp_path):
    _, svc = _service(tmp_path)
    item = svc.create(title="x", payload={"workflow_id": "wf"})
    svc.approve(item["id"])
    with pytest.raises(InvalidReviewTransition):
        svc.run_now(item["id"], runner=lambda _stored: "run-1")


# ── API routes ───────────────────────────────────────────────────────────
def test_api_uses_automation_reviews_route(tmp_path):
    client, _svc = _api_client(tmp_path)
    created = client.post(
        "/automation/reviews",
        json={"title": "API item", "source": "workflow_run", "payload": {"workflow_id": "wf-1"}},
    )
    assert created.status_code == 200
    item_id = created.json()["id"]

    listed = client.get("/automation/reviews", params={"source": "workflow_run"})
    assert listed.status_code == 200
    assert any(it["id"] == item_id for it in listed.json()["items"])

    dismissed = client.post(f"/automation/reviews/{item_id}/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"


# ── review_sink opt-in enqueue ───────────────────────────────────────────
def test_trigger_service_enqueues_when_opted_in(tmp_path):
    from latticeai.services.triggers import TriggerService

    store, review = _service(tmp_path)
    store.save_state({
        "workflows": [_workflow(review_queue=True)],
        "review_items": [],
    })
    fired = []

    svc = TriggerService(
        store=store,
        run_workflow=lambda wf_id, inputs: fired.append((wf_id, inputs)) or {"run": {"id": "run-trig-1"}},
        data_dir=tmp_path,
        review_sink=review,
    )
    svc._maybe_enqueue_review(
        "wf-digest",
        {"type": "interval", "interval_seconds": 3600},
        {"run": {"id": "run-trig-1"}},
    )
    items = review.list()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "trigger"
    assert items[0]["provenance"]["run_id"] == "run-trig-1"


def test_trigger_service_skips_enqueue_without_opt_in(tmp_path):
    from latticeai.services.triggers import TriggerService

    store, review = _service(tmp_path)
    store.save_state({"workflows": [_workflow(review_queue=False)], "review_items": []})
    svc = TriggerService(
        store=store,
        run_workflow=lambda *_a, **_k: {"run": {"id": "run-trig-1"}},
        data_dir=tmp_path,
        review_sink=review,
    )
    svc._maybe_enqueue_review(
        "wf-digest",
        {"type": "brain_event", "source_type": "note"},
        {"run": {"id": "run-trig-1"}},
    )
    assert review.list()["items"] == []


def test_trigger_brain_event_uses_kg_change_digest_source(tmp_path):
    from latticeai.services.triggers import TriggerService

    store, review = _service(tmp_path)
    store.save_state({"workflows": [_workflow(review_queue=True)], "review_items": []})
    svc = TriggerService(
        store=store,
        run_workflow=lambda *_a, **_k: {"run": {"id": "run-kg-1"}},
        data_dir=tmp_path,
        review_sink=review,
    )
    svc._maybe_enqueue_review(
        "wf-digest",
        {"type": "brain_event", "source_type": "note"},
        {"run": {"id": "run-kg-1"}},
    )
    assert review.list(source="kg_change_digest")["items"][0]["source"] == "kg_change_digest"


def test_run_executor_enqueues_when_opted_in(tmp_path):
    from latticeai.services.run_executor import RunExecutor

    store, review = _service(tmp_path)
    ex = RunExecutor(
        store=store,
        agent_runtime=object(),
        build_workflow_runners=lambda u, s: {},
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        review_sink=review,
    )
    ex._maybe_enqueue_review(
        _workflow(review_queue=True),
        run_result={"run": {"id": "run-ex-1"}},
        user_email="u@x.com",
        workspace_id="personal",
    )
    items = review.list(source="workflow_run")["items"]
    assert len(items) == 1
    assert items[0]["provenance"]["run_id"] == "run-ex-1"


def test_run_executor_skips_enqueue_without_opt_in(tmp_path):
    from latticeai.services.run_executor import RunExecutor

    store, review = _service(tmp_path)
    ex = RunExecutor(
        store=store,
        agent_runtime=object(),
        build_workflow_runners=lambda u, s: {},
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
        review_sink=review,
    )
    ex._maybe_enqueue_review(
        _workflow(review_queue=False),
        run_result={"run": {"id": "run-ex-1"}},
        user_email="u@x.com",
        workspace_id="personal",
    )
    assert review.list()["items"] == []


def test_trigger_service_constructs_without_review_sink(tmp_path):
    from latticeai.services.triggers import TriggerService

    svc = TriggerService(
        store=type("S", (), {"load_state": lambda self: {"workflows": []}})(),
        run_workflow=lambda wf_id, inputs: {"status": "ok"},
        data_dir=tmp_path,
    )
    assert svc._review_sink is None
    assert svc.describe()["armed"] == []


def test_run_executor_constructs_without_review_sink():
    from latticeai.services.run_executor import RunExecutor

    ex = RunExecutor(
        store=object(),
        agent_runtime=object(),
        build_workflow_runners=lambda u, s: {},
        workspace_graph=lambda: None,
        append_audit_event=lambda *a, **k: None,
    )
    assert ex.review_sink is None