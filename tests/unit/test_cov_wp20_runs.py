"""Agent and workflow run persistence: graph fan-out, patches, and replay."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.core.workspace_runs import WorkspaceRuns


class RecordingGraph:
    """Accepts every event and hands back a node id."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def ingest_event(self, event_type: str, title: str, **kwargs: Any) -> Dict[str, Any]:
        self.events.append({"event_type": event_type, "title": title, **kwargs})
        return {"node_id": "node-" + str(len(self.events))}


class RefusingGraph:
    """Stands in for a graph that is disabled, locked, or out of disk."""

    def ingest_event(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("graph refused the event")


def _store(tmp_path: Path, name: str = "runs") -> WorkspaceOSStore:
    target = tmp_path / name
    target.mkdir()
    return WorkspaceOSStore(target)


def _timeline_types(store: WorkspaceOSStore) -> List[str]:
    return [event.get("event_type") for event in store.load_state()["timeline"]]


def _agent_run(store: WorkspaceOSStore, **overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "agent_id": "agent:planner",
        "status": "queued",
        "input_text": "index my notes",
        "output_text": "",
        "user_email": "alice@example.com",
        "mode": "live",
    }
    payload.update(overrides)
    return store.record_agent_run(**payload)


def test_failed_agent_run_keeps_the_graph_error_and_emits_the_failure_event(tmp_path: Path):
    store = _store(tmp_path)

    run = _agent_run(store, status="failed", output_text="boom", graph=RefusingGraph())

    assert run["graph_error"] == "graph refused the event"
    assert "graph_node_id" not in run
    assert "execution_failed" in _timeline_types(store)


def test_updating_an_agent_run_ingests_it_once_it_reaches_a_terminal_state(tmp_path: Path):
    store = _store(tmp_path)
    run_id = _agent_run(store)["id"]
    graph = RecordingGraph()

    updated = store.update_agent_run(run_id, status="ok", output_text="done", graph=graph)

    assert updated["graph_node_id"] == "node-1"
    assert updated["output_preview"] == "done"
    assert updated["completed_at"]
    assert graph.events[0]["metadata"]["run_id"] == run_id
    # A second terminal update must not double-ingest the same run.
    store.update_agent_run(run_id, status="ok", graph=graph)
    assert len(graph.events) == 1


def test_agent_run_update_records_why_the_graph_refused(tmp_path: Path):
    store = _store(tmp_path)
    run_id = _agent_run(store)["id"]

    updated = store.update_agent_run(run_id, status="failed", graph=RefusingGraph())

    assert updated["graph_error"] == "graph refused the event"
    assert "execution_failed" in _timeline_types(store)


def test_interrupted_agent_run_update_emits_the_interrupt_event(tmp_path: Path):
    store = _store(tmp_path)
    run_id = _agent_run(store)["id"]

    store.update_agent_run(run_id, status="interrupted")

    assert "execution_interrupted" in _timeline_types(store)


def test_unknown_agent_runs_are_reported_not_invented(tmp_path: Path):
    store = _store(tmp_path)
    run = _agent_run(store)

    with pytest.raises(FileNotFoundError):
        store.update_agent_run("agent-run-ghost", status="ok")
    with pytest.raises(FileNotFoundError):
        store.get_agent_run("agent-run-ghost")
    with pytest.raises(FileNotFoundError):
        store.get_agent_run(run["id"], workspace_id="org-other")


def test_creating_a_workflow_links_it_to_the_graph(tmp_path: Path):
    store = _store(tmp_path)
    graph = RecordingGraph()

    workflow = store.create_workflow(
        name="Nightly digest", steps=[{"id": "s1"}], user_email=None, graph=graph
    )
    refused = store.create_workflow(
        name="Broken", steps=[], user_email=None, graph=RefusingGraph()
    )

    assert workflow["graph_node_id"] == "node-1"
    assert graph.events[0]["metadata"]["workflow_id"] == workflow["id"]
    assert refused["graph_error"] == "graph refused the event"


def test_workflow_runs_are_ingested_and_cross_linked_to_their_workflow(tmp_path: Path):
    store = _store(tmp_path)
    workflow = store.create_workflow(name="Digest", steps=[], user_email=None)
    graph = RecordingGraph()

    ok_run = store.record_workflow_run(
        workflow_id=workflow["id"], name="Digest", status="ok", timeline=[], graph=graph, mode="live"
    )
    failed_run = store.record_workflow_run(
        workflow_id=workflow["id"],
        name="Digest",
        status="failed",
        timeline=[],
        graph=RefusingGraph(),
        mode="live",
    )

    assert ok_run["graph_node_id"] == "node-1"
    assert failed_run["graph_error"] == "graph refused the event"
    events = store.get_workflow(workflow["id"])["events"]
    assert [event["payload"]["run_id"] for event in events if event["type"] == "run"] == [
        ok_run["id"],
        failed_run["id"],
    ]
    assert "execution_failed" in _timeline_types(store)


def test_updating_a_workflow_run_patches_history_and_graph_links(tmp_path: Path):
    store = _store(tmp_path)
    workflow = store.create_workflow(name="Digest", steps=[], user_email=None)
    run_id = store.record_workflow_run(
        workflow_id=workflow["id"], name="Digest", status="queued", timeline=[], mode="live"
    )["id"]
    graph = RecordingGraph()

    updated = store.update_workflow_run(run_id, status="ok", graph=graph)

    assert updated["graph_node_id"] == "node-1"
    assert updated["completed_at"]
    assert [event["type"] for event in store.get_workflow(workflow["id"])["events"]] == [
        "created",
        "run",
        "run_update",
    ]

    with pytest.raises(FileNotFoundError):
        store.update_workflow_run("workflow-run-ghost", status="ok")


def test_workflow_run_terminal_states_each_emit_their_own_event(tmp_path: Path):
    store = _store(tmp_path)

    def fresh_run(status: str) -> str:
        return store.record_workflow_run(
            workflow_id=None, name="Digest", status="queued", timeline=[], mode="live"
        )["id"]

    store.update_workflow_run(fresh_run("failed"), status="failed", graph=RefusingGraph())
    store.update_workflow_run(fresh_run("cancelled"), status="cancelled")
    store.update_workflow_run(fresh_run("interrupted"), status="interrupted")

    emitted = _timeline_types(store)
    assert "execution_failed" in emitted
    assert "execution_cancelled" in emitted
    assert "execution_interrupted" in emitted


def test_workflow_run_listing_can_be_narrowed_to_one_workflow(tmp_path: Path):
    store = _store(tmp_path)
    store.record_workflow_run(workflow_id="wf-a", name="A", status="ok", timeline=[])
    store.record_workflow_run(workflow_id="wf-b", name="B", status="ok", timeline=[])

    listed = store.list_workflow_runs(workflow_id="wf-a")

    assert [run["workflow_id"] for run in listed["runs"]] == ["wf-a"]


def test_activity_run_titles_fall_back_through_the_input_payload():
    assert WorkspaceRuns.activity_run_title({"name": "Nightly digest"}) == "Nightly digest"
    assert WorkspaceRuns.activity_run_title({"input": "  raw goal  "}) == "raw goal"
    assert WorkspaceRuns.activity_run_title({"input": {"prompt": "summarize"}}) == "summarize"
    assert WorkspaceRuns.activity_run_title({"input": {"note": "unused"}}) == ""
    assert WorkspaceRuns.activity_run_title({}) == ""


def test_activity_rows_infer_a_source_when_the_caller_supplies_none():
    workflow_row = WorkspaceRuns.activity_run_row({"workflow_id": "wf-a", "status": "queued"}, source="")
    agent_row = WorkspaceRuns.activity_run_row({"status": "paused"}, source="bogus")

    assert workflow_row["source"] == "workflow"
    assert workflow_row["can_stop"] is True
    assert agent_row["source"] == "agent"
    assert agent_row["can_resume"] is True


def test_combined_run_feed_merges_agent_and_workflow_rows(tmp_path: Path):
    store = _store(tmp_path)
    _agent_run(store, status="ok", output_text="done")
    store.record_workflow_run(workflow_id="wf-a", name="Digest", status="ok", timeline=[])

    combined = store.list_combined_runs(limit=5)

    assert combined["total"] == 2
    assert {row["source"] for row in combined["runs"]} == {"agent", "workflow"}
    assert combined["truncated"] is False


def test_resolving_a_paused_run_records_the_decision_once(tmp_path: Path):
    store = _store(tmp_path)
    run_id = store.record_workflow_run(
        workflow_id="wf-a", name="Digest", status="paused", timeline=[],
        pause={"reason": "approval"},
    )["id"]

    resolved = store.mark_workflow_run_resolved(run_id, resumed_run_id="run-2", approved=True)
    denied_id = store.record_workflow_run(
        workflow_id="wf-a", name="Digest", status="paused", timeline=[]
    )["id"]
    denied = store.mark_workflow_run_resolved(denied_id, resumed_run_id="run-3", approved=False)

    assert resolved["status"] == "resumed"
    assert resolved["resumed_run_id"] == "run-2"
    assert resolved["resolved_at"]
    assert denied["status"] == "denied"
    with pytest.raises(FileNotFoundError):
        store.mark_workflow_run_resolved("workflow-run-ghost", resumed_run_id="x", approved=True)
    with pytest.raises(FileNotFoundError):
        store.get_workflow_run(run_id, workspace_id="org-other")


def test_workflow_runs_replay_frame_by_frame(tmp_path: Path):
    store = _store(tmp_path)
    run_id = store.record_workflow_run(
        workflow_id="wf-a",
        name="Digest",
        status="ok",
        timeline=[{"event": "step_started", "node": "fetch", "timestamp": "2026-01-01T00:00:00"}],
        outputs={"summary": "3 items"},
    )["id"]

    replay = store.replay_workflow_run(run_id)

    assert replay["kind"] == "workflow"
    assert replay["replayable"] is True
    assert replay["outputs"] == {"summary": "3 items"}
    assert replay["frames"][0]["actor"] == "fetch"
    assert replay["contract"]["kind"] == "workflow_run"


def test_replay_frames_describe_each_timeline_entry():
    frames = WorkspaceOSStore._replay_frames(
        {
            "created_at": "2026-01-01T00:00:00",
            "input": "goal",
            "timeline": [{"type": "handoff", "role": "reviewer", "output": "looks good"}],
        },
        kind="agent",
    )

    assert frames[0]["event"] == "handoff"
    assert frames[0]["actor"] == "reviewer"
    assert frames[0]["output"] == "looks good"
    assert frames[0]["when"] == "2026-01-01T00:00:00"


def test_editing_a_workflow_keeps_its_history(tmp_path: Path):
    store = _store(tmp_path)
    workflow = store.create_workflow(name="Digest", steps=[{"id": "s1"}], user_email=None)

    edited = store.update_workflow_definition(
        workflow["id"],
        name="  Daily digest  ",
        nodes=[{"id": "n1", "type": "fetch"}],
        metadata={"owner": "alice"},
    )

    assert edited["name"] == "Daily digest"
    assert edited["nodes"] == [{"id": "n1", "type": "fetch"}]
    assert edited["metadata"] == {"owner": "alice"}
    assert [event["type"] for event in edited["events"]] == ["created", "edited"]
    with pytest.raises(FileNotFoundError):
        store.update_workflow_definition("workflow-ghost", name="x")


def test_workflow_search_matches_names_and_step_bodies(tmp_path: Path):
    store = _store(tmp_path)
    store.create_workflow(name="Nightly digest", steps=[{"tool": "summarize"}], user_email=None)
    store.create_workflow(name="Backup", steps=[{"tool": "copy_files"}], user_email=None)

    assert [wf["name"] for wf in store.list_workflows()["workflows"]] == ["Backup", "Nightly digest"]
    assert [wf["name"] for wf in store.list_workflows("digest")["workflows"]] == ["Nightly digest"]
    assert [wf["name"] for wf in store.list_workflows("copy_files")["workflows"]] == ["Backup"]
    assert store.list_workflows("nothing-matches")["workflows"] == []


def test_workflow_events_are_appended_to_the_stored_definition(tmp_path: Path):
    store = _store(tmp_path)
    workflow = store.create_workflow(name="Digest", steps=[], user_email=None)

    updated = store.record_workflow_event(workflow["id"], "paused", {"reason": "approval"})

    assert updated["events"][-1]["type"] == "paused"
    assert updated["events"][-1]["payload"] == {"reason": "approval"}
    assert "workflow_event" in _timeline_types(store)
    with pytest.raises(FileNotFoundError):
        store.record_workflow_event("workflow-ghost", "paused")
