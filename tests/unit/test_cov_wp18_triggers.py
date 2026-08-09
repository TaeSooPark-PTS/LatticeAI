"""wp18 — trigger service degradation, dedup guards, and lifecycle.

Complements ``tests/unit/test_triggers.py`` (happy paths) with the seams that
keep the scheduler honest when things go wrong: an unusable timezone, a
corrupt state file, a store that raises, the interval/brain-event dedup
guards, launch failures counted into ``describe()``, review-sink failures that
must not be mistaken for run failures, and start/stop idempotency.

The scheduler thread is driven by an ``Event`` the fake tick sets, never by a
sleep, so the test is deterministic.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from latticeai.services.triggers import TriggerService


class RecordingStore:
    """``load_state`` that can be scripted to fail on specific call numbers."""

    def __init__(self, workflows: List[Dict[str, Any]],
                 fail_on: Optional[set] = None) -> None:
        self.workflows = list(workflows)
        self.fail_on = set(fail_on or ())
        self.calls = 0

    def load_state(self) -> Dict[str, Any]:
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError(f"state read failed on call {self.calls}")
        return {"workflows": list(self.workflows)}


class RecordingSink:
    def __init__(self, *, explode: bool = False) -> None:
        self.created: List[Dict[str, Any]] = []
        self.explode = explode

    def create(self, **kwargs):
        self.created.append(kwargs)
        if self.explode:
            raise RuntimeError("review queue write failed")
        return {"id": f"rev-{len(self.created)}"}


def _interval_wf(wf_id: str = "wf-int", seconds: int = 60) -> Dict[str, Any]:
    return {
        "id": wf_id, "name": "scheduled",
        "nodes": [{"id": "t", "type": "trigger",
                   "config": {"trigger": "interval", "interval_seconds": seconds}}],
    }


def _event_wf(wf_id: str = "wf-evt", *, review_queue: bool = False) -> Dict[str, Any]:
    config: Dict[str, Any] = {"trigger": "brain_event"}
    if review_queue:
        config["review_queue"] = True
    return {"id": wf_id, "name": "on-ingest",
            "nodes": [{"id": "t", "type": "trigger", "config": config}]}


def _service(tmp_path, workflows, *, now=1000.0, store=None, run_workflow=None,
             review_sink=None, tick_seconds=5.0, tz_name=None):
    fired: List[Any] = []
    clock = {"now": now}

    def _default_run(workflow_id, inputs):
        fired.append((workflow_id, inputs))
        return {"status": "ok", "run_id": "run-1"}

    service = TriggerService(
        store=store if store is not None else RecordingStore(workflows),
        run_workflow=run_workflow or _default_run,
        data_dir=tmp_path,
        clock=lambda: clock["now"],
        tick_seconds=tick_seconds,
        review_sink=review_sink,
        tz_name=tz_name,
    )
    return service, fired, clock


def _drain_fire_threads() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith("trigger-wf"):
            thread.join(timeout=5)


def _state(tmp_path) -> Dict[str, Any]:
    return json.loads((tmp_path / "triggers_state.json").read_text(encoding="utf-8"))


# ── construction / durable state degradation ────────────────────────────

def test_unusable_timezone_falls_back_to_utc(tmp_path):
    service, _, _ = _service(tmp_path, [_interval_wf()], tz_name="Not/A_Zone")
    # The configured name is reported honestly; the resolved zone is UTC.
    assert service.describe()["tz"] == "Not/A_Zone"
    assert str(service._tz) == "UTC"


def test_corrupt_state_file_is_ignored_instead_of_crashing(tmp_path):
    (tmp_path / "triggers_state.json").write_text("{not json", encoding="utf-8")
    service, _, _ = _service(tmp_path, [_interval_wf()])
    armed = service.describe()["armed"]
    assert [item["workflow_id"] for item in armed] == ["wf-int"]
    assert armed[0]["last_fired_at"] is None
    assert armed[0]["consecutive_failures"] == 0


def test_unreadable_store_arms_nothing(tmp_path):
    store = RecordingStore([_interval_wf()], fail_on={1, 2, 3, 4, 5})
    service, _, _ = _service(tmp_path, [], store=store)
    status = service.describe()
    assert status["armed"] == []
    assert status["running"] is False


# ── interval scheduling ─────────────────────────────────────────────────

def test_non_interval_triggers_are_skipped_by_the_interval_passes(tmp_path):
    service, fired, clock = _service(tmp_path, [_event_wf("wf-evt"),
                                                _interval_wf("wf-int")])
    assert service.reconcile_missed() == 0
    assert service.tick_intervals() == 0, "first sighting only arms the schedule"
    clock["now"] = 1100.0
    assert service.tick_intervals() == 1
    _drain_fire_threads()
    assert [wf_id for wf_id, _ in fired] == ["wf-int"]
    # Only the interval workflow ever got scheduler state.
    assert set(_state(tmp_path)) == {"wf-int"}


def test_recent_attempt_blocks_a_due_interval_from_double_firing(tmp_path):
    (tmp_path / "triggers_state.json").write_text(
        json.dumps({"wf-int": {"last_fired_at": 900.0, "last_attempt_at": 995.0}}),
        encoding="utf-8",
    )
    service, fired, _ = _service(tmp_path, [_interval_wf(seconds=60)], now=1000.0)
    assert service.tick_intervals() == 0, "due, but attempted 5s ago"
    _drain_fire_threads()
    assert fired == []
    assert _state(tmp_path)["wf-int"]["last_fired_at"] == 900.0


# ── brain events ────────────────────────────────────────────────────────

def test_brain_event_burst_fires_once_per_dedup_window(tmp_path):
    service, fired, _ = _service(tmp_path, [_event_wf("wf-evt")])
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 0
    _drain_fire_threads()
    assert len(fired) == 1


def test_hook_runner_ignores_failed_ingestion_and_normalizes_tool_events(tmp_path):
    service, fired, _ = _service(tmp_path, [_event_wf("wf-evt")])
    runner = service.hook_runner()

    class _Failed:
        event = "kg_ingest.note"
        payload = {"source_type": "note", "status": "error"}

    out = runner(_Failed())
    _drain_fire_threads()
    assert out == {"status": "ok", "output": "ignored failed ingestion event"}
    assert fired == []

    class _DispatchEvent:
        event = "tool.kg_ingest.upload"
        payload = {"node_id": "n1"}

    out = runner(_DispatchEvent())
    _drain_fire_threads()
    assert out["output"] == "fired 1 workflow trigger(s)"
    assert fired[0][1]["__trigger__"]["source_type"] == "upload"


# ── firing outcomes ─────────────────────────────────────────────────────

def test_fire_without_a_readable_definition_still_runs_with_empty_provenance(tmp_path):
    scoped = _event_wf("wf-evt")
    scoped["workspace_id"] = "personal"

    # Baseline: _fire reads the definition and back-fills the workflow's scope.
    healthy, healthy_fired, _ = _service(tmp_path / "ok", [scoped])
    assert healthy.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    _drain_fire_threads()
    assert healthy_fired[0][1]["__trigger__"]["workspace_id"] == "personal"

    # Call 1 is the definition scan; call 2 is _fire's own workflow lookup.
    store = RecordingStore([scoped], fail_on={2})
    service, fired, _ = _service(tmp_path / "degraded", [], store=store)
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    _drain_fire_threads()
    trigger = fired[0][1]["__trigger__"]
    assert trigger["type"] == "brain_event"
    assert trigger["workspace_id"] is None, "unreadable definition carries no scope"
    assert trigger["user_email"] is None


def test_launch_failures_are_counted_into_describe(tmp_path):
    def _boom(workflow_id, inputs):
        raise RuntimeError("runner exploded")

    service, _, _ = _service(tmp_path, [_event_wf("wf-evt")], run_workflow=_boom)
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    _drain_fire_threads()

    armed = service.describe()["armed"][0]
    assert armed["consecutive_failures"] == 1
    assert armed["status"] == "armed", "one failure is not yet degraded"
    failures = [e for e in armed["recent_events"] if e["type"] == "failed"]
    assert failures and "runner exploded" in failures[0]["detail"]


def test_review_sink_is_skipped_when_the_definition_vanished(tmp_path):
    sink = RecordingSink()
    store = RecordingStore([_event_wf("wf-evt", review_queue=True)])

    def _run_and_delete(workflow_id, inputs):
        store.workflows.clear()
        return {"status": "ok"}

    service, _, _ = _service(tmp_path, [], store=store, run_workflow=_run_and_delete,
                             review_sink=sink)
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    _drain_fire_threads()
    assert sink.created == []


def test_review_sink_failure_is_not_counted_as_a_run_failure(tmp_path):
    sink = RecordingSink(explode=True)
    service, _, _ = _service(tmp_path, [_event_wf("wf-evt", review_queue=True)],
                             review_sink=sink)
    assert service.on_brain_event("kg_ingest.note", {"source_type": "note"}) == 1
    _drain_fire_threads()

    assert len(sink.created) == 1
    assert sink.created[0]["source"] == "kg_change_digest"
    # The run itself succeeded; only surfacing failed.
    assert service.describe()["armed"][0]["consecutive_failures"] == 0


# ── scheduler lifecycle ─────────────────────────────────────────────────

def _schedulers() -> List[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "trigger-scheduler"]


def test_scheduler_starts_once_survives_failing_ticks_and_stops(tmp_path, monkeypatch):
    service, _, _ = _service(tmp_path, [_interval_wf()], tick_seconds=0.01)
    ticked = threading.Event()

    def _failing_tick():
        ticked.set()
        raise RuntimeError("tick exploded")

    monkeypatch.setattr(service, "tick_intervals", _failing_tick)
    # Other suites may leave their own scheduler running; only count new ones.
    pre_existing = _schedulers()
    try:
        service.start()
        service.start()  # idempotent — no second scheduler thread
        assert ticked.wait(10) is True
        started = [
            thread for thread in _schedulers()
            if all(thread is not other for other in pre_existing)
        ]
        assert len(started) == 1
        assert service.describe()["running"] is True, "a failing tick must not kill the loop"
    finally:
        service.stop()
    assert service.describe()["running"] is False


def test_stop_before_start_is_a_no_op(tmp_path):
    service, _, _ = _service(tmp_path, [_interval_wf()])
    service.stop()
    assert service.describe()["running"] is False
