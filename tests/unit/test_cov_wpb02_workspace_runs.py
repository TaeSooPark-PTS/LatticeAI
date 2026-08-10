"""wpb02 branch coverage — agent/workflow run persistence.

Three shapes drive everything here, all against a real ``WorkspaceOSStore`` on
``tmp_path``:

* **the second record** — a store that already holds one run/workflow, so every
  back-fill scan has to walk past a non-matching row before it finds its own;
* **the record that vanished** — between the ``save_state`` that persists a run
  and the ``load_state`` that re-reads it to stamp the contract, another writer
  can replace the whole state document (a restore, a second process). The
  back-fill loop must then find nothing rather than raise. The moment is pinned
  deterministically: the contract builder is the last call before the re-read,
  so wrapping it is an exact seam;
* **the optional argument that was not given** — handoffs holding a non-dict, a
  handoff listing with no run filter, a workflow edit with no new name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from latticeai.core import workspace_runs as workspace_runs_mod
from latticeai.core.workspace_os import WorkspaceOSStore


@pytest.fixture()
def store(tmp_path: Path) -> WorkspaceOSStore:
    target = tmp_path / "workspace"
    target.mkdir()
    return WorkspaceOSStore(target)


def _agent_run(store: WorkspaceOSStore, **overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "agent_id": "agent:planner",
        "status": "queued",
        "input_text": "index my notes",
        "output_text": "",
        "user_email": "alice@example.com",
    }
    payload.update(overrides)
    return store.record_agent_run(**payload)


def _workflow(store: WorkspaceOSStore, name: str) -> Dict[str, Any]:
    return store.runs.create_workflow(
        name=name, steps=[{"id": "s1"}], user_email="alice@example.com"
    )


def _workflow_run(store: WorkspaceOSStore, workflow_id: str, **overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "workflow_id": workflow_id,
        "name": "nightly index",
        "status": "queued",
        "timeline": [],
    }
    payload.update(overrides)
    return store.runs.record_workflow_run(**payload)


def _forget_runs_when_the_contract_is_stamped(
    store: WorkspaceOSStore, monkeypatch, contract_name: str
) -> None:
    """Make the state lose its run rows at the contract-stamping moment."""
    forgetting = {"on": False}
    real_load = store.load_state
    real_contract = getattr(workspace_runs_mod, contract_name)

    def _load() -> Dict[str, Any]:
        state = real_load()
        if forgetting["on"]:
            state["agent_runs"] = []
            state["workflow_runs"] = []
        return state

    def _contract(run: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        forgetting["on"] = True
        return real_contract(run, **kwargs)

    monkeypatch.setattr(store, "load_state", _load)
    monkeypatch.setattr(workspace_runs_mod, contract_name, _contract)


# ── agent runs ──────────────────────────────────────────────────────────────


def test_recording_an_agent_run_survives_the_row_disappearing_before_the_backfill(
    store: WorkspaceOSStore, monkeypatch
):
    _forget_runs_when_the_contract_is_stamped(store, monkeypatch, "run_record_contract")

    run = _agent_run(store)

    assert run["contract"]["run_id"] == run["id"]
    assert store.load_state()["agent_runs"] == []


def test_updating_an_agent_run_survives_the_row_disappearing_before_the_backfill(
    store: WorkspaceOSStore, monkeypatch
):
    run_id = _agent_run(store)["id"]
    _forget_runs_when_the_contract_is_stamped(store, monkeypatch, "run_record_contract")

    updated = store.runs.update_agent_run(run_id, status="ok")

    assert updated["status"] == "ok"
    assert updated["contract"]["run_id"] == run_id


def test_updating_the_second_agent_run_walks_past_the_first(store: WorkspaceOSStore):
    first = _agent_run(store, input_text="first")["id"]
    second = _agent_run(store, input_text="second")["id"]
    assert first != second

    store.runs.update_agent_run(second, status="ok", output_text="done")

    rows = {row["id"]: row for row in store.load_state()["agent_runs"]}
    assert rows[second]["contract"]["status"] == "ok"
    assert rows[first]["contract"]["status"] == "queued"


def test_a_handoff_that_is_not_a_dict_is_dropped_from_the_stored_list(
    store: WorkspaceOSStore,
):
    run_id = _agent_run(store)["id"]

    store.runs.update_agent_run(
        run_id,
        handoffs=[{"from": "planner", "to": "executor"}, "planner -> executor"],
    )

    stored: List[Dict[str, Any]] = store.load_state()["handoffs"]
    assert [item["from"] for item in stored] == ["planner"]
    assert all(isinstance(item, dict) for item in stored)


def test_listing_handoffs_without_a_run_filter_returns_every_handoff(
    store: WorkspaceOSStore,
):
    first = _agent_run(store, input_text="first", handoffs=[{"from": "planner", "to": "executor"}])
    second = _agent_run(store, input_text="second", handoffs=[{"from": "executor", "to": "reviewer"}])

    listed = store.runs.list_handoffs()["handoffs"]

    assert {item["run_id"] for item in listed} == {first["id"], second["id"]}
    assert len(store.runs.list_handoffs(run_id=second["id"])["handoffs"]) == 1


# ── workflow runs ───────────────────────────────────────────────────────────


def test_recording_a_workflow_run_survives_the_row_disappearing_before_the_backfill(
    store: WorkspaceOSStore, monkeypatch
):
    workflow_id = _workflow(store, "Nightly")["id"]
    _forget_runs_when_the_contract_is_stamped(store, monkeypatch, "workflow_run_contract")

    run = _workflow_run(store, workflow_id)

    assert run["contract"]["run_id"] == run["id"]
    assert store.load_state()["workflow_runs"] == []


def test_updating_a_workflow_run_survives_the_row_disappearing_before_the_backfill(
    store: WorkspaceOSStore, monkeypatch
):
    workflow_id = _workflow(store, "Nightly")["id"]
    run_id = _workflow_run(store, workflow_id)["id"]
    _forget_runs_when_the_contract_is_stamped(store, monkeypatch, "workflow_run_contract")

    updated = store.runs.update_workflow_run(run_id, status="ok")

    assert updated["status"] == "ok"
    assert updated["contract"]["run_id"] == run_id


def test_a_run_for_the_second_workflow_walks_past_the_first(store: WorkspaceOSStore):
    _workflow(store, "First")
    second = _workflow(store, "Second")["id"]

    run = _workflow_run(store, second)

    workflows = {wf["id"]: wf for wf in store.load_state()["workflows"]}
    assert [event["type"] for event in workflows[second]["events"]] == ["created", "run"]
    assert [event["type"] for event in workflows[run["workflow_id"]]["events"]][-1] == "run"
    assert len(workflows) == 2


def test_updating_a_run_of_the_second_workflow_walks_past_the_first(
    store: WorkspaceOSStore,
):
    first = _workflow(store, "First")["id"]
    second = _workflow(store, "Second")["id"]
    run_id = _workflow_run(store, second)["id"]

    store.runs.update_workflow_run(run_id, status="ok")

    workflows = {wf["id"]: wf for wf in store.load_state()["workflows"]}
    assert [event["type"] for event in workflows[second]["events"]] == [
        "created", "run", "run_update",
    ]
    assert [event["type"] for event in workflows[first]["events"]] == ["created"]


def test_updating_the_second_workflow_run_walks_past_the_first(store: WorkspaceOSStore):
    workflow_id = _workflow(store, "Nightly")["id"]
    first = _workflow_run(store, workflow_id, name="first")["id"]
    second = _workflow_run(store, workflow_id, name="second")["id"]
    assert first != second

    store.runs.update_workflow_run(second, status="ok")

    rows = {row["id"]: row for row in store.load_state()["workflow_runs"]}
    assert rows[second]["contract"]["status"] == "ok"
    assert rows[first]["contract"]["status"] == "queued"


def test_editing_a_workflow_without_a_new_name_keeps_the_old_one(store: WorkspaceOSStore):
    workflow_id = _workflow(store, "Nightly")["id"]

    updated = store.runs.update_workflow_definition(
        workflow_id, nodes=[{"id": "n1", "type": "tool"}]
    )

    assert updated["name"] == "Nightly"
    assert updated["nodes"] == [{"id": "n1", "type": "tool"}]
    assert [event["type"] for event in updated["events"]] == ["created", "edited"]
