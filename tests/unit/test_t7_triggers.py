"""T7d: workflows fire beyond 'manual' — interval + brain-event triggers.

Missed interval firings while down are recorded as skip events (never
silently dropped, never replayed in a catch-up storm); brain events fan out
to matching workflows; trigger-fired runs carry __trigger__ provenance.
"""

from lattice_brain.runtime.hooks import HooksRegistry, dispatch_tool
from latticeai.services.triggers import TriggerService


class _Store:
    def __init__(self, workflows):
        self._workflows = workflows

    def load_state(self):
        return {"workflows": self._workflows}


def _interval_wf(wf_id="wf-int", seconds=60):
    return {
        "id": wf_id, "name": "scheduled",
        "nodes": [{"id": "t", "type": "trigger",
                   "config": {"trigger": "interval", "interval_seconds": seconds}}],
    }


def _event_wf(wf_id="wf-evt", source_type=""):
    cfg = {"trigger": "brain_event"}
    if source_type:
        cfg["source_type"] = source_type
    return {"id": wf_id, "name": "on-ingest",
            "nodes": [{"id": "t", "type": "trigger", "config": cfg}]}


def _service(tmp_path, workflows, now):
    fired = []
    clock = {"now": now}
    svc = TriggerService(
        store=_Store(workflows),
        run_workflow=lambda wf_id, inputs: fired.append((wf_id, inputs)) or {"status": "ok"},
        data_dir=tmp_path,
        clock=lambda: clock["now"],
    )
    return svc, fired, clock


def _drain_threads():
    import threading
    for t in threading.enumerate():
        if t.name.startswith("trigger-wf"):
            t.join(timeout=2)


def test_interval_fires_when_due(tmp_path):
    svc, fired, clock = _service(tmp_path, [_interval_wf(seconds=60)], now=1000.0)
    assert svc.tick_intervals() == 0, "first sighting arms the schedule, no immediate fire"
    clock["now"] = 1059.0
    assert svc.tick_intervals() == 0, "not due yet"
    clock["now"] = 1061.0
    assert svc.tick_intervals() == 1
    _drain_threads()
    wf_id, inputs = fired[0]
    assert wf_id == "wf-int"
    assert inputs["__trigger__"]["type"] == "interval", "trigger provenance must ride the inputs"


def test_missed_firings_are_skipped_with_record(tmp_path):
    svc, fired, clock = _service(tmp_path, [_interval_wf(seconds=60)], now=1000.0)
    svc.tick_intervals()           # arm at t=1000
    clock["now"] = 1000.0 + 60 * 10  # server "down" for 10 intervals
    skipped = svc.reconcile_missed()
    assert skipped >= 9, "missed firings must be counted"
    assert svc.tick_intervals() == 0, "no catch-up storm after reconcile"
    _drain_threads()
    assert fired == []
    status = svc.describe()
    events = status["armed"][0]["recent_events"]
    assert any(e["type"] == "skipped" for e in events), "skips must be visible"


def test_brain_event_fires_matching_workflows(tmp_path):
    svc, fired, clock = _service(
        tmp_path,
        [_event_wf("wf-any"), _event_wf("wf-notes", source_type="note"),
         _event_wf("wf-uploads", source_type="upload"), _interval_wf("wf-int")],
        now=1000.0,
    )
    count = svc.on_brain_event("kg_ingest.note", {"source_type": "note", "node_id": "n1"})
    _drain_threads()
    assert count == 2, "unfiltered + matching-filter workflows fire; others do not"
    fired_ids = {wf_id for wf_id, _ in fired}
    assert fired_ids == {"wf-any", "wf-notes"}
    trig = dict(fired)["wf-notes"]["__trigger__"]
    assert trig["source_type"] == "note" and trig["node_id"] == "n1"


def test_hook_runner_ignores_non_ingest_events(tmp_path):
    svc, fired, _ = _service(tmp_path, [_event_wf("wf-any")], now=1000.0)
    runner = svc.hook_runner()

    class _Ctx:
        event = "agent.run"
        payload = {}

    out = runner(_Ctx())
    _drain_threads()
    assert fired == []
    assert out["status"] == "ok"

    class _Ingest:
        event = "kg_ingest.upload"
        payload = {"source_type": "upload", "node_id": "n9"}

    out = runner(_Ingest())
    _drain_threads()
    assert len(fired) == 1
    assert "fired 1" in out["output"]


def test_real_dispatch_tool_ingestion_event_fires_scoped_brain_workflow(tmp_path):
    matching = _event_wf("wf-org")
    matching["workspace_id"] = "org:acme"
    other = _event_wf("wf-other")
    other["workspace_id"] = "org:other"
    svc, fired, _ = _service(tmp_path, [matching, other], now=1000.0)

    registry = HooksRegistry(tmp_path / "hooks.json")
    hook = registry.register(
        name="brain-event-triggers",
        kind="post_tool",
        description="test ingestion trigger binding",
    )
    registry.register_hook(hook["id"], svc.hook_runner())

    dispatch_tool(
        registry,
        "kg_ingest.note",
        {"source_type": "note"},
        lambda: {"node_id": "node:note"},
        user_email="owner@example.com",
        workspace_id="org:acme",
        source="ingestion",
    )
    _drain_threads()

    assert [workflow_id for workflow_id, _inputs in fired] == ["wf-org"]
    trigger = fired[0][1]["__trigger__"]
    assert trigger["source_type"] == "note"
    assert trigger["user_email"] == "owner@example.com"
    assert trigger["workspace_id"] == "org:acme"


def test_hook_runner_preserves_scope_carried_by_legacy_payload(tmp_path):
    matching = _event_wf("wf-org")
    matching["workspace_id"] = "org:acme"
    other = _event_wf("wf-other")
    other["workspace_id"] = "org:other"
    svc, fired, _ = _service(tmp_path, [matching, other], now=1000.0)

    class _LegacyContext:
        event = "kg_ingest.note"
        payload = {
            "source_type": "note",
            "user_email": "owner@example.com",
            "workspace_id": "org:acme",
        }

    out = svc.hook_runner()(_LegacyContext())
    _drain_threads()

    assert out["status"] == "ok"
    assert [workflow_id for workflow_id, _inputs in fired] == ["wf-org"]
    trigger = fired[0][1]["__trigger__"]
    assert trigger["user_email"] == "owner@example.com"
    assert trigger["workspace_id"] == "org:acme"


def test_brain_event_never_crosses_workspace_or_workflow_owner(tmp_path):
    owned = _event_wf("wf-owned")
    owned.update({"workspace_id": "org:acme", "user_email": "owner@example.com"})
    other_user = _event_wf("wf-other-user")
    other_user.update({"workspace_id": "org:acme", "user_email": "other@example.com"})
    other_workspace = _event_wf("wf-other-workspace")
    other_workspace.update({"workspace_id": "org:other", "user_email": "owner@example.com"})
    svc, fired, _ = _service(tmp_path, [owned, other_user, other_workspace], now=1000.0)

    count = svc.on_brain_event("kg_ingest.note", {
        "source_type": "note",
        "workspace_id": "org:acme",
        "user_email": "owner@example.com",
    })
    _drain_threads()

    assert count == 1
    assert [workflow_id for workflow_id, _inputs in fired] == ["wf-owned"]


def test_describe_reports_armed_state_honestly(tmp_path):
    svc, _, _ = _service(tmp_path, [_interval_wf(), _event_wf()], now=1000.0)
    status = svc.describe()
    assert status["running"] is False, "scheduler not started must say so"
    kinds = {a["kind"] for a in status["armed"]}
    assert kinds == {"interval", "brain_event"}


def test_disabled_trigger_recipe_drafts_are_not_armed(tmp_path):
    disabled = _interval_wf("wf-draft")
    disabled["nodes"][0]["config"]["enabled"] = False
    svc, fired, clock = _service(tmp_path, [disabled, _interval_wf("wf-live")], now=1000.0)

    status = svc.describe()
    assert [item["workflow_id"] for item in status["armed"]] == ["wf-live"]

    svc.tick_intervals()
    clock["now"] = 1061.0
    assert svc.tick_intervals() == 1
    _drain_threads()
    assert [wf_id for wf_id, _ in fired] == ["wf-live"]
